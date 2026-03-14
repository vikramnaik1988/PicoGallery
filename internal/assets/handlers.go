package assets

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/picogallery/picogallery/internal/auth"
)

type Handler struct {
	svc *Service
	db  *sql.DB
}

func NewHandler(svc *Service, db *sql.DB) *Handler {
	return &Handler{svc: svc, db: db}
}

// POST /api/v1/assets/upload
func (h *Handler) Upload(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())

	// 500 MB max in-memory parse
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeError(w, "BAD_REQUEST", "Could not parse multipart form.", http.StatusBadRequest)
		return
	}

	file, fh, err := r.FormFile("file")
	if err != nil {
		writeError(w, "BAD_REQUEST", "Missing 'file' field.", http.StatusBadRequest)
		return
	}
	defer file.Close()

	mediaType := fh.Header.Get("Content-Type")
	if mediaType == "" || mediaType == "application/octet-stream" {
		mediaType = detectMediaType(fh.Filename)
	}

	deviceAssetID := r.FormValue("device_asset_id")
	deviceID := r.FormValue("device_id")

	var takenAt *time.Time
	if s := r.FormValue("taken_at"); s != "" {
		if t, err := time.Parse(time.RFC3339, s); err == nil {
			takenAt = &t
		}
	}

	asset, isDup, err := h.svc.SaveAsset(user.ID, fh.Filename, mediaType, file, deviceAssetID, deviceID, takenAt)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", fmt.Sprintf("Upload failed: %v", err), http.StatusInternalServerError)
		return
	}

	status := "created"
	httpStatus := http.StatusCreated
	if isDup {
		status = "duplicate"
		httpStatus = http.StatusOK
	}
	writeJSON(w, httpStatus, map[string]interface{}{
		"id":        asset.ID,
		"status":    status,
		"duplicate": isDup,
	})
}

// POST /api/v1/assets/upload/chunk
// Uploads one chunk of a large file. When all chunks are received the server
// assembles them and saves the asset exactly like a regular upload.
// Form fields: upload_id, chunk_index, total_chunks, filename, mime_type,
//              device_asset_id, device_id, taken_at, chunk (the binary slice).
func (h *Handler) UploadChunk(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())

	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeError(w, "BAD_REQUEST", "Could not parse multipart form.", http.StatusBadRequest)
		return
	}

	uploadID := r.FormValue("upload_id")
	filename := r.FormValue("filename")
	if uploadID == "" || filename == "" {
		writeError(w, "BAD_REQUEST", "Missing upload_id or filename.", http.StatusBadRequest)
		return
	}

	chunkIndex, err := strconv.Atoi(r.FormValue("chunk_index"))
	if err != nil || chunkIndex < 0 {
		writeError(w, "BAD_REQUEST", "Invalid chunk_index.", http.StatusBadRequest)
		return
	}
	totalChunks, err := strconv.Atoi(r.FormValue("total_chunks"))
	if err != nil || totalChunks < 1 {
		writeError(w, "BAD_REQUEST", "Invalid total_chunks.", http.StatusBadRequest)
		return
	}

	chunk, _, err := r.FormFile("chunk")
	if err != nil {
		writeError(w, "BAD_REQUEST", "Missing 'chunk' field.", http.StatusBadRequest)
		return
	}
	defer chunk.Close()

	// Stage chunks under tmp/<upload_id>/
	uploadDir := filepath.Join(h.svc.tmpRoot, "chunks_"+uploadID)
	if err := os.MkdirAll(uploadDir, 0755); err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to create staging directory.", http.StatusInternalServerError)
		return
	}

	chunkPath := filepath.Join(uploadDir, fmt.Sprintf("%05d", chunkIndex))
	cf, err := os.Create(chunkPath)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to write chunk.", http.StatusInternalServerError)
		return
	}
	if _, err := io.Copy(cf, chunk); err != nil {
		cf.Close()
		writeError(w, "INTERNAL_ERROR", "Failed to write chunk data.", http.StatusInternalServerError)
		return
	}
	cf.Close()

	// Count received chunks
	entries, _ := os.ReadDir(uploadDir)
	if len(entries) < totalChunks {
		writeJSON(w, http.StatusAccepted, map[string]interface{}{
			"status":   "partial",
			"received": len(entries),
			"total":    totalChunks,
		})
		return
	}

	// All chunks received — assemble via pipe into SaveAsset
	mediaType := r.FormValue("mime_type")
	if mediaType == "" {
		mediaType = detectMediaType(filename)
	}
	deviceAssetID := r.FormValue("device_asset_id")
	deviceID := r.FormValue("device_id")
	var takenAt *time.Time
	if s := r.FormValue("taken_at"); s != "" {
		if t, err2 := time.Parse(time.RFC3339, s); err2 == nil {
			takenAt = &t
		}
	}

	pr, pw := io.Pipe()
	go func() {
		defer pw.Close()
		for i := 0; i < totalChunks; i++ {
			p := filepath.Join(uploadDir, fmt.Sprintf("%05d", i))
			f, err := os.Open(p)
			if err != nil {
				_ = pw.CloseWithError(fmt.Errorf("chunk %d missing", i))
				return
			}
			_, err = io.Copy(pw, f)
			f.Close()
			if err != nil {
				_ = pw.CloseWithError(err)
				return
			}
		}
	}()

	asset, isDup, err := h.svc.SaveAsset(user.ID, filename, mediaType, pr, deviceAssetID, deviceID, takenAt)
	_ = os.RemoveAll(uploadDir)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", fmt.Sprintf("Assembly failed: %v", err), http.StatusInternalServerError)
		return
	}

	status := "created"
	httpStatus := http.StatusCreated
	if isDup {
		status = "duplicate"
		httpStatus = http.StatusOK
	}
	writeJSON(w, httpStatus, map[string]interface{}{
		"id":        asset.ID,
		"status":    status,
		"duplicate": isDup,
	})
}

