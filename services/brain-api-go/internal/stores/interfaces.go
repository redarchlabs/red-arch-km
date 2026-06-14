// Package stores provides interfaces and implementations for external data stores.
package stores

import (
	"context"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
)

// VectorStore defines the interface for vector database operations.
type VectorStore interface {
	EnsureCollections(ctx context.Context, tenantID string, reset bool) error
	UpsertVectors(ctx context.Context, tenantID string, records []models.VectorRecord, collType string) error
	Search(ctx context.Context, tenantID string, query []float32, limit int, accessKeys []int, tags []string) ([]models.SearchResult, error)
	DeleteDocument(ctx context.Context, tenantID, documentKey string) error
	DeleteTenant(ctx context.Context, tenantID string) error
	UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int, title *string) error
	GetDocumentChunks(ctx context.Context, tenantID, documentKey string, limit int) ([]models.SearchResult, error)
	Healthy(ctx context.Context) bool
	Close() error
}

// GraphStore defines the interface for knowledge graph operations.
type GraphStore interface {
	InitializeTenant(ctx context.Context, tenantID string) error
	InsertTriplets(ctx context.Context, tenantID string, triplets []models.Triplet, docKey string, tags []string, accessKeys []int) error
	FuzzyEntitySearch(ctx context.Context, tenantID, term string, tags []string, accessKeys []int) ([]models.Entity, error)
	FuzzyRelationshipSearch(ctx context.Context, tenantID, term string, tags []string, accessKeys []int) ([]models.Triplet, error)
	DeleteByDocumentKey(ctx context.Context, tenantID, documentKey string) error
	DeleteTenant(ctx context.Context, tenantID string) error
	UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int) error
	Healthy(ctx context.Context) bool
	Close(ctx context.Context) error
}

// EmbeddingClient defines the interface for embedding generation.
type EmbeddingClient interface {
	Embed(ctx context.Context, text string) ([]float32, error)
	EmbedBatch(ctx context.Context, texts []string) ([][]float32, error)
	Dimension() int
}

// Summarizer defines the interface for text summarization.
type Summarizer interface {
	SummarizeChunks(ctx context.Context, chunks []string) []string
	SummarizeDocument(ctx context.Context, chunkSummaries []string) string
}

// TripletExtractorInterface defines the interface for knowledge graph extraction.
type TripletExtractorInterface interface {
	Extract(ctx context.Context, text string) ([]Triplet, error)
}

// Verify that concrete types implement their interfaces.
var (
	_ VectorStore              = (*QdrantStore)(nil)
	_ GraphStore               = (*Neo4jStore)(nil)
	_ EmbeddingClient          = (*OpenAIClient)(nil)
	_ Summarizer               = (*ChunkSummarizer)(nil)
	_ TripletExtractorInterface = (*TripletExtractor)(nil)
)
