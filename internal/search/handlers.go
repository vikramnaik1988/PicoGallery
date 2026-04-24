package search

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"

	"github.com/picogallery/picogallery/internal/auth"
)

type Handler struct {
	db *sql.DB
}

func NewHandler(db *sql.DB) *Handler {
	return &Handler{db: db}
}

// GET /api/v1/search
// Returns full asset objects (not just IDs) to avoid N+1 fetches on the client.
// GPS radius search uses a SQL bounding-box pre-filter then Haversine post-filter.
func (h *Handler) Search(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	q := r.URL.Query()

	where := []string{"user_id=?"}
	args := []interface{}{user.ID}

	// Filename substring
	if s := q.Get("q"); s != "" {
		where = append(where, "filename LIKE ?")
		args = append(args, "%"+s+"%")
	}
	// Date range
	if v := q.Get("taken_after"); v != "" {
		where = append(where, "taken_at>=?")
		args = append(args, v)
	}
	if v := q.Get("taken_before"); v != "" {
		where = append(where, "taken_at<=?")
		args = append(args, v)
	}
	// Favorites
	if q.Get("is_favorited") == "true" {
		where = append(where, "is_favorited=1")
	}
	// Camera make / model — uses idx_assets_exif_make / idx_assets_exif_model
	if v := q.Get("camera_make"); v != "" {
		where = append(where, "exif_make LIKE ?")
		args = append(args, "%"+v+"%")
	}
	if v := q.Get("camera_model"); v != "" {
		where = append(where, "exif_model LIKE ?")
		args = append(args, "%"+v+"%")
	}

	// GPS radius search — bounding-box SQL pre-filter (uses idx_assets_gps),
	// then exact Haversine post-filter in Go on the much smaller result set.
	var gpsLat, gpsLng, gpsRadius float64
	hasGPS := false
	if latS := q.Get("gps_lat"); latS != "" {
		if lat, err := strconv.ParseFloat(latS, 64); err == nil {
			if lng, err2 := strconv.ParseFloat(q.Get("gps_lng"), 64); err2 == nil {
				if rad, err3 := strconv.ParseFloat(q.Get("gps_radius_km"), 64); err3 == nil && rad > 0 {
					gpsLat, gpsLng, gpsRadius = lat, lng, rad
					hasGPS = true
					// Bounding box: 1 degree lat ≈ 111 km, 1 degree lng ≈ 111*cos(lat) km
					latDelta := rad / 111.0
					lngDelta := rad / (111.0 * math.Cos(lat*math.Pi/180))
					where = append(where,
						"exif_gps_lat IS NOT NULL AND exif_gps_lng IS NOT NULL",
						"exif_gps_lat BETWEEN ? AND ?",
						"exif_gps_lng BETWEEN ? AND ?",
					)
					args = append(args, lat-latDelta, lat+latDelta, lng-lngDelta, lng+lngDelta)
				}
			}
		}
	}

	// Always exclude archived
	where = append(where, "is_archived=0")

	page := intParam(q.Get("page"), 1)
	pageSize := intParam(q.Get("page_size"), 50)
	if pageSize > 100 {
		pageSize = 100
	}

	whereClause := strings.Join(where, " AND ")

	// Fetch full asset rows — no N+1; client gets everything in one response.
	selectSQL := fmt.Sprintf(`
		SELECT id, filename, media_type, file_size_bytes, width, height, duration_seconds,
		       taken_at, created_at, is_favorited, is_archived, checksum_sha256,
		       exif_make, exif_model, exif_gps_lat, exif_gps_lng,
		       exif_focal_mm, exif_aperture, exif_iso, exif_shutter
		FROM assets WHERE %s ORDER BY taken_at DESC`, whereClause)

	rows, err := h.db.Query(selectSQL, args...)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Query failed.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type assetRow struct {
		id, filename, mediaType, checksum, createdAt string
		fileSize                                      int64
		width, height                                 *int
		dur                                           *float64
		takenAt                                       *string
		isFavorited, isArchived                       bool
		exifMake, exifModel, exifShutter              *string
		exifLat, exifLng, exifFocal, exifAperture     *float64
		exifISO                                       *int
	}

	var allRows []assetRow
	for rows.Next() {
		var row assetRow
		if err := rows.Scan(
			&row.id, &row.filename, &row.mediaType, &row.fileSize,
			&row.width, &row.height, &row.dur, &row.takenAt, &row.createdAt,
			&row.isFavorited, &row.isArchived, &row.checksum,
			&row.exifMake, &row.exifModel, &row.exifLat, &row.exifLng,
			&row.exifFocal, &row.exifAperture, &row.exifISO, &row.exifShutter,
		); err != nil {
			continue
		}
		allRows = append(allRows, row)
	}

	// Haversine post-filter (only runs when GPS search requested — bounding box
	// already reduced the set to a small neighbourhood)
	var filtered []assetRow
	for _, row := range allRows {
		if hasGPS && row.exifLat != nil && row.exifLng != nil {
			if haversineKm(gpsLat, gpsLng, *row.exifLat, *row.exifLng) > gpsRadius {
				continue
			}
		}
		filtered = append(filtered, row)
	}

	total := len(filtered)
	start := (page - 1) * pageSize
	end := start + pageSize
	if start > total {
		start = total
	}
	if end > total {
		end = total
	}
	paged := filtered[start:end]

	// Build response assets
	assets := make([]map[string]interface{}, 0, len(paged))
	for _, row := range paged {
		a := map[string]interface{}{
			"id":              row.id,
			"filename":        row.filename,
			"media_type":      row.mediaType,
			"file_size_bytes": row.fileSize,
			"width":           row.width,
			"height":          row.height,
			"duration_seconds": row.dur,
			"taken_at":        row.takenAt,
			"created_at":      row.createdAt,
			"is_favorited":    row.isFavorited,
			"is_archived":     row.isArchived,
			"checksum_sha256": row.checksum,
			"thumbnail_url":   "/api/v1/assets/" + row.id + "/thumbnail",
			"original_url":    "/api/v1/assets/" + row.id + "/original",
		}
		if row.exifMake != nil || row.exifModel != nil || row.exifLat != nil {
			a["exif"] = map[string]interface{}{
				"make": row.exifMake, "model": row.exifModel,
				"gps_lat": row.exifLat, "gps_lng": row.exifLng,
				"focal_length_mm": row.exifFocal, "aperture": row.exifAperture,
				"iso": row.exifISO, "shutter_speed": row.exifShutter,
			}
		}
		assets = append(assets, a)
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"total":     total,
		"page":      page,
		"page_size": pageSize,
		"assets":    assets,
	})
}

// haversineKm returns the great-circle distance in km between two GPS coordinates.
func haversineKm(lat1, lng1, lat2, lng2 float64) float64 {
	const R = 6371.0
	dLat := (lat2 - lat1) * math.Pi / 180
	dLng := (lng2 - lng1) * math.Pi / 180
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*math.Pi/180)*math.Cos(lat2*math.Pi/180)*
			math.Sin(dLng/2)*math.Sin(dLng/2)
	return R * 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
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
