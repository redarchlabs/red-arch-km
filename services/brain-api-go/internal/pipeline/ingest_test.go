package pipeline

import (
	"context"
	"testing"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/stores"
)

// Test centroid calculation
func TestCentroid_Empty(t *testing.T) {
	result := centroid(nil)
	if result != nil {
		t.Errorf("expected nil for empty input, got %v", result)
	}

	result = centroid([][]float32{})
	if result != nil {
		t.Errorf("expected nil for empty slice, got %v", result)
	}
}

func TestCentroid_SingleVector(t *testing.T) {
	vec := []float32{1.0, 2.0, 3.0}
	result := centroid([][]float32{vec})

	if len(result) != 3 {
		t.Fatalf("expected length 3, got %d", len(result))
	}

	for i, v := range result {
		if v != vec[i] {
			t.Errorf("expected %f at index %d, got %f", vec[i], i, v)
		}
	}
}

func TestCentroid_MultipleVectors(t *testing.T) {
	vecs := [][]float32{
		{1.0, 2.0, 3.0},
		{3.0, 4.0, 5.0},
	}
	result := centroid(vecs)

	expected := []float32{2.0, 3.0, 4.0} // average
	if len(result) != len(expected) {
		t.Fatalf("expected length %d, got %d", len(expected), len(result))
	}

	for i, v := range result {
		if v != expected[i] {
			t.Errorf("expected %f at index %d, got %f", expected[i], i, v)
		}
	}
}

func TestCentroid_EmptyDimension(t *testing.T) {
	vecs := [][]float32{{}}
	result := centroid(vecs)
	if result != nil {
		t.Errorf("expected nil for empty dimension, got %v", result)
	}
}

// Test IngestResult JSON serialization
func TestIngestResult_Fields(t *testing.T) {
	result := IngestResult{
		DocumentKey: "doc-1",
		DocumentID:  "id-123",
		Chunks:      10,
		Triplets:    5,
	}

	if result.DocumentKey != "doc-1" {
		t.Errorf("expected DocumentKey 'doc-1', got %q", result.DocumentKey)
	}
	if result.DocumentID != "id-123" {
		t.Errorf("expected DocumentID 'id-123', got %q", result.DocumentID)
	}
	if result.Chunks != 10 {
		t.Errorf("expected Chunks 10, got %d", result.Chunks)
	}
	if result.Triplets != 5 {
		t.Errorf("expected Triplets 5, got %d", result.Triplets)
	}
}

// Test IngestRequest fields
func TestIngestRequest_Fields(t *testing.T) {
	req := IngestRequest{
		TenantID:          "tenant1",
		DocumentKey:       "doc1",
		Title:             "Test Document",
		Text:              "Document content",
		Tags:              []string{"tag1", "tag2"},
		AccessKeys:        []int{1, 2, 3},
		UseKnowledgeGraph: true,
		Metadata:          map[string]any{"custom": "value"},
	}

	if req.TenantID != "tenant1" {
		t.Errorf("expected TenantID 'tenant1', got %q", req.TenantID)
	}
	if len(req.Tags) != 2 {
		t.Errorf("expected 2 tags, got %d", len(req.Tags))
	}
	if len(req.AccessKeys) != 3 {
		t.Errorf("expected 3 access keys, got %d", len(req.AccessKeys))
	}
	if !req.UseKnowledgeGraph {
		t.Error("expected UseKnowledgeGraph to be true")
	}
	if req.Metadata["custom"] != "value" {
		t.Errorf("expected metadata custom='value', got %v", req.Metadata["custom"])
	}
}

// Test NewPipeline
func TestNewPipeline(t *testing.T) {
	p := NewPipeline(nil, nil, nil, nil, nil)
	if p == nil {
		t.Error("expected non-nil pipeline")
	}
}

// Test models types
func TestVectorRecord_Fields(t *testing.T) {
	rec := models.VectorRecord{
		ID:      "rec-1",
		Vector:  []float32{0.1, 0.2, 0.3},
		Payload: map[string]any{"text": "hello"},
	}

	if rec.ID != "rec-1" {
		t.Errorf("expected ID 'rec-1', got %q", rec.ID)
	}
	if len(rec.Vector) != 3 {
		t.Errorf("expected 3 dimensions, got %d", len(rec.Vector))
	}
	if rec.Payload["text"] != "hello" {
		t.Errorf("expected text 'hello', got %v", rec.Payload["text"])
	}
}

