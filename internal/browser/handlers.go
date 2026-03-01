package browser

import (
	"archive/zip"
	"database/sql"
	"encoding/json"
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
	db           *sql.DB
	originalsRoot string
}

func NewHandler(db *sql.DB, originalsRoot string) *Handler {
	return &Handler{db: db, originalsRoot: originalsRoot}
}

// GET /api/v1/files
func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	relPath := sanitizePath(r.URL.Query().Get("path"))
	absPath := filepath.Join(h.originalsRoot, user.ID, relPath)

	page := intParam(r.URL.Query().Get("page"), 1)
	pageSize := intParam(r.URL.Query().Get("page_size"), 100)

	entries, err := os.ReadDir(absPath)
	if err != nil {
		writeError(w, "NOT_FOUND", "Path not found.", http.StatusNotFound)
		return
	}

	items := []map[string]interface{}{}
	for _, e := range entries {
		info, _ := e.Info()
		item := map[string]interface{}{
			"name":        e.Name(),
			"type":        "file",
			"size_bytes":  int64(0),
			"modified_at": "",
			"asset_id":    nil,
			"mime_type":   nil,
		}
		if e.IsDir() {
			item["type"] = "directory"
		} else if info != nil {
			item["size_bytes"] = info.Size()
			item["modified_at"] = info.ModTime().UTC().Format(time.RFC3339)
			mt := detectMime(e.Name())
			item["mime_type"] = mt
			// Try to link to gallery asset
			fullPath := filepath.Join(absPath, e.Name())
			var assetID string
			_ = h.db.QueryRow(`SELECT id FROM assets WHERE original_path=? AND user_id=?`, fullPath, user.ID).Scan(&assetID)
			if assetID != "" {
				item["asset_id"] = assetID
			}
		}
		items = append(items, item)
	}

	total := len(items)
	start := (page - 1) * pageSize
	end := start + pageSize
	if start > total {
		start = total
	}
	if end > total {
		end = total
	}
	pagedItems := items[start:end]

	parentPath := "/"
	if relPath != "/" && relPath != "" {
		parentPath = "/" + filepath.Dir(strings.TrimPrefix(relPath, "/"))
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"path":        "/" + relPath,
		"parent_path": parentPath,
		"total":       total,
		"page":        page,
		"items":       pagedItems,
	})
}

// POST /api/v1/files/directory
func (h *Handler) CreateDirectory(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		Path string `json:"path"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	rel := sanitizePath(req.Path)
	abs := filepath.Join(h.originalsRoot, user.ID, rel)
	if err := os.MkdirAll(abs, 0755); err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to create directory.", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{
		"path": "/" + rel, "message": "Directory created.",
	})
}

// DELETE /api/v1/files
func (h *Handler) Delete(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		Path string `json:"path"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	rel := sanitizePath(req.Path)
	abs := filepath.Join(h.originalsRoot, user.ID, rel)
	if err := os.RemoveAll(abs); err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to delete.", http.StatusInternalServerError)
		return
	}
	// Remove any assets pointing to files in this path
	_, _ = h.db.Exec(`DELETE FROM assets WHERE user_id=? AND original_path LIKE ?`, user.ID, abs+"%")
	writeJSON(w, http.StatusOK, map[string]string{"message": "Deleted."})
}

// POST /api/v1/files/move
func (h *Handler) Move(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	var req struct {
		SourcePath string `json:"source_path"`
		DestPath   string `json:"dest_path"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid body.", http.StatusBadRequest)
		return
	}
	srcRel := sanitizePath(req.SourcePath)
	dstRel := sanitizePath(req.DestPath)
	srcAbs := filepath.Join(h.originalsRoot, user.ID, srcRel)
	dstAbs := filepath.Join(h.originalsRoot, user.ID, dstRel)

	if err := os.MkdirAll(filepath.Dir(dstAbs), 0755); err != nil {
		writeError(w, "INTERNAL_ERROR", "Could not create destination directory.", http.StatusInternalServerError)
		return
	}
	if err := os.Rename(srcAbs, dstAbs); err != nil {
		writeError(w, "INTERNAL_ERROR", "Move failed.", http.StatusInternalServerError)
		return
	}
	// Update asset records
	_, _ = h.db.Exec(`UPDATE assets SET original_path=? WHERE original_path=? AND user_id=?`,
		dstAbs, srcAbs, user.ID)
	writeJSON(w, http.StatusOK, map[string]string{"new_path": "/" + dstRel})
}

// GET /api/v1/files/download
func (h *Handler) Download(w http.ResponseWriter, r *http.Request) {
	user := auth.UserFromContext(r.Context())
	rel := sanitizePath(r.URL.Query().Get("path"))
	abs := filepath.Join(h.originalsRoot, user.ID, rel)

	info, err := os.Stat(abs)
	if err != nil {
		writeError(w, "NOT_FOUND", "Path not found.", http.StatusNotFound)
		return
	}

	if !info.IsDir() {
		w.Header().Set("Content-Disposition", "attachment; filename="+strconv.Quote(info.Name()))
		http.ServeFile(w, r, abs)
		return
	}

	// Directory: serve as ZIP
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", "attachment; filename="+strconv.Quote(info.Name()+".zip"))
	zw := zip.NewWriter(w)
	defer zw.Close()

	_ = filepath.Walk(abs, func(path string, fi os.FileInfo, err error) error {
		if err != nil || fi.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(abs, path)
		f, err := zw.Create(rel)
		if err != nil {
			return err
		}
		src, err := os.Open(path)
		if err != nil {
			return err
		}
		defer src.Close()
		_, _ = io.Copy(f, src)
		return nil
	})
}

func sanitizePath(p string) string {
	// Strip leading slash, clean, prevent directory traversal
	p = filepath.Clean("/" + strings.TrimPrefix(p, "/"))
	p = strings.TrimPrefix(p, "/")
	// Guard against traversal
	if strings.Contains(p, "..") {
		return ""
	}
	return p
}

func detectMime(name string) string {
	ext := strings.ToLower(filepath.Ext(name))
	switch ext {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".gif":
		return "image/gif"
	case ".webp":
		return "image/webp"
	case ".heic", ".heif":
		return "image/heic"
	case ".mp4":
		return "video/mp4"
	case ".mov":
		return "video/quicktime"
	case ".mkv":
		return "video/x-matroska"
	case ".pdf":
		return "application/pdf"
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
