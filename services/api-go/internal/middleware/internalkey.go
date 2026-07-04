package middleware

import (
	"crypto/subtle"
	"net/http"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
)

// InternalAPIKeyAuth creates middleware that authenticates internal
// service-to-service calls (e.g. worker document-status callbacks) via a
// shared X-Internal-API-Key secret.
//
// This secret is DISTINCT from the brain-api X-API-Key (see APIKeyAuth) by
// design — parity with the Python services/api internal router — so that
// compromise of one service credential does not grant access to the other
// surface. Contract mirrors api.auth.dependencies.require_internal_api_key:
//
//   - validKey == "" (unconfigured): the endpoint is DISABLED and returns 503
//     rather than allowing anonymous access (fail-closed).
//   - missing or mismatched X-Internal-API-Key: 401.
//
// Comparison is constant-time to avoid leaking the key via timing.
func InternalAPIKeyAuth(validKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if validKey == "" {
				httputil.ServiceUnavailable(w, "Internal API disabled (no key configured)")
				return
			}

			key := r.Header.Get("X-Internal-API-Key")
			if key == "" || subtle.ConstantTimeCompare([]byte(key), []byte(validKey)) != 1 {
				httputil.Unauthorized(w, "Invalid internal API credentials")
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