// GET /api/v1/assets
func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	q := r.URL.Query()

	page := intParam(q.Get("page"), 1)
	pageSize := intParam(q.Get("page_size"), 50)
	if pageSize > 200 {
		pageSize = 200
	}
	order := "DESC"
	if strings.ToLower(q.Get("order")) == "asc" {
		order = "ASC"
	}

	where := []string{"user_id=?"}
	args := []interface{}{user.ID}

	if q.Get("is_favorited") == "true" {
		where = append(where, "is_favorited=1")
	}
	if q.Get("is_archived") != "true" {
		where = append(where, "is_archived=0")
	}
	if mt := q.Get("media_type"); mt != "" {
		where = append(where, "media_type LIKE ?")
		args = append(args, mt+"%")
	}
	if v := q.Get("taken_after"); v != "" {
		where = append(where, "taken_at>=?")
		args = append(args, v)
	}
	if v := q.Get("taken_before"); v != "" {
		where = append(where, "taken_at<=?")
		args = append(args, v)
	}
	if v := q.Get("album_id"); v != "" {
		where = append(where, "id IN (SELECT asset_id FROM album_assets WHERE album_id=?)")
		args = append(args, v)
	}

	whereClause := strings.Join(where, " AND ")
	var total int
	countArgs := make([]interface{}, len(args))
	copy(countArgs, args)
	_ = h.db.QueryRow("SELECT COUNT(*) FROM assets WHERE "+whereClause, countArgs...).Scan(&total)

	offset := (page - 1) * pageSize
	query := fmt.Sprintf(`
		SELECT id,filename,media_type,file_size_bytes,width,height,duration_seconds,
		       taken_at,created_at,is_favorited,is_archived,checksum_sha256,
		       exif_make,exif_model,exif_gps_lat,exif_gps_lng,
		       exif_focal_mm,exif_aperture,exif_iso,exif_shutter
		FROM assets WHERE %s ORDER BY taken_at %s, created_at %s LIMIT ? OFFSET ?`,
		whereClause, order, order)
	args = append(args, pageSize, offset)

	rows, err := h.db.Query(query, args...)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Query failed.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	assetList := []interface{}{}
	for rows.Next() {
		var a Asset
		var width, height *int
		var dur *float64
		var takenAt *string
		var exifMake, exifModel, exifShutter *string
		var exifLat, exifLng, exifFocal, exifAperture *float64
		var exifISO *int
		if err := rows.Scan(
			&a.ID, &a.Filename, &a.MediaType, &a.FileSizeBytes,
			&width, &height, &dur, &takenAt, &a.CreatedAt,
			&a.IsFavorited, &a.IsArchived, &a.ChecksumSHA256,
			&exifMake, &exifModel, &exifLat, &exifLng,
			&exifFocal, &exifAperture, &exifISO, &exifShutter,
		); err != nil {
			continue
		}
		a.Width = width
		a.Height = height
		a.DurationSeconds = dur
		a.TakenAt = takenAt
		a.ThumbnailURL = "/api/v1/assets/" + a.ID + "/thumbnail"
		a.OriginalURL = "/api/v1/assets/" + a.ID + "/original"
		if exifMake != nil || exifModel != nil || exifLat != nil {
			a.EXIF = &EXIFData{
				Make: exifMake, Model: exifModel,
				GPSLat: exifLat, GPSLng: exifLng,
				FocalLengthMM: exifFocal, Aperture: exifAperture,
				ISO: exifISO, ShutterSpeed: exifShutter,
			}
		}
		assetList = append(assetList, &a)
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"total":     total,
		"page":      page,
		"page_size": pageSize,
		"assets":    assetList,
	})
}

