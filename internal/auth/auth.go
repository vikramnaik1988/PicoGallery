package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"
)

type contextKey string

const UserContextKey contextKey = "user"

type Claims struct {
	UserID  string `json:"uid"`
	IsAdmin bool   `json:"adm"`
	jwt.RegisteredClaims
}

type User struct {
	ID      string
	Email   string
	Name    string
	IsAdmin bool
}

type Service struct {
	db         *sql.DB
	jwtSecret  []byte
	jwtExpiry  time.Duration
}

func NewService(db *sql.DB, secret string, expiryHours int) *Service {
	return &Service{
		db:        db,
		jwtSecret: []byte(secret),
		jwtExpiry: time.Duration(expiryHours) * time.Hour,
	}
}

// IssueToken creates a signed JWT for the user.
func (s *Service) IssueToken(userID string, isAdmin bool) (string, time.Time, error) {
	expiresAt := time.Now().Add(s.jwtExpiry)
	claims := Claims{
		UserID:  userID,
		IsAdmin: isAdmin,
		RegisteredClaims: jwt.RegisteredClaims{
			ID:        uuid.NewString(),
			ExpiresAt: jwt.NewNumericDate(expiresAt),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
		},
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := tok.SignedString(s.jwtSecret)
	return signed, expiresAt, err
}

// ValidateToken parses and validates a JWT string.
func (s *Service) ValidateToken(tokenStr string) (*Claims, error) {
	tok, err := jwt.ParseWithClaims(tokenStr, &Claims{}, func(t *jwt.Token) (interface{}, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("unexpected signing method")
		}
		return s.jwtSecret, nil
	})
	if err != nil {
		return nil, err
	}
	claims, ok := tok.Claims.(*Claims)
	if !ok || !tok.Valid {
		return nil, errors.New("invalid token")
	}
	// Check blocklist
	var blocked int
	_ = s.db.QueryRow(`SELECT COUNT(*) FROM jwt_blocklist WHERE token_id=?`, claims.ID).Scan(&blocked)
	if blocked > 0 {
		return nil, errors.New("token revoked")
	}
	return claims, nil
}

// BlockToken adds a JWT ID to the blocklist.
func (s *Service) BlockToken(tokenID string, expiresAt time.Time) error {
	_, err := s.db.Exec(`INSERT OR IGNORE INTO jwt_blocklist(token_id, expires_at) VALUES(?,?)`,
		tokenID, expiresAt.UTC().Format(time.RFC3339))
	return err
}

// HashPassword bcrypts a password.
func HashPassword(password string) (string, error) {
	b, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	return string(b), err
}

// CheckPassword verifies a bcrypt hash.
func CheckPassword(hash, password string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) == nil
}

// GenerateAPIKey generates a random API key and returns (raw, hash).
func GenerateAPIKey() (string, string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", "", err
	}
	raw := "pg_live_" + hex.EncodeToString(b)
	h := sha256.Sum256([]byte(raw))
	return raw, hex.EncodeToString(h[:]), nil
}

// HashAPIKey hashes a raw API key for lookup.
func HashAPIKey(raw string) string {
	h := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(h[:])
}

// Middleware validates JWT or API key and injects User into context.
func (s *Service) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, err := s.extractUser(r)
		if err != nil {
			http.Error(w, `{"error":{"code":"UNAUTHORIZED","message":"Authentication required.","status":401}}`, http.StatusUnauthorized)
			return
		}
		ctx := context.WithValue(r.Context(), UserContextKey, user)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// AdminMiddleware ensures the user is an admin.
func AdminMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user := UserFromContext(r.Context())
		if user == nil || !user.IsAdmin {
			http.Error(w, `{"error":{"code":"FORBIDDEN","message":"Admin access required.","status":403}}`, http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Service) extractUser(r *http.Request) (*User, error) {
	// Try API Key header first
	if key := r.Header.Get("X-Api-Key"); key != "" {
		return s.userFromAPIKey(key)
	}
	// Try Bearer token (header or ?token= query param for <img src> requests)
	tokenStr := ""
	if a := r.Header.Get("Authorization"); strings.HasPrefix(a, "Bearer ") {
		tokenStr = strings.TrimPrefix(a, "Bearer ")
	} else if t := r.URL.Query().Get("token"); t != "" {
		tokenStr = t
	}
	if tokenStr == "" {
		return nil, errors.New("no credentials")
	}
	claims, err := s.ValidateToken(tokenStr)
	if err != nil {
		return nil, err
	}
	var u User
	err = s.db.QueryRow(`SELECT id, email, name, is_admin FROM users WHERE id=?`, claims.UserID).
		Scan(&u.ID, &u.Email, &u.Name, &u.IsAdmin)
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (s *Service) userFromAPIKey(raw string) (*User, error) {
	hash := HashAPIKey(raw)
	var userID string
	var keyID string
	err := s.db.QueryRow(`SELECT ak.id, ak.user_id FROM api_keys ak WHERE ak.key_hash=?`, hash).
		Scan(&keyID, &userID)
	if err != nil {
		return nil, errors.New("invalid API key")
	}
	// Update last_used_at
	_, _ = s.db.Exec(`UPDATE api_keys SET last_used_at=? WHERE id=?`, time.Now().UTC().Format(time.RFC3339), keyID)
	var u User
	err = s.db.QueryRow(`SELECT id, email, name, is_admin FROM users WHERE id=?`, userID).
		Scan(&u.ID, &u.Email, &u.Name, &u.IsAdmin)
	return &u, err
}

// UserFromContext retrieves the authenticated user from context.
func UserFromContext(ctx context.Context) *User {
	u, _ := ctx.Value(UserContextKey).(*User)
	return u
}
