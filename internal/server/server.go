package server

import (
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"
	_ "modernc.org/sqlite"

	"github.com/picogallery/picogallery/config"
	"github.com/picogallery/picogallery/internal/albums"
	"github.com/picogallery/picogallery/internal/assets"
	"github.com/picogallery/picogallery/internal/auth"
	"github.com/picogallery/picogallery/internal/browser"
	"github.com/picogallery/picogallery/internal/search"
	"github.com/picogallery/picogallery/migrations"
)

const version = "1.0.0"

// Server holds all initialized dependencies.
type Server struct {
	cfg          *config.Config
	db           *sql.DB
	assetSvc     *assets.Service
	originalsRoot string // HDD path — original files
	storageRoot  string  // SSD root — for disk-space reporting
}

// New initializes the server and all its subsystems.
func New(cfg *config.Config) (*Server, error) {
	originalsRoot := cfg.Storage.EffectiveOriginalsRoot()
	thumbRoot := cfg.Storage.EffectiveThumbnailsRoot()
	tmpRoot := cfg.Storage.EffectiveTmpRoot()

	// Ensure all storage directories exist (NewService also does this, but
	// fail early here so the error message names the misconfigured path).
	for _, dir := range []string{originalsRoot, thumbRoot, tmpRoot} {
		if err := os.MkdirAll(dir, 0755); err != nil {
			return nil, fmt.Errorf("creating storage dir %s: %w", dir, err)
		}
	}

	// Open SQLite — database lives on SSD for fast metadata queries
	db, err := sql.Open("sqlite", cfg.Database.Path+"?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, fmt.Errorf("opening database: %w", err)
	}
	db.SetMaxOpenConns(1) // SQLite: single writer
	db.SetMaxIdleConns(5)

	if err := migrations.Run(db); err != nil {
		return nil, fmt.Errorf("running migrations: %w", err)
	}

	// Ensure at least one admin user exists on first run
	if err := ensureAdminUser(db); err != nil {
		log.Printf("warning: could not ensure admin user: %v", err)
	}

	assetSvc := assets.NewService(db, originalsRoot, thumbRoot, tmpRoot, cfg.Thumbnails.Workers, cfg.Thumbnails.JpegQuality)

	return &Server{
		cfg:          cfg,
		db:           db,
		assetSvc:     assetSvc,
		originalsRoot: originalsRoot,
		storageRoot:  cfg.Storage.Root,
	}, nil
}

// Router builds and returns the HTTP mux.
func (s *Server) Router() http.Handler {
	r := chi.NewRouter()

	// Global middleware
	r.Use(middleware.RealIP)
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Timeout(120 * time.Second))
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"*"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-Api-Key"},
		ExposedHeaders:   []string{"Content-Length", "Content-Disposition"},
		AllowCredentials: false,
		MaxAge:           300,
	}))

	// Initialize handlers
	authSvc := auth.NewService(s.db, s.cfg.Auth.JWTSecret, s.cfg.Auth.JWTExpiryHours)
	authH := auth.NewHandler(authSvc, s.db)
	assetH := assets.NewHandler(s.assetSvc, s.db)
	albumH := albums.NewHandler(s.db)
	browserH := browser.NewHandler(s.db, s.originalsRoot)
	searchH := search.NewHandler(s.db)
	infoH := NewInfoHandler(s.db, s.originalsRoot, s.storageRoot, version, s.assetSvc)

	r.Route("/api/v1", func(r chi.Router) {

		// Public
		r.Post("/auth/login", authH.Login)
		r.Get("/server/info", infoH.Info)

		// Authenticated
		r.Group(func(r chi.Router) {
			r.Use(authSvc.Middleware)

			r.Post("/auth/logout", authH.Logout)
			r.Post("/auth/refresh", authH.Refresh)
			r.Post("/auth/change-password", authH.ChangePassword)

			// Users
			r.Get("/users/me", authH.Me)
			r.Patch("/users/me", authH.UpdateMe)

			// Admin-only user management
			r.Group(func(r chi.Router) {
				r.Use(auth.AdminMiddleware)
				r.Get("/users", authH.ListUsers)
				r.Post("/users", authH.CreateUser)
				r.Delete("/users/{userID}", authH.DeleteUser)
			})

			// API Keys
			r.Get("/api-keys", authH.ListAPIKeys)
			r.Post("/api-keys", authH.CreateAPIKey)
			r.Delete("/api-keys/{keyID}", authH.DeleteAPIKey)

			// Assets
			r.Post("/assets/upload", assetH.Upload)
			r.Post("/assets/check-duplicates", assetH.CheckDuplicates)
			r.Post("/assets/bulk-delete", assetH.BulkDelete)
			r.Get("/assets/timeline", assetH.Timeline)
			r.Get("/assets", assetH.List)
			r.Get("/assets/{id}", assetH.Get)
			r.Get("/assets/{id}/original", assetH.ServeOriginal)
			r.Get("/assets/{id}/thumbnail", assetH.ServeThumbnail)
			r.Patch("/assets/{id}", assetH.Update)
			r.Delete("/assets/{id}", assetH.Delete)

			// Albums
			r.Get("/albums", albumH.List)
			r.Post("/albums", albumH.Create)
			r.Get("/albums/{id}", albumH.Get)
			r.Patch("/albums/{id}", albumH.Update)
			r.Delete("/albums/{id}", albumH.Delete)
			r.Post("/albums/{id}/assets", albumH.AddAssets)
			r.Delete("/albums/{id}/assets", albumH.RemoveAssets)

			// File browser
			r.Get("/files", browserH.List)
			r.Post("/files/directory", browserH.CreateDirectory)
			r.Delete("/files", browserH.Delete)
			r.Post("/files/move", browserH.Move)
			r.Get("/files/download", browserH.Download)

			// Sync
			r.Get("/sync/status", assetH.SyncStatus)
			r.Post("/sync/changes", assetH.SyncChanges)
			r.Get("/sync/stream", assetH.SyncStream)

			// Search
			r.Get("/search", searchH.Search)

			// Server admin
			r.Group(func(r chi.Router) {
				r.Use(auth.AdminMiddleware)
				r.Get("/server/stats", infoH.Stats)
				r.Post("/server/rescan", infoH.Rescan)
				r.Get("/server/jobs/{jobID}", infoH.GetJob)
			})
		})
	})

	// Health check
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})

	// Serve embedded web UI for all non-API routes
	r.Handle("/*", webHandler())

	return r
}

func ensureAdminUser(db *sql.DB) error {
	var count int
	_ = db.QueryRow(`SELECT COUNT(*) FROM users WHERE is_admin=1`).Scan(&count)
	if count > 0 {
		return nil
	}
	// Create default admin
	hash, err := auth.HashPassword("admin")
	if err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339)
	_, err = db.Exec(`INSERT INTO users(id,email,name,password,is_admin,quota_bytes,created_at,updated_at) VALUES(?,?,?,?,1,?,?,?)`,
		"usr_admin", "admin@picogallery.local", "Administrator", hash, int64(107374182400), now, now)
	if err != nil {
		return err // likely already exists, ignore
	}
	log.Println("Created default admin user: admin@picogallery.local / admin — CHANGE THIS PASSWORD!")
	return nil
}