func TestSearchResult_Fields(t *testing.T) {
	res := models.SearchResult{
		ID:      "res-1",
		Score:   0.95,
		Payload: map[string]any{"summary": "test summary"},
	}

	if res.ID != "res-1" {
		t.Errorf("expected ID 'res-1', got %q", res.ID)
	}
	if res.Score != 0.95 {
		t.Errorf("expected Score 0.95, got %f", res.Score)
	}
	if res.Payload["summary"] != "test summary" {
		t.Errorf("expected summary 'test summary', got %v", res.Payload["summary"])
	}
}

func TestTriplet_Fields(t *testing.T) {
	trip := models.Triplet{
		Subject:   "Alice",
		Predicate: "knows",
		Object:    "Bob",
	}

	if trip.Subject != "Alice" {
		t.Errorf("expected Subject 'Alice', got %q", trip.Subject)
	}
	if trip.Predicate != "knows" {
		t.Errorf("expected Predicate 'knows', got %q", trip.Predicate)
	}
	if trip.Object != "Bob" {
		t.Errorf("expected Object 'Bob', got %q", trip.Object)
	}
}

func TestEntity_Fields(t *testing.T) {
	ent := models.Entity{
		Name: "Test Entity",
	}

	if ent.Name != "Test Entity" {
		t.Errorf("expected Name 'Test Entity', got %q", ent.Name)
	}
}

// Pipeline method tests using mocks

