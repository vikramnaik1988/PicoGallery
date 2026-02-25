package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Server     ServerConfig     `yaml:"server"`
	Storage    StorageConfig    `yaml:"storage"`
	Database   DatabaseConfig   `yaml:"database"`
	Thumbnails ThumbnailConfig  `yaml:"thumbnails"`
	Auth       AuthConfig       `yaml:"auth"`
	Upload     UploadConfig     `yaml:"upload"`
}

type ServerConfig struct {
	Port    int    `yaml:"port"`
	Host    string `yaml:"host"`
	TLSCert string `yaml:"tls_cert"`
	TLSKey  string `yaml:"tls_key"`
}

type StorageConfig struct {
	Root string `yaml:"root"`
}

type DatabaseConfig struct {
	Path string `yaml:"path"`
}

type ThumbnailConfig struct {
	Workers     int `yaml:"workers"`
	JpegQuality int `yaml:"jpeg_quality"`
}

type AuthConfig struct {
	JWTSecret     string `yaml:"jwt_secret"`
	JWTExpiryHours int   `yaml:"jwt_expiry_hours"`
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
			Workers:     2,
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
}
