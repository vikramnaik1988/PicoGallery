package server

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/picogallery/picogallery/internal/assets"
)

type InfoHandler struct {
	db          *sql.DB
	storageRoot string
	version     string
	assetSvc    *assets.Service
}

func NewInfoHandler(db *sql.DB, storageRoot, version string, svc *assets.Service) *InfoHandler {
	return &InfoHandler{db: db, storageRoot: storageRoot, version: version, assetSvc: svc}
}

// GET /api/v1/server/info
func (h *InfoHandler) Info(w http.ResponseWriter, r *http.Request) {
	var total, free uint64
	if stat, err := diskUsage(h.storageRoot); err == nil {
		total = stat.Total
		free = stat.Free
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"server_name": "PicoGallery",
		"version":     h.version,
		"features": map[string]bool{
			"video":            true,
			"raw_files":        false,
			"face_recognition": false,
			"semantic_search":  false,
			"partner_sharing":  false,
			"public_links":     false,
		},
		"storage": map[string]interface{}{
			"total_bytes": total,
			"used_bytes":  total - free,
			"free_bytes":  free,
		},
	})
}

// GET /api/v1/server/stats (admin)
func (h *InfoHandler) Stats(w http.ResponseWriter, r *http.Request) {
	var totalUsers, totalAssets, totalAlbums int
	var totalSize int64
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM users`).Scan(&totalUsers)
	_ = h.db.QueryRow(`SELECT COUNT(*), COALESCE(SUM(file_size_bytes),0) FROM assets`).Scan(&totalAssets, &totalSize)
	_ = h.db.QueryRow(`SELECT COUNT(*) FROM albums`).Scan(&totalAlbums)

	rows, _ := h.db.Query(`
		SELECT u.id, u.name, COUNT(a.id), COALESCE(SUM(a.file_size_bytes),0)
		FROM users u LEFT JOIN assets a ON a.user_id=u.id
		GROUP BY u.id`)
	defer rows.Close()
	perUser := []map[string]interface{}{}
	for rows.Next() {
		var uid, name string
		var count int
		var size int64
		_ = rows.Scan(&uid, &name, &count, &size)
		perUser = append(perUser, map[string]interface{}{
			"user_id": uid, "name": name, "asset_count": count, "used_bytes": size,
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"total_users":      totalUsers,
		"total_assets":     totalAssets,
		"total_size_bytes": totalSize,
		"total_albums":     totalAlbums,
		"per_user":         perUser,
	})
}

// POST /api/v1/server/rescan (admin)
func (h *InfoHandler) Rescan(w http.ResponseWriter, r *http.Request) {
	jobID := "job_" + uuid.NewString()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = h.db.Exec(`INSERT INTO background_jobs(id,type,status,created_at) VALUES(?,?,?,?)`,
		jobID, "rescan", "running", now)

	go h.runRescan(jobID)

	writeJSON(w, http.StatusOK, map[string]string{
		"message": "Rescan started.", "job_id": jobID,
	})
}

func (h *InfoHandler) runRescan(jobID string) {
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = h.db.Exec(`UPDATE background_jobs SET started_at=? WHERE id=?`, now, jobID)

	// Find all user directories under originals/
	originalsRoot := h.storageRoot + "/originals"
	scanned, newFiles := 0, 0

	// Walk the storage tree. For files that don't have a DB record, ingest them.
	_ = walkDir(originalsRoot, func(path string) {
		scanned++
		var count int
		_ = h.db.QueryRow(`SELECT COUNT(*) FROM assets WHERE original_path=?`, path).Scan(&count)
		if count > 0 {
			return
		}
		// Try to determine user from path (originals/<userID>/...)
		rel := path[len(originalsRoot)+1:]
		parts := splitPath(rel)
		if len(parts) < 1 {
			return
		}
		userID := parts[0]
		// Verify user exists
		var uCount int
		_ = h.db.QueryRow(`SELECT COUNT(*) FROM users WHERE id=?`, userID).Scan(&uCount)
		if uCount == 0 {
			return
		}
		f, err := os.Open(path)
		if err != nil {
			return
		}
		defer f.Close()
		fi, _ := f.Stat()
		filename := fi.Name()
		mt := detectMime(filename)
		_, _, _ = h.assetSvc.SaveAsset(userID, filename, mt, f, "", "rescan", nil)
		newFiles++
	})

	done := time.Now().UTC().Format(time.RFC3339)
	progress, _ := json.Marshal(map[string]int{"scanned": scanned, "new": newFiles, "errors": 0})
	_, _ = h.db.Exec(`UPDATE background_jobs SET status='done', finished_at=?, progress=? WHERE id=?`,
		done, string(progress), jobID)
}

// GET /api/v1/server/jobs/{jobID}
func (h *InfoHandler) GetJob(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("jobID")
	var id, jobType, status string
	var progress, startedAt, finishedAt *string
	var createdAt string
	err := h.db.QueryRow(`SELECT id,type,status,progress,started_at,finished_at,created_at FROM background_jobs WHERE id=?`, jobID).
		Scan(&id, &jobType, &status, &progress, &startedAt, &finishedAt, &createdAt)
	if err != nil {
		writeError(w, "NOT_FOUND", "Job not found.", http.StatusNotFound)
		return
	}
	var progressObj interface{}
	if progress != nil {
		_ = json.Unmarshal([]byte(*progress), &progressObj)
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"id": id, "type": jobType, "status": status,
		"progress": progressObj, "started_at": startedAt,
		"finished_at": finishedAt, "created_at": createdAt,
	})
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

// Stubs for platform-specific disk usage and path helpers.
// disk.go provides the real implementation per OS.
type diskStat struct {
	Total uint64
	Free  uint64
}

func splitPath(p string) []string {
	var parts []string
	for _, s := range splitSlash(p) {
		if s != "" {
			parts = append(parts, s)
		}
	}
	return parts
}

func splitSlash(s string) []string {
	result := []string{}
	cur := ""
	for _, c := range s {
		if c == '/' || c == '\\' {
			result = append(result, cur)
			cur = ""
		} else {
			cur += string(c)
		}
	}
	if cur != "" {
		result = append(result, cur)
	}
	return result
}

func detectMime(name string) string {
	if len(name) == 0 {
		return "application/octet-stream"
	}
	dot := strings.LastIndex(name, ".")
	if dot < 0 {
		return "application/octet-stream"
	}
	switch strings.ToLower(name[dot:]) {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".gif":
		return "image/gif"
	case ".webp":
		return "image/webp"
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
	}
	return "application/octet-stream"
}

func walkDir(root string, fn func(path string)) error {
	entries, err := os.ReadDir(root)
	if err != nil {
		return err
	}
	for _, e := range entries {
		p := root + "/" + e.Name()
		if e.IsDir() {
			_ = walkDir(p, fn)
		} else {
			fn(p)
		}
	}
	return nil
}
