// Package stores provides client wrappers for vector/graph/LLM backends.
package stores

import (
	"context"
	"fmt"
	"log/slog"
	"sort"
	"strings"

	"github.com/google/uuid"
	"github.com/qdrant/go-client/qdrant"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
)

// QdrantStore wraps the Qdrant gRPC client for vector operations.
type QdrantStore struct {
	client      *qdrant.Client
	chunkSuffix string
	docSuffix   string
	dimension   uint64
}

// NewQdrantStore creates a new QdrantStore.
func NewQdrantStore(url string, apiKey string, chunkSuffix, docSuffix string, dimension int) (*QdrantStore, error) {
	// Parse URL to get host:port
	host := url
	port := 6334 // gRPC port

	// Strip protocol if present
	host = strings.TrimPrefix(host, "http://")
	host = strings.TrimPrefix(host, "https://")

	// Check for port in URL
	if idx := strings.LastIndex(host, ":"); idx != -1 {
		// Qdrant HTTP is typically 6333, gRPC is 6334
		// If they specified HTTP port, convert to gRPC
		if strings.HasSuffix(host, ":6333") {
			host = host[:idx]
			port = 6334
		} else {
			// Use what they specified
			var err error
			_, err = fmt.Sscanf(host[idx+1:], "%d", &port)
			if err != nil {
				port = 6334
			}
			host = host[:idx]
		}
	}

	var client *qdrant.Client
	var err error

	if apiKey != "" {
		client, err = qdrant.NewClient(&qdrant.Config{
			Host:   host,
			Port:   port,
			APIKey: apiKey,
		})
	} else {
		client, err = qdrant.NewClient(&qdrant.Config{
			Host: host,
			Port: port,
		})
	}
	if err != nil {
		return nil, fmt.Errorf("create qdrant client: %w", err)
	}

	return &QdrantStore{
		client:      client,
		chunkSuffix: chunkSuffix,
		docSuffix:   docSuffix,
		dimension:   uint64(dimension),
	}, nil
}

func (q *QdrantStore) chunkCollection(tenantID string) string {
	return fmt.Sprintf("%s-%s", tenantID, q.chunkSuffix)
}

func (q *QdrantStore) docCollection(tenantID string) string {
	return fmt.Sprintf("%s-%s", tenantID, q.docSuffix)
}

// EnsureCollections creates chunk and document collections for a tenant if they don't exist.
func (q *QdrantStore) EnsureCollections(ctx context.Context, tenantID string, reset bool) error {
	collections := []string{q.chunkCollection(tenantID), q.docCollection(tenantID)}

	for _, name := range collections {
		if reset {
			if err := q.recreateCollection(ctx, name); err != nil {
				return err
			}
		} else {
			if err := q.ensureCollection(ctx, name); err != nil {
				return err
			}
		}
	}
	return nil
}

func (q *QdrantStore) recreateCollection(ctx context.Context, name string) error {
	// Try to delete first (ignore errors if doesn't exist)
	_ = q.client.DeleteCollection(ctx, name)

	return q.createCollection(ctx, name)
}

func (q *QdrantStore) ensureCollection(ctx context.Context, name string) error {
	exists, err := q.client.CollectionExists(ctx, name)
	if err != nil {
		return fmt.Errorf("check collection %s: %w", name, err)
	}
	if !exists {
		return q.createCollection(ctx, name)
	}
	return nil
}

func (q *QdrantStore) createCollection(ctx context.Context, name string) error {
	err := q.client.CreateCollection(ctx, &qdrant.CreateCollection{
		CollectionName: name,
		VectorsConfig: qdrant.NewVectorsConfigMap(map[string]*qdrant.VectorParams{
			"embedding": {
				Size:     q.dimension,
				Distance: qdrant.Distance_Cosine,
			},
		}),
	})
	if err != nil {
		return fmt.Errorf("create collection %s: %w", name, err)
	}
	slog.Info("created Qdrant collection", "name", name)
	return nil
}

