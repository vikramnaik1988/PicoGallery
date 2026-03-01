package config

import (
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Server     ServerConfig    `yaml:"server"`
	Storage    StorageConfig   `yaml:"storage"`
	Database   DatabaseConfig  `yaml:"database"`
	Thumbnails ThumbnailConfig `yaml:"thumbnails"`
	Auth       AuthConfig      `yaml:"auth"`
	Upload     UploadConfig    `yaml:"upload"`
}

type ServerConfig struct {
	Port    int    `yaml:"port"`
	Host    string `yaml:"host"`
	TLSCert string `yaml:"tls_cert"`
	TLSKey  string `yaml:"tls_key"`
}

// StorageConfig supports a hybrid split-drive layout:
//
//	originals_root → slow HDD (large sequential reads/writes are fine)
//	thumbnails_root → fast SSD (random small reads for gallery scroll)
//	tmp_root        → fast SSD (upload staging before move to HDD)
//
// If originals_root / thumbnails_root / tmp_root are empty, they fall back
// to sub-directories of root (backward-compatible single-drive mode).
type StorageConfig struct {
	Root          string `yaml:"root"`            // fallback / legacy single-drive root
	OriginalsRoot string `yaml:"originals_root"`  // HDD mount — originals only
	ThumbnailsRoot string `yaml:"thumbnails_root"` // SSD path — all thumbnail tiers
	TmpRoot       string `yaml:"tmp_root"`         // SSD path — upload staging
}

// EffectiveOriginalsRoot returns the configured originals path or falls back
// to {root}/originals for single-drive / legacy deployments.
func (s StorageConfig) EffectiveOriginalsRoot() string {
	if s.OriginalsRoot != "" {
		return s.OriginalsRoot
	}
	return filepath.Join(s.Root, "originals")
}

// EffectiveThumbnailsRoot returns the configured thumbnails path or falls back
// to {root}/.thumbnails.
func (s StorageConfig) EffectiveThumbnailsRoot() string {
	if s.ThumbnailsRoot != "" {
		return s.ThumbnailsRoot
	}
	return filepath.Join(s.Root, ".thumbnails")
}

// EffectiveTmpRoot returns the configured tmp path or falls back to {root}/tmp.
func (s StorageConfig) EffectiveTmpRoot() string {
	if s.TmpRoot != "" {
		return s.TmpRoot
	}
	return filepath.Join(s.Root, "tmp")
}

type DatabaseConfig struct {
	Path string `yaml:"path"`
}

type ThumbnailConfig struct {
	Workers     int `yaml:"workers"`
	JpegQuality int `yaml:"jpeg_quality"`
}

type AuthConfig struct {
	JWTSecret      string `yaml:"jwt_secret"`
	JWTExpiryHours int    `yaml:"jwt_expiry_hours"`
}

type UploadConfig struct {
	MaxFileSizeMB int  `yaml:"max_file_size_mb"`
	AllowVideo    bool `yaml:"allowed_video"`
	AllowRaw      bool `yaml:"allowed_raw"`
}

func Load() (*Config, error) {
	cfg := defaults()

	path := "config.yaml"
	if p := os.Getenv("PICO_CONFIG"); p != "" {
		path = p
	}

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			applyEnvOverrides(cfg)
			return cfg, nil
		}
		return nil, fmt.Errorf("reading config: %w", err)
	}

	if err := yaml.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("parsing config: %w", err)
	}

	applyEnvOverrides(cfg)
	return cfg, nil
}

func defaults() *Config {
	return &Config{
		Server: ServerConfig{
			Port: 3456,
			Host: "0.0.0.0",
		},
		Storage: StorageConfig{
			Root: "./data/storage",
		},
		Database: DatabaseConfig{
			Path: "./data/picogallery.db",
		},
		Thumbnails: ThumbnailConfig{
			Workers:     4, // 4 workers suits the A7S octa-core; reduce to 2 on quad-core SBCs
			JpegQuality: 85,
		},
		Auth: AuthConfig{
			JWTSecret:      "change-this-to-a-random-secret",
			JWTExpiryHours: 24,
		},
		Upload: UploadConfig{
			MaxFileSizeMB: 500,
			AllowVideo:    true,
			AllowRaw:      false,
		},
	}
}

func applyEnvOverrides(cfg *Config) {
	if v := os.Getenv("PICO_JWT_SECRET"); v != "" {
		cfg.Auth.JWTSecret = v
	}
	if v := os.Getenv("PICO_STORAGE_ROOT"); v != "" {
		cfg.Storage.Root = v
	}
	if v := os.Getenv("PICO_DB_PATH"); v != "" {
		cfg.Database.Path = v
	}
	// Hybrid split-drive overrides
	if v := os.Getenv("PICO_ORIGINALS_ROOT"); v != "" {
		cfg.Storage.OriginalsRoot = v
	}
	if v := os.Getenv("PICO_THUMBNAILS_ROOT"); v != "" {
		cfg.Storage.ThumbnailsRoot = v
	}
	if v := os.Getenv("PICO_TMP_ROOT"); v != "" {
		cfg.Storage.TmpRoot = v
	}
}
