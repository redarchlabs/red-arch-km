package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// First-login provisioning refusals (all surfaced as a single generic 403 to
// avoid an account-existence/email-enumeration oracle):
var (
	// errEmailTakenUnverified: the IdP email matches an existing profile but the
	// IdP has NOT verified it — refuse rather than relink (anti-takeover, AC-4.3).
	errEmailTakenUnverified = errors.New("email matches an existing account but is unverified")
	// errEmailBoundToOtherAccount: the verified email matches a profile already
	// bound to a Clerk subject (e.g. an email/mailbox reassigned to a new hire).
	// Relinking would transfer memberships + access_mask to a different person,
	// so refuse — relink is confined to the legacy Keycloak→Clerk remap (M-1).
	errEmailBoundToOtherAccount = errors.New("email is bound to a different account")
	// errEmailConflict: a fresh provision would violate the email UNIQUE
	// constraint (case-variant / race) — refuse cleanly instead of a 500.
	errEmailConflict = errors.New("email already in use")
)

// isClerkSubject reports whether an auth_subject was issued by Clerk (user_…).
// Legacy Keycloak subjects are UUIDs; relink only rebinds those.
func isClerkSubject(sub string) bool {
	return strings.HasPrefix(sub, "user_")
}

// isUniqueViolation reports whether err is a Postgres unique-constraint
// violation (SQLSTATE 23505).
func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

// userProvisioner is the subset of repository.Queries that first-login
// provisioning needs; an interface so the relink branches are unit-testable
// without a database. *repository.Queries satisfies it.
type userProvisioner interface {
	GetUserProfileByEmailCI(ctx context.Context, email string) (repository.UserProfile, error)
	RelinkAuthSubject(ctx context.Context, arg repository.RelinkAuthSubjectParams) (repository.UserProfile, error)
	UpsertUserProfile(ctx context.Context, arg repository.UpsertUserProfileParams) (repository.UserProfile, error)
}

// provisionOrRelinkUser resolves the profile on first login (D3 user remap):
//   - verified IdP email matching a LEGACY (non-Clerk) profile → rebind its
//     auth_subject to the new subject (relink; memberships/access_mask preserved);
//   - email matches but is NOT verified → refuse (anti-takeover, AC-4.3);
//   - email matches a profile already bound to a Clerk subject → refuse (M-1);
//   - no email match → provision a brand-new profile (is_site_admin=false, no
//     membership — unchanged first-login semantics, AC-4.4).
func provisionOrRelinkUser(
	ctx context.Context,
	q userProvisioner,
	claims middleware.UserClaims,
) (repository.UserProfile, error) {
	if claims.Email != "" {
		existing, err := q.GetUserProfileByEmailCI(ctx, claims.Email)
		switch {
		case err == nil:
			if !claims.EmailVerified {
				return repository.UserProfile{}, errEmailTakenUnverified
			}
			// M-1: confine relink to the legacy migration — never transfer a
			// profile already bound to another Clerk user.
			if isClerkSubject(existing.AuthSubject) {
				return repository.UserProfile{}, errEmailBoundToOtherAccount
			}
			relinked, rerr := q.RelinkAuthSubject(ctx, repository.RelinkAuthSubjectParams{
				ID:          existing.ID,
				AuthSubject: claims.Sub,
			})
			if rerr != nil {
				return repository.UserProfile{}, rerr
			}
			// Auditable security event: record old→new subject + the matched id.
			slog.Info("relinked user by verified email (legacy -> clerk)",
				"old_sub", existing.AuthSubject,
				"new_sub", claims.Sub,
				"profile_id", FromPgUUID(existing.ID).String(),
			)
			return relinked, nil
		case errors.Is(err, pgx.ErrNoRows):
			// No email match — fall through to fresh provisioning.
		default:
			return repository.UserProfile{}, err
		}
	}

	profile, err := q.UpsertUserProfile(ctx, repository.UpsertUserProfileParams{
		ID:          ToPgUUID(uuid.New()),
		AuthSubject: claims.Sub,
		Username:    claims.PreferredUsername,
		Email:       claims.Email,
		Description: pgtype.Text{Valid: false},
		IsSiteAdmin: pgtype.Bool{Bool: false, Valid: true},
	})
	if err != nil && isUniqueViolation(err) {
		// Email/username collided (case-variant or race) — refuse cleanly so the
		// caller returns 403, not a 500.
		return repository.UserProfile{}, errEmailConflict
	}
	return profile, err
}

// UserResponse represents a user profile in API responses.
type UserResponse struct {
	ID          string  `json:"id"`
	Username    string  `json:"username"`
	Email       string  `json:"email"`
	Description *string `json:"description"`
	IsSiteAdmin bool    `json:"is_site_admin"`
	CreatedAt   string  `json:"created_at"`
	UpdatedAt   string  `json:"updated_at"`
}

// CurrentUserResponse extends UserResponse with accessible orgs.
type CurrentUserResponse struct {
	ID          string       `json:"id"`
	Username    string       `json:"username"`
	Email       string       `json:"email"`
	Description *string      `json:"description"`
	IsSiteAdmin bool         `json:"is_site_admin"`
	Orgs        []OrgSummary `json:"orgs"`
}

