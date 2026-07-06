package handlers

import (
	"context"
	"encoding/json"
	"fmt"
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
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/services"
)

// DocumentResponse represents a document in API responses.
type DocumentResponse struct {
	ID                string                 `json:"id"`
	Title             string                 `json:"title"`
	Description       *string                `json:"description"`
	Text              *string                `json:"text"`
	DocumentKey       string                 `json:"document_key"`
	DocumentURL       *string                `json:"document_url"`
	ProcessingStatus  string                 `json:"processing_status"`
	ProcessingDetails map[string]interface{} `json:"processing_details"`
	Metadata          map[string]interface{} `json:"metadata"`
	UseKnowledgeGraph *bool                  `json:"use_knowledge_graph"`
	FolderID          *string                `json:"folder_id"`
	UploadedByID      *string                `json:"uploaded_by_id"`
	Tags              []TagResponse          `json:"tags"`
	CreatedAt         string                 `json:"created_at"`
	UpdatedAt         string                 `json:"updated_at"`
}

// DocumentCreateRequest is the request body for creating a document.
type DocumentCreateRequest struct {
	Title             string                 `json:"title"`
	Description       *string                `json:"description"`
	Text              *string                `json:"text"`
	DocumentURL       *string                `json:"document_url"`
	FolderID          *string                `json:"folder_id"`
	UseKnowledgeGraph *bool                  `json:"use_knowledge_graph"`
	Metadata          map[string]interface{} `json:"metadata"`
	TagIDs            []string               `json:"tag_ids"`
}

// DocumentUpdateRequest is the request body for updating a document.
type DocumentUpdateRequest struct {
	Title             *string                `json:"title"`
	Description       *string                `json:"description"`
	FolderID          *string                `json:"folder_id"`
	ClearFolder       bool                   `json:"clear_folder"`
	UseKnowledgeGraph *bool                  `json:"use_knowledge_graph"`
	Metadata          map[string]interface{} `json:"metadata"`
	TagIDs            []string               `json:"tag_ids"`
}

// DocumentHandler handles document-related requests.
type DocumentHandler struct {
	pool        *db.Pool
	brainClient *client.BrainAPIClient
}

// NewDocumentHandler creates a new DocumentHandler.
func NewDocumentHandler(pool *db.Pool, brainClient *client.BrainAPIClient) *DocumentHandler {
	return &DocumentHandler{
		pool:        pool,
		brainClient: brainClient,
	}
}

// toDocumentResponse converts a repository.Document to DocumentResponse.
func toDocumentResponse(d repository.Document, tags []repository.Tag) DocumentResponse {
	resp := DocumentResponse{
		ID:               FromPgUUID(d.ID).String(),
		Title:            d.Title,
		DocumentKey:      d.DocumentKey,
		ProcessingStatus: d.ProcessingStatus.String,
		CreatedAt:        d.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		UpdatedAt:        d.UpdatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
		Tags:             make([]TagResponse, len(tags)),
	}

	if d.Description.Valid {
		resp.Description = &d.Description.String
	}
	if d.Text.Valid {
		resp.Text = &d.Text.String
	}
	if d.DocumentUrl.Valid {
		resp.DocumentURL = &d.DocumentUrl.String
	}
	if d.UseKnowledgeGraph.Valid {
		resp.UseKnowledgeGraph = &d.UseKnowledgeGraph.Bool
	}
	if d.FolderID.Valid {
		folderID := FromPgUUID(d.FolderID).String()
		resp.FolderID = &folderID
	}
	if d.UploadedByID.Valid {
		uploadedByID := FromPgUUID(d.UploadedByID).String()
		resp.UploadedByID = &uploadedByID
	}

	// Parse JSON fields
	if len(d.ProcessingDetails) > 0 {
		var details map[string]interface{}
		if err := json.Unmarshal(d.ProcessingDetails, &details); err == nil {
			resp.ProcessingDetails = details
		}
	}
	if len(d.Metadata) > 0 {
		var metadata map[string]interface{}
		if err := json.Unmarshal(d.Metadata, &metadata); err == nil {
			resp.Metadata = metadata
		}
	}

	for i, t := range tags {
		resp.Tags[i] = toTagResponse(t)
	}

	return resp
}

