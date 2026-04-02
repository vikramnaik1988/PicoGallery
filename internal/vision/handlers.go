package vision

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"

	_ "modernc.org/sqlite"
)

// Handler handles vision-related API endpoints.
type Handler struct {
	chatbotDir string // path to Chatbot/ directory
}

// NewHandler creates a vision Handler.
// chatbotDir should be the absolute path to the Chatbot/ directory containing vision/.
func NewHandler(chatbotDir string) *Handler {
	return &Handler{chatbotDir: chatbotDir}
}

// AnalyseAll runs the vision indexer and waits for it to complete.
// POST /api/v1/vision/analyse-all
func (h *Handler) AnalyseAll(w http.ResponseWriter, r *http.Request) {
	chatbotDir := h.chatbotDir
	if chatbotDir == "" {
		home, _ := os.UserHomeDir()
		chatbotDir = filepath.Join(home, "PicoGallery", "Chatbot")
	}

	cmd := exec.Command("python3", "-m", "vision.indexer")
	cmd.Dir = chatbotDir

	if err := cmd.Run(); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "Indexer failed: " + err.Error(),
		})
		return
	}

	// Return final count from DB
	count := h.countIndexed(chatbotDir)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"indexed": count,
		"message": fmt.Sprintf("Done. %d photos indexed.", count),
	})
}

// Status returns the count of indexed photos.
// GET /api/v1/vision/status
func (h *Handler) Status(w http.ResponseWriter, r *http.Request) {
	chatbotDir := h.chatbotDir
	if chatbotDir == "" {
		home, _ := os.UserHomeDir()
		chatbotDir = filepath.Join(home, "PicoGallery", "Chatbot")
	}

	dbPath := filepath.Join(chatbotDir, "vision_metadata.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"indexed": 0, "message": "Database not found"})
		return
	}
	defer db.Close()

	var count int
	db.QueryRow("SELECT COUNT(*) FROM photos").Scan(&count)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"indexed": count,
		"message": fmt.Sprintf("%d photos indexed", count),
	})
}