// GET /api/v1/assets/{id}
func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")
	a := h.fetchAsset(id)
	if a == nil || !h.userOwnsAsset(user.ID, id) {
		writeError(w, "ASSET_NOT_FOUND", "Asset not found.", http.StatusNotFound)
		return
	}
	writeJSON(w, http.StatusOK, a)
}

// GET /api/v1/assets/{id}/original
func (h *Handler) ServeOriginal(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")
	if !h.userOwnsAsset(user.ID, id) {
		writeError(w, "ASSET_NOT_FOUND", "Asset not found.", http.StatusNotFound)
		return
	}
	var path, mt string
	_ = h.db.QueryRow(`SELECT original_path, media_type FROM assets WHERE id=?`, id).Scan(&path, &mt)
	if path == "" {
		writeError(w, "ASSET_NOT_FOUND", "File not found.", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", mt)
	http.ServeFile(w, r, path)
}

// GET /api/v1/assets/{id}/thumbnail
func (h *Handler) ServeThumbnail(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")
	if !h.userOwnsAsset(user.ID, id) {
		writeError(w, "ASSET_NOT_FOUND", "Asset not found.", http.StatusNotFound)
		return
	}

	// Map API size names to on-disk folder names.
	// "thumb" is kept for backward-compatibility with older clients.
	var size string
	switch r.URL.Query().Get("size") {
	case "small", "thumb":
		size = "small"
	case "large":
		size = "large"
	case "blur":
		size = "blur"
	default:
		size = "preview"
	}

	thumbPath := filepath.Join(h.svc.thumbRoot, size, id[:2], id+".jpg")
	if _, statErr := os.Stat(thumbPath); os.IsNotExist(statErr) {
		// Thumbnail not generated yet — do not cache; it may appear shortly.
		w.Header().Set("Cache-Control", "no-store")
		var origPath, mediaType string
		_ = h.db.QueryRow(`SELECT original_path, media_type FROM assets WHERE id=?`, id).Scan(&origPath, &mediaType)
		if origPath == "" {
			writeError(w, "ASSET_NOT_FOUND", "Thumbnail not available.", http.StatusNotFound)
			return
		}
		// HEIC/HEIF: transcode to JPEG on-the-fly (browsers can't render natively)
		if isHEICType(mediaType) || isHEICPath(origPath) {
			data := heicToJPEGBytes(origPath)
			if data == nil {
				writeError(w, "HEIC_CONVERTER_MISSING",
					"Install imagemagick on the server (apt install imagemagick).",
					http.StatusServiceUnavailable)
				return
			}
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write(data)
			return
		}
		// Videos, PDFs, etc. have no thumbnail yet — return 404 rather than
		// streaming the full multi-MB original file as a broken image.
		if !strings.HasPrefix(mediaType, "image/") {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "image/jpeg")
		http.ServeFile(w, r, origPath)
		return
	}

	// Thumbnail exists on disk — serve with caching.
	// Thumbnails are immutable content (same ID → same pixels), so we allow
	// the browser to cache for 1 hour and rely on ETags for freshness.
	f, err := os.Open(thumbPath)
	if err != nil {
		http.Error(w, "thumbnail read error", http.StatusInternalServerError)
		return
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		http.Error(w, "thumbnail stat error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	http.ServeContent(w, r, "", fi.ModTime(), f)
}

// PATCH /api/v1/assets/{id}
func (h *Handler) Update(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")
	if !h.userOwnsAsset(user.ID, id) {
		writeError(w, "ASSET_NOT_FOUND", "Asset not found.", http.StatusNotFound)
		return
	}
	var req struct {
		IsFavorited *bool `json:"is_favorited"`
		IsArchived  *bool `json:"is_archived"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	if req.IsFavorited != nil {
		fav := 0
		if *req.IsFavorited {
			fav = 1
		}
		_, _ = h.db.Exec(`UPDATE assets SET is_favorited=? WHERE id=?`, fav, id)
	}
	if req.IsArchived != nil {
		arc := 0
		if *req.IsArchived {
			arc = 1
		}
		_, _ = h.db.Exec(`UPDATE assets SET is_archived=? WHERE id=?`, arc, id)
	}
	writeJSON(w, http.StatusOK, h.fetchAsset(id))
}

// DELETE /api/v1/assets/{id}
func (h *Handler) Delete(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")
	if !h.userOwnsAsset(user.ID, id) {
		writeError(w, "ASSET_NOT_FOUND", "Asset not found.", http.StatusNotFound)
		return
	}
	var origPath string
	_ = h.db.QueryRow(`SELECT original_path FROM assets WHERE id=?`, id).Scan(&origPath)
	_, _ = h.db.Exec(`DELETE FROM assets WHERE id=?`, id)
	if origPath != "" {
		_ = os.Remove(origPath)
	}
	// Remove all thumbnail tiers from SSD
	for _, sz := range []string{"blur", "small", "preview", "large"} {
		_ = os.Remove(filepath.Join(h.svc.thumbRoot, sz, id[:2], id+".jpg"))
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "Asset deleted."})
}

// POST /api/v1/assets/bulk-delete
func (h *Handler) BulkDelete(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		IDs []string `json:"ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	deleted := 0
	for _, id := range req.IDs {
		if !h.userOwnsAsset(user.ID, id) {
			continue
		}
		var origPath string
		_ = h.db.QueryRow(`SELECT original_path FROM assets WHERE id=?`, id).Scan(&origPath)
		_, _ = h.db.Exec(`DELETE FROM assets WHERE id=?`, id)
		if origPath != "" {
			_ = os.Remove(origPath)
		}
		for _, sz := range []string{"blur", "small", "preview", "large"} {
			_ = os.Remove(filepath.Join(h.svc.thumbRoot, sz, id[:2], id+".jpg"))
		}
		deleted++
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"deleted": deleted, "failed": len(req.IDs) - deleted})
}

// GET /api/v1/assets/timeline
func (h *Handler) Timeline(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	rows, err := h.db.Query(`
		SELECT substr(taken_at, 1, 10) as day, COUNT(*) 
		FROM assets 
		WHERE user_id=? AND is_archived=0 AND taken_at IS NOT NULL
		GROUP BY day ORDER BY day DESC`, user.ID)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Query failed.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	buckets := []map[string]interface{}{}
	for rows.Next() {
		var day string
		var count int
		_ = rows.Scan(&day, &count)
		buckets = append(buckets, map[string]interface{}{"date": day, "count": count})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"buckets": buckets})
}

// POST /api/v1/assets/check-duplicates
func (h *Handler) CheckDuplicates(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		Checksums []struct {
			DeviceAssetID string `json:"device_asset_id"`
			Checksum      string `json:"checksum"`
		} `json:"checksums"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	results := []map[string]interface{}{}
	for _, c := range req.Checksums {
		var existingID string
		err := h.db.QueryRow(`SELECT id FROM assets WHERE user_id=? AND checksum_sha256=?`, user.ID, c.Checksum).Scan(&existingID)
		isDup := err == nil
		var assetID interface{}
		if isDup {
			assetID = existingID
		}
		results = append(results, map[string]interface{}{
			"device_asset_id": c.DeviceAssetID,
			"is_duplicate":    isDup,
			"asset_id":        assetID,
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"results": results})
}

// --- SSE sync stream ---
// GET /api/v1/sync/stream
func (h *Handler) SyncStream(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, "INTERNAL_ERROR", "Streaming not supported.", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	// Keep the connection alive; real events are sent via other mechanisms
	// (in production you'd use a per-user channel map)
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	_, _ = fmt.Fprintf(w, ": connected\n\n")
	flusher.Flush()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			_, _ = fmt.Fprintf(w, ": keepalive\n\n")
			flusher.Flush()
		}
	}
}

// POST /api/v1/sync/changes
func (h *Handler) SyncChanges(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		DeviceID string `json:"device_id"`
		Assets   []struct {
			DeviceAssetID string `json:"device_asset_id"`
			Checksum      string `json:"checksum"`
		} `json:"assets"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}

	toUpload := []string{}
	alreadySynced := []string{}
	for _, a := range req.Assets {
		var id string
		err := h.db.QueryRow(`SELECT id FROM assets WHERE user_id=? AND checksum_sha256=?`, user.ID, a.Checksum).Scan(&id)
		if err == nil {
			alreadySynced = append(alreadySynced, a.DeviceAssetID)
		} else {
			toUpload = append(toUpload, a.DeviceAssetID)
		}
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"to_upload":      toUpload,
		"already_synced": alreadySynced,
		"server_deleted": []string{},
	})
}

// GET /api/v1/sync/status
func (h *Handler) SyncStatus(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var count int
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM assets WHERE user_id=?`, user.ID).Scan(&count)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"device_id":          r.URL.Query().Get("device_id"),
		"last_sync_at":       nil,
		"server_asset_count": count,
	})
}

// --- helpers ---

func (h *Handler) fetchAsset(id string) *Asset {
	var a Asset
	var width, height *int
	var dur *float64
	var takenAt *string
	var exifMake, exifModel, exifShutter *string
	var exifLat, exifLng, exifFocal, exifAperture *float64
	var exifISO *int

	err := h.db.QueryRow(`
		SELECT id,filename,media_type,file_size_bytes,width,height,duration_seconds,
		       taken_at,created_at,is_favorited,is_archived,checksum_sha256,
		       exif_make,exif_model,exif_gps_lat,exif_gps_lng,
		       exif_focal_mm,exif_aperture,exif_iso,exif_shutter
		FROM assets WHERE id=?`, id).Scan(
		&a.ID, &a.Filename, &a.MediaType, &a.FileSizeBytes,
		&width, &height, &dur, &takenAt, &a.CreatedAt,
		&a.IsFavorited, &a.IsArchived, &a.ChecksumSHA256,
		&exifMake, &exifModel, &exifLat, &exifLng,
		&exifFocal, &exifAperture, &exifISO, &exifShutter,
	)
	if err != nil {
		return nil
	}
	a.Width = width
	a.Height = height
	a.DurationSeconds = dur
	a.TakenAt = takenAt
	a.ThumbnailURL = "/api/v1/assets/" + id + "/thumbnail"
	a.OriginalURL = "/api/v1/assets/" + id + "/original"

	exd := &EXIFData{
		Make: exifMake, Model: exifModel,
		GPSLat: exifLat, GPSLng: exifLng,
		FocalLengthMM: exifFocal, Aperture: exifAperture,
		ISO: exifISO, ShutterSpeed: exifShutter,
	}
	// Only attach if any field is set
	if exifMake != nil || exifModel != nil || exifLat != nil {
		a.EXIF = exd
	}
	return &a
}

func (h *Handler) userOwnsAsset(userID, assetID string) bool {
	var count int
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM assets WHERE id=? AND user_id=?`, assetID, userID).Scan(&count)
	return count > 0
}