// ListDocuments lists documents the user can view through folder permissions.
func (h *DocumentHandler) ListDocuments(w http.ResponseWriter, r *http.Request) {
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

	// Get visible folders based on permissions. Filtered by org_id explicitly
	// (defense in depth) rather than relying solely on RLS.
	folders, err := queries.ListFoldersForOrg(ctx, repository.ListFoldersForOrgParams{
		OrgID:  ToPgUUID(orgID),
		Limit:  int32(10000),
		Offset: 0,
	})
	if err != nil {
		slog.Error("list folders", "error", err)
		httputil.InternalError(w, "")
		return
	}

	isAdmin, userMasks, err := h.getUserMasksForOrg(ctx, queries, claims.Sub, orgID)
	if err != nil {
		slog.Error("get user masks", "error", err)
		httputil.InternalError(w, "")
		return
	}

	var visibleFolderIDs []uuid.UUID
	if isAdmin {
		for _, f := range folders {
			visibleFolderIDs = append(visibleFolderIDs, FromPgUUID(f.ID))
		}
	} else {
		visibleFolders := services.FilterVisibleFolders(folders, userMasks)
		for _, f := range visibleFolders {
			visibleFolderIDs = append(visibleFolderIDs, FromPgUUID(f.ID))
		}
	}

	// Convert to pgtype.UUID array
	folderIDArray := make([]pgtype.UUID, len(visibleFolderIDs))
	for i, id := range visibleFolderIDs {
		folderIDArray[i] = ToPgUUID(id)
	}

	// Optional ?folder_id=<uuid> scopes the list to a single folder's contents
	// (folder-browse view). The caller must be able to see the folder. When
	// scoped, unfiled docs are excluded (includeUnfiled=false).
	includeUnfiled := isAdmin
	if raw := r.URL.Query().Get("folder_id"); raw != "" {
		fid, err := uuid.Parse(raw)
		if err != nil {
			httputil.BadRequest(w, "Invalid folder_id")
			return
		}
		visible := false
		for _, id := range visibleFolderIDs {
			if id == fid {
				visible = true
				break
			}
		}
		if !visible {
			httputil.NotFound(w, "Folder not found or not visible")
			return
		}
		folderIDArray = []pgtype.UUID{ToPgUUID(fid)}
		includeUnfiled = false
	}

	// List documents in visible folders (including null folder_id documents for admins)
	documents, err := queries.ListDocumentsForFolders(ctx, repository.ListDocumentsForFoldersParams{
		OrgID:   ToPgUUID(orgID),
		Column2: folderIDArray,
		Column3: includeUnfiled, // Include null folder_id docs only for admins (never in folder-scoped view)
		Limit:   pagination.Limit(),
		Offset:  pagination.Offset(),
	})
	if err != nil {
		slog.Error("list documents", "error", err)
		httputil.InternalError(w, "")
		return
	}

	total, err := queries.CountDocumentsForFolders(ctx, repository.CountDocumentsForFoldersParams{
		OrgID:   ToPgUUID(orgID),
		Column2: folderIDArray,
		Column3: includeUnfiled,
	})
	if err != nil {
		slog.Error("count documents", "error", err)
		httputil.InternalError(w, "")
		return
	}

	items := make([]DocumentResponse, len(documents))
	for i, d := range documents {
		tags, _ := queries.ListTagsForDocument(ctx, d.ID)
		items[i] = toDocumentResponse(d, tags)
	}

	httputil.Success(w, MakePage(items, total, pagination))
}

