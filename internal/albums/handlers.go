package albums

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/picogallery/picogallery/internal/auth"
)

type Handler struct {
	db *sql.DB
}

func NewHandler(db *sql.DB) *Handler {
	return &Handler{db: db}
}

// GET /api/v1/albums
func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	rows, err := h.db.Query(`
		SELECT a.id, a.name, a.description, a.cover_asset_id, a.created_at, a.updated_at,
		       COUNT(aa.asset_id) as asset_count
		FROM albums a
		LEFT JOIN album_assets aa ON aa.album_id = a.id
		WHERE a.user_id=?
		GROUP BY a.id
		ORDER BY a.updated_at DESC`, user.ID)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Query failed.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	result := []map[string]interface{}{}
	for rows.Next() {
		var id, name, created, updated string
		var desc, coverID *string
		var count int
		_ = rows.Scan(&id, &name, &desc, &coverID, &created, &updated, &count)
		result = append(result, map[string]interface{}{
			"id": id, "name": name, "description": desc,
			"cover_asset_id": coverID, "asset_count": count,
			"created_at": created, "updated_at": updated,
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"albums": result})
}

// POST /api/v1/albums
func (h *Handler) Create(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		Name        string   `json:"name"`
		Description string   `json:"description"`
		AssetIDs    []string `json:"asset_ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	if strings.TrimSpace(req.Name) == "" {
		writeError(w, "BAD_REQUEST", "name is required.", http.StatusBadRequest)
		return
	}
	id := "alb_" + uuid.NewString()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := h.db.Exec(`INSERT INTO albums(id,user_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?)`,
		id, user.ID, req.Name, req.Description, now, now)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to create album.", http.StatusInternalServerError)
		return
	}
	for _, aid := range req.AssetIDs {
		_, _ = h.db.Exec(`INSERT OR IGNORE INTO album_assets(album_id,asset_id,added_at) VALUES(?,?,?)`, id, aid, now)
	}
	writeJSON(w, http.StatusCreated, map[string]interface{}{
		"id": id, "name": req.Name, "description": req.Description,
		"asset_count": len(req.AssetIDs), "created_at": now, "updated_at": now,
	})
}

// GET /api/v1/albums/{id}
func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")

	var name, created, updated string
	var desc, coverID *string
	err := h.db.QueryRow(`SELECT name, description, cover_asset_id, created_at, updated_at FROM albums WHERE id=? AND user_id=?`,
		id, user.ID).Scan(&name, &desc, &coverID, &created, &updated)
	if err != nil {
		writeError(w, "ALBUM_NOT_FOUND", "Album not found.", http.StatusNotFound)
		return
	}

	page := intParam(r.URL.Query().Get("page"), 1)
	pageSize := intParam(r.URL.Query().Get("page_size"), 50)
	if pageSize > 200 {
		pageSize = 200
	}
	offset := (page - 1) * pageSize

	var total int
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM album_assets WHERE album_id=?`, id).Scan(&total)

	// JOIN to return full asset objects — eliminates N+1 fetches on the client.
	rows, _ := h.db.Query(`
		SELECT a.id, a.filename, a.media_type, a.file_size_bytes, a.width, a.height,
		       a.duration_seconds, a.taken_at, a.created_at, a.is_favorited, a.is_archived,
		       a.checksum_sha256, a.exif_make, a.exif_model, a.exif_gps_lat, a.exif_gps_lng,
		       a.exif_focal_mm, a.exif_aperture, a.exif_iso, a.exif_shutter
		FROM album_assets aa
		JOIN assets a ON a.id = aa.asset_id
		WHERE aa.album_id=?
		ORDER BY aa.added_at DESC
		LIMIT ? OFFSET ?`, id, pageSize, offset)
	defer rows.Close()

	assets := []map[string]interface{}{}
	for rows.Next() {
		var aid, filename, mediaType, checksum, createdAt string
		var fileSize int64
		var width, height *int
		var dur *float64
		var takenAt *string
		var isFavorited, isArchived bool
		var exifMake, exifModel, exifShutter *string
		var exifLat, exifLng, exifFocal, exifAperture *float64
		var exifISO *int
		if err := rows.Scan(
			&aid, &filename, &mediaType, &fileSize, &width, &height,
			&dur, &takenAt, &createdAt, &isFavorited, &isArchived, &checksum,
			&exifMake, &exifModel, &exifLat, &exifLng,
			&exifFocal, &exifAperture, &exifISO, &exifShutter,
		); err != nil {
			continue
		}
		a := map[string]interface{}{
			"id": aid, "filename": filename, "media_type": mediaType,
			"file_size_bytes": fileSize, "width": width, "height": height,
			"duration_seconds": dur, "taken_at": takenAt, "created_at": createdAt,
			"is_favorited": isFavorited, "is_archived": isArchived,
			"checksum_sha256": checksum,
			"thumbnail_url": "/api/v1/assets/" + aid + "/thumbnail",
			"original_url":  "/api/v1/assets/" + aid + "/original",
		}
		if exifMake != nil || exifModel != nil || exifLat != nil {
			a["exif"] = map[string]interface{}{
				"make": exifMake, "model": exifModel,
				"gps_lat": exifLat, "gps_lng": exifLng,
				"focal_length_mm": exifFocal, "aperture": exifAperture,
				"iso": exifISO, "shutter_speed": exifShutter,
			}
		}
		assets = append(assets, a)
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"album": map[string]interface{}{
			"id": id, "name": name, "description": desc,
			"cover_asset_id": coverID, "asset_count": total,
			"created_at": created, "updated_at": updated,
		},
		"total": total, "page": page, "page_size": pageSize,
		"assets": assets,
	})
}

