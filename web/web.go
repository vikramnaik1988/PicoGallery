package web

import (
	"embed"
	"io/fs"
	"net/http"
	"strings"
)

// staticMIME maps file extensions to MIME types for embedded static assets.
var staticMIME = map[string]string{
	".png":  "image/png",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".svg":  "image/svg+xml",
	".ico":  "image/x-icon",
}

//go:embed index.html image
var files embed.FS

// Handler returns an http.Handler that serves the embedded web UI.
// All unknown paths fall back to index.html for SPA client-side routing.
func Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		assetPath := strings.TrimPrefix(path, "/")

		// Serve embedded static assets (images, etc.) directly.
		if assetPath != "" && assetPath != "index.html" {
			data, err := files.ReadFile(assetPath)
			if err == nil {
				mime := "application/octet-stream"
				if dot := strings.LastIndex(assetPath, "."); dot >= 0 {
					if m, ok := staticMIME[assetPath[dot:]]; ok {
						mime = m
					}
				}
				w.Header().Set("Content-Type", mime)
				w.Header().Set("Cache-Control", "public, max-age=86400")
				_, _ = w.Write(data)
				return
			}
		}

		// Everything else (SPA routes) → index.html
		content, _ := fs.ReadFile(files, "index.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(content)
	})
}