// UpsertVectors inserts or updates vectors in Qdrant.
func (q *QdrantStore) UpsertVectors(ctx context.Context, tenantID string, records []models.VectorRecord, collectionType string) error {
	var collection string
	if collectionType == "chunks" {
		collection = q.chunkCollection(tenantID)
	} else {
		collection = q.docCollection(tenantID)
	}

	points := make([]*qdrant.PointStruct, 0, len(records))
	for _, r := range records {
		id := r.ID
		if id == "" {
			id = uuid.NewString()
		}

		payload := make(map[string]*qdrant.Value)
		for k, v := range r.Payload {
			payload[k] = toQdrantValue(v)
		}

		points = append(points, &qdrant.PointStruct{
			Id:      qdrant.NewIDUUID(id),
			Vectors: qdrant.NewVectorsMap(map[string]*qdrant.Vector{"embedding": qdrant.NewVectorDense(r.Vector)}),
			Payload: payload,
		})
	}

	// Batch in groups of 500
	const batchSize = 500
	for i := 0; i < len(points); i += batchSize {
		end := i + batchSize
		if end > len(points) {
			end = len(points)
		}
		batch := points[i:end]

		_, err := q.client.Upsert(ctx, &qdrant.UpsertPoints{
			CollectionName: collection,
			Points:         batch,
			Wait:           qdrant.PtrOf(true),
		})
		if err != nil {
			return fmt.Errorf("upsert batch to %s: %w", collection, err)
		}
		slog.Debug("upserted points", "collection", collection, "count", len(batch))
	}

	return nil
}

// Search performs similarity search on chunk collection.
func (q *QdrantStore) Search(ctx context.Context, tenantID string, queryVector []float32, limit int, accessKeys []int, requiredTags []string) ([]models.SearchResult, error) {
	collection := q.chunkCollection(tenantID)

	var must []*qdrant.Condition

	// Filter by required tags
	for _, tag := range requiredTags {
		must = append(must, &qdrant.Condition{
			ConditionOneOf: &qdrant.Condition_Field{
				Field: &qdrant.FieldCondition{
					Key: "tags",
					Match: &qdrant.Match{
						MatchValue: &qdrant.Match_Keyword{
							Keyword: tag,
						},
					},
				},
			},
		})
	}

	// Filter by access keys (any match)
	if len(accessKeys) > 0 {
		vals := make([]*qdrant.Value, len(accessKeys))
		for i, k := range accessKeys {
			vals[i] = &qdrant.Value{Kind: &qdrant.Value_IntegerValue{IntegerValue: int64(k)}}
		}
		must = append(must, &qdrant.Condition{
			ConditionOneOf: &qdrant.Condition_Field{
				Field: &qdrant.FieldCondition{
					Key: "access_keys",
					Match: &qdrant.Match{
						MatchValue: &qdrant.Match_Integers{
							Integers: &qdrant.RepeatedIntegers{Integers: toInt64Slice(accessKeys)},
						},
					},
				},
			},
		})
	}

	var filter *qdrant.Filter
	if len(must) > 0 {
		filter = &qdrant.Filter{Must: must}
	}

	results, err := q.client.Query(ctx, &qdrant.QueryPoints{
		CollectionName: collection,
		Query:          qdrant.NewQueryDense(queryVector),
		Using:          qdrant.PtrOf("embedding"),
		Limit:          qdrant.PtrOf(uint64(limit)),
		Filter:         filter,
		WithPayload:    qdrant.NewWithPayloadInclude("text", "summary", "chunk_order", "document_id", "document_key", "document_title", "type"),
	})
	if err != nil {
		return nil, fmt.Errorf("query %s: %w", collection, err)
	}

	out := make([]models.SearchResult, 0, len(results))
	for _, p := range results {
		out = append(out, models.SearchResult{
			ID:      p.GetId().GetUuid(),
			Score:   p.GetScore(),
			Payload: fromQdrantPayload(p.GetPayload()),
		})
	}
	return out, nil
}

