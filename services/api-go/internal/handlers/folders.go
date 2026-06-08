package handlers

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/services"
)

// FolderResponse represents a folder in API responses.
type FolderResponse struct {
	ID                           string                   `json:"id"`
	Name                         string                   `json:"name"`
	Description                  *string                  `json:"description"`
	Order                        int32                    `json:"order"`
	DotPath                      string                   `json:"dot_path"`
	ViewPermissionMasks          []int64                  `json:"view_permission_masks"`
	ContributorPermissionMasks   []int64                  `json:"contributor_permission_masks"`
	ViewerPermissionsConfig      []map[string]interface{} `json:"viewer_permissions_config"`
	ContributorPermissionsConfig []map[string]interface{} `json:"contributor_permissions_config"`
	ParentID                     *string                  `json:"parent_id"`
	CreatedAt                    string                   `json:"created_at"`
	UpdatedAt                    string                   `json:"updated_at"`
}

// FolderCreateRequest is the request body for creating a folder.
type FolderCreateRequest struct {
	Name                         string                   `json:"name"`
	Description                  *string                  `json:"description"`
	ParentID                     *string                  `json:"parent_id"`
	ViewerPermissionsConfig      []map[string]interface{} `json:"viewer_permissions_config"`
	ContributorPermissionsConfig []map[string]interface{} `json:"contributor_permissions_config"`
}

// FolderUpdateRequest is the request body for updating a folder.
type FolderUpdateRequest struct {
	Name                         *string                  `json:"name"`
	Description                  *string                  `json:"description"`
	ParentID                     *string                  `json:"parent_id"`
	ClearParent                  bool                     `json:"clear_parent"`
	ViewerPermissionsConfig      []map[string]interface{} `json:"viewer_permissions_config"`
	ContributorPermissionsConfig []map[string]interface{} `json:"contributor_permissions_config"`
}

// FolderReorderRequest is the request body for reordering folders.
type FolderReorderRequest struct {
	FolderID string `json:"folder_id"`
	Order    int32  `json:"order"`
}

// FolderHandler handles folder-related requests.
type FolderHandler struct {
	pool *db.Pool
}

// NewFolderHandler creates a new FolderHandler.
func NewFolderHandler(pool *db.Pool) *FolderHandler {
	return &FolderHandler{pool: pool}
}

// toFolderResponse converts a repository.Folder to FolderResponse.
func toFolderResponse(f repository.Folder) FolderResponse {
	resp := FolderResponse{
		ID:                         FromPgUUID(f.ID).String(),
		Name:                       f.Name,
		Order:                      f.Order.Int32,
		DotPath:                    f.DotPath.String,
		ViewPermissionMasks:        f.ViewPermissionMasks,
		ContributorPermissionMasks: f.ContributorPermissionMasks,
		CreatedAt:                  f.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:                  f.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}

	if f.Description.Valid {
		resp.Description = &f.Description.String
	}
	if f.ParentID.Valid {
		parentID := FromPgUUID(f.ParentID).String()
		resp.ParentID = &parentID
	}

	// Parse JSON configs
	if len(f.ViewerPermissionsConfig) > 0 {
		var config []map[string]interface{}
		if err := json.Unmarshal(f.ViewerPermissionsConfig, &config); err == nil {
			resp.ViewerPermissionsConfig = config
		}
	}
	if len(f.ContributorPermissionsConfig) > 0 {
		var config []map[string]interface{}
		if err := json.Unmarshal(f.ContributorPermissionsConfig, &config); err == nil {
			resp.ContributorPermissionsConfig = config
		}
	}

	return resp
}

