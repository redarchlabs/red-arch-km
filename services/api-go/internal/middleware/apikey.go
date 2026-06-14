package middleware

import (
	"crypto/subtle"
	"net/http"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
)

// APIKeyAuth creates middleware that validates an X-API-Key header.
// Uses constant-time comparison to prevent timing attacks.
func APIKeyAuth(validKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			key := r.Header.Get("X-API-Key")
			if key == "" {
				httputil.Unauthorized(w, "Missing API key")
				return
			}

			if subtle.ConstantTimeCompare([]byte(key), []byte(validKey)) != 1 {
				httputil.Forbidden(w, "Invalid API key")
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
