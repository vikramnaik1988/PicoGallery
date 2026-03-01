package assets

import (
	"bytes"
	"fmt"
	"image"
	"image/jpeg"
	"os"
	"os/exec"
	"strings"
)

// isHEICType reports whether mediaType is a HEIC or HEIF image.
func isHEICType(mediaType string) bool {
	return mediaType == "image/heic" || mediaType == "image/heif"
}

// isHEICPath reports whether path is a HEIC/HEIF file by extension.
func isHEICPath(path string) bool {
	lower := strings.ToLower(path)
	return strings.HasSuffix(lower, ".heic") || strings.HasSuffix(lower, ".heif")
}

// heicToJPEGBytes converts a HEIC/HEIF file to in-memory JPEG bytes using an
// external command. Returns nil if no suitable converter is found — callers
// should treat nil as "HEIC tools not installed" and skip gracefully.
//
// Tool priority (first that succeeds wins):
//  1. magick      — ImageMagick 7.x (Windows + Linux: apt install imagemagick)
//  2. convert     — ImageMagick 6.x (Linux only; on Windows this is a system tool)
//  3. heif-convert — apt install libheif-examples
func heicToJPEGBytes(path string) []byte {
	// ImageMagick 7.x: `magick` is the unified binary (works on Windows & Linux)
	out, err := exec.Command("magick", path+"[0]", "-auto-orient", "jpeg:-").Output()
	if err == nil && len(out) > 0 {
		return out
	}

	// ImageMagick 6.x (Linux): `convert` subcommand
	out, err = exec.Command("convert", path+"[0]", "-auto-orient", "jpeg:-").Output()
	if err == nil && len(out) > 0 {
		return out
	}

	// heif-convert: must write to a temp file
	tmp, err := os.CreateTemp("", "pico_heic_*.jpg")
	if err != nil {
		return nil
	}
	tmpPath := tmp.Name()
	tmp.Close()
	defer os.Remove(tmpPath)

	if err := exec.Command("heif-convert", "-q", "90", path, tmpPath).Run(); err == nil {
		if data, err := os.ReadFile(tmpPath); err == nil {
			return data
		}
	}
	return nil
}

// decodeHEIC returns an image.Image for a HEIC/HEIF file.
// Requires ImageMagick (magick or convert) or heif-convert to be on PATH.
func decodeHEIC(path string) (image.Image, error) {
	// ImageMagick 7.x
	out, err := exec.Command("magick", path+"[0]", "-auto-orient", "jpeg:-").Output()
	if err == nil && len(out) > 0 {
		return jpeg.Decode(bytes.NewReader(out))
	}

	// ImageMagick 6.x (Linux)
	out, err = exec.Command("convert", path+"[0]", "-auto-orient", "jpeg:-").Output()
	if err == nil && len(out) > 0 {
		return jpeg.Decode(bytes.NewReader(out))
	}

	// Fallback: heif-convert writes to a temp file
	tmp, err2 := os.CreateTemp("", "pico_heic_*.jpg")
	if err2 != nil {
		return nil, fmt.Errorf("HEIC decode requires ImageMagick (magick/convert) or heif-convert: %v", err)
	}
	tmpPath := tmp.Name()
	tmp.Close()
	defer os.Remove(tmpPath)

	if err2 := exec.Command("heif-convert", "-q", "90", path, tmpPath).Run(); err2 == nil {
		f, err3 := os.Open(tmpPath)
		if err3 != nil {
			return nil, err3
		}
		defer f.Close()
		return jpeg.Decode(f)
	}
	return nil, fmt.Errorf("HEIC decode requires ImageMagick (magick/convert) or heif-convert: %v", err)
}
