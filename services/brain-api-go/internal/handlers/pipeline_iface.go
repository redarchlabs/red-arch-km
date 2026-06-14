package handlers

import (
	"context"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/pipeline"
)

// PipelineService defines the interface for pipeline operations used by handlers.
type PipelineService interface {
	IngestDocument(ctx context.Context, req pipeline.IngestRequest) (*pipeline.IngestResult, error)
	RemoveDocument(ctx context.Context, tenantID, documentKey string) error
	UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int, title *string) error
	InitTenant(ctx context.Context, tenantID string) error
	RemoveTenant(ctx context.Context, tenantID string) error
	GetDocumentChunks(ctx context.Context, tenantID, documentKey string, limit int) ([]models.SearchResult, error)
	Search(ctx context.Context, tenantID, query string, limit int, accessKeys []int, tags []string) ([]models.SearchResult, error)
	GraphSearch(ctx context.Context, tenantID, term, searchType string, tags []string, accessKeys []int) (any, error)
}

// Verify that pipeline.Pipeline implements PipelineService.
var _ PipelineService = (*pipeline.Pipeline)(nil)