// DeleteDocument removes all chunks and document records for a document.
func (q *QdrantStore) DeleteDocument(ctx context.Context, tenantID, documentKey string) error {
	filter := &qdrant.Filter{
		Must: []*qdrant.Condition{
			{
				ConditionOneOf: &qdrant.Condition_Field{
					Field: &qdrant.FieldCondition{
						Key:   "document_key",
						Match: &qdrant.Match{MatchValue: &qdrant.Match_Keyword{Keyword: documentKey}},
					},
				},
			},
			{
				ConditionOneOf: &qdrant.Condition_Field{
					Field: &qdrant.FieldCondition{
						Key:   "tenant_id",
						Match: &qdrant.Match{MatchValue: &qdrant.Match_Keyword{Keyword: tenantID}},
					},
				},
			},
		},
	}

	for _, collection := range []string{q.chunkCollection(tenantID), q.docCollection(tenantID)} {
		_, err := q.client.Delete(ctx, &qdrant.DeletePoints{
			CollectionName: collection,
			Points: &qdrant.PointsSelector{
				PointsSelectorOneOf: &qdrant.PointsSelector_Filter{Filter: filter},
			},
		})
		if err != nil {
			return fmt.Errorf("delete from %s: %w", collection, err)
		}
	}

	slog.Info("deleted document from Qdrant", "tenant", tenantID, "document", documentKey)
	return nil
}

// DeleteTenant removes all collections for a tenant.
func (q *QdrantStore) DeleteTenant(ctx context.Context, tenantID string) error {
	for _, collection := range []string{q.chunkCollection(tenantID), q.docCollection(tenantID)} {
		err := q.client.DeleteCollection(ctx, collection)
		if err != nil {
			// Ignore "not found" errors
			if !strings.Contains(strings.ToLower(err.Error()), "not found") &&
				!strings.Contains(strings.ToLower(err.Error()), "doesn't exist") {
				return fmt.Errorf("delete collection %s: %w", collection, err)
			}
		}
	}
	slog.Info("deleted Qdrant tenant collections", "tenant", tenantID)
	return nil
}

// UpdateMetadata updates tags/access_keys/title on all chunks for a document.
func (q *QdrantStore) UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int, title *string) error {
	collection := q.chunkCollection(tenantID)

	filter := &qdrant.Filter{
		Must: []*qdrant.Condition{
			{
				ConditionOneOf: &qdrant.Condition_Field{
					Field: &qdrant.FieldCondition{
						Key:   "document_key",
						Match: &qdrant.Match{MatchValue: &qdrant.Match_Keyword{Keyword: documentKey}},
					},
				},
			},
			{
				ConditionOneOf: &qdrant.Condition_Field{
					Field: &qdrant.FieldCondition{
						Key:   "tenant_id",
						Match: &qdrant.Match{MatchValue: &qdrant.Match_Keyword{Keyword: tenantID}},
					},
				},
			},
		},
	}

	payload := make(map[string]*qdrant.Value)
	if tags != nil {
		payload["tags"] = toQdrantValue(tags)
	}
	if accessKeys != nil {
		payload["access_keys"] = toQdrantValue(accessKeys)
	}
	if title != nil {
		payload["document_title"] = toQdrantValue(*title)
	}

	if len(payload) == 0 {
		return nil
	}

	_, err := q.client.SetPayload(ctx, &qdrant.SetPayloadPoints{
		CollectionName: collection,
		Payload:        payload,
		PointsSelector: &qdrant.PointsSelector{
			PointsSelectorOneOf: &qdrant.PointsSelector_Filter{Filter: filter},
		},
	})
	if err != nil {
		return fmt.Errorf("set payload: %w", err)
	}

	return nil
}

// GetDocumentChunks returns all chunks for a document, ordered by chunk_order.
func (q *QdrantStore) GetDocumentChunks(ctx context.Context, tenantID, documentKey string, limit int) ([]models.SearchResult, error) {
	collection := q.chunkCollection(tenantID)

	filter := &qdrant.Filter{
		Must: []*qdrant.Condition{
			{
				ConditionOneOf: &qdrant.Condition_Field{
					Field: &qdrant.FieldCondition{
						Key:   "document_key",
						Match: &qdrant.Match{MatchValue: &qdrant.Match_Keyword{Keyword: documentKey}},
					},
				},
			},
			{
				ConditionOneOf: &qdrant.Condition_Field{
					Field: &qdrant.FieldCondition{
						Key:   "tenant_id",
						Match: &qdrant.Match{MatchValue: &qdrant.Match_Keyword{Keyword: tenantID}},
					},
				},
			},
		},
	}

	var allResults []models.SearchResult
	var offset *qdrant.PointId

	for {
		scrollResp, err := q.client.GetPointsClient().Scroll(ctx, &qdrant.ScrollPoints{
			CollectionName: collection,
			Filter:         filter,
			Limit:          qdrant.PtrOf(uint32(min(500, limit-len(allResults)))),
			Offset:         offset,
			WithPayload:    qdrant.NewWithPayload(true),
			WithVectors:    qdrant.NewWithVectorsEnable(false),
		})
		if err != nil {
			return nil, fmt.Errorf("scroll %s: %w", collection, err)
		}

		for _, p := range scrollResp.GetResult() {
			allResults = append(allResults, models.SearchResult{
				ID:      p.GetId().GetUuid(),
				Score:   0,
				Payload: fromQdrantPayload(p.GetPayload()),
			})
		}

		offset = scrollResp.GetNextPageOffset()
		if offset == nil || len(allResults) >= limit {
			break
		}
	}

	// Sort by chunk_order
	sort.Slice(allResults, func(i, j int) bool {
		orderI, _ := allResults[i].Payload["chunk_order"].(float64)
		orderJ, _ := allResults[j].Payload["chunk_order"].(float64)
		return orderI < orderJ
	})

	return allResults, nil
}

