package auth

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
)

type Handler struct {
	svc *Service
	db  *sql.DB
}

func NewHandler(svc *Service, db *sql.DB) *Handler {
	return &Handler{svc: svc, db: db}
}

// POST /api/v1/auth/login
func (h *Handler) Login(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid request body.", http.StatusBadRequest)
		return
	}
	req.Email = strings.ToLower(strings.TrimSpace(req.Email))

	var id, hash, name string
	var isAdmin bool
	err := h.db.QueryRow(`SELECT id, password, name, is_admin FROM users WHERE email=?`, req.Email).
		Scan(&id, &hash, &name, &isAdmin)
	if err != nil || !CheckPassword(hash, req.Password) {
		writeError(w, "UNAUTHORIZED", "Invalid email or password.", http.StatusUnauthorized)
		return
	}

	token, expiresAt, err := h.svc.IssueToken(id, isAdmin)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Could not issue token.", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"access_token": token,
		"token_type":   "Bearer",
		"expires_at":   expiresAt.UTC().Format(time.RFC3339),
		"user": map[string]interface{}{
			"id":       id,
			"email":    req.Email,
			"name":     name,
			"is_admin": isAdmin,
		},
	})
}

// POST /api/v1/auth/logout
func (h *Handler) Logout(w http.ResponseWriter, r *http.Request) {
	auth := r.Header.Get("Authorization")
	if strings.HasPrefix(auth, "Bearer ") {
		tok := strings.TrimPrefix(auth, "Bearer ")
		claims, err := h.svc.ValidateToken(tok)
		if err == nil {
			_ = h.svc.BlockToken(claims.ID, claims.ExpiresAt.Time)
		}
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "Logged out successfully."})
}

// POST /api/v1/auth/refresh
func (h *Handler) Refresh(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid request body.", http.StatusBadRequest)
		return
	}
	claims, err := h.svc.ValidateToken(req.Token)
	if err != nil {
		writeError(w, "UNAUTHORIZED", "Invalid or expired token.", http.StatusUnauthorized)
		return
	}
	// Block old token
	_ = h.svc.BlockToken(claims.ID, claims.ExpiresAt.Time)

	newToken, expiresAt, err := h.svc.IssueToken(claims.UserID, claims.IsAdmin)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Could not issue token.", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"access_token": newToken,
		"token_type":   "Bearer",
		"expires_at":   expiresAt.UTC().Format(time.RFC3339),
	})
}

// POST /api/v1/auth/change-password
func (h *Handler) ChangePassword(w http.ResponseWriter, r *http.Request) {
	user := UserFromContext(r.Context())
	var req struct {
		CurrentPassword string `json:"current_password"`
		NewPassword     string `json:"new_password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid request body.", http.StatusBadRequest)
		return
	}
	var hash string
	_ = h.db.QueryRow(`SELECT password FROM users WHERE id=?`, user.ID).Scan(&hash)
	if !CheckPassword(hash, req.CurrentPassword) {
		writeError(w, "UNAUTHORIZED", "Current password is incorrect.", http.StatusUnauthorized)
		return
	}
	if len(req.NewPassword) < 8 {
		writeError(w, "BAD_REQUEST", "Password must be at least 8 characters.", http.StatusBadRequest)
		return
	}
	newHash, err := HashPassword(req.NewPassword)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to hash password.", http.StatusInternalServerError)
		return
	}
	_, _ = h.db.Exec(`UPDATE users SET password=?, updated_at=? WHERE id=?`,
		newHash, time.Now().UTC().Format(time.RFC3339), user.ID)
	writeJSON(w, http.StatusOK, map[string]string{"message": "Password updated."})
}

// GET /api/v1/users/me
func (h *Handler) Me(w http.ResponseWriter, r *http.Request) {
	user := UserFromContext(r.Context())
	var email, name, createdAt string
	var isAdmin bool
	var quotaBytes, usedBytes int64
	_ = h.db.QueryRow(`SELECT email, name, is_admin, quota_bytes, created_at FROM users WHERE id=?`, user.ID).
		Scan(&email, &name, &isAdmin, &quotaBytes, &createdAt)
	_ = h.db.QueryRow(`SELECT COALESCE(SUM(file_size_bytes),0) FROM assets WHERE user_id=?`, user.ID).
		Scan(&usedBytes)

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"id":          user.ID,
		"email":       email,
		"name":        name,
		"is_admin":    isAdmin,
		"quota_bytes": quotaBytes,
		"used_bytes":  usedBytes,
		"created_at":  createdAt,
	})
}

// PATCH /api/v1/users/me
func (h *Handler) UpdateMe(w http.ResponseWriter, r *http.Request) {
	user := UserFromContext(r.Context())
	var req struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid request body.", http.StatusBadRequest)
		return
	}
	if req.Name == "" {
		writeError(w, "BAD_REQUEST", "Name is required.", http.StatusBadRequest)
		return
	}
	_, _ = h.db.Exec(`UPDATE users SET name=?, updated_at=? WHERE id=?`,
		req.Name, time.Now().UTC().Format(time.RFC3339), user.ID)
	writeJSON(w, http.StatusOK, map[string]string{"message": "Profile updated."})
}

// GET /api/v1/users (admin)
func (h *Handler) ListUsers(w http.ResponseWriter, r *http.Request) {
	rows, err := h.db.Query(`SELECT id, email, name, is_admin FROM users ORDER BY created_at`)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to fetch users.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	users := []map[string]interface{}{}
	for rows.Next() {
		var id, email, name string
		var isAdmin bool
		_ = rows.Scan(&id, &email, &name, &isAdmin)
		users = append(users, map[string]interface{}{
			"id": id, "email": email, "name": name, "is_admin": isAdmin,
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"users": users})
}

// POST /api/v1/users (admin)
func (h *Handler) CreateUser(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email      string `json:"email"`
		Password   string `json:"password"`
		Name       string `json:"name"`
		QuotaBytes int64  `json:"quota_bytes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid request body.", http.StatusBadRequest)
		return
	}
	req.Email = strings.ToLower(strings.TrimSpace(req.Email))
	if req.Email == "" || req.Password == "" || req.Name == "" {
		writeError(w, "BAD_REQUEST", "email, password, and name are required.", http.StatusBadRequest)
		return
	}
	if req.QuotaBytes == 0 {
		req.QuotaBytes = 10737418240 // 10 GB default
	}
	hash, err := HashPassword(req.Password)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to hash password.", http.StatusInternalServerError)
		return
	}
	id := "usr_" + uuid.NewString()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err = h.db.Exec(`INSERT INTO users(id,email,name,password,is_admin,quota_bytes,created_at,updated_at) VALUES(?,?,?,?,0,?,?,?)`,
		id, req.Email, req.Name, hash, req.QuotaBytes, now, now)
	if err != nil {
		writeError(w, "CONFLICT", "Email already in use.", http.StatusConflict)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]interface{}{
		"id": id, "email": req.Email, "name": req.Name, "is_admin": false,
	})
}