// CreateDocument creates a new document and dispatches ingestion.
func (h *DocumentHandler) CreateDocument(w http.ResponseWriter, r *http.Request) {
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

	var req DocumentCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid JSON")
		return
	}

	if req.Title == "" {
		httputil.BadRequest(w, "Title is required")
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

	// Get user profile for uploaded_by_id
	profile, err := h.getUserProfile(ctx, claims.Sub)
	if err != nil {
		slog.Error("get user profile", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Validate folder_id if provided
	var folderID pgtype.UUID
	var accessKeys []int64
	var tagNames []string

	if req.FolderID != nil && *req.FolderID != "" {
		fid, err := ParseUUID(*req.FolderID)
		if err != nil {
			httputil.BadRequest(w, "Invalid folder_id")
			return
		}
		folder, err := queries.GetFolder(ctx, ToPgUUID(fid))
		if err != nil {
			httputil.BadRequest(w, "folder_id does not exist in this organization")
			return
		}
		folderID = ToPgUUID(fid)
		accessKeys = folder.ViewPermissionMasks
		tagNames = append(tagNames, fmt.Sprintf("folder:%s", FromPgUUID(folder.ID).String()))
	}

	// Generate document key
	documentKey := uuid.New().String()

	// Serialize metadata
	var metadataJSON []byte
	if req.Metadata != nil {
		metadataJSON, _ = json.Marshal(req.Metadata)
	}

	// Create document
	doc, err := queries.CreateDocument(ctx, repository.CreateDocumentParams{
		ID:    ToPgUUID(uuid.New()),
		Title: req.Title,
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		Text: pgtype.Text{
			String: derefString(req.Text),
			Valid:  req.Text != nil,
		},
		DocumentKey: documentKey,
		DocumentUrl: pgtype.Text{
			String: derefString(req.DocumentURL),
			Valid:  req.DocumentURL != nil,
		},
		ProcessingStatus:  pgtype.Text{String: "PENDING", Valid: true},
		ProcessingDetails: nil,
		Metadata:          metadataJSON,
		UseKnowledgeGraph: pgtype.Bool{
			Bool:  req.UseKnowledgeGraph == nil || *req.UseKnowledgeGraph,
			Valid: true,
		},
		OrgID:        ToPgUUID(orgID),
		FolderID:     folderID,
		UploadedByID: profile.ID,
	})
	if err != nil {
		slog.Error("create document", "error", err)
		httputil.InternalError(w, "Failed to create document")
		return
	}

	// Assign tags
	for _, tagIDStr := range req.TagIDs {
		tagID, err := ParseUUID(tagIDStr)
		if err != nil {
			continue
		}
		if err := queries.AddDocumentTag(ctx, repository.AddDocumentTagParams{
			DocumentID: doc.ID,
			TagID:      ToPgUUID(tagID),
		}); err != nil {
			slog.Warn("add document tag", "tag_id", tagIDStr, "error", err)
		}
	}

	// Dispatch ingestion if text is provided
	if req.Text != nil && *req.Text != "" {
		if h.brainClient != nil {
			useKG := true
			if req.UseKnowledgeGraph != nil {
				useKG = *req.UseKnowledgeGraph
			}
			if err := h.brainClient.IngestDocument(ctx, client.IngestRequest{
				DocumentID:        FromPgUUID(doc.ID).String(),
				TenantID:          orgID.String(),
				DocumentKey:       documentKey,
				Title:             req.Title,
				Text:              *req.Text,
				Tags:              tagNames,
				AccessKeys:        accessKeys,
				UseKnowledgeGraph: useKG,
				Metadata:          req.Metadata,
			}); err != nil {
				slog.Error("dispatch ingestion", "document_id", doc.ID, "error", err)
				// Best effort - don't fail the create
			} else {
				slog.Info("document queued for ingestion", "document_id", doc.ID)
			}
		}
	} else {
		slog.Info("document created without text; skipping ingestion", "document_id", doc.ID)
	}

	tags, _ := queries.ListTagsForDocument(ctx, doc.ID)
	httputil.Created(w, toDocumentResponse(doc, tags))
}

// GetDocument gets a single document by ID.
func (h *DocumentHandler) GetDocument(w http.ResponseWriter, r *http.Request) {
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

	documentIDStr := chi.URLParam(r, "documentID")
	documentID, err := ParseUUID(documentIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid document ID")
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

	doc, err := queries.GetDocument(ctx, ToPgUUID(documentID))
	if err != nil {
		httputil.NotFound(w, "Document not found")
		return
	}

	tags, _ := queries.ListTagsForDocument(ctx, doc.ID)
	httputil.Success(w, toDocumentResponse(doc, tags))
}

// UpdateDocument updates a document.
func (h *DocumentHandler) UpdateDocument(w http.ResponseWriter, r *http.Request) {
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

	documentIDStr := chi.URLParam(r, "documentID")
	documentID, err := ParseUUID(documentIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid document ID")
		return
	}

	var req DocumentUpdateRequest
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

	// Check document exists
	_, err = queries.GetDocument(ctx, ToPgUUID(documentID))
	if err != nil {
		httputil.NotFound(w, "Document not found")
		return
	}

	// Build update params
	var folderID pgtype.UUID
	var clearFolder pgtype.Bool

	if req.FolderID != nil {
		fid, err := ParseUUID(*req.FolderID)
		if err != nil {
			httputil.BadRequest(w, "Invalid folder_id")
			return
		}
		folderID = ToPgUUID(fid)
	} else if req.ClearFolder {
		clearFolder = pgtype.Bool{Bool: true, Valid: true}
	}

	var metadataJSON []byte
	if req.Metadata != nil {
		metadataJSON, _ = json.Marshal(req.Metadata)
	}

	doc, err := queries.UpdateDocument(ctx, repository.UpdateDocumentParams{
		ID: ToPgUUID(documentID),
		Title: pgtype.Text{
			String: derefString(req.Title),
			Valid:  req.Title != nil,
		},
		Description: pgtype.Text{
			String: derefString(req.Description),
			Valid:  req.Description != nil,
		},
		Metadata:    metadataJSON,
		FolderID:    folderID,
		ClearFolder: clearFolder,
		UseKnowledgeGraph: pgtype.Bool{
			Bool:  derefBool(req.UseKnowledgeGraph),
			Valid: req.UseKnowledgeGraph != nil,
		},
	})
	if err != nil {
		slog.Error("update document", "error", err)
		httputil.InternalError(w, "Failed to update document")
		return
	}

	// Update tags if provided
	if req.TagIDs != nil {
		if err := queries.ClearDocumentTags(ctx, doc.ID); err != nil {
			slog.Error("clear document tags", "error", err)
		}
		for _, tagIDStr := range req.TagIDs {
			tagID, err := ParseUUID(tagIDStr)
			if err != nil {
				continue
			}
			if err := queries.AddDocumentTag(ctx, repository.AddDocumentTagParams{
				DocumentID: doc.ID,
				TagID:      ToPgUUID(tagID),
			}); err != nil {
				slog.Warn("add document tag", "tag_id", tagIDStr, "error", err)
			}
		}
	}

	slog.Info("updated document", "document_id", documentID, "org_id", orgID)
	tags, _ := queries.ListTagsForDocument(ctx, doc.ID)
	httputil.Success(w, toDocumentResponse(doc, tags))
}

// DeleteDocument deletes a document and cascades to brain-api.
func (h *DocumentHandler) DeleteDocument(w http.ResponseWriter, r *http.Request) {
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

	documentIDStr := chi.URLParam(r, "documentID")
	documentID, err := ParseUUID(documentIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid document ID")
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

	// Get document for key
	doc, err := queries.GetDocument(ctx, ToPgUUID(documentID))
	if err != nil {
		httputil.NotFound(w, "Document not found")
		return
	}

	documentKey := doc.DocumentKey

	// Delete from PostgreSQL
	if err := queries.DeleteDocument(ctx, ToPgUUID(documentID)); err != nil {
		slog.Error("delete document", "error", err)
		httputil.InternalError(w, "Failed to delete document")
		return
	}

	// Cascade to brain-api (best effort)
	if h.brainClient != nil {
		if err := h.brainClient.RemoveDocument(ctx, orgID.String(), documentKey); err != nil {
			slog.Error("brain-api cleanup failed for deleted document",
				"document_id", documentID,
				"document_key", documentKey,
				"org_id", orgID,
				"error", err,
			)
		}
	}

	slog.Info("deleted document", "document_id", documentID, "org_id", orgID)
	httputil.NoContent(w)
}

// GetDocumentChunks proxies to brain-api to get indexed chunks.
func (h *DocumentHandler) GetDocumentChunks(w http.ResponseWriter, r *http.Request) {
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

	documentIDStr := chi.URLParam(r, "documentID")
	documentID, err := ParseUUID(documentIDStr)
	if err != nil {
		httputil.BadRequest(w, "Invalid document ID")
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

	doc, err := queries.GetDocument(ctx, ToPgUUID(documentID))
	if err != nil {
		httputil.NotFound(w, "Document not found")
		return
	}

	if h.brainClient == nil {
		httputil.Error(w, http.StatusBadGateway, "Brain API not configured")
		return
	}

	chunks, err := h.brainClient.GetDocumentChunks(ctx, orgID.String(), doc.DocumentKey)
	if err != nil {
		slog.Error("fetch chunks from brain-api", "document_key", doc.DocumentKey, "error", err)
		httputil.Error(w, http.StatusBadGateway, "Failed to fetch chunks from brain-api")
		return
	}

	httputil.Success(w, chunks)
}

// getUserMasksForOrg returns whether user is org admin and their permission masks.
func (h *DocumentHandler) getUserMasksForOrg(
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
	profile, err := profileQueries.GetUserProfileByAuthSubject(ctx, keycloakSub)
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

// getUserProfile gets the user profile for the given keycloak sub.
func (h *DocumentHandler) getUserProfile(ctx context.Context, keycloakSub string) (repository.UserProfile, error) {
	conn, err := h.pool.Acquire(ctx)
	if err != nil {
		return repository.UserProfile{}, err
	}
	defer conn.Release()

	queries := repository.New(conn)
	return queries.GetUserProfileByAuthSubject(ctx, keycloakSub)
}
