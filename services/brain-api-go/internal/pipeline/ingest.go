package pipeline

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/stores"
)

const (
	defaultChunkSizeWords   = 200 // ~500 tokens
	defaultChunkOverlapWords = 20  // ~50 tokens
	tripletWorkers           = 8
)

// IngestResult contains the results of document ingestion.
type IngestResult struct {
	DocumentKey string `json:"document_key"`
	DocumentID  string `json:"document_id"`
	Chunks      int    `json:"chunks"`
	Triplets    int    `json:"triplets"`
}

// IngestRequest contains the parameters for document ingestion.
type IngestRequest struct {
	TenantID          string
	DocumentKey       string
	Title             string
	Text              string
	Tags              []string
	AccessKeys        []int
	UseKnowledgeGraph bool
	Metadata          map[string]any
}

// Pipeline orchestrates the document ingestion process.
type Pipeline struct {
	vector     stores.VectorStore
	graph      stores.GraphStore
	embedding  stores.EmbeddingClient
	summarizer stores.Summarizer
	extractor  stores.TripletExtractorInterface
}

// NewPipeline creates a new ingestion pipeline.
func NewPipeline(
	vector stores.VectorStore,
	graph stores.GraphStore,
	embedding stores.EmbeddingClient,
	summarizer stores.Summarizer,
	extractor stores.TripletExtractorInterface,
) *Pipeline {
	return &Pipeline{
		vector:     vector,
		graph:      graph,
		embedding:  embedding,
		summarizer: summarizer,
		extractor:  extractor,
	}
}

// IngestDocument runs the full ingestion pipeline.
func (p *Pipeline) IngestDocument(ctx context.Context, req IngestRequest) (*IngestResult, error) {
	start := time.Now()

	slog.Info("ingesting document",
		"document_key", req.DocumentKey,
		"tenant_id", req.TenantID,
		"text_length", len(req.Text),
	)

	// Ensure collections exist
	if err := p.vector.EnsureCollections(ctx, req.TenantID, false); err != nil {
		return nil, fmt.Errorf("ensure collections: %w", err)
	}

	// Chunk the text
	chunks := ChunkText(req.Text, defaultChunkSizeWords, defaultChunkOverlapWords)
	if len(chunks) == 0 {
		slog.Warn("no chunks produced", "document_key", req.DocumentKey)
		return &IngestResult{
			DocumentKey: req.DocumentKey,
			Chunks:      0,
			Triplets:    0,
		}, nil
	}

	// Run embedding and summarization concurrently
	var embeddings [][]float32
	var summaries []string
	var embedErr, sumErr error

	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		embeddings, embedErr = p.embedding.EmbedBatch(ctx, chunks)
	}()

	go func() {
		defer wg.Done()
		summaries = p.summarizer.SummarizeChunks(ctx, chunks)
	}()

	wg.Wait()

	if embedErr != nil {
		return nil, fmt.Errorf("embed chunks: %w", embedErr)
	}
	if sumErr != nil {
		return nil, fmt.Errorf("summarize chunks: %w", sumErr)
	}

	// Build chunk records
	docID := uuid.NewString()
	accessKeys := req.AccessKeys
	if len(accessKeys) == 0 {
		accessKeys = []int{0} // default access key
	}

	chunkRecords := make([]models.VectorRecord, len(chunks))
	for i, chunk := range chunks {
		payload := map[string]any{
			"text":           chunk,
			"summary":        summaries[i],
			"chunk_order":    i,
			"document_id":    docID,
			"document_key":   req.DocumentKey,
			"document_title": req.Title,
			"tenant_id":      req.TenantID,
			"tags":           req.Tags,
			"access_keys":    accessKeys,
			"type":           "chunk",
		}
		// Merge metadata
		for k, v := range req.Metadata {
			payload[k] = v
		}

		chunkRecords[i] = models.VectorRecord{
			ID:      uuid.NewString(),
			Vector:  embeddings[i],
			Payload: payload,
		}
	}

	// Upsert chunk vectors
	if err := p.vector.UpsertVectors(ctx, req.TenantID, chunkRecords, "chunks"); err != nil {
		return nil, fmt.Errorf("upsert chunks: %w", err)
	}

	// Create document-level summary and vector
	docSummary := p.safeDocumentSummary(ctx, summaries)
	docVector := p.chooseDocumentVector(ctx, docSummary, embeddings)

	docPayload := map[string]any{
		"document_id":    docID,
		"document_key":   req.DocumentKey,
		"document_title": req.Title,
		"summary":        docSummary,
		"tenant_id":      req.TenantID,
		"tags":           req.Tags,
		"access_keys":    accessKeys,
		"type":           "document",
	}
	for k, v := range req.Metadata {
		docPayload[k] = v
	}

	docRecord := models.VectorRecord{
		ID:      docID,
		Vector:  docVector,
		Payload: docPayload,
	}

	if err := p.vector.UpsertVectors(ctx, req.TenantID, []models.VectorRecord{docRecord}, "documents"); err != nil {
		return nil, fmt.Errorf("upsert document: %w", err)
	}

	// Extract and store triplets if knowledge graph is enabled
	tripletCount := 0
	if req.UseKnowledgeGraph && p.graph != nil {
		tripletCount = p.extractAndStoreTriplets(ctx, req.TenantID, req.DocumentKey, chunks, req.Tags, accessKeys)
	}

	duration := time.Since(start)
	slog.Info("ingest complete",
		"document_key", req.DocumentKey,
		"chunks", len(chunkRecords),
		"triplets", tripletCount,
		"duration_ms", duration.Milliseconds(),
	)

	return &IngestResult{
		DocumentKey: req.DocumentKey,
		DocumentID:  docID,
		Chunks:      len(chunkRecords),
		Triplets:    tripletCount,
	}, nil
}