// PATCH /api/v1/albums/{id}
func (h *Handler) Update(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")

	var count int
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM albums WHERE id=? AND user_id=?`, id, user.ID).Scan(&count)
	if count == 0 {
		writeError(w, "ALBUM_NOT_FOUND", "Album not found.", http.StatusNotFound)
		return
	}

	var req struct {
		Name        *string `json:"name"`
		Description *string `json:"description"`
		CoverAssetID *string `json:"cover_asset_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	if req.Name != nil {
		_, _ = h.db.Exec(`UPDATE albums SET name=?, updated_at=? WHERE id=?`, *req.Name, now, id)
	}
	if req.Description != nil {
		_, _ = h.db.Exec(`UPDATE albums SET description=?, updated_at=? WHERE id=?`, *req.Description, now, id)
	}
	if req.CoverAssetID != nil {
		_, _ = h.db.Exec(`UPDATE albums SET cover_asset_id=?, updated_at=? WHERE id=?`, *req.CoverAssetID, now, id)
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "Album updated."})
}

// DELETE /api/v1/albums/{id}
func (h *Handler) Delete(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")
	_, _ = h.db.Exec(`DELETE FROM albums WHERE id=? AND user_id=?`, id, user.ID)
	writeJSON(w, http.StatusOK, map[string]string{"message": "Album deleted."})
}

// POST /api/v1/albums/{id}/assets
func (h *Handler) AddAssets(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")

	var count int
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM albums WHERE id=? AND user_id=?`, id, user.ID).Scan(&count)
	if count == 0 {
		writeError(w, "ALBUM_NOT_FOUND", "Album not found.", http.StatusNotFound)
		return
	}

	var req struct {
		AssetIDs []string `json:"asset_ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}

	added := 0
	alreadyPresent := 0
	now := time.Now().UTC().Format(time.RFC3339)
	for _, aid := range req.AssetIDs {
		result, _ := h.db.Exec(`INSERT OR IGNORE INTO album_assets(album_id,asset_id,added_at) VALUES(?,?,?)`, id, aid, now)
		if n, _ := result.RowsAffected(); n > 0 {
			added++
		} else {
			alreadyPresent++
		}
	}
	_, _ = h.db.Exec(`UPDATE albums SET updated_at=? WHERE id=?`, now, id)
	writeJSON(w, http.StatusOK, map[string]interface{}{"added": added, "already_present": alreadyPresent})
}

// DELETE /api/v1/albums/{id}/assets
func (h *Handler) RemoveAssets(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	id := r.PathValue("id")

	var count int
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM albums WHERE id=? AND user_id=?`, id, user.ID).Scan(&count)
	if count == 0 {
		writeError(w, "ALBUM_NOT_FOUND", "Album not found.", http.StatusNotFound)
		return
	}

	var req struct {
		AssetIDs []string `json:"asset_ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	removed := 0
	for _, aid := range req.AssetIDs {
		result, _ := h.db.Exec(`DELETE FROM album_assets WHERE album_id=? AND asset_id=?`, id, aid)
		if n, _ := result.RowsAffected(); n > 0 {
			removed++
		}
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"removed": removed})
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

// suppress unused import
var _ = fmt.Sprintf
