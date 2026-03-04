package migrations

import (
	"database/sql"
	"fmt"
)

// Run applies all schema migrations in order.
func Run(db *sql.DB) error {
	if err := enableWAL(db); err != nil {
		return err
	}
	for i, m := range all {
		if err := apply(db, i+1, m); err != nil {
			return fmt.Errorf("migration %d failed: %w", i+1, err)
		}
	}
	return nil
}

func enableWAL(db *sql.DB) error {
	_, err := db.Exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;`)
	return err
}

// apply runs a list of SQL statements as a single migration version.
// Each statement is executed individually inside one transaction so that
// ALTER TABLE (single-column-at-a-time in SQLite) works correctly.
func apply(db *sql.DB, version int, stmts []string) error {
	var exists int
	_ = db.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE version=?`, version).Scan(&exists)
	if exists > 0 {
		return nil
	}
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	for _, s := range stmts {
		if s == "" {
			continue
		}
		if _, err := tx.Exec(s); err != nil {
			_ = tx.Rollback()
			preview := s
			if len(preview) > 60 {
				preview = preview[:60]
			}
			return fmt.Errorf("stmt %q: %w", preview, err)
		}
	}
	if _, err := tx.Exec(`INSERT INTO schema_migrations(version) VALUES(?)`, version); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

var all = [][]string{
	// Migration 1: base schema
	{
		`CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)`,
		`CREATE TABLE IF NOT EXISTS users (
	id          TEXT PRIMARY KEY,
	email       TEXT NOT NULL UNIQUE,
	name        TEXT NOT NULL,
	password    TEXT NOT NULL,
	is_admin    INTEGER NOT NULL DEFAULT 0,
	quota_bytes INTEGER NOT NULL DEFAULT 10737418240,
	created_at  TEXT NOT NULL,
	updated_at  TEXT NOT NULL
)`,
		`CREATE TABLE IF NOT EXISTS api_keys (
	id          TEXT PRIMARY KEY,
	user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	name        TEXT NOT NULL,
	key_hash    TEXT NOT NULL UNIQUE,
	created_at  TEXT NOT NULL,
	last_used_at TEXT
)`,
		`CREATE TABLE IF NOT EXISTS assets (
	id              TEXT PRIMARY KEY,
	user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	filename        TEXT NOT NULL,
	original_path   TEXT NOT NULL,
	media_type      TEXT NOT NULL,
	file_size_bytes INTEGER NOT NULL,
	width           INTEGER,
	height          INTEGER,
	duration_seconds REAL,
	taken_at        TEXT,
	created_at      TEXT NOT NULL,
	is_favorited    INTEGER NOT NULL DEFAULT 0,
	is_archived     INTEGER NOT NULL DEFAULT 0,
	checksum_sha256 TEXT NOT NULL,
	device_asset_id TEXT,
	device_id       TEXT,
	exif_make       TEXT,
	exif_model      TEXT,
	exif_gps_lat    REAL,
	exif_gps_lng    REAL,
	exif_focal_mm   REAL,
	exif_aperture   REAL,
	exif_iso        INTEGER,
	exif_shutter    TEXT,
	thumb_small     INTEGER NOT NULL DEFAULT 0,
	thumb_preview   INTEGER NOT NULL DEFAULT 0
)`,
		`CREATE INDEX IF NOT EXISTS idx_assets_user_taken ON assets(user_id, taken_at DESC)`,
		`CREATE INDEX IF NOT EXISTS idx_assets_checksum ON assets(checksum_sha256)`,
		`CREATE INDEX IF NOT EXISTS idx_assets_device ON assets(user_id, device_asset_id)`,
		`CREATE INDEX IF NOT EXISTS idx_assets_favorited ON assets(user_id, is_favorited)`,
		`CREATE TABLE IF NOT EXISTS albums (
	id             TEXT PRIMARY KEY,
	user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	name           TEXT NOT NULL,
	description    TEXT,
	cover_asset_id TEXT,
	created_at     TEXT NOT NULL,
	updated_at     TEXT NOT NULL
)`,
		`CREATE TABLE IF NOT EXISTS album_assets (
	album_id   TEXT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
	asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
	added_at   TEXT NOT NULL,
	PRIMARY KEY (album_id, asset_id)
)`,
		`CREATE TABLE IF NOT EXISTS jwt_blocklist (
	token_id   TEXT PRIMARY KEY,
	expires_at TEXT NOT NULL
)`,
		`CREATE TABLE IF NOT EXISTS background_jobs (
	id          TEXT PRIMARY KEY,
	type        TEXT NOT NULL,
	status      TEXT NOT NULL DEFAULT 'pending',
	progress    TEXT,
	started_at  TEXT,
	finished_at TEXT,
	created_at  TEXT NOT NULL
)`,
	},

	// Migration 2: add large (1080 px) and blur (32 px placeholder) thumbnail
	// columns to support the SSD fast-tier caching strategy for SBC deployments.
	// SQLite only allows one column per ALTER TABLE statement.
	{
		`ALTER TABLE assets ADD COLUMN thumb_large INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE assets ADD COLUMN thumb_blur  INTEGER NOT NULL DEFAULT 0`,
	},

	// Migration 3: indices for common query patterns in the List handler.
	{
		`CREATE INDEX IF NOT EXISTS idx_assets_user_archived ON assets(user_id, is_archived)`,
		`CREATE INDEX IF NOT EXISTS idx_assets_user_media    ON assets(user_id, media_type)`,
	},
}