func (p *Pipeline) safeDocumentSummary(ctx context.Context, chunkSummaries []string) string {
	summary := p.summarizer.SummarizeDocument(ctx, chunkSummaries)
	if summary == "" {
		// Fallback: concatenate summaries
		var sb strings.Builder
		for _, s := range chunkSummaries {
			if s != "" {
				sb.WriteString(s)
				sb.WriteString(" ")
			}
		}
		summary = sb.String()
		if len(summary) > 2000 {
			summary = summary[:2000]
		}
	}
	return summary
}

func (p *Pipeline) chooseDocumentVector(ctx context.Context, summary string, chunkEmbeddings [][]float32) []float32 {
	if summary != "" {
		vec, err := p.embedding.Embed(ctx, summary)
		if err == nil {
			return vec
		}
		slog.Error("doc summary embedding failed; using centroid", "error", err)
	} else {
		slog.Warn("doc summary empty; using centroid of chunk embeddings")
	}
	return centroid(chunkEmbeddings)
}

func centroid(vectors [][]float32) []float32 {
	if len(vectors) == 0 {
		return nil
	}
	dim := len(vectors[0])
	if dim == 0 {
		return nil
	}

	result := make([]float32, dim)
	for _, v := range vectors {
		for i, val := range v {
			result[i] += val
		}
	}

	n := float32(len(vectors))
	for i := range result {
		result[i] /= n
	}

	return result
}

func (p *Pipeline) extractAndStoreTriplets(ctx context.Context, tenantID, documentKey string, chunks []string, tags []string, accessKeys []int) int {
	// Extract triplets from all chunks in parallel
	type extractResult struct {
		triplets []stores.Triplet
		err      error
	}

	results := make([]extractResult, len(chunks))
	var wg sync.WaitGroup
	sem := make(chan struct{}, tripletWorkers)

	for i, chunk := range chunks {
		wg.Add(1)
		go func(idx int, text string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			triplets, err := p.extractor.Extract(ctx, text)
			results[idx] = extractResult{triplets: triplets, err: err}
		}(i, chunk)
	}

	wg.Wait()

	// Collect all triplets
	var allTriplets []models.Triplet
	for _, r := range results {
		if r.err != nil {
			slog.Warn("triplet extraction failed for chunk", "error", r.err)
			continue
		}
		for _, t := range r.triplets {
			allTriplets = append(allTriplets, t.ToModelTriplet())
		}
	}

	if len(allTriplets) == 0 {
		return 0
	}

	// Insert into Neo4j
	if err := p.graph.InsertTriplets(ctx, tenantID, allTriplets, documentKey, tags, accessKeys); err != nil {
		slog.Error("batch triplet insert failed", "error", err)
		return 0
	}

	return len(allTriplets)
}

