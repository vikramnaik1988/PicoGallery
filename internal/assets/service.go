package assets

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/gif"
	_ "image/png"
	"io"
	"log"
	"mime"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/rwcarlsen/goexif/exif"
	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

type Asset struct {
	ID              string    `json:"id"`
	Filename        string    `json:"filename"`
	MediaType       string    `json:"media_type"`
	FileSizeBytes   int64     `json:"file_size_bytes"`
	Width           *int      `json:"width"`
	Height          *int      `json:"height"`
	DurationSeconds *float64  `json:"duration_seconds"`
	TakenAt         *string   `json:"taken_at"`
	CreatedAt       string    `json:"created_at"`
	IsFavorited     bool      `json:"is_favorited"`
	IsArchived      bool      `json:"is_archived"`
	ChecksumSHA256  string    `json:"checksum_sha256"`
	EXIF            *EXIFData `json:"exif,omitempty"`
	ThumbnailURL    string    `json:"thumbnail_url"`
	OriginalURL     string    `json:"original_url"`
}

type EXIFData struct {
	Make          *string  `json:"make,omitempty"`
	Model         *string  `json:"model,omitempty"`
	GPSLat        *float64 `json:"gps_lat,omitempty"`
	GPSLng        *float64 `json:"gps_lng,omitempty"`
	FocalLengthMM *float64 `json:"focal_length_mm,omitempty"`
	Aperture      *float64 `json:"aperture,omitempty"`
	ISO           *int     `json:"iso,omitempty"`
	ShutterSpeed  *string  `json:"shutter_speed,omitempty"`
}

type thumbJob struct {
	assetID      string
	originalPath string
	mediaType    string
	quality      int
	thumbRoot    string
}

// Service manages asset operations.
type Service struct {
	db          *sql.DB
	storageRoot string
	thumbRoot   string
	thumbQ      chan thumbJob
	quality     int
	wg          sync.WaitGroup
}

func NewService(db *sql.DB, storageRoot string, workers, quality int) *Service {
	thumbRoot := filepath.Join(storageRoot, ".thumbnails")
	_ = os.MkdirAll(thumbRoot, 0755)
	_ = os.MkdirAll(filepath.Join(storageRoot, "originals"), 0755)
	_ = os.MkdirAll(filepath.Join(storageRoot, "tmp"), 0755)

	s := &Service{
		db:          db,
		storageRoot: storageRoot,
		thumbRoot:   thumbRoot,
		thumbQ:      make(chan thumbJob, 512),
		quality:     quality,
	}
	for i := 0; i < workers; i++ {
		s.wg.Add(1)
		go s.thumbWorker()
	}
	return s
}

func (s *Service) Shutdown() {
	close(s.thumbQ)
	s.wg.Wait()
}

func (s *Service) thumbWorker() {
	defer s.wg.Done()
	for job := range s.thumbQ {
		if err := generateThumbnails(job); err != nil {
			log.Printf("thumbnail error for %s: %v", job.assetID, err)
			continue
		}
		_, _ = s.db.Exec(`UPDATE assets SET thumb_small=1, thumb_preview=1 WHERE id=?`, job.assetID)
	}
}

func (s *Service) EnqueueThumbnail(assetID, originalPath, mediaType string) {
	select {
	case s.thumbQ <- thumbJob{
		assetID:      assetID,
		originalPath: originalPath,
		mediaType:    mediaType,
		quality:      s.quality,
		thumbRoot:    s.thumbRoot,
	}:
	default:
		log.Printf("thumbnail queue full, skipping %s", assetID)
	}
}