// OrgSummary is a minimal org representation for user context.
type OrgSummary struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// UserProfileUpdateRequest is the request body for updating user profile.
type UserProfileUpdateRequest struct {
	Description *string `json:"description"`
}

// UserHandler handles user-related requests.
type UserHandler struct {
	pool *db.Pool
}

// NewUserHandler creates a new UserHandler.
func NewUserHandler(pool *db.Pool) *UserHandler {
	return &UserHandler{pool: pool}
}

// toUserResponse converts a repository.UserProfile to UserResponse.
func toUserResponse(profile repository.UserProfile) UserResponse {
	resp := UserResponse{
		ID:          FromPgUUID(profile.ID).String(),
		Username:    profile.Username,
		Email:       profile.Email,
		IsSiteAdmin: profile.IsSiteAdmin.Bool,
		CreatedAt:   profile.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:   profile.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if profile.Description.Valid {
		resp.Description = &profile.Description.String
	}
	return resp
}

// GetMe returns the current user with their accessible orgs.
// Auto-provisions user if not exists.
func (h *UserHandler) GetMe(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		slog.Error("acquire connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer conn.Release()

	queries := repository.New(conn)

	// Resolve the profile for this IdP subject. On first login this provisions
	// a fresh profile or relinks an existing user by verified email (D3).
	profile, err := queries.GetUserProfileByAuthSubject(ctx, claims.Sub)
	if err != nil {
		profile, err = provisionOrRelinkUser(ctx, queries, claims)
		if err != nil {
			switch {
			case errors.Is(err, errEmailTakenUnverified),
				errors.Is(err, errEmailBoundToOtherAccount),
				errors.Is(err, errEmailConflict):
				// Generic message — do not confirm whether the email exists
				// (avoids an account-existence/enumeration oracle).
				slog.Warn("provision refused", "sub", claims.Sub, "reason", err.Error())
				httputil.Forbidden(w, "We couldn't complete sign-in for this account. Verify your email or contact your administrator.")
				return
			default:
				slog.Error("provision user", "error", err)
				httputil.InternalError(w, "Failed to provision user")
				return
			}
		}
	}

	// Get accessible orgs
	var orgs []repository.Org
	if profile.IsSiteAdmin.Bool {
		orgs, err = queries.ListAllOrgs(ctx, repository.ListAllOrgsParams{Limit: 10000, Offset: 0})
	} else {
		orgs, err = queries.ListOrgsForUser(ctx, repository.ListOrgsForUserParams{ProfileID: profile.ID, Limit: 10000, Offset: 0})
	}
	if err != nil {
		slog.Error("list orgs for user", "error", err)
		httputil.InternalError(w, "")
		return
	}

	orgSummaries := make([]OrgSummary, len(orgs))
	for i, org := range orgs {
		orgSummaries[i] = OrgSummary{
			ID:   FromPgUUID(org.ID).String(),
			Name: org.Name,
		}
	}

	resp := CurrentUserResponse{
		ID:          FromPgUUID(profile.ID).String(),
		Username:    profile.Username,
		Email:       profile.Email,
		IsSiteAdmin: profile.IsSiteAdmin.Bool,
		Orgs:        orgSummaries,
	}
	if profile.Description.Valid {
		resp.Description = &profile.Description.String
	}

	httputil.Success(w, resp)
}

// UpdateMe updates the current user's profile.
func (h *UserHandler) UpdateMe(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	var req UserProfileUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		slog.Error("acquire connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer conn.Release()

	queries := repository.New(conn)

	// Get existing profile
	profile, err := queries.GetUserProfileByAuthSubject(ctx, claims.Sub)
	if err != nil {
		httputil.NotFound(w, "User not found")
		return
	}

	// Update profile (only description is editable by user)
	updated, err := queries.UpdateUserProfile(ctx, repository.UpdateUserProfileParams{
		ID: profile.ID,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
	})
	if err != nil {
		slog.Error("update user profile", "error", err)
		httputil.InternalError(w, "Failed to update profile")
		return
	}

	httputil.Success(w, toUserResponse(updated))
}

// ListUsersInOrg lists users with membership in the current org.
func (h *UserHandler) ListUsersInOrg(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	_, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	pagination := ParsePagination(r)

	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		slog.Error("acquire tenant connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tenantConn.Release()

	queries := repository.New(tenantConn)

	users, err := queries.ListUsersInOrg(ctx, repository.ListUsersInOrgParams{
		OrgID:  ToPgUUID(orgID),
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list users in org", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountUsersInOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		slog.Error("count users in org", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]UserResponse, len(users))
	for i, user := range users {
		items[i] = toUserResponse(user)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// GetUser gets a user profile by ID.
func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	_, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	userIDStr := chi.URLParam(r, "userID")
	userID, err := ParseUUID(userIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid user ID")
		return
	}

	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		slog.Error("acquire connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer conn.Release()

	queries := repository.New(conn)

	profile, err := queries.GetUserProfile(ctx, ToPgUUID(userID))
	if err != nil {
		httputil.NotFound(w, "User not found")
		return
	}

	httputil.Success(w, toUserResponse(profile))
}