func TestPipeline_IngestDocument_Success(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	req := IngestRequest{
		TenantID:    "tenant1",
		DocumentKey: "doc1",
		Title:       "Test Document",
		Text:        "This is a test document. It has multiple sentences. And some more content.",
		Tags:        []string{"test"},
		AccessKeys:  []int{1},
	}

	result, err := p.IngestDocument(ctx(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.DocumentKey != "doc1" {
		t.Errorf("expected DocumentKey 'doc1', got %q", result.DocumentKey)
	}
	if result.Chunks == 0 {
		t.Error("expected at least one chunk")
	}
}

func TestPipeline_IngestDocument_EmptyText(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	req := IngestRequest{
		TenantID:    "tenant1",
		DocumentKey: "doc1",
		Title:       "Empty Doc",
		Text:        "",
	}

	result, err := p.IngestDocument(ctx(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.Chunks != 0 {
		t.Errorf("expected 0 chunks for empty text, got %d", result.Chunks)
	}
}

func TestPipeline_IngestDocument_EnsureCollectionsError(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.EnsureCollectionsErr = &mockError{"ensure collections failed"}

	req := IngestRequest{
		TenantID:    "tenant1",
		DocumentKey: "doc1",
		Title:       "Test",
		Text:        "Some text.",
	}

	_, err := p.IngestDocument(ctx(), req)
	if err == nil {
		t.Error("expected error when EnsureCollections fails")
	}
}

func TestPipeline_IngestDocument_EmbedError(t *testing.T) {
	p, _, _, embeddingClient, _, _ := newMockPipeline()
	embeddingClient.EmbedBatchErr = &mockError{"embedding failed"}

	req := IngestRequest{
		TenantID:    "tenant1",
		DocumentKey: "doc1",
		Title:       "Test",
		Text:        "Some text that will be chunked.",
	}

	_, err := p.IngestDocument(ctx(), req)
	if err == nil {
		t.Error("expected error when embedding fails")
	}
}

func TestPipeline_IngestDocument_UpsertError(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.UpsertErr = &mockError{"upsert failed"}

	req := IngestRequest{
		TenantID:    "tenant1",
		DocumentKey: "doc1",
		Title:       "Test",
		Text:        "Some text that will be chunked.",
	}

	_, err := p.IngestDocument(ctx(), req)
	if err == nil {
		t.Error("expected error when upsert fails")
	}
}

func TestPipeline_IngestDocument_WithKnowledgeGraph(t *testing.T) {
	p, _, _, _, _, extractor := newMockPipeline()
	extractor.ExtractResults = []stores.Triplet{
		{Subject: "Alice", Predicate: "knows", Object: "Bob"},
	}

	req := IngestRequest{
		TenantID:          "tenant1",
		DocumentKey:       "doc1",
		Title:             "Test",
		Text:              "Alice knows Bob. They work together.",
		UseKnowledgeGraph: true,
	}

	result, err := p.IngestDocument(ctx(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.Triplets == 0 {
		t.Error("expected triplets to be extracted")
	}
}

func TestPipeline_RemoveDocument_Success(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	err := p.RemoveDocument(ctx(), "tenant1", "doc1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPipeline_RemoveDocument_VectorError(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.DeleteDocErr = &mockError{"delete failed"}

	// Should not return error - just logs it
	err := p.RemoveDocument(ctx(), "tenant1", "doc1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPipeline_UpdateMetadata_Success(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	title := "New Title"
	err := p.UpdateMetadata(ctx(), "tenant1", "doc1", []string{"tag"}, []int{1}, &title)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPipeline_UpdateMetadata_VectorError(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.UpdateMetadataErr = &mockError{"update failed"}

	err := p.UpdateMetadata(ctx(), "tenant1", "doc1", nil, nil, nil)
	if err == nil {
		t.Error("expected error when vector update fails")
	}
}

func TestPipeline_InitTenant_Success(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	err := p.InitTenant(ctx(), "tenant1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPipeline_InitTenant_VectorError(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.EnsureCollectionsErr = &mockError{"init failed"}

	err := p.InitTenant(ctx(), "tenant1")
	if err == nil {
		t.Error("expected error when ensure collections fails")
	}
}

func TestPipeline_InitTenant_GraphError(t *testing.T) {
	p, _, graphStore, _, _, _ := newMockPipeline()
	graphStore.InitErr = &mockError{"graph init failed"}

	err := p.InitTenant(ctx(), "tenant1")
	if err == nil {
		t.Error("expected error when graph init fails")
	}
}

func TestPipeline_RemoveTenant_Success(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	err := p.RemoveTenant(ctx(), "tenant1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPipeline_Search_Success(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.SearchResults = []models.SearchResult{
		{ID: "chunk1", Score: 0.9, Payload: map[string]any{"text": "result"}},
	}

	results, err := p.Search(ctx(), "tenant1", "query", 5, nil, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(results) != 1 {
		t.Errorf("expected 1 result, got %d", len(results))
	}
}

func TestPipeline_Search_EmbedError(t *testing.T) {
	p, _, _, embeddingClient, _, _ := newMockPipeline()
	embeddingClient.EmbedErr = &mockError{"embed failed"}

	_, err := p.Search(ctx(), "tenant1", "query", 5, nil, nil)
	if err == nil {
		t.Error("expected error when embedding fails")
	}
}

func TestPipeline_Search_VectorError(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.SearchErr = &mockError{"search failed"}

	_, err := p.Search(ctx(), "tenant1", "query", 5, nil, nil)
	if err == nil {
		t.Error("expected error when search fails")
	}
}

func TestPipeline_GraphSearch_Entity(t *testing.T) {
	p, _, graphStore, _, _, _ := newMockPipeline()
	graphStore.EntitySearchResults = []models.Entity{{Name: "Alice"}}

	results, err := p.GraphSearch(ctx(), "tenant1", "Alice", "entity", nil, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	entities, ok := results.([]models.Entity)
	if !ok {
		t.Fatalf("expected []models.Entity, got %T", results)
	}
	if len(entities) != 1 {
		t.Errorf("expected 1 entity, got %d", len(entities))
	}
}

func TestPipeline_GraphSearch_Relationship(t *testing.T) {
	p, _, graphStore, _, _, _ := newMockPipeline()
	graphStore.RelSearchResults = []models.Triplet{{Subject: "A", Predicate: "rel", Object: "B"}}

	results, err := p.GraphSearch(ctx(), "tenant1", "rel", "relationship", nil, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	triplets, ok := results.([]models.Triplet)
	if !ok {
		t.Fatalf("expected []models.Triplet, got %T", results)
	}
	if len(triplets) != 1 {
		t.Errorf("expected 1 triplet, got %d", len(triplets))
	}
}

func TestPipeline_GraphSearch_NilGraph(t *testing.T) {
	p := NewPipeline(&MockVectorStore{}, nil, &MockEmbeddingClient{}, &MockSummarizer{}, &MockTripletExtractor{})

	_, err := p.GraphSearch(ctx(), "tenant1", "term", "entity", nil, nil)
	if err == nil {
		t.Error("expected error when graph is nil")
	}
}

func TestPipeline_GraphSearch_InvalidType(t *testing.T) {
	p, _, _, _, _, _ := newMockPipeline()

	_, err := p.GraphSearch(ctx(), "tenant1", "term", "invalid", nil, nil)
	if err == nil {
		t.Error("expected error for invalid search type")
	}
}

func TestPipeline_GetDocumentChunks_Success(t *testing.T) {
	p, vectorStore, _, _, _, _ := newMockPipeline()
	vectorStore.ChunksResults = []models.SearchResult{
		{ID: "chunk1", Payload: map[string]any{"text": "chunk text"}},
	}

	results, err := p.GetDocumentChunks(ctx(), "tenant1", "doc1", 100)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(results) != 1 {
		t.Errorf("expected 1 chunk, got %d", len(results))
	}
}

// mockError for testing
type mockError struct {
	msg string
}

func (e *mockError) Error() string {
	return e.msg
}

// ctx is a context helper for testing
func ctx() context.Context {
	return context.Background()
}
