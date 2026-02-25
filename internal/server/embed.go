package server

import (
	"net/http"

	"github.com/picogallery/picogallery/web"
)

// webHandler serves the embedded web UI, falling back to index.html for SPA routing.
func webHandler() http.Handler {
	return web.Handler()
}