// ListFolders lists folders visible to the current user via permission masks.
func (h *FolderHandler) ListFolders(w http.ResponseWriter, r *http.Request) {
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

	// Get all folders for org
	folders, err := queries.ListFolders(ctx, repository.ListFoldersParams{
		Limit:  int32(10000), // Get all for filtering
		Offset: 0,
	})
	if err != nil {
		slog.Error("list folders", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Check if user is org admin
	isAdmin, userMasks, err := h.getUserMasksForOrg(ctx, queries, claims.Sub, orgID)
	if err != nil {
		slog.Error("get user masks", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Filter folders based on permissions
	var visibleFolders []repository.Folder
	if isAdmin {
		visibleFolders = folders
	} else {
		visibleFolders = services.FilterVisibleFolders(folders, userMasks)
	}

	// Apply pagination
	total := int64(len(visibleFolders))
	offset := pagination.Offset()
	limit := pagination.Limit()
	end := offset + limit
	if int(end) > len(visibleFolders) {
		end = int32(len(visibleFolders))
	}
	if int(offset) >= len(visibleFolders) {
		visibleFolders = nil
	} else {
		visibleFolders = visibleFolders[offset:end]
	}

	items := make([]FolderResponse, len(visibleFolders))
	for i, f := range visibleFolders {
		items[i] = toFolderResponse(f)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// CreateFolder creates a new folder.
func (h *FolderHandler) CreateFolder(w http.ResponseWriter, r *http.Request) {
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

	var req FolderCreateRequest
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

	// Check if caller is org admin
	if err := h.requireOrgAdmin(ctx, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	// Build dot_path
	dotPath := req.Name
	var parentID pgtype.UUID
	if req.ParentID != nil && *req.ParentID != "" {
		pid, err := ParseUUID(*req.ParentID)
		if err != nil {
			httputil.BadRequest(w, "Invalid parent_id")
			return
		}
		parentID = ToPgUUID(pid)
		parent, err := queries.GetFolder(ctx, parentID)
		if err != nil {
			httputil.BadRequest(w, "Parent folder not found")
			return
		}
		if parent.DotPath.Valid && parent.DotPath.String != "" {
			dotPath = parent.DotPath.String + "." + req.Name
		}
	}

	// Calculate permission masks from config
	permSvc := services.NewPermissionService(queries)
	viewerConfig := parsePermissionConfig(req.ViewerPermissionsConfig)
	contributorConfig := parsePermissionConfig(req.ContributorPermissionsConfig)

	viewMasks, err := permSvc.PermissionConfigToMasks(ctx, orgID, viewerConfig)
	if err != nil {
		slog.Error("calculate view masks", "error", err)
		httputil.InternalError(w, "")
		return
	}
	contribMasks, err := permSvc.PermissionConfigToMasks(ctx, orgID, contributorConfig)
	if err != nil {
		slog.Error("calculate contributor masks", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Get next order
	order, err := queries.GetNextFolderOrder(ctx, repository.GetNextFolderOrderParams{
		OrgID:    ToPgUUID(orgID),
		ParentID: parentID,
	})
	if err != nil {
		slog.Error("get next folder order", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Serialize permission configs
	viewerConfigJSON, _ := json.Marshal(req.ViewerPermissionsConfig)
	contributorConfigJSON, _ := json.Marshal(req.ContributorPermissionsConfig)

	folder, err := queries.CreateFolder(ctx, repository.CreateFolderParams{
		ID:   ToPgUUID(uuid.New()),
		Name: req.Name,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		Order:                        pgtype.Int4{Int32: int32(order), Valid: true},
		DotPath:                      pgtype.Text{String: dotPath, Valid: true},
		ViewPermissionMasks:          viewMasks,
		ContributorPermissionMasks:   contribMasks,
		ViewerPermissionsConfig:      viewerConfigJSON,
		ContributorPermissionsConfig: contributorConfigJSON,
		OrgID:                        ToPgUUID(orgID),
		ParentID:                     parentID,
	})
	if err != nil {
		slog.Error("create folder", "error", err)
		httputil.InternalError(w, "Failed to create folder")
		return
	}

	slog.Info("created folder", "folder_id", folder.ID, "org_id", orgID)
	httputil.Created(w, toFolderResponse(folder))
}

// GetFolder gets a single folder by ID.
func (h *FolderHandler) GetFolder(w http.ResponseWriter, r *http.Request) {
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

	folderIDStr := chi.URLParam(r, "folderID")
	folderID, err := ParseUUID(folderIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid folder ID")
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

	folder, err := queries.GetFolder(ctx, ToPgUUID(folderID))
	if err != nil {
		httputil.NotFound(w, "Folder not found")
		return
	}

	httputil.Success(w, toFolderResponse(folder))
}

// UpdateFolder updates a folder.
func (h *FolderHandler) UpdateFolder(w http.ResponseWriter, r *http.Request) {
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

	folderIDStr := chi.URLParam(r, "folderID")
	folderID, err := ParseUUID(folderIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid folder ID")
		return
	}

	var req FolderUpdateRequest
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
	if err := h.requireOrgAdmin(ctx, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	// Get existing folder
	folder, err := queries.GetFolder(ctx, ToPgUUID(folderID))
	if err != nil {
		httputil.NotFound(w, "Folder not found")
		return
	}

	// Handle parent change
	var newParentID pgtype.UUID
	var newDotPath pgtype.Text
	var clearParent pgtype.Bool

	if req.ParentID != nil {
		pid, err := ParseUUID(*req.ParentID)
		if err != nil {
			httputil.BadRequest(w, "Invalid parent_id")
			return
		}
		newParentID = ToPgUUID(pid)

		// Check for cycle
		if err := h.checkForCycle(ctx, queries, folder, pid); err != nil {
			httputil.BadRequest(w, err.Error())
			return
		}

		// Get new parent to build dot_path
		parent, err := queries.GetFolder(ctx, newParentID)
		if err != nil {
			httputil.BadRequest(w, "Parent folder not found")
			return
		}

		name := folder.Name
		if req.Name != nil {
			name = *req.Name
		}
		if parent.DotPath.Valid && parent.DotPath.String != "" {
			newDotPath = pgtype.Text{String: parent.DotPath.String + "." + name, Valid: true}
		} else {
			newDotPath = pgtype.Text{String: name, Valid: true}
		}
	} else if req.ClearParent {
		clearParent = pgtype.Bool{Bool: true, Valid: true}
		name := folder.Name
		if req.Name != nil {
			name = *req.Name
		}
		newDotPath = pgtype.Text{String: name, Valid: true}
	} else if req.Name != nil {
		// Name change only, rebuild dot_path
		oldDotPath := folder.DotPath.String
		parts := strings.Split(oldDotPath, ".")
		if len(parts) > 1 {
			parts[len(parts)-1] = *req.Name
			newDotPath = pgtype.Text{String: strings.Join(parts, "."), Valid: true}
		} else {
			newDotPath = pgtype.Text{String: *req.Name, Valid: true}
		}
	}

	// Calculate permission masks from config if provided
	var viewMasks, contribMasks []int64
	if req.ViewerPermissionsConfig != nil {
		permSvc := services.NewPermissionService(queries)
		viewerConfig := parsePermissionConfig(req.ViewerPermissionsConfig)
		viewMasks, _ = permSvc.PermissionConfigToMasks(ctx, orgID, viewerConfig)
	}
	if req.ContributorPermissionsConfig != nil {
		permSvc := services.NewPermissionService(queries)
		contributorConfig := parsePermissionConfig(req.ContributorPermissionsConfig)
		contribMasks, _ = permSvc.PermissionConfigToMasks(ctx, orgID, contributorConfig)
	}

	// Serialize permission configs
	var viewerConfigJSON, contributorConfigJSON []byte
	if req.ViewerPermissionsConfig != nil {
		viewerConfigJSON, _ = json.Marshal(req.ViewerPermissionsConfig)
	}
	if req.ContributorPermissionsConfig != nil {
		contributorConfigJSON, _ = json.Marshal(req.ContributorPermissionsConfig)
	}

	updated, err := queries.UpdateFolder(ctx, repository.UpdateFolderParams{
		ID: ToPgUUID(folderID),
		Name: pgtype.Text{
			String: derefString(req.Name),
			Valid:  req.Name != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		DotPath:                      newDotPath,
		ViewPermissionMasks:          viewMasks,
		ContributorPermissionMasks:   contribMasks,
		ViewerPermissionsConfig:      viewerConfigJSON,
		ContributorPermissionsConfig: contributorConfigJSON,
		ParentID:                     newParentID,
		ClearParent:                  clearParent,
	})
	if err != nil {
		slog.Error("update folder", "error", err)
		httputil.InternalError(w, "Failed to update folder")
		return
	}

	// Update descendant dot_paths if name/parent changed
	if newDotPath.Valid {
		oldPrefix := folder.DotPath.String
		newPrefix := newDotPath.String
		if oldPrefix != newPrefix {
			if err := queries.UpdateFolderDotPath(ctx, repository.UpdateFolderDotPathParams{
				NewPrefix:    newPrefix,
				OldPrefixLen: int32(len(oldPrefix)),
				OldPrefix:    pgtype.Text{String: oldPrefix, Valid: true},
			}); err != nil {
				slog.Error("update descendant dot_paths", "error", err)
			}
		}
	}

	slog.Info("updated folder", "folder_id", folderID, "org_id", orgID)
	httputil.Success(w, toFolderResponse(updated))
}

// DeleteFolder deletes a folder.
func (h *FolderHandler) DeleteFolder(w http.ResponseWriter, r *http.Request) {
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

	folderIDStr := chi.URLParam(r, "folderID")
	folderID, err := ParseUUID(folderIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid folder ID")
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
	if err := h.requireOrgAdmin(ctx, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	// Check folder exists
	folder, err := queries.GetFolder(ctx, ToPgUUID(folderID))
	if err != nil {
		httputil.NotFound(w, "Folder not found")
		return
	}

	// Check for children
	count, err := queries.CountFolderDescendants(ctx, folder.ID)
	if err != nil {
		slog.Error("count folder descendants", "error", err)
		httputil.InternalError(w, "")
		return
	}
	if count > 1 {
		httputil.Error(w, http.StatusConflict, "Cannot delete folder with children")
		return
	}

	// Delete folder
	if err := queries.DeleteFolder(ctx, folder.ID); err != nil {
		slog.Error("delete folder", "error", err)
		httputil.InternalError(w, "Failed to delete folder")
		return
	}

	slog.Info("deleted folder", "folder_id", folderID, "org_id", orgID)
	httputil.NoContent(w)
}

// ReorderFolders handles drag-and-drop reordering of folders.
func (h *FolderHandler) ReorderFolders(w http.ResponseWriter, r *http.Request) {
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

	var reorders []FolderReorderRequest
	if err := json.NewDecoder(r.Body).Decode(&reorders); err != nil {
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
	if err := h.requireOrgAdmin(ctx, claims.Sub); err != nil {
		httputil.Forbidden(w, "Org admin required")
		return
	}

	for _, reorder := range reorders {
		folderID, err := ParseUUID(reorder.FolderID)
		if err != nil {
			httputil.BadRequest(w, "Invalid folder_id: "+reorder.FolderID)
			return
		}
		if err := queries.ReorderFolders(ctx, repository.ReorderFoldersParams{
			ID:    ToPgUUID(folderID),
			Order: pgtype.Int4{Int32: reorder.Order, Valid: true},
		}); err != nil {
			slog.Error("reorder folder", "folder_id", folderID, "error", err)
			httputil.InternalError(w, "Failed to reorder folders")
			return
		}
	}

	httputil.Success(w, map[string]string{"status": "ok"})
}

// getUserMasksForOrg returns whether user is org admin and their permission masks.
func (h *FolderHandler) getUserMasksForOrg(
	ctx context.Context,
	queries *repository.Queries,
	keycloakSub string,
	orgID uuid.UUID,
) (bool, []int64, error) {
	// Get user profile
	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		return false, nil, err
	}
	defer conn.Release()

	profileQueries := repository.New(conn)
	profile, err := profileQueries.GetUserProfileByKeycloakSub(ctx, keycloakSub)
	if err != nil {
		return false, nil, err
	}

	// Site admins see all
	if profile.IsSiteAdmin.Bool {
		return true, nil, nil
	}

	// Get membership
	membership, err := queries.GetMembershipByUserAndOrg(ctx, repository.GetMembershipByUserAndOrgParams{
		ProfileID: profile.ID,
		OrgID:     ToPgUUID(orgID),
	})
	if err != nil {
		return false, nil, nil // No membership = no access
	}

	if membership.IsOrgAdmin.Bool {
		return true, nil, nil
	}

	// Get org permission number
	org, err := queries.GetOrg(ctx, ToPgUUID(orgID))
	if err != nil {
		return false, nil, err
	}

	// Get dimension assignments
	regions, err := queries.ListMembershipRegions(ctx, membership.ID)
	if err != nil {
		return false, nil, err
	}
	departments, err := queries.ListMembershipDepartments(ctx, membership.ID)
	if err != nil {
		return false, nil, err
	}
	roles, err := queries.ListMembershipRoles(ctx, membership.ID)
	if err != nil {
		return false, nil, err
	}
	groups, err := queries.ListMembershipGroups(ctx, membership.ID)
	if err != nil {
		return false, nil, err
	}

	masks := services.CalculateUserMasksFromMembership(
		org.PermissionNumber.Int16,
		regions, departments, roles, groups,
	)

	return false, masks, nil
}

// requireOrgAdmin checks if the caller is an org admin.
func (h *FolderHandler) requireOrgAdmin(ctx context.Context, keycloakSub string) error {
	orgID, ok := middleware.GetOrgID(ctx)
	if !ok {
		return ErrNotOrgAdmin
	}

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

	if profile.IsSiteAdmin.Bool {
		return nil
	}

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

// checkForCycle checks if moving a folder would create a cycle.
func (h *FolderHandler) checkForCycle(
	ctx context.Context,
	queries *repository.Queries,
	folder repository.Folder,
	newParentID uuid.UUID,
) error {
	if FromPgUUID(folder.ID) == newParentID {
		return &cycleError{message: "Cannot move a folder under itself"}
	}

	// Check if new parent is a descendant of the folder
	descendants, err := queries.GetFolderDescendants(ctx, folder.ID)
	if err != nil {
		return err
	}

	for _, d := range descendants {
		if FromPgUUID(d.ID) == newParentID {
			return &cycleError{message: "Cannot move a folder under one of its descendants"}
		}
	}

	return nil
}

type cycleError struct {
	message string
}

func (e *cycleError) Error() string {
	return e.message
}

// parsePermissionConfig converts generic map config to typed entries.
func parsePermissionConfig(config []map[string]interface{}) []services.PermissionConfigEntry {
	if config == nil {
		return nil
	}
	entries := make([]services.PermissionConfigEntry, len(config))
	for i, c := range config {
		if region, ok := c["region"].(string); ok {
			entries[i].Region = region
		}
		if dept, ok := c["department"].(string); ok {
			entries[i].Department = dept
		}
		if role, ok := c["role"].(string); ok {
			entries[i].Role = role
		}
		if group, ok := c["group"].(string); ok {
			entries[i].Group = group
		}
	}
	return entries
}
