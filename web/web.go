package web

import (
	"embed"
	"io/fs"
	"net/http"
)

//go:embed index.html
var files embed.FS

// Handler returns an http.Handler that serves the embedded web UI.
// All unknown paths fall back to index.html for SPA client-side routing.
func Handler() http.Handler {
	fileServer := http.FileServer(http.FS(files))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Only index.html is embedded; everything else falls back to it.
		path := r.URL.Path
		if path != "/" && path != "/index.html" {
			// Check if file exists in embed
			f, err := files.Open(path)
			if err != nil {
				// Not found → serve index.html (SPA fallback)
				r.URL.Path = "/"
			} else {
				f.Close()
			}
		}
		// Remove leading slash for embed.FS compatibility
		if r.URL.Path == "/" {
			content, _ := fs.ReadFile(files, "index.html")
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			_, _ = w.Write(content)
			return
		}
		fileServer.ServeHTTP(w, r)
	})
}
