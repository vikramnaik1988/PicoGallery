package assets

import (
	"bytes"
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
	"os/exec"
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
	db           *sql.DB
	originalsRoot string // HDD — where original files are stored
	thumbRoot    string  // SSD — where all thumbnail tiers are cached
	tmpRoot      string  // SSD — upload staging area
	thumbQ       chan thumbJob
	quality      int
	wg           sync.WaitGroup
}

// NewService creates the asset service with a hybrid split-drive layout.
//
//   - originalsRoot: path on the slow HDD for original files
//   - thumbRoot:     path on the fast SSD for all thumbnail tiers
//   - tmpRoot:       path on the fast SSD for upload staging
func NewService(db *sql.DB, originalsRoot, thumbRoot, tmpRoot string, workers, quality int) *Service {
	for _, dir := range []string{
		originalsRoot,
		thumbRoot,
		tmpRoot,
		// Pre-create thumbnail sub-directories so workers never block on mkdir
		filepath.Join(thumbRoot, "small"),
		filepath.Join(thumbRoot, "preview"),
		filepath.Join(thumbRoot, "large"),
		filepath.Join(thumbRoot, "blur"),
	} {
		_ = os.MkdirAll(dir, 0755)
	}

	s := &Service{
		db:           db,
		originalsRoot: originalsRoot,
		thumbRoot:    thumbRoot,
		tmpRoot:      tmpRoot,
		thumbQ:       make(chan thumbJob, 512),
		quality:      quality,
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
		_, _ = s.db.Exec(
			`UPDATE assets SET thumb_small=1, thumb_preview=1, thumb_large=1, thumb_blur=1 WHERE id=?`,
			job.assetID,
		)
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
// The original is written to originalsRoot (HDD), staging uses tmpRoot (SSD).
func (s *Service) SaveAsset(userID, filename, mediaType string, r io.Reader, deviceAssetID, deviceID string, takenAtOverride *time.Time) (*Asset, bool, error) {
	// Stage to SSD tmp dir — fast write, avoids touching the HDD until we know the file is good
	tmpFile, err := os.CreateTemp(s.tmpRoot, "upload_*")
	if err != nil {
		return nil, false, fmt.Errorf("creating temp file: %w", err)
	}
	tmpPath := tmpFile.Name()

	// Stream + hash simultaneously
	h := sha256.New()
	written, err := io.Copy(io.MultiWriter(tmpFile, h), r)
	tmpFile.Close() // close before rename/delete on Windows
	if err != nil {
		_ = os.Remove(tmpPath)
		return nil, false, fmt.Errorf("streaming upload: %w", err)
	}
	checksum := hex.EncodeToString(h.Sum(nil))

	// Deduplicate by checksum
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

	// Determine final path on HDD: {originalsRoot}/{userID}/{YYYY}/{MM}/{assetID}.ext
	id := "ast_" + uuid.NewString()
	now := time.Now().UTC()
	dir := filepath.Join(s.originalsRoot, userID, now.Format("2006"), now.Format("01"))
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

	// Move SSD tmp → HDD final (handles cross-device via copy+delete fallback)
	if err := moveFile(tmpPath, finalPath); err != nil {
		_ = os.Remove(tmpPath)
		return nil, false, fmt.Errorf("moving file: %w", err)
	}

	// Extract EXIF and dimensions
	exifData, width, height := extractEXIF(finalPath)

	// Determine taken_at: override → EXIF datetime → now
	var takenAtStr *string
	if takenAtOverride != nil {
		s2 := takenAtOverride.UTC().Format(time.RFC3339)
		takenAtStr = &s2
	} else {
		if t := extractEXIFDateTime(finalPath); t != nil {
			s2 := t.UTC().Format(time.RFC3339)
			takenAtStr = &s2
		} else {
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

// thumbSpec defines a thumbnail tier.
type thumbSpec struct {
	name    string // folder name under thumbRoot
	maxSide int    // max dimension in pixels
	square  bool   // true = center-crop to square; false = fit aspect ratio
	quality int    // JPEG quality override; 0 = use job.quality
}

// thumbnailTiers are all four SSD-cached thumbnail sizes.
var thumbnailTiers = []thumbSpec{
	// blur: tiny 32×32 JPEG placeholder for progressive loading.
	// Clients display it stretched + CSS blur(20px) while the real image loads.
	{name: "blur", maxSide: 32, square: true, quality: 40},
	// small: 256×256 square grid thumbnail.
	{name: "small", maxSide: 256, square: true, quality: 0},
	// preview: 720 px longest-side fit for the detail/swipe view.
	{name: "preview", maxSide: 720, square: false, quality: 0},
	// large: 1080 px longest-side fit for full-screen on Retina/high-DPI phones.
	{name: "large", maxSide: 1080, square: false, quality: 0},
}

func generateVideoThumbnails(job thumbJob) error {
	// Extract a frame at 1s (fall back to first frame if video is shorter)
	tmp, err := os.CreateTemp("", "vthumb_*.jpg")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	tmp.Close()
	defer os.Remove(tmpPath)

	// Try at 1 second first
	cmd := exec.Command("ffmpeg", "-y", "-ss", "00:00:01", "-i", job.originalPath,
		"-vframes", "1", "-q:v", "2", tmpPath)
	if err := cmd.Run(); err != nil {
		// Fall back to very first frame
		cmd2 := exec.Command("ffmpeg", "-y", "-i", job.originalPath,
			"-vframes", "1", "-q:v", "2", tmpPath)
		if err2 := cmd2.Run(); err2 != nil {
			return fmt.Errorf("ffmpeg frame extract: %w", err2)
		}
	}

	f, err := os.Open(tmpPath)
	if err != nil {
		return err
	}
	img, _, err := image.Decode(f)
	f.Close()
	if err != nil {
		return fmt.Errorf("decode video frame: %w", err)
	}

	for _, spec := range thumbnailTiers {
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
		q := spec.quality
		if q == 0 {
			q = job.quality
		}
		err = jpeg.Encode(out, dst, &jpeg.Options{Quality: q})
		out.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func generateThumbnails(job thumbJob) error {
	if strings.HasPrefix(job.mediaType, "video/") {
		return generateVideoThumbnails(job)
	}
	if !strings.HasPrefix(job.mediaType, "image/") {
		return nil // non-image/non-video files have no thumbnail
	}

	var img image.Image
	if isHEICType(job.mediaType) {
		var err error
		img, err = decodeHEIC(job.originalPath)
		if err != nil {
			return fmt.Errorf("HEIC: %w", err)
		}
	} else {
		f, err := os.Open(job.originalPath)
		if err != nil {
			return err
		}
		defer f.Close()
		img, _, err = image.Decode(f)
		if err != nil {
			return fmt.Errorf("decode: %w", err)
		}
	}

	for _, spec := range thumbnailTiers {
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

		q := spec.quality
		if q == 0 {
			q = job.quality
		}
		err = jpeg.Encode(out, dst, &jpeg.Options{Quality: q})
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
	make_, model          string
	lat, lng              float64
	focal, aperture       float64
	iso                   int
	shutter               string
	hasMake, hasModel     bool
	hasGPS                bool
	hasFocal, hasAperture bool
	hasISO, hasShutter    bool
}

// extractEXIFFromReader reads EXIF metadata and pixel dimensions from rs.
// rs must be seekable (supports io.SeekStart) so it can be read twice.
func extractEXIFFromReader(rs io.ReadSeeker) (*rawEXIF, *int, *int) {
	var w, h int
	if cfg, _, err := image.DecodeConfig(rs); err == nil {
		w, h = cfg.Width, cfg.Height
	}
	_, _ = rs.Seek(0, io.SeekStart)

	x, err := exif.Decode(rs)
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

// extractEXIF extracts EXIF metadata and pixel dimensions from the file at path.
// For HEIC/HEIF files it first converts to JPEG via an external tool (ImageMagick
// or heif-convert) so that goexif can parse the embedded EXIF data.
func extractEXIF(path string) (*rawEXIF, *int, *int) {
	if isHEICPath(path) {
		data := heicToJPEGBytes(path)
		if data == nil {
			return nil, nil, nil
		}
		return extractEXIFFromReader(bytes.NewReader(data))
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, nil
	}
	defer f.Close()
	return extractEXIFFromReader(f)
}

// extractEXIFDateTime returns the capture time embedded in a file's EXIF.
// For HEIC/HEIF the file is converted to JPEG first so goexif can read it.
func extractEXIFDateTime(path string) *time.Time {
	var rs io.ReadSeeker
	if isHEICPath(path) {
		data := heicToJPEGBytes(path)
		if data == nil {
			return nil
		}
		rs = bytes.NewReader(data)
	} else {
		f, err := os.Open(path)
		if err != nil {
			return nil
		}
		defer f.Close()
		rs = f
	}
	x, err := exif.Decode(rs)
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
