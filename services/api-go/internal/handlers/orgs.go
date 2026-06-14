package handlers

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// OrgResponse represents an org in API responses.
type OrgResponse struct {
	ID                string  `json:"id"`
	Name              string  `json:"name"`
	Description       *string `json:"description"`
	UseKnowledgeGraph bool    `json:"use_knowledge_graph"`
	CreatedAt         string  `json:"created_at"`
	UpdatedAt         string  `json:"updated_at"`
}

// OrgCreateRequest is the request body for creating an org.
type OrgCreateRequest struct {
	Name              string  `json:"name"`
	Description       *string `json:"description"`
	UseKnowledgeGraph *bool   `json:"use_knowledge_graph"`
}

// OrgUpdateRequest is the request body for updating an org.
type OrgUpdateRequest struct {
	Name              *string `json:"name"`
	Description       *string `json:"description"`
	UseKnowledgeGraph *bool   `json:"use_knowledge_graph"`
}

// OrgHandler handles org-related requests.
type OrgHandler struct {
	pool        *db.Pool
	brainClient *client.BrainAPIClient
}

// NewOrgHandler creates a new OrgHandler.
func NewOrgHandler(pool *db.Pool, brainClient *client.BrainAPIClient) *OrgHandler {
	return &OrgHandler{
		pool:        pool,
		brainClient: brainClient,
	}
}

