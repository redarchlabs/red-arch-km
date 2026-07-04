// Package middleware provides HTTP middleware for authentication and request context.
package middleware

import (
	"context"
	"errors"
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

// UserClaims represents the decoded JWT claims for a user extracted from a
// Clerk session token (via the configured JWT template).
type UserClaims struct {
	Sub               string `json:"sub"`
	Email             string `json:"email"`
	EmailVerified     bool   `json:"email_verified"`
	PreferredUsername string `json:"preferred_username"`
	Name              string `json:"name"`
}

// GetUserClaims extracts user claims from request context.
func GetUserClaims(ctx context.Context) (UserClaims, bool) {
	claims, ok := ctx.Value(userClaimsKey).(UserClaims)
	return claims, ok
}

// ErrNoVerifier indicates the token's issuer did not match the configured
// Clerk issuer.
var ErrNoVerifier = errors.New("token issuer does not match the configured auth provider")

// JWTConfig holds JWT middleware configuration for the Clerk verifier.
type JWTConfig struct {
	// Clerk.
	//   ClerkIssuer     = Clerk Frontend API URL (the token `iss`), e.g.
	//                     https://clerk.example.com or https://<slug>.clerk.accounts.dev
	//   ClerkAllowedAZP = allowlist of authorized parties (UI origins). Clerk
	//                     default session tokens carry no `aud`; the security-
	//                     critical replacement is the `azp` allowlist (G-AZP/R2).
	ClerkIssuer     string
	ClerkAllowedAZP []string

	// CacheTTL is the JWKS minimum refresh interval (default 5m).
	CacheTTL time.Duration
}

// verifier holds the Clerk verification parameters, keyed by issuer.
type verifier struct {
	issuer  string
	jwksURL string
	// allowedAZP enables strict `azp` enforcement (G-AZP): the token's `azp`
	// MUST be present AND a member of the set.
	allowedAZP map[string]struct{}
}

// JWTMiddleware validates RS256 Clerk session tokens selected by issuer.
type JWTMiddleware struct {
	verifiers map[string]*verifier // keyed by issuer
	jwksURLs  []string
	cacheTTL  time.Duration
	cache     jwk.Cache
	cacheOnce sync.Once
}

// NewJWTMiddleware creates a new JWT validation middleware. It registers the
// Clerk verifier when ClerkIssuer is set; it must be configured for any request
// to authenticate.
func NewJWTMiddleware(cfg JWTConfig) *JWTMiddleware {
	ttl := cfg.CacheTTL
	if ttl == 0 {
		ttl = 5 * time.Minute
	}

	m := &JWTMiddleware{
		verifiers: make(map[string]*verifier),
		cacheTTL:  ttl,
	}

	// Clerk verifier (azp allowlist instead of audience).
	if cfg.ClerkIssuer != "" {
		issuer := strings.TrimRight(cfg.ClerkIssuer, "/")
		allow := make(map[string]struct{}, len(cfg.ClerkAllowedAZP))
		for _, azp := range cfg.ClerkAllowedAZP {
			if azp = strings.TrimSpace(azp); azp != "" {
				allow[azp] = struct{}{}
			}
		}
		v := &verifier{
			issuer:     issuer,
			jwksURL:    issuer + "/.well-known/jwks.json",
			allowedAZP: allow,
		}
		m.verifiers[issuer] = v
		m.jwksURLs = append(m.jwksURLs, v.jwksURL)
	}

	return m
}

// initCache initializes the shared JWKS cache lazily, registering the Clerk
// JWKS endpoint. A failed initial fetch is logged but
// not fatal — the cache retries on first use, and validateToken surfaces a
// clean 401 if keys are still unavailable (AC-1.4 graceful outage).
func (m *JWTMiddleware) initCache(ctx context.Context) error {
	var initErr error
	m.cacheOnce.Do(func() {
		// Use context.Background() so the cache's background-refresh goroutine
		// is not tied to the first request's context (which is cancelled when
		// that request completes, killing all subsequent JWKS refreshes).
		c := jwk.NewCache(context.Background())
		for _, u := range m.jwksURLs {
			if err := c.Register(u, jwk.WithMinRefreshInterval(m.cacheTTL)); err != nil {
				// Non-fatal: a URL that fails to register simply yields a 401 on
				// Get (fail-closed). Don't abort — that would leave m.cache
				// unassigned and, since cacheOnce won't re-run, brick every
				// later request with a zero-value cache.
				slog.Warn("JWKS register failed", "url", u, "error", err)
				continue
			}
			if _, err := c.Refresh(ctx, u); err != nil {
				slog.Warn("initial JWKS fetch failed", "url", u, "error", err)
				// Non-fatal: the cache retries on first use.
			}
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

// validateToken validates the JWT against the Clerk verifier matching its
// issuer and returns the extracted claims. The `iss` is read unverified only to
// select the verifier; the verified parse below re-pins the issuer, so a forged
// `iss` cannot bypass signature/issuer validation.
func (m *JWTMiddleware) validateToken(ctx context.Context, tokenString string) (UserClaims, error) {
	if err := m.initCache(ctx); err != nil {
		return UserClaims{}, err
	}

	// Read the issuer WITHOUT trusting the signature, purely to route to the
	// correct verifier. The verified parse below re-pins the issuer, so a
	// forged `iss` cannot bypass signature/issuer validation.
	unverified, err := jwt.Parse([]byte(tokenString), jwt.WithVerify(false), jwt.WithValidate(false))
	if err != nil {
		return UserClaims{}, err
	}

	v, ok := m.verifiers[unverified.Issuer()]
	if !ok {
		return UserClaims{}, ErrNoVerifier
	}

	keySet, err := m.cache.Get(ctx, v.jwksURL)
	if err != nil {
		return UserClaims{}, err
	}

	token, err := jwt.Parse([]byte(tokenString),
		jwt.WithKeySet(keySet),
		jwt.WithValidate(true),
		jwt.WithIssuer(v.issuer),
	)
	if err != nil {
		return UserClaims{}, err
	}

	// G-AZP (R2): Clerk tokens carry no `aud`; enforce the authorized-party
	// allowlist instead. The token's `azp` MUST be present and a member of
	// CLERK_ALLOWED_AZP, else the request is rejected — this blocks token-origin
	// confusion across Clerk frontends.
	if err := checkAuthorizedParty(token, v.allowedAZP); err != nil {
		return UserClaims{}, err
	}

	// Fail closed if the token carries no subject — auth must never grant an
	// empty identity (downstream provisioning keys on `sub`; a misconfigured
	// Clerk JWT template could omit it). Mirrors the Python get_current_user
	// "Token missing subject claim" guard.
	claims := extractClaims(token)
	if claims.Sub == "" {
		return UserClaims{}, errors.New("token missing subject claim")
	}
	return claims, nil
}

// checkAuthorizedParty enforces the Clerk `azp` allowlist (G-AZP).
func checkAuthorizedParty(token jwt.Token, allowed map[string]struct{}) error {
	raw, ok := token.Get("azp")
	if !ok {
		return errors.New("token missing azp claim")
	}
	azp, ok := raw.(string)
	if !ok || azp == "" {
		return errors.New("token has empty azp claim")
	}
	if _, ok := allowed[azp]; !ok {
		return errors.New("token azp is not an authorized party")
	}
	return nil
}

// extractClaims maps verified Clerk token claims onto UserClaims. Clerk exposes
// `username` via the JWT template; a `preferred_username` claim is also accepted
// as a fallback. Either populates PreferredUsername for downstream provisioning.
func extractClaims(token jwt.Token) UserClaims {
	claims := UserClaims{Sub: token.Subject()}

	if email, ok := token.Get("email"); ok {
		if s, ok := email.(string); ok {
			claims.Email = s
		}
	}
	// email_verified gates the verified-email relink (anti-takeover, AC-4.3).
	// Tolerate both the OIDC boolean and a stringified form.
	if ev, ok := token.Get("email_verified"); ok {
		switch v := ev.(type) {
		case bool:
			claims.EmailVerified = v
		case string:
			claims.EmailVerified = v == "true"
		}
	}
	if username, ok := token.Get("username"); ok {
		if s, ok := username.(string); ok && s != "" {
			claims.PreferredUsername = s
		}
	}
	if claims.PreferredUsername == "" {
		if username, ok := token.Get("preferred_username"); ok {
			if s, ok := username.(string); ok {
				claims.PreferredUsername = s
			}
		}
	}
	if name, ok := token.Get("name"); ok {
		if s, ok := name.(string); ok {
			claims.Name = s
		}
	}

	return claims
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
