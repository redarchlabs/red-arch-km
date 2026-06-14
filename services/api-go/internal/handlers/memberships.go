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

// MembershipResponse represents a membership in API responses.
type MembershipResponse struct {
	ID          string             `json:"id"`
	ProfileID   string             `json:"profile_id"`
	OrgID       string             `json:"org_id"`
	IsOrgAdmin  bool               `json:"is_org_admin"`
	Regions     []DimensionSummary `json:"regions"`
	Departments []DimensionSummary `json:"departments"`
	Roles       []DimensionSummary `json:"roles"`
	Groups      []DimensionSummary `json:"groups"`
	CreatedAt   string             `json:"created_at"`
	UpdatedAt   string             `json:"updated_at"`
}

// DimensionSummary is a minimal dimension representation.
type DimensionSummary struct {
	ID               string `json:"id"`
	Name             string `json:"name"`
	PermissionNumber int16  `json:"permission_number"`
}

// MembershipCreateRequest is the request body for creating a membership.
type MembershipCreateRequest struct {
	ProfileID     string   `json:"profile_id"`
	IsOrgAdmin    bool     `json:"is_org_admin"`
	RegionIDs     []string `json:"region_ids"`
	DepartmentIDs []string `json:"department_ids"`
	RoleIDs       []string `json:"role_ids"`
	GroupIDs      []string `json:"group_ids"`
}

// MembershipUpdateRequest is the request body for updating a membership.
type MembershipUpdateRequest struct {
	IsOrgAdmin    *bool    `json:"is_org_admin"`
	RegionIDs     []string `json:"region_ids"`
	DepartmentIDs []string `json:"department_ids"`
	RoleIDs       []string `json:"role_ids"`
	GroupIDs      []string `json:"group_ids"`
}

// MembershipHandler handles membership-related requests.
type MembershipHandler struct {
	pool *db.Pool
}

// NewMembershipHandler creates a new MembershipHandler.
func NewMembershipHandler(pool *db.Pool) *MembershipHandler {
	return &MembershipHandler{pool: pool}
}

// toMembershipResponse converts repository types to MembershipResponse.
func toMembershipResponse(
	m repository.UserOrgMembership,
	regions []repository.Region,
	departments []repository.Department,
	roles []repository.Role,
	groups []repository.Group,
) MembershipResponse {
	resp := MembershipResponse{
		ID:         FromPgUUID(m.ID).String(),
		ProfileID:  FromPgUUID(m.ProfileID).String(),
		OrgID:      FromPgUUID(m.OrgID).String(),
		IsOrgAdmin: m.IsOrgAdmin.Bool,
		CreatedAt:  m.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:  m.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		Regions:    make([]DimensionSummary, len(regions)),
		Departments: make([]DimensionSummary, len(departments)),
		Roles:       make([]DimensionSummary, len(roles)),
		Groups:      make([]DimensionSummary, len(groups)),
	}

	for i, r := range regions {
		resp.Regions[i] = DimensionSummary{
			ID:               FromPgUUID(r.ID).String(),
			Name:             r.Name,
			PermissionNumber: r.PermissionNumber.Int16,
		}
	}
	for i, d := range departments {
		resp.Departments[i] = DimensionSummary{
			ID:               FromPgUUID(d.ID).String(),
			Name:             d.Name,
			PermissionNumber: d.PermissionNumber.Int16,
		}
	}
	for i, r := range roles {
		resp.Roles[i] = DimensionSummary{
			ID:               FromPgUUID(r.ID).String(),
			Name:             r.Name,
			PermissionNumber: r.PermissionNumber.Int16,
		}
	}
	for i, g := range groups {
		resp.Groups[i] = DimensionSummary{
			ID:               FromPgUUID(g.ID).String(),
			Name:             g.Name,
			PermissionNumber: g.PermissionNumber.Int16,
		}
	}

	return resp
}