// Healthy checks if Qdrant is reachable.
func (q *QdrantStore) Healthy(ctx context.Context) bool {
	_, err := q.client.HealthCheck(ctx)
	return err == nil
}

// Close closes the Qdrant client connection.
func (q *QdrantStore) Close() error {
	return q.client.Close()
}

// Helper functions

func toQdrantValue(v any) *qdrant.Value {
	switch val := v.(type) {
	case string:
		return &qdrant.Value{Kind: &qdrant.Value_StringValue{StringValue: val}}
	case int:
		return &qdrant.Value{Kind: &qdrant.Value_IntegerValue{IntegerValue: int64(val)}}
	case int64:
		return &qdrant.Value{Kind: &qdrant.Value_IntegerValue{IntegerValue: val}}
	case float64:
		return &qdrant.Value{Kind: &qdrant.Value_DoubleValue{DoubleValue: val}}
	case float32:
		return &qdrant.Value{Kind: &qdrant.Value_DoubleValue{DoubleValue: float64(val)}}
	case bool:
		return &qdrant.Value{Kind: &qdrant.Value_BoolValue{BoolValue: val}}
	case []string:
		vals := make([]*qdrant.Value, len(val))
		for i, s := range val {
			vals[i] = &qdrant.Value{Kind: &qdrant.Value_StringValue{StringValue: s}}
		}
		return &qdrant.Value{Kind: &qdrant.Value_ListValue{ListValue: &qdrant.ListValue{Values: vals}}}
	case []int:
		vals := make([]*qdrant.Value, len(val))
		for i, n := range val {
			vals[i] = &qdrant.Value{Kind: &qdrant.Value_IntegerValue{IntegerValue: int64(n)}}
		}
		return &qdrant.Value{Kind: &qdrant.Value_ListValue{ListValue: &qdrant.ListValue{Values: vals}}}
	default:
		return &qdrant.Value{Kind: &qdrant.Value_NullValue{}}
	}
}

func fromQdrantPayload(payload map[string]*qdrant.Value) map[string]any {
	out := make(map[string]any)
	for k, v := range payload {
		out[k] = fromQdrantValue(v)
	}
	return out
}

func fromQdrantValue(v *qdrant.Value) any {
	if v == nil {
		return nil
	}
	switch val := v.Kind.(type) {
	case *qdrant.Value_StringValue:
		return val.StringValue
	case *qdrant.Value_IntegerValue:
		return float64(val.IntegerValue) // JSON numbers are float64
	case *qdrant.Value_DoubleValue:
		return val.DoubleValue
	case *qdrant.Value_BoolValue:
		return val.BoolValue
	case *qdrant.Value_ListValue:
		list := make([]any, len(val.ListValue.Values))
		for i, item := range val.ListValue.Values {
			list[i] = fromQdrantValue(item)
		}
		return list
	case *qdrant.Value_StructValue:
		m := make(map[string]any)
		for k, item := range val.StructValue.Fields {
			m[k] = fromQdrantValue(item)
		}
		return m
	default:
		return nil
	}
}

func toInt64Slice(ints []int) []int64 {
	out := make([]int64, len(ints))
	for i, n := range ints {
		out[i] = int64(n)
	}
	return out
}
