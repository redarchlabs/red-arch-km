package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

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

	// Try to get existing profile, or auto-provision
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
	if err != nil {
		// Auto-provision user on first login
		profile, err = queries.UpsertUserProfile(ctx, repository.UpsertUserProfileParams{
			ID:          ToPgUUID(uuid.New()),
			KeycloakSub: claims.Sub,
			Username:    claims.PreferredUsername,
			Email:       claims.Email,
			Description: pgtype.Text{Valid: false},
			IsSiteAdmin: pgtype.Bool{Bool: false, Valid: true},
		})
		if err != nil {
			slog.Error("upsert user profile", "error", err)
			httputil.InternalError(w, "Failed to provision user")
			return
		}
		slog.Info("auto-provisioned user", "sub", claims.Sub, "username", claims.PreferredUsername)
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
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
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