// SaveAsset saves an uploaded file and its metadata to the database.
func (s *Service) SaveAsset(userID, filename, mediaType string, r io.Reader, deviceAssetID, deviceID string, takenAtOverride *time.Time) (*Asset, bool, error) {
	// Write to a named temp file in our tmp dir (avoids Windows open-file-delete issues)
	tmpDir := filepath.Join(s.storageRoot, "tmp")
	tmpFile, err := os.CreateTemp(tmpDir, "upload_*")
	if err != nil {
		return nil, false, fmt.Errorf("creating temp file: %w", err)
	}
	tmpPath := tmpFile.Name()

	// Stream + hash
	h := sha256.New()
	written, err := io.Copy(io.MultiWriter(tmpFile, h), r)
	tmpFile.Close() // close before any rename/delete on Windows
	if err != nil {
		_ = os.Remove(tmpPath)
		return nil, false, fmt.Errorf("streaming upload: %w", err)
	}
	checksum := hex.EncodeToString(h.Sum(nil))

	// Deduplicate
	var existingID string
	err = s.db.QueryRow(`SELECT id FROM assets WHERE user_id=? AND checksum_sha256=?`, userID, checksum).Scan(&existingID)
	if err == nil {
		_ = os.Remove(tmpPath)
		return &Asset{ID: existingID}, true, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		_ = os.Remove(tmpPath)
		return nil, false, err
	}

	// Determine final storage path
	id := "ast_" + uuid.NewString()
	now := time.Now().UTC()
	dir := filepath.Join(s.storageRoot, "originals", userID, now.Format("2006"), now.Format("01"))
	if err := os.MkdirAll(dir, 0755); err != nil {
		_ = os.Remove(tmpPath)
		return nil, false, fmt.Errorf("mkdir: %w", err)
	}

	ext := filepath.Ext(filename)
	if ext == "" {
		exts, _ := mime.ExtensionsByType(mediaType)
		if len(exts) > 0 {
			ext = exts[0]
		}
	}
	finalPath := filepath.Join(dir, id+ext)

	// Move temp → final (os.Rename works cross-drive on Windows via copy+delete)
	if err := moveFile(tmpPath, finalPath); err != nil {
		_ = os.Remove(tmpPath)
		return nil, false, fmt.Errorf("moving file: %w", err)
	}

	// Extract EXIF and dimensions
	exifData, width, height := extractEXIF(finalPath)

	// Determine taken_at: override → EXIF datetime → file mod time → now
	var takenAtStr *string
	if takenAtOverride != nil {
		s2 := takenAtOverride.UTC().Format(time.RFC3339)
		takenAtStr = &s2
	} else {
		// Try EXIF DateTime
		if t := extractEXIFDateTime(finalPath); t != nil {
			s2 := t.UTC().Format(time.RFC3339)
			takenAtStr = &s2
		} else {
			// Fall back to now
			s2 := now.Format(time.RFC3339)
			takenAtStr = &s2
		}
	}

	nowStr := now.Format(time.RFC3339)
	_, err = s.db.Exec(`
		INSERT INTO assets(
			id,user_id,filename,original_path,media_type,file_size_bytes,
			width,height,taken_at,created_at,checksum_sha256,
			device_asset_id,device_id,
			exif_make,exif_model,exif_gps_lat,exif_gps_lng,
			exif_focal_mm,exif_aperture,exif_iso,exif_shutter
		) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		id, userID, filename, finalPath, mediaType, written,
		width, height, takenAtStr, nowStr, checksum,
		deviceAssetID, deviceID,
		exifStr(exifData, "make"), exifStr(exifData, "model"),
		exifFloat(exifData, "lat"), exifFloat(exifData, "lng"),
		exifFloat(exifData, "focal"), exifFloat(exifData, "aperture"),
		exifInt(exifData, "iso"), exifStr(exifData, "shutter"),
	)
	if err != nil {
		_ = os.Remove(finalPath)
		return nil, false, fmt.Errorf("db insert: %w", err)
	}

	s.EnqueueThumbnail(id, finalPath, mediaType)

	log.Printf("saved asset %s (%s, %d bytes) for user %s", id, filename, written, userID)

	return &Asset{
		ID:             id,
		Filename:       filename,
		MediaType:      mediaType,
		FileSizeBytes:  written,
		ChecksumSHA256: checksum,
		CreatedAt:      nowStr,
		TakenAt:        takenAtStr,
	}, false, nil
}

// moveFile copies src to dst then removes src — works across drives on Windows.
func moveFile(src, dst string) error {
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	// Rename failed (cross-device) — fall back to copy+delete
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	if _, err := io.Copy(out, in); err != nil {
		_ = os.Remove(dst)
		return err
	}
	in.Close()
	_ = os.Remove(src)
	return nil
}

func generateThumbnails(job thumbJob) error {
	if !strings.HasPrefix(job.mediaType, "image/") {
		return nil // non-image files have no thumbnail
	}

	f, err := os.Open(job.originalPath)
	if err != nil {
		return err
	}
	defer f.Close()

	img, _, err := image.Decode(f)
	if err != nil {
		return fmt.Errorf("decode: %w", err)
	}

	for _, spec := range []struct {
		name    string
		maxSide int
		square  bool
	}{
		{"small", 256, true},
		{"preview", 720, false},
	} {
		var dst image.Image
		if spec.square {
			dst = cropSquare(img, spec.maxSide)
		} else {
			dst = resizeFit(img, spec.maxSide)
		}
		dir := filepath.Join(job.thumbRoot, spec.name, job.assetID[:2])
		if err := os.MkdirAll(dir, 0755); err != nil {
			return err
		}
		out, err := os.Create(filepath.Join(dir, job.assetID+".jpg"))
		if err != nil {
			return err
		}
		err = jpeg.Encode(out, dst, &jpeg.Options{Quality: job.quality})
		out.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func cropSquare(img image.Image, size int) image.Image {
	b := img.Bounds()
	w, h := b.Max.X, b.Max.Y
	side := w
	if h < w {
		side = h
	}
	x0 := (w - side) / 2
	y0 := (h - side) / 2
	cropped := image.NewRGBA(image.Rect(0, 0, side, side))
	draw.Draw(cropped, cropped.Bounds(), img, image.Point{x0, y0}, draw.Src)
	dst := image.NewRGBA(image.Rect(0, 0, size, size))
	draw.CatmullRom.Scale(dst, dst.Bounds(), cropped, cropped.Bounds(), draw.Over, nil)
	return dst
}

func resizeFit(img image.Image, maxSide int) image.Image {
	b := img.Bounds()
	w, h := b.Max.X, b.Max.Y
	scale := float64(maxSide) / float64(w)
	if float64(h)*scale > float64(maxSide) {
		scale = float64(maxSide) / float64(h)
	}
	nw := int(float64(w) * scale)
	nh := int(float64(h) * scale)
	if nw < 1 {
		nw = 1
	}
	if nh < 1 {
		nh = 1
	}
	dst := image.NewRGBA(image.Rect(0, 0, nw, nh))
	draw.CatmullRom.Scale(dst, dst.Bounds(), img, img.Bounds(), draw.Over, nil)
	return dst
}

type rawEXIF struct {
	make_, model            string
	lat, lng                float64
	focal, aperture         float64
	iso                     int
	shutter                 string
	hasMake, hasModel       bool
	hasGPS                  bool
	hasFocal, hasAperture   bool
	hasISO, hasShutter      bool
}

func extractEXIF(path string) (*rawEXIF, *int, *int) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, nil
	}
	defer f.Close()

	var w, h int
	if cfg, _, err2 := image.DecodeConfig(f); err2 == nil {
		w, h = cfg.Width, cfg.Height
	}
	_, _ = f.Seek(0, io.SeekStart)

	x, err := exif.Decode(f)
	if err != nil {
		if w > 0 {
			return nil, &w, &h
		}
		return nil, nil, nil
	}

	r := &rawEXIF{}
	if v, err := x.Get(exif.Make); err == nil {
		r.make_, _ = v.StringVal()
		r.hasMake = true
	}
	if v, err := x.Get(exif.Model); err == nil {
		r.model, _ = v.StringVal()
		r.hasModel = true
	}
	if lat, lng, err := x.LatLong(); err == nil {
		r.lat, r.lng = lat, lng
		r.hasGPS = true
	}
	if v, err := x.Get(exif.FocalLength); err == nil {
		num, denom, _ := v.Rat2(0)
		if denom != 0 {
			r.focal = float64(num) / float64(denom)
			r.hasFocal = true
		}
	}
	if v, err := x.Get(exif.FNumber); err == nil {
		num, denom, _ := v.Rat2(0)
		if denom != 0 {
			r.aperture = float64(num) / float64(denom)
			r.hasAperture = true
		}
	}
	if v, err := x.Get(exif.ISOSpeedRatings); err == nil {
		iso, _ := v.Int(0)
		r.iso = iso
		r.hasISO = true
	}
	if v, err := x.Get(exif.ExposureTime); err == nil {
		r.shutter, _ = v.StringVal()
		r.hasShutter = true
	}
	if w > 0 {
		return r, &w, &h
	}
	return r, nil, nil
}

func extractEXIFDateTime(path string) *time.Time {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	x, err := exif.Decode(f)
	if err != nil {
		return nil
	}
	t, err := x.DateTime()
	if err != nil {
		return nil
	}
	return &t
}

func exifStr(r *rawEXIF, field string) *string {
	if r == nil {
		return nil
	}
	switch field {
	case "make":
		if r.hasMake {
			return &r.make_
		}
	case "model":
		if r.hasModel {
			return &r.model
		}
	case "shutter":
		if r.hasShutter {
			return &r.shutter
		}
	}
	return nil
}

func exifFloat(r *rawEXIF, field string) *float64 {
	if r == nil {
		return nil
	}
	switch field {
	case "lat":
		if r.hasGPS {
			return &r.lat
		}
	case "lng":
		if r.hasGPS {
			return &r.lng
		}
	case "focal":
		if r.hasFocal {
			return &r.focal
		}
	case "aperture":
		if r.hasAperture {
			return &r.aperture
		}
	}
	return nil
}

func exifInt(r *rawEXIF, field string) *int {
	if r == nil {
		return nil
	}
	if field == "iso" && r.hasISO {
		return &r.iso
	}
	return nil
}
