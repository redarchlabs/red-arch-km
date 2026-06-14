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
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// DimensionResponse represents a dimension in API responses.
type DimensionResponse struct {
	ID               string  `json:"id"`
	Name             string  `json:"name"`
	Description      *string `json:"description"`
	PermissionNumber int16   `json:"permission_number"`
	OrgID            string  `json:"org_id"`
	CreatedAt        string  `json:"created_at"`
	UpdatedAt        string  `json:"updated_at"`
}

// DimensionCreateRequest is the request body for creating a dimension.
type DimensionCreateRequest struct {
	Name        string  `json:"name"`
	Description *string `json:"description"`
}

// DimensionUpdateRequest is the request body for updating a dimension.
type DimensionUpdateRequest struct {
	Name        *string `json:"name"`
	Description *string `json:"description"`
}

// DimensionHandler handles dimension-related requests.
type DimensionHandler struct {
	pool *db.Pool
}

// NewDimensionHandler creates a new DimensionHandler.
func NewDimensionHandler(pool *db.Pool) *DimensionHandler {
	return &DimensionHandler{pool: pool}
}

// toRegionResponse converts a repository.Region to DimensionResponse.
func toRegionResponse(r repository.Region) DimensionResponse {
	resp := DimensionResponse{
		ID:               FromPgUUID(r.ID).String(),
		Name:             r.Name,
		PermissionNumber: r.PermissionNumber.Int16,
		OrgID:            FromPgUUID(r.OrgID).String(),
		CreatedAt:        r.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:        r.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if r.Description.Valid {
		resp.Description = &r.Description.String
	}
	return resp
}

// toDepartmentResponse converts a repository.Department to DimensionResponse.
func toDepartmentResponse(d repository.Department) DimensionResponse {
	resp := DimensionResponse{
		ID:               FromPgUUID(d.ID).String(),
		Name:             d.Name,
		PermissionNumber: d.PermissionNumber.Int16,
		OrgID:            FromPgUUID(d.OrgID).String(),
		CreatedAt:        d.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:        d.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if d.Description.Valid {
		resp.Description = &d.Description.String
	}
	return resp
}

// toRoleResponse converts a repository.Role to DimensionResponse.
func toRoleResponse(r repository.Role) DimensionResponse {
	resp := DimensionResponse{
		ID:               FromPgUUID(r.ID).String(),
		Name:             r.Name,
		PermissionNumber: r.PermissionNumber.Int16,
		OrgID:            FromPgUUID(r.OrgID).String(),
		CreatedAt:        r.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:        r.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if r.Description.Valid {
		resp.Description = &r.Description.String
	}
	return resp
}

// toGroupResponse converts a repository.Group to DimensionResponse.
func toGroupResponse(g repository.Group) DimensionResponse {
	resp := DimensionResponse{
		ID:               FromPgUUID(g.ID).String(),
		Name:             g.Name,
		PermissionNumber: g.PermissionNumber.Int16,
		OrgID:            FromPgUUID(g.OrgID).String(),
		CreatedAt:        g.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:        g.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if g.Description.Valid {
		resp.Description = &g.Description.String
	}
	return resp
}

// requireOrgAdminForDimension checks if the caller is an org admin.
func (h *DimensionHandler) requireOrgAdminForDimension(ctx context.Context, keycloakSub string, orgID uuid.UUID) error {
	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()

	queries := repository.New(conn)
	profile, err := queries.GetUserProfileByKeycloakSub(ctx, keycloakSub)
	if err != nil {
		return err
	}

	// Site admins are always allowed
	if profile.IsSiteAdmin.Bool {
		return nil
	}

	// Check org admin via membership
	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		return err
	}
	defer tenantConn.Release()

	tenantQueries := repository.New(tenantConn)
	membership, err := tenantQueries.GetMembershipByUserAndOrg(ctx, repository.GetMembershipByUserAndOrgParams{
		ProfileID: profile.ID,
		OrgID:     ToPgUUID(orgID),
	})
	if err != nil {
		return ErrNotOrgAdmin
	}

	if !membership.IsOrgAdmin.Bool {
		return ErrNotOrgAdmin
	}

	return nil
}

// ============================================================
// REGIONS
// ============================================================

// ListRegions lists all regions in the current org.
func (h *DimensionHandler) ListRegions(w http.ResponseWriter, r *http.Request) {
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

	regions, err := queries.ListRegions(ctx, repository.ListRegionsParams{
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list regions", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountRegions(ctx)
	if err != nil {
		slog.Error("count regions", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]DimensionResponse, len(regions))
	for i, r := range regions {
		items[i] = toRegionResponse(r)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// GetRegion gets a region by ID.
func (h *DimensionHandler) GetRegion(w http.ResponseWriter, r *http.Request) {
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

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
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

	region, err := queries.GetRegion(ctx, ToPgUUID(dimensionID))
	if err != nil {
		httputil.NotFound(w, "Region not found")
		return
	}

	httputil.Success(w, toRegionResponse(region))
}

// CreateRegion creates a new region.
func (h *DimensionHandler) CreateRegion(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	var req DimensionCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	permNum, err := queries.GetNextRegionPermissionNumber(ctx)
	if err != nil {
		slog.Error("get next permission number", "error", err)
		httputil.InternalError(w, "")
		return
	}

	region, err := queries.CreateRegion(ctx, repository.CreateRegionParams{
		ID:   ToPgUUID(uuid.New()),
		Name: req.Name,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		PermissionNumber: pgtype.Int2{Int16: int16(permNum), Valid: true},
		OrgID:            ToPgUUID(orgID),
	})
	if err != nil {
		slog.Error("create region", "error", err)
		httputil.InternalError(w, "Failed to create region")
		return
	}

	httputil.Created(w, toRegionResponse(region))
}

// UpdateRegion updates a region.
func (h *DimensionHandler) UpdateRegion(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	var req DimensionUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	region, err := queries.UpdateRegion(ctx, repository.UpdateRegionParams{
		ID: ToPgUUID(dimensionID),
		Name: pgtype.Text{
			String: derefString(req.Name),
			Valid:  req.Name != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
	})
	if err != nil {
		httputil.NotFound(w, "Region not found")
		return
	}

	httputil.Success(w, toRegionResponse(region))
}

// DeleteRegion deletes a region.
func (h *DimensionHandler) DeleteRegion(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	// Verify exists
	if _, err := queries.GetRegion(ctx, ToPgUUID(dimensionID)); err != nil {
		httputil.NotFound(w, "Region not found")
		return
	}

	if err := queries.DeleteRegion(ctx, ToPgUUID(dimensionID)); err != nil {
		slog.Error("delete region", "error", err)
		httputil.InternalError(w, "Failed to delete region")
		return
	}

	httputil.NoContent(w)
}

// ============================================================
// DEPARTMENTS
// ============================================================

// ListDepartments lists all departments in the current org.
func (h *DimensionHandler) ListDepartments(w http.ResponseWriter, r *http.Request) {
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

	departments, err := queries.ListDepartments(ctx, repository.ListDepartmentsParams{
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list departments", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountDepartments(ctx)
	if err != nil {
		slog.Error("count departments", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]DimensionResponse, len(departments))
	for i, d := range departments {
		items[i] = toDepartmentResponse(d)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// GetDepartment gets a department by ID.
func (h *DimensionHandler) GetDepartment(w http.ResponseWriter, r *http.Request) {
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

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
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

	department, err := queries.GetDepartment(ctx, ToPgUUID(dimensionID))
	if err != nil {
		httputil.NotFound(w, "Department not found")
		return
	}

	httputil.Success(w, toDepartmentResponse(department))
}

// CreateDepartment creates a new department.
func (h *DimensionHandler) CreateDepartment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	var req DimensionCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	permNum, err := queries.GetNextDepartmentPermissionNumber(ctx)
	if err != nil {
		slog.Error("get next permission number", "error", err)
		httputil.InternalError(w, "")
		return
	}

	department, err := queries.CreateDepartment(ctx, repository.CreateDepartmentParams{
		ID:   ToPgUUID(uuid.New()),
		Name: req.Name,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		PermissionNumber: pgtype.Int2{Int16: int16(permNum), Valid: true},
		OrgID:            ToPgUUID(orgID),
	})
	if err != nil {
		slog.Error("create department", "error", err)
		httputil.InternalError(w, "Failed to create department")
		return
	}

	httputil.Created(w, toDepartmentResponse(department))
}

// UpdateDepartment updates a department.
func (h *DimensionHandler) UpdateDepartment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	var req DimensionUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	department, err := queries.UpdateDepartment(ctx, repository.UpdateDepartmentParams{
		ID: ToPgUUID(dimensionID),
		Name: pgtype.Text{
			String: derefString(req.Name),
			Valid:  req.Name != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
	})
	if err != nil {
		httputil.NotFound(w, "Department not found")
		return
	}

	httputil.Success(w, toDepartmentResponse(department))
}

// DeleteDepartment deletes a department.
func (h *DimensionHandler) DeleteDepartment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	// Verify exists
	if _, err := queries.GetDepartment(ctx, ToPgUUID(dimensionID)); err != nil {
		httputil.NotFound(w, "Department not found")
		return
	}

	if err := queries.DeleteDepartment(ctx, ToPgUUID(dimensionID)); err != nil {
		slog.Error("delete department", "error", err)
		httputil.InternalError(w, "Failed to delete department")
		return
	}

	httputil.NoContent(w)
}

// ============================================================
// ROLES
// ============================================================

// ListRoles lists all roles in the current org.
func (h *DimensionHandler) ListRoles(w http.ResponseWriter, r *http.Request) {
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

	roles, err := queries.ListRoles(ctx, repository.ListRolesParams{
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list roles", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountRoles(ctx)
	if err != nil {
		slog.Error("count roles", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]DimensionResponse, len(roles))
	for i, r := range roles {
		items[i] = toRoleResponse(r)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// GetRole gets a role by ID.
func (h *DimensionHandler) GetRole(w http.ResponseWriter, r *http.Request) {
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

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
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

	role, err := queries.GetRole(ctx, ToPgUUID(dimensionID))
	if err != nil {
		httputil.NotFound(w, "Role not found")
		return
	}

	httputil.Success(w, toRoleResponse(role))
}

// CreateRole creates a new role.
func (h *DimensionHandler) CreateRole(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	var req DimensionCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	permNum, err := queries.GetNextRolePermissionNumber(ctx)
	if err != nil {
		slog.Error("get next permission number", "error", err)
		httputil.InternalError(w, "")
		return
	}

	role, err := queries.CreateRole(ctx, repository.CreateRoleParams{
		ID:   ToPgUUID(uuid.New()),
		Name: req.Name,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		PermissionNumber: pgtype.Int2{Int16: int16(permNum), Valid: true},
		OrgID:            ToPgUUID(orgID),
	})
	if err != nil {
		slog.Error("create role", "error", err)
		httputil.InternalError(w, "Failed to create role")
		return
	}

	httputil.Created(w, toRoleResponse(role))
}

// UpdateRole updates a role.
func (h *DimensionHandler) UpdateRole(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	var req DimensionUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	role, err := queries.UpdateRole(ctx, repository.UpdateRoleParams{
		ID: ToPgUUID(dimensionID),
		Name: pgtype.Text{
			String: derefString(req.Name),
			Valid:  req.Name != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
	})
	if err != nil {
		httputil.NotFound(w, "Role not found")
		return
	}

	httputil.Success(w, toRoleResponse(role))
}

// DeleteRole deletes a role.
func (h *DimensionHandler) DeleteRole(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	// Verify exists
	if _, err := queries.GetRole(ctx, ToPgUUID(dimensionID)); err != nil {
		httputil.NotFound(w, "Role not found")
		return
	}

	if err := queries.DeleteRole(ctx, ToPgUUID(dimensionID)); err != nil {
		slog.Error("delete role", "error", err)
		httputil.InternalError(w, "Failed to delete role")
		return
	}

	httputil.NoContent(w)
}

// ============================================================
// GROUPS
// ============================================================

// ListGroups lists all groups in the current org.
func (h *DimensionHandler) ListGroups(w http.ResponseWriter, r *http.Request) {
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

	groups, err := queries.ListGroups(ctx, repository.ListGroupsParams{
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list groups", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountGroups(ctx)
	if err != nil {
		slog.Error("count groups", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]DimensionResponse, len(groups))
	for i, g := range groups {
		items[i] = toGroupResponse(g)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// GetGroup gets a group by ID.
func (h *DimensionHandler) GetGroup(w http.ResponseWriter, r *http.Request) {
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

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
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

	group, err := queries.GetGroup(ctx, ToPgUUID(dimensionID))
	if err != nil {
		httputil.NotFound(w, "Group not found")
		return
	}

	httputil.Success(w, toGroupResponse(group))
}

// CreateGroup creates a new group.
func (h *DimensionHandler) CreateGroup(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	var req DimensionCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Name == "" {
		httputil.BadRequest(w, "Name is required")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	permNum, err := queries.GetNextGroupPermissionNumber(ctx)
	if err != nil {
		slog.Error("get next permission number", "error", err)
		httputil.InternalError(w, "")
		return
	}

	group, err := queries.CreateGroup(ctx, repository.CreateGroupParams{
		ID:   ToPgUUID(uuid.New()),
		Name: req.Name,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		PermissionNumber: pgtype.Int2{Int16: int16(permNum), Valid: true},
		OrgID:            ToPgUUID(orgID),
	})
	if err != nil {
		slog.Error("create group", "error", err)
		httputil.InternalError(w, "Failed to create group")
		return
	}

	httputil.Created(w, toGroupResponse(group))
}

// UpdateGroup updates a group.
func (h *DimensionHandler) UpdateGroup(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	var req DimensionUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	group, err := queries.UpdateGroup(ctx, repository.UpdateGroupParams{
		ID: ToPgUUID(dimensionID),
		Name: pgtype.Text{
			String: derefString(req.Name),
			Valid:  req.Name != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
	})
	if err != nil {
		httputil.NotFound(w, "Group not found")
		return
	}

	httputil.Success(w, toGroupResponse(group))
}

// DeleteGroup deletes a group.
func (h *DimensionHandler) DeleteGroup(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	claims, ok := middleware.GetUserClaims(ctx)
	if !ok {
		httputil.Unauthorized(w, "")
		return
	}

	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		httputil.BadRequest(w, "X-Org-ID header required")
		return
	}

	dimensionIDStr := chi.URLParam(r, "dimensionID")
	dimensionID, err := ParseUUID(dimensionIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid dimension ID")
		return
	}

	if err := h.requireOrgAdminForDimension(ctx, claims.Sub, orgID); err != nil {
		httputil.Forbidden(w, "Org admin required")
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

	// Verify exists
	if _, err := queries.GetGroup(ctx, ToPgUUID(dimensionID)); err != nil {
		httputil.NotFound(w, "Group not found")
		return
	}

	if err := queries.DeleteGroup(ctx, ToPgUUID(dimensionID)); err != nil {
		slog.Error("delete group", "error", err)
		httputil.InternalError(w, "Failed to delete group")
		return
	}

	httputil.NoContent(w)
}