// GetMembershipByUser returns a user's membership in the current org.
func (h *MembershipHandler) GetMembershipByUser(w http.ResponseWriter, r *http.Request) {
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

	userIDStr := chi.URLParam(r, "userID")
	userID, err := ParseUUID(userIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid user ID")
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

	// Check if caller is org admin
	if err := h.requireOrgAdmin(ctx, queries, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	membership, err := queries.GetMembershipByUserAndOrg(ctx, repository.GetMembershipByUserAndOrgParams{
		ProfileID: ToPgUUID(userID),
		OrgID:     ToPgUUID(orgID),
	})
	if err != nil {
		// No membership - return null
		httputil.Success(w, nil)
		return
	}

	resp, err := h.loadMembershipWithDimensions(ctx, queries, membership)
	if err != nil {
		slog.Error("load membership dimensions", "error", err)
		httputil.InternalError(w, "")
		return
	}

	httputil.Success(w, resp)
}

// CreateMembership creates or updates a membership.
func (h *MembershipHandler) CreateMembership(w http.ResponseWriter, r *http.Request) {
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

	var req MembershipCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	profileID, err := ParseUUID(req.ProfileID)
	if err != nil {
		httputil.BadRequest(w, "Invalid profile_id")
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

	// Check if caller is org admin
	if err := h.requireOrgAdmin(ctx, queries, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	// Upsert membership
	membership, err := queries.UpsertMembership(ctx, repository.UpsertMembershipParams{
		ID:         ToPgUUID(uuid.New()),
		ProfileID:  ToPgUUID(profileID),
		OrgID:      ToPgUUID(orgID),
		IsOrgAdmin: pgtype.Bool{Bool: req.IsOrgAdmin, Valid: true},
	})
	if err != nil {
		slog.Error("upsert membership", "error", err)
		httputil.InternalError(w, "Failed to create membership")
		return
	}

	// Assign dimensions
	if err := h.assignDimensions(ctx, queries, membership.ID, req.RegionIDs, req.DepartmentIDs, req.RoleIDs, req.GroupIDs); err != nil {
		slog.Error("assign dimensions", "error", err)
		httputil.BadRequest(w, err.Error())
		return
	}

	// Reload with dimensions
	resp, err := h.loadMembershipWithDimensions(ctx, queries, membership)
	if err != nil {
		slog.Error("load membership dimensions", "error", err)
		httputil.InternalError(w, "")
		return
	}

	httputil.Created(w, resp)
}

// UpdateMembership updates a membership.
func (h *MembershipHandler) UpdateMembership(w http.ResponseWriter, r *http.Request) {
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

	membershipIDStr := chi.URLParam(r, "membershipID")
	membershipID, err := ParseUUID(membershipIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid membership ID")
		return
	}

	var req MembershipUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
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

	// Check if caller is org admin
	if err := h.requireOrgAdmin(ctx, queries, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	// Get membership
	membership, err := queries.GetMembership(ctx, ToPgUUID(membershipID))
	if err != nil {
		httputil.NotFound(w, "Membership not found")
		return
	}

	// Update is_org_admin if provided
	if req.IsOrgAdmin != nil {
		membership, err = queries.UpdateMembership(ctx, repository.UpdateMembershipParams{
			ID: membership.ID,
			IsOrgAdmin: pgtype.Bool{
				Bool:  *req.IsOrgAdmin,
				Valid: true,
			},
		})
		if err != nil {
			slog.Error("update membership", "error", err)
			httputil.InternalError(w, "Failed to update membership")
			return
		}
	}

	// Assign dimensions if any are provided
	if req.RegionIDs != nil || req.DepartmentIDs != nil || req.RoleIDs != nil || req.GroupIDs != nil {
		if err := h.assignDimensions(ctx, queries, membership.ID, req.RegionIDs, req.DepartmentIDs, req.RoleIDs, req.GroupIDs); err != nil {
			slog.Error("assign dimensions", "error", err)
			httputil.BadRequest(w, err.Error())
			return
		}
	}

	// Reload with dimensions
	resp, err := h.loadMembershipWithDimensions(ctx, queries, membership)
	if err != nil {
		slog.Error("load membership dimensions", "error", err)
		httputil.InternalError(w, "")
		return
	}

	httputil.Success(w, resp)
}

// DeleteMembership removes a membership.
func (h *MembershipHandler) DeleteMembership(w http.ResponseWriter, r *http.Request) {
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

	membershipIDStr := chi.URLParam(r, "membershipID")
	membershipID, err := ParseUUID(membershipIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid membership ID")
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

	// Check if caller is org admin
	if err := h.requireOrgAdmin(ctx, queries, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	// Check membership exists
	_, err = queries.GetMembership(ctx, ToPgUUID(membershipID))
	if err != nil {
		httputil.NotFound(w, "Membership not found")
		return
	}

	// Delete membership (junction tables cascade)
	if err := queries.DeleteMembership(ctx, ToPgUUID(membershipID)); err != nil {
		slog.Error("delete membership", "error", err)
		httputil.InternalError(w, "Failed to delete membership")
		return
	}

	httputil.NoContent(w)
}

// ListMemberships lists all memberships in the current org.
func (h *MembershipHandler) ListMemberships(w http.ResponseWriter, r *http.Request) {
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

	pagination := ParsePagination(r)

	tenantConn, err := h.pool.WithTenant(ctx, orgID)
	if err != nil {
		slog.Error("acquire tenant connection", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tenantConn.Release()

	queries := repository.New(tenantConn)

	// Check if caller is org admin
	if err := h.requireOrgAdmin(ctx, queries, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	memberships, err := queries.ListMembershipsInOrg(ctx, repository.ListMembershipsInOrgParams{
		OrgID:  ToPgUUID(orgID),
		Limit:  pagination.Limit(),
		Offset: pagination.Offset(),
	})
	if err != nil {
		slog.Error("list memberships", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountMembershipsInOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		slog.Error("count memberships", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]MembershipResponse, len(memberships))
	for i, m := range memberships {
		resp, err := h.loadMembershipWithDimensions(ctx, queries, m)
		if err != nil {
			slog.Error("load membership dimensions", "error", err)
			httputil.InternalError(w, "")
			return
		}
		items[i] = resp
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// requireOrgAdmin checks if the caller is an org admin.
func (h *MembershipHandler) requireOrgAdmin(ctx context.Context, queries *repository.Queries, keycloakSub string) error {
	// Get user's profile first (without tenant context)
	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()

	profileQueries := repository.New(conn)
	profile, err := profileQueries.GetUserProfileByKeycloakSub(ctx, keycloakSub)
	if err != nil {
		return err
	}

	// Site admins are always allowed
	if profile.IsSiteAdmin.Bool {
		return nil
	}

	// Check org admin via membership
	orgID, _ := middleware.GetOrgID(ctx)
	membership, err := queries.GetMembershipByUserAndOrg(ctx, repository.GetMembershipByUserAndOrgParams{
		ProfileID: profile.ID,
		OrgID:     ToPgUUID(orgID),
	})
	if err != nil {
		return err
	}

	if !membership.IsOrgAdmin.Bool {
		return ErrNotOrgAdmin
	}

	return nil
}

// ErrNotOrgAdmin indicates the user is not an org admin.
var ErrNotOrgAdmin = &httpError{code: 403, message: "Org admin required"}

type httpError struct {
	code    int
	message string
}

func (e *httpError) Error() string {
	return e.message
}

// loadMembershipWithDimensions loads a membership with its dimension assignments.
func (h *MembershipHandler) loadMembershipWithDimensions(
	ctx context.Context,
	queries *repository.Queries,
	membership repository.UserOrgMembership,
) (MembershipResponse, error) {
	regions, err := queries.ListMembershipRegions(ctx, membership.ID)
	if err != nil {
		return MembershipResponse{}, err
	}

	departments, err := queries.ListMembershipDepartments(ctx, membership.ID)
	if err != nil {
		return MembershipResponse{}, err
	}

	roles, err := queries.ListMembershipRoles(ctx, membership.ID)
	if err != nil {
		return MembershipResponse{}, err
	}

	groups, err := queries.ListMembershipGroups(ctx, membership.ID)
	if err != nil {
		return MembershipResponse{}, err
	}

	return toMembershipResponse(membership, regions, departments, roles, groups), nil
}

// assignDimensions assigns dimension IDs to a membership.
func (h *MembershipHandler) assignDimensions(
	ctx context.Context,
	queries *repository.Queries,
	membershipID pgtype.UUID,
	regionIDs, departmentIDs, roleIDs, groupIDs []string,
) error {
	// Clear existing assignments
	if regionIDs != nil {
		if err := queries.ClearMembershipRegions(ctx, membershipID); err != nil {
			return err
		}
		for _, idStr := range regionIDs {
			id, err := ParseUUID(idStr)
			if err != nil {
				return &validationError{field: "region_ids", message: "invalid UUID: " + idStr}
			}
			// Verify region exists by trying to get it
			if _, err := queries.GetRegion(ctx, ToPgUUID(id)); err != nil {
				return &validationError{field: "region_ids", message: "region not found: " + idStr}
			}
			if err := queries.AddMembershipRegion(ctx, repository.AddMembershipRegionParams{
				MembershipID: membershipID,
				RegionID:     ToPgUUID(id),
			}); err != nil {
				return err
			}
		}
	}

	if departmentIDs != nil {
		if err := queries.ClearMembershipDepartments(ctx, membershipID); err != nil {
			return err
		}
		for _, idStr := range departmentIDs {
			id, err := ParseUUID(idStr)
			if err != nil {
				return &validationError{field: "department_ids", message: "invalid UUID: " + idStr}
			}
			if _, err := queries.GetDepartment(ctx, ToPgUUID(id)); err != nil {
				return &validationError{field: "department_ids", message: "department not found: " + idStr}
			}
			if err := queries.AddMembershipDepartment(ctx, repository.AddMembershipDepartmentParams{
				MembershipID: membershipID,
				DepartmentID: ToPgUUID(id),
			}); err != nil {
				return err
			}
		}
	}

	if roleIDs != nil {
		if err := queries.ClearMembershipRoles(ctx, membershipID); err != nil {
			return err
		}
		for _, idStr := range roleIDs {
			id, err := ParseUUID(idStr)
			if err != nil {
				return &validationError{field: "role_ids", message: "invalid UUID: " + idStr}
			}
			if _, err := queries.GetRole(ctx, ToPgUUID(id)); err != nil {
				return &validationError{field: "role_ids", message: "role not found: " + idStr}
			}
			if err := queries.AddMembershipRole(ctx, repository.AddMembershipRoleParams{
				MembershipID: membershipID,
				RoleID:       ToPgUUID(id),
			}); err != nil {
				return err
			}
		}
	}

	if groupIDs != nil {
		if err := queries.ClearMembershipGroups(ctx, membershipID); err != nil {
			return err
		}
		for _, idStr := range groupIDs {
			id, err := ParseUUID(idStr)
			if err != nil {
				return &validationError{field: "group_ids", message: "invalid UUID: " + idStr}
			}
			if _, err := queries.GetGroup(ctx, ToPgUUID(id)); err != nil {
				return &validationError{field: "group_ids", message: "group not found: " + idStr}
			}
			if err := queries.AddMembershipGroup(ctx, repository.AddMembershipGroupParams{
				MembershipID: membershipID,
				GroupID:      ToPgUUID(id),
			}); err != nil {
				return err
			}
		}
	}

	return nil
}

type validationError struct {
	field   string
	message string
}

func (e *validationError) Error() string {
	return e.field + ": " + e.message
}
