// Package middleware provides HTTP middleware for authentication and request context.
package middleware

import (
	"context"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/lestrrat-go/jwx/v2/jwk"
	"github.com/lestrrat-go/jwx/v2/jwt"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
)

// Context keys for storing auth info.
type contextKey string

const (
	userClaimsKey contextKey = "user_claims"
)

// UserClaims represents the decoded JWT claims for a user.
type UserClaims struct {
	Sub               string `json:"sub"`
	Email             string `json:"email"`
	PreferredUsername string `json:"preferred_username"`
	Name              string `json:"name"`
}

// GetUserClaims extracts user claims from request context.
func GetUserClaims(ctx context.Context) (UserClaims, bool) {
	claims, ok := ctx.Value(userClaimsKey).(UserClaims)
	return claims, ok
}

// JWTConfig holds JWT middleware configuration.
type JWTConfig struct {
	KeycloakURL  string
	Realm        string
	ClientID     string
	CacheTTL     time.Duration
}

// JWTMiddleware validates Keycloak JWTs.
type JWTMiddleware struct {
	config     JWTConfig
	jwksURL    string
	issuer     string
	cache      jwk.Cache
	cacheOnce  sync.Once
}

// NewJWTMiddleware creates a new JWT validation middleware.
func NewJWTMiddleware(cfg JWTConfig) *JWTMiddleware {
	if cfg.CacheTTL == 0 {
		cfg.CacheTTL = 5 * time.Minute
	}

	return &JWTMiddleware{
		config:  cfg,
		jwksURL: cfg.KeycloakURL + "/realms/" + cfg.Realm + "/protocol/openid-connect/certs",
		issuer:  cfg.KeycloakURL + "/realms/" + cfg.Realm,
	}
}

// initCache initializes the JWKS cache lazily.
func (m *JWTMiddleware) initCache(ctx context.Context) error {
	var initErr error
	m.cacheOnce.Do(func() {
		c := jwk.NewCache(ctx)
		if err := c.Register(m.jwksURL, jwk.WithMinRefreshInterval(m.config.CacheTTL)); err != nil {
			initErr = err
			return
		}
		// Trigger initial fetch
		if _, err := c.Refresh(ctx, m.jwksURL); err != nil {
			slog.Warn("initial JWKS fetch failed", "error", err)
			// Don't fail - the cache will retry on first use
		}
		m.cache = *c
	})
	return initErr
}

// Handler returns the middleware handler function.
func (m *JWTMiddleware) Handler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token := extractBearerToken(r)
		if token == "" {
			httputil.Unauthorized(w, "Missing bearer token")
			return
		}

		claims, err := m.validateToken(r.Context(), token)
		if err != nil {
			slog.Debug("JWT validation failed", "error", err)
			httputil.Unauthorized(w, "Invalid token")
			return
		}

		ctx := context.WithValue(r.Context(), userClaimsKey, claims)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// validateToken validates the JWT and returns claims.
func (m *JWTMiddleware) validateToken(ctx context.Context, tokenString string) (UserClaims, error) {
	if err := m.initCache(ctx); err != nil {
		return UserClaims{}, err
	}

	keySet, err := m.cache.Get(ctx, m.jwksURL)
	if err != nil {
		return UserClaims{}, err
	}

	token, err := jwt.Parse(
		[]byte(tokenString),
		jwt.WithKeySet(keySet),
		jwt.WithValidate(true),
		jwt.WithIssuer(m.issuer),
		jwt.WithAudience(m.config.ClientID),
	)
	if err != nil {
		return UserClaims{}, err
	}

	claims := UserClaims{
		Sub: token.Subject(),
	}

	// Extract additional claims
	if email, ok := token.Get("email"); ok {
		if s, ok := email.(string); ok {
			claims.Email = s
		}
	}
	if username, ok := token.Get("preferred_username"); ok {
		if s, ok := username.(string); ok {
			claims.PreferredUsername = s
		}
	}
	if name, ok := token.Get("name"); ok {
		if s, ok := name.(string); ok {
			claims.Name = s
		}
	}

	return claims, nil
}

// extractBearerToken extracts the JWT from the Authorization header.
func extractBearerToken(r *http.Request) string {
	auth := r.Header.Get("Authorization")
	if auth == "" {
		return ""
	}
	parts := strings.SplitN(auth, " ", 2)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "bearer") {
		return ""
	}
	return parts[1]
}
