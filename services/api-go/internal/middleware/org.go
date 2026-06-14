package middleware

import (
	"context"
	"net/http"

	"github.com/google/uuid"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
)

const (
	orgIDKey contextKey = "org_id"
)

// GetOrgID extracts the org ID from request context.
func GetOrgID(ctx context.Context) (uuid.UUID, bool) {
	id, ok := ctx.Value(orgIDKey).(uuid.UUID)
	return id, ok
}

// MustGetOrgID extracts the org ID from request context, panicking if not present.
// Only use in handlers where RequireOrg middleware is guaranteed to have run.
func MustGetOrgID(ctx context.Context) uuid.UUID {
	id, ok := GetOrgID(ctx)
	if !ok {
		panic("org ID not in context - RequireOrg middleware not applied")
	}
	return id
}

// RequireOrg is middleware that requires and extracts the X-Org-ID header.
func RequireOrg(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		orgIDStr := r.Header.Get("X-Org-ID")
		if orgIDStr == "" {
			httputil.BadRequest(w, "X-Org-ID header is required")
			return
		}

		orgID, err := uuid.Parse(orgIDStr)
		if err != nil {
			httputil.BadRequest(w, "X-Org-ID must be a valid UUID")
			return
		}

		ctx := context.WithValue(r.Context(), orgIDKey, orgID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// OptionalOrg is middleware that extracts the X-Org-ID header if present.
// Unlike RequireOrg, it does not fail if the header is missing.
func OptionalOrg(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		orgIDStr := r.Header.Get("X-Org-ID")
		if orgIDStr != "" {
			orgID, err := uuid.Parse(orgIDStr)
			if err != nil {
				httputil.BadRequest(w, "X-Org-ID must be a valid UUID")
				return
			}
			ctx := context.WithValue(r.Context(), orgIDKey, orgID)
			r = r.WithContext(ctx)
		}
		next.ServeHTTP(w, r)
	})
}