// DELETE /api/v1/users/{userID} (admin)
func (h *Handler) DeleteUser(w http.ResponseWriter, r *http.Request) {
	// chi router — extract from URL
	userID := r.PathValue("userID")
	if userID == "" {
		writeError(w, "BAD_REQUEST", "Missing userID.", http.StatusBadRequest)
		return
	}
	_, _ = h.db.Exec(`DELETE FROM users WHERE id=?`, userID)
	writeJSON(w, http.StatusOK, map[string]string{"message": "User deleted."})
}

// --- API Keys ---

// GET /api/v1/api-keys
func (h *Handler) ListAPIKeys(w http.ResponseWriter, r *http.Request) {
	user := UserFromContext(r.Context())
	rows, err := h.db.Query(`SELECT id, name, created_at, last_used_at FROM api_keys WHERE user_id=? ORDER BY created_at`, user.ID)
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to fetch keys.", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	keys := []map[string]interface{}{}
	for rows.Next() {
		var id, name, created string
		var lastUsed *string
		_ = rows.Scan(&id, &name, &created, &lastUsed)
		k := map[string]interface{}{"id": id, "name": name, "created_at": created, "last_used_at": lastUsed}
		keys = append(keys, k)
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"keys": keys})
}

// POST /api/v1/api-keys
func (h *Handler) CreateAPIKey(w http.ResponseWriter, r *http.Request) {
	user := UserFromContext(r.Context())
	var req struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, "BAD_REQUEST", "Invalid request body.", http.StatusBadRequest)
		return
	}
	raw, hash, err := GenerateAPIKey()
	if err != nil {
		writeError(w, "INTERNAL_ERROR", "Failed to generate key.", http.StatusInternalServerError)
		return
	}
	id := "key_" + uuid.NewString()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = h.db.Exec(`INSERT INTO api_keys(id,user_id,name,key_hash,created_at) VALUES(?,?,?,?,?)`,
		id, user.ID, req.Name, hash, now)
	writeJSON(w, http.StatusCreated, map[string]interface{}{
		"id": id, "name": req.Name, "key": raw,
	})
}

// DELETE /api/v1/api-keys/{keyID}
func (h *Handler) DeleteAPIKey(w http.ResponseWriter, r *http.Request) {
	user := UserFromContext(r.Context())
	keyID := r.PathValue("keyID")
	_, _ = h.db.Exec(`DELETE FROM api_keys WHERE id=? AND user_id=?`, keyID, user.ID)
	writeJSON(w, http.StatusOK, map[string]string{"message": "Key revoked."})
}

// --- helpers ---

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
			"code":    code,
			"message": message,
			"status":  status,
		},
	})
}