func detectMediaType(filename string) string {
	ext := strings.ToLower(filepath.Ext(filename))
	switch ext {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".webp":
		return "image/webp"
	case ".heic", ".heif":
		return "image/heic"
	case ".gif":
		return "image/gif"
	case ".tiff", ".tif":
		return "image/tiff"
	case ".mp4":
		return "video/mp4"
	case ".mov":
		return "video/quicktime"
	case ".mkv":
		return "video/x-matroska"
	case ".webm":
		return "video/webm"
	case ".hevc":
		return "video/hevc"
	case ".m4v":
		return "video/x-m4v"
	case ".avi":
		return "video/x-msvideo"
	case ".3gp":
		return "video/3gpp"
	// Documents
	case ".pdf":
		return "application/pdf"
	case ".doc":
		return "application/msword"
	case ".docx":
		return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
	case ".xls":
		return "application/vnd.ms-excel"
	case ".xlsx":
		return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	case ".ppt":
		return "application/vnd.ms-powerpoint"
	case ".pptx":
		return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
	case ".txt":
		return "text/plain"
	case ".md":
		return "text/markdown"
	case ".csv":
		return "text/csv"
	case ".zip":
		return "application/zip"
	case ".rar":
		return "application/x-rar-compressed"
	case ".7z":
		return "application/x-7z-compressed"
	default:
		return "application/octet-stream"
	}
}

func intParam(s string, def int) int {
	if v, err := strconv.Atoi(s); err == nil && v > 0 {
		return v
	}
	return def
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code, message string, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"error": map[string]interface{}{
			"code": code, "message": message, "status": status,
		},
	})
}

// ServeFile proxies io.Copy for downloads
func serveStream(w http.ResponseWriter, path, contentType string) {
	f, err := os.Open(path)
	if err != nil {
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", contentType)
	_, _ = io.Copy(w, f)
}
