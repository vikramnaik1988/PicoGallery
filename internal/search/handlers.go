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
	// Camera make / model
	if v := q.Get("camera_make"); v != "" {
		where = append(where, "exif_make LIKE ?")
		args = append(args, "%"+v+"%")
	}
	if v := q.Get("camera_model"); v != "" {
		where = append(where, "exif_model LIKE ?")
		args = append(args, "%"+v+"%")
	}

	// GPS radius search — post-filter in Go (SQLite has no geo functions)
	var gpsLat, gpsLng, gpsRadius float64
	hasGPS := false
	if latS := q.Get("gps_lat"); latS != "" {
		if lat, err := strconv.ParseFloat(latS, 64); err == nil {
			if lng, err2 := strconv.ParseFloat(q.Get("gps_lng"), 64); err2 == nil {
				if rad, err3 := strconv.ParseFloat(q.Get("gps_radius_km"), 64); err3 == nil {
					gpsLat, gpsLng, gpsRadius = lat, lng, rad
					hasGPS = true
					where = append(where, "exif_gps_lat IS NOT NULL AND exif_gps_lng IS NOT NULL")
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
	countArgs := make([]interface{}, len(args))
	copy(countArgs, args)

	var allIDs []string
	rows, err := h.db.Query(fmt.Sprintf("SELECT id, exif_gps_lat, exif_gps_lng FROM assets WHERE %s ORDER BY taken_at DESC", whereClause), args...)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Query failed.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type row struct {
		id  string
		lat *float64
		lng *float64
	}
	var candidates []row
	for rows.Next() {
		var id string
		var lat, lng *float64
		_ = rows.Scan(&id, &lat, &lng)
		candidates = append(candidates, row{id, lat, lng})
	}

	for _, c := range candidates {
		if hasGPS && c.lat != nil && c.lng != nil {
			if haversineKm(gpsLat, gpsLng, *c.lat, *c.lng) > gpsRadius {
				continue
			}
		}
		allIDs = append(allIDs, c.id)
	}

	total := len(allIDs)
	start := (page - 1) * pageSize
	end := start + pageSize
	if start > total {
		start = total
	}
	if end > total {
		end = total
	}
	pagedIDs := allIDs[start:end]

	assets := []interface{}{}
	for _, id := range pagedIDs {
		assets = append(assets, id) // client resolves full asset via /assets/:id
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"total":     total,
		"page":      page,
		"page_size": pageSize,
		"asset_ids": assets,
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