// RemoveDocument removes a document from both vector and graph stores.
func (p *Pipeline) RemoveDocument(ctx context.Context, tenantID, documentKey string) error {
	if err := p.vector.DeleteDocument(ctx, tenantID, documentKey); err != nil {
		slog.Error("vector delete failed", "document", documentKey, "error", err)
	}

	if p.graph != nil {
		if err := p.graph.DeleteByDocumentKey(ctx, tenantID, documentKey); err != nil {
			slog.Error("graph delete failed", "document", documentKey, "error", err)
		}
	}

	return nil
}

// UpdateMetadata updates tags/access_keys/title in both stores.
func (p *Pipeline) UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int, title *string) error {
	if err := p.vector.UpdateMetadata(ctx, tenantID, documentKey, tags, accessKeys, title); err != nil {
		return fmt.Errorf("vector update: %w", err)
	}

	if p.graph != nil {
		if err := p.graph.UpdateMetadata(ctx, tenantID, documentKey, tags, accessKeys); err != nil {
			return fmt.Errorf("graph update: %w", err)
		}
	}

	return nil
}

// InitTenant initializes vector collections and graph schema for a tenant.
func (p *Pipeline) InitTenant(ctx context.Context, tenantID string) error {
	if err := p.vector.EnsureCollections(ctx, tenantID, false); err != nil {
		return fmt.Errorf("ensure collections: %w", err)
	}

	if p.graph != nil {
		if err := p.graph.InitializeTenant(ctx, tenantID); err != nil {
			return fmt.Errorf("initialize graph tenant: %w", err)
		}
	}

	return nil
}

// RemoveTenant deletes all data for a tenant.
func (p *Pipeline) RemoveTenant(ctx context.Context, tenantID string) error {
	if err := p.vector.DeleteTenant(ctx, tenantID); err != nil {
		slog.Error("vector tenant delete failed", "tenant", tenantID, "error", err)
	}

	if p.graph != nil {
		if err := p.graph.DeleteTenant(ctx, tenantID); err != nil {
			slog.Error("graph tenant delete failed", "tenant", tenantID, "error", err)
		}
	}

	return nil
}

// Search performs vector similarity search.
func (p *Pipeline) Search(ctx context.Context, tenantID, query string, limit int, accessKeys []int, tags []string) ([]models.SearchResult, error) {
	// Embed the query
	queryVector, err := p.embedding.Embed(ctx, query)
	if err != nil {
		return nil, fmt.Errorf("embed query: %w", err)
	}

	// Search Qdrant
	results, err := p.vector.Search(ctx, tenantID, queryVector, limit, accessKeys, tags)
	if err != nil {
		return nil, fmt.Errorf("search: %w", err)
	}

	return results, nil
}

// GraphSearch performs entity/relationship search in the knowledge graph.
func (p *Pipeline) GraphSearch(ctx context.Context, tenantID, term string, searchType string, tags []string, accessKeys []int) (any, error) {
	if p.graph == nil {
		return nil, fmt.Errorf("knowledge graph not enabled")
	}

	switch searchType {
	case "entity":
		return p.graph.FuzzyEntitySearch(ctx, tenantID, term, tags, accessKeys)
	case "relationship":
		return p.graph.FuzzyRelationshipSearch(ctx, tenantID, term, tags, accessKeys)
	default:
		return nil, fmt.Errorf("unknown search type: %s", searchType)
	}
}

// GetDocumentChunks returns all chunks for a document.
func (p *Pipeline) GetDocumentChunks(ctx context.Context, tenantID, documentKey string, limit int) ([]models.SearchResult, error) {
	return p.vector.GetDocumentChunks(ctx, tenantID, documentKey, limit)
}
