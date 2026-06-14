package pipeline

import (
	"context"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/stores"
)

// MockVectorStore implements stores.VectorStore for testing.
type MockVectorStore struct {
	EnsureCollectionsErr error
	UpsertErr            error
	SearchResults        []models.SearchResult
	SearchErr            error
	DeleteDocErr         error
	DeleteTenantErr      error
	UpdateMetadataErr    error
	ChunksResults        []models.SearchResult
	ChunksErr            error
	HealthyResult        bool
}

func (m *MockVectorStore) EnsureCollections(ctx context.Context, tenantID string, reset bool) error {
	return m.EnsureCollectionsErr
}

func (m *MockVectorStore) UpsertVectors(ctx context.Context, tenantID string, records []models.VectorRecord, collType string) error {
	return m.UpsertErr
}

func (m *MockVectorStore) Search(ctx context.Context, tenantID string, query []float32, limit int, accessKeys []int, tags []string) ([]models.SearchResult, error) {
	if m.SearchErr != nil {
		return nil, m.SearchErr
	}
	return m.SearchResults, nil
}

func (m *MockVectorStore) DeleteDocument(ctx context.Context, tenantID, documentKey string) error {
	return m.DeleteDocErr
}

func (m *MockVectorStore) DeleteTenant(ctx context.Context, tenantID string) error {
	return m.DeleteTenantErr
}

func (m *MockVectorStore) UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int, title *string) error {
	return m.UpdateMetadataErr
}

func (m *MockVectorStore) GetDocumentChunks(ctx context.Context, tenantID, documentKey string, limit int) ([]models.SearchResult, error) {
	if m.ChunksErr != nil {
		return nil, m.ChunksErr
	}
	return m.ChunksResults, nil
}

func (m *MockVectorStore) Healthy(ctx context.Context) bool {
	return m.HealthyResult
}

func (m *MockVectorStore) Close() error {
	return nil
}

// MockGraphStore implements stores.GraphStore for testing.
type MockGraphStore struct {
	InitErr                error
	InsertTripletsErr      error
	EntitySearchResults    []models.Entity
	EntitySearchErr        error
	RelSearchResults       []models.Triplet
	RelSearchErr           error
	DeleteByDocErr         error
	DeleteTenantErr        error
	UpdateMetadataErr      error
	HealthyResult          bool
}

func (m *MockGraphStore) InitializeTenant(ctx context.Context, tenantID string) error {
	return m.InitErr
}

func (m *MockGraphStore) InsertTriplets(ctx context.Context, tenantID string, triplets []models.Triplet, docKey string, tags []string, accessKeys []int) error {
	return m.InsertTripletsErr
}

func (m *MockGraphStore) FuzzyEntitySearch(ctx context.Context, tenantID, term string, tags []string, accessKeys []int) ([]models.Entity, error) {
	if m.EntitySearchErr != nil {
		return nil, m.EntitySearchErr
	}
	return m.EntitySearchResults, nil
}

func (m *MockGraphStore) FuzzyRelationshipSearch(ctx context.Context, tenantID, term string, tags []string, accessKeys []int) ([]models.Triplet, error) {
	if m.RelSearchErr != nil {
		return nil, m.RelSearchErr
	}
	return m.RelSearchResults, nil
}

func (m *MockGraphStore) DeleteByDocumentKey(ctx context.Context, tenantID, documentKey string) error {
	return m.DeleteByDocErr
}

func (m *MockGraphStore) DeleteTenant(ctx context.Context, tenantID string) error {
	return m.DeleteTenantErr
}

func (m *MockGraphStore) UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int) error {
	return m.UpdateMetadataErr
}

func (m *MockGraphStore) Healthy(ctx context.Context) bool {
	return m.HealthyResult
}

func (m *MockGraphStore) Close(ctx context.Context) error {
	return nil
}

// MockEmbeddingClient implements stores.EmbeddingClient for testing.
type MockEmbeddingClient struct {
	EmbedResult     []float32
	EmbedErr        error
	EmbedBatchResult [][]float32
	EmbedBatchErr   error
	DimensionResult int
}

func (m *MockEmbeddingClient) Embed(ctx context.Context, text string) ([]float32, error) {
	if m.EmbedErr != nil {
		return nil, m.EmbedErr
	}
	if m.EmbedResult != nil {
		return m.EmbedResult, nil
	}
	// Default: return a 1536-dimension zero vector
	return make([]float32, 1536), nil
}

func (m *MockEmbeddingClient) EmbedBatch(ctx context.Context, texts []string) ([][]float32, error) {
	if m.EmbedBatchErr != nil {
		return nil, m.EmbedBatchErr
	}
	if m.EmbedBatchResult != nil {
		return m.EmbedBatchResult, nil
	}
	// Default: return zero vectors for each input
	result := make([][]float32, len(texts))
	for i := range texts {
		result[i] = make([]float32, 1536)
	}
	return result, nil
}

func (m *MockEmbeddingClient) Dimension() int {
	if m.DimensionResult > 0 {
		return m.DimensionResult
	}
	return 1536
}

// MockSummarizer implements stores.Summarizer for testing.
type MockSummarizer struct {
	ChunkSummaries []string
	DocSummary     string
}

func (m *MockSummarizer) SummarizeChunks(ctx context.Context, chunks []string) []string {
	if m.ChunkSummaries != nil {
		return m.ChunkSummaries
	}
	// Default: return "summary" for each chunk
	result := make([]string, len(chunks))
	for i := range chunks {
		result[i] = "chunk summary"
	}
	return result
}

func (m *MockSummarizer) SummarizeDocument(ctx context.Context, chunkSummaries []string) string {
	if m.DocSummary != "" {
		return m.DocSummary
	}
	return "document summary"
}

// MockTripletExtractor implements stores.TripletExtractorInterface for testing.
type MockTripletExtractor struct {
	ExtractResults []stores.Triplet
	ExtractErr     error
}

func (m *MockTripletExtractor) Extract(ctx context.Context, text string) ([]stores.Triplet, error) {
	if m.ExtractErr != nil {
		return nil, m.ExtractErr
	}
	if m.ExtractResults != nil {
		return m.ExtractResults, nil
	}
	// Default: return empty
	return nil, nil
}

// Helper to create a pipeline with all mocks
func newMockPipeline() (*Pipeline, *MockVectorStore, *MockGraphStore, *MockEmbeddingClient, *MockSummarizer, *MockTripletExtractor) {
	vector := &MockVectorStore{HealthyResult: true}
	graph := &MockGraphStore{HealthyResult: true}
	embedding := &MockEmbeddingClient{}
	summarizer := &MockSummarizer{}
	extractor := &MockTripletExtractor{}

	p := NewPipeline(vector, graph, embedding, summarizer, extractor)
	return p, vector, graph, embedding, summarizer, extractor
}
