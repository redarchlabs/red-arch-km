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

// TagResponse represents a tag in API responses.
type TagResponse struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

// TagCreateRequest is the request body for creating a tag.
type TagCreateRequest struct {
	Name string `json:"name"`
}

// TagHandler handles tag-related requests.
type TagHandler struct {
	pool *db.Pool
}

// NewTagHandler creates a new TagHandler.
func NewTagHandler(pool *db.Pool) *TagHandler {
	return &TagHandler{pool: pool}
}

// toTagResponse converts a repository.Tag to TagResponse.
func toTagResponse(t repository.Tag) TagResponse {
	return TagResponse{
		ID:        FromPgUUID(t.ID).String(),
		Name:      t.Name,
		CreatedAt: t.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt: t.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
}

// ListTags lists all tags in the organization.
func (h *TagHandler) ListTags(w http.ResponseWriter, r *http.Request) {
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

	// Filtered by org_id explicitly (defense in depth) rather than relying
	// solely on RLS to scope the result set.
	tags, err := queries.ListTagsForOrg(ctx, repository.ListTagsForOrgParams{
		OrgID:  ToPgUUID(orgID),
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list tags", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountTagsForOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		slog.Error("count tags", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]TagResponse, len(tags))
	for i, t := range tags {
		items[i] = toTagResponse(t)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// CreateTag creates a new tag.
func (h *TagHandler) CreateTag(w http.ResponseWriter, r *http.Request) {
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

	var req TagCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
		return
	}

	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		slog.Error("acquire tenant connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tenantConn.Release()

	queries := repository.New(tenantConn)

	tag, err := queries.CreateTag(ctx, repository.CreateTagParams{
		ID:    ToPgUUID(uuid.New()),
		Name:  req.Name,
		OrgID: ToPgUUID(orgID),
	})
	if err != nil {
		slog.Error("create tag", "error", err)
		httputil.InternalError(w, "Failed to create tag")
		return
	}

	httputil.Created(w, toTagResponse(tag))
}

// GetTag gets a single tag by ID.
func (h *TagHandler) GetTag(w http.ResponseWriter, r *http.Request) {
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

	tagIDStr := chi.URLParam(r, "tagID")
	tagID, err := ParseUUID(tagIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid tag ID")
		return
	}

	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		slog.Error("acquire tenant connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tenantConn.Release()

	queries := repository.New(tenantConn)

	tag, err := queries.GetTag(ctx, ToPgUUID(tagID))
	if err != nil {
		httputil.NotFound(w, "Tag not found")
		return
	}

	httputil.Success(w, toTagResponse(tag))
}

// UpdateTag updates a tag.
func (h *TagHandler) UpdateTag(w http.ResponseWriter, r *http.Request) {
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

	tagIDStr := chi.URLParam(r, "tagID")
	tagID, err := ParseUUID(tagIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid tag ID")
		return
	}

	var req TagCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
		return
	}

	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		slog.Error("acquire tenant connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tenantConn.Release()

	queries := repository.New(tenantConn)

	// Check tag exists
	_, err = queries.GetTag(ctx, ToPgUUID(tagID))
	if err != nil {
		httputil.NotFound(w, "Tag not found")
		return
	}

	tag, err := queries.UpdateTag(ctx, repository.UpdateTagParams{
		ID:   ToPgUUID(tagID),
		Name: pgtype.Text{String: req.Name, Valid: true},
	})
	if err != nil {
		slog.Error("update tag", "error", err)
		httputil.InternalError(w, "Failed to update tag")
		return
	}

	httputil.Success(w, toTagResponse(tag))
}

// DeleteTag deletes a tag.
func (h *TagHandler) DeleteTag(w http.ResponseWriter, r *http.Request) {
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

	tagIDStr := chi.URLParam(r, "tagID")
	tagID, err := ParseUUID(tagIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid tag ID")
		return
	}

	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		slog.Error("acquire tenant connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tenantConn.Release()

	queries := repository.New(tenantConn)

	// Check tag exists
	_, err = queries.GetTag(ctx, ToPgUUID(tagID))
	if err != nil {
		httputil.NotFound(w, "Tag not found")
		return
	}

	if err := queries.DeleteTag(ctx, ToPgUUID(tagID)); err != nil {
		slog.Error("delete tag", "error", err)
		httputil.InternalError(w, "Failed to delete tag")
		return
	}

	httputil.NoContent(w)
}