// toOrgResponse converts a repository.Org to OrgResponse.
func toOrgResponse(org repository.Org) OrgResponse {
	resp := OrgResponse{
		ID:                FromPgUUID(org.ID).String(),
		Name:              org.Name,
		UseKnowledgeGraph: org.UseKnowledgeGraph.Bool,
		CreatedAt:         org.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:         org.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if org.Description.Valid {
		resp.Description = &org.Description.String
	}
	return resp
}

// ListOrgs lists orgs (site admin sees all, regular user sees only their memberships).
func (h *OrgHandler) ListOrgs(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	pagination := ParsePagination(r)

	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		slog.Error("acquire connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer conn.Release()

	queries := repository.New(conn)

	// Get user profile to check site admin status
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
	if err != nil {
		// User not provisioned yet - return empty list
		httputil.Success(w, MakePage([]OrgResponse{}, 0, pagination))
		return
	}

	var orgs []repository.Org
	var total int64

	if profile.IsSiteAdmin.Bool {
		// Site admin sees all orgs
		orgs, err = queries.ListAllOrgs(ctx, repository.ListAllOrgsParams{
			Limit:  pagination.Limit(),
			Offset: pagination.Offset(),
		})
		if err != nil {
			slog.Error("list all orgs", "error", err)
			httputil.InternalError(w, "")
			return
		}
		total, err = queries.CountAllOrgs(ctx)
		if err != nil {
			slog.Error("count all orgs", "error", err)
			httputil.InternalError(w, "")
			return
		}
	} else {
		// Regular user sees only their memberships
		orgs, err = queries.ListOrgsForUser(ctx, repository.ListOrgsForUserParams{
			ProfileID: profile.ID,
			Limit:     pagination.Limit(),
			Offset:    pagination.Offset(),
		})
		if err != nil {
			slog.Error("list orgs for user", "error", err)
			httputil.InternalError(w, "")
			return
		}
		total, err = queries.CountOrgsForUser(ctx, profile.ID)
		if err != nil {
			slog.Error("count orgs for user", "error", err)
			httputil.InternalError(w, "")
			return
		}
	}

	items := make([]OrgResponse, len(orgs))
	for i, org := range orgs {
		items[i] = toOrgResponse(org)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// CreateOrg creates a new org (site admin only).
func (h *OrgHandler) CreateOrg(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	var req OrgCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
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

	// Check if user is site admin
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
	if err != nil {
		httputil.Forbidden(w, "User not provisioned")
		return
	}
	if !profile.IsSiteAdmin.Bool {
		httputil.Forbidden(w, "Site admin required")
		return
	}

	// Get next permission number
	permNum, err := queries.GetNextOrgPermissionNumber(ctx)
	if err != nil {
		slog.Error("get next permission number", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Create org
	useKnowledgeGraph := true
	if req.UseKnowledgeGraph != nil {
		useKnowledgeGraph = *req.UseKnowledgeGraph
	}

	org, err := queries.CreateOrg(ctx, repository.CreateOrgParams{
		ID:   ToPgUUID(uuid.New()),
		Name: req.Name,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		UseKnowledgeGraph: pgtype.Bool{Bool: useKnowledgeGraph, Valid: true},
		PermissionNumber:  pgtype.Int2{Int16: int16(permNum), Valid: true},
	})
	if err != nil {
		slog.Error("create org", "error", err)
		httputil.InternalError(w, "Failed to create org")
		return
	}

	// Initialize tenant in brain-api (best effort)
	if h.brainClient != nil {
		if err := h.brainClient.InitTenant(ctx, FromPgUUID(org.ID).String()); err != nil {
			slog.Error("brain-api init tenant failed", "org_id", org.ID, "error", err)
		}
	}

	httputil.Created(w, toOrgResponse(org))
}

// GetOrg gets a single org by ID.
func (h *OrgHandler) GetOrg(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgIDStr := chi.URLParam(r, "orgID")
	orgID, err := ParseUUID(orgIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid org ID")
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

	org, err := queries.GetOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		httputil.NotFound(w, "Org not found")
		return
	}

	// Check access: site admin or member
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
	if err != nil {
		httputil.Forbidden(w, "User not provisioned")
		return
	}

	if !profile.IsSiteAdmin.Bool {
		isMember, err := queries.IsUserMemberOfOrg(ctx, repository.IsUserMemberOfOrgParams{
			ProfileID: profile.ID,
			OrgID:     org.ID,
		})
		if err != nil || !isMember {
			httputil.Forbidden(w, "Not a member")
			return
		}
	}

	httputil.Success(w, toOrgResponse(org))
}

// UpdateOrg updates an org (site admin only).
func (h *OrgHandler) UpdateOrg(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgIDStr := chi.URLParam(r, "orgID")
	orgID, err := ParseUUID(orgIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid org ID")
		return
	}

	var req OrgUpdateRequest
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

	// Check if user is site admin
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
	if err != nil {
		httputil.Forbidden(w, "User not provisioned")
		return
	}
	if !profile.IsSiteAdmin.Bool {
		httputil.Forbidden(w, "Site admin required")
		return
	}

	// Check org exists
	_, err = queries.GetOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		httputil.NotFound(w, "Org not found")
		return
	}

	// Update org
	org, err := queries.UpdateOrg(ctx, repository.UpdateOrgParams{
		ID: ToPgUUID(orgID),
		Name: pgtype.Text{
			String: derefString(req.Name),
			Valid:  req.Name != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		UseKnowledgeGraph: pgtype.Bool{
			Bool:  derefBool(req.UseKnowledgeGraph),
			Valid: req.UseKnowledgeGraph != nil,
		},
	})
	if err != nil {
		slog.Error("update org", "error", err)
		httputil.InternalError(w, "Failed to update org")
		return
	}

	httputil.Success(w, toOrgResponse(org))
}

// DeleteOrg deletes an org (site admin only).
func (h *OrgHandler) DeleteOrg(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgIDStr := chi.URLParam(r, "orgID")
	orgID, err := ParseUUID(orgIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid org ID")
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

	// Check if user is site admin
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, claims.Sub)
	if err != nil {
		httputil.Forbidden(w, "User not provisioned")
		return
	}
	if !profile.IsSiteAdmin.Bool {
		httputil.Forbidden(w, "Site admin required")
		return
	}

	// Check org exists
	_, err = queries.GetOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		httputil.NotFound(w, "Org not found")
		return
	}

	// Delete org
	if err := queries.DeleteOrg(ctx, ToPgUUID(orgID)); err != nil {
		slog.Error("delete org", "error", err)
		httputil.InternalError(w, "Failed to delete org")
		return
	}

	// Remove tenant from brain-api (best effort)
	if h.brainClient != nil {
		if err := h.brainClient.RemoveTenant(context.Background(), orgID.String()); err != nil {
			slog.Error("brain-api remove tenant failed - manual cleanup may be required",
				"org_id", orgID, "error", err)
		}
	}

	slog.Warn("site-admin deleted org", "org_id", orgID)
	httputil.NoContent(w)
}

// Helper functions
func derefString(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func derefBool(b *bool) bool {
	if b == nil {
		return false
	}
	return *b
}
