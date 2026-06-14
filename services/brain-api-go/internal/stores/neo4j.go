package stores

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"strings"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
)

const (
	labelEntity   = "Entity"
	relType       = "REL"
	propName      = "name"
	propDocKey    = "document_key"
	propTags      = "tags"
	propAccessKey = "access_keys"
	propType      = "type"
)

var nonAlphanumericRe = regexp.MustCompile(`[^A-Za-z0-9_]`)

// Neo4jStore wraps the Neo4j driver for graph operations.
type Neo4jStore struct {
	driver neo4j.DriverWithContext
	db     string
}

// NewNeo4jStore creates a new Neo4jStore.
func NewNeo4jStore(uri, user, password string) (*Neo4jStore, error) {
	driver, err := neo4j.NewDriverWithContext(uri, neo4j.BasicAuth(user, password, ""))
	if err != nil {
		return nil, fmt.Errorf("create neo4j driver: %w", err)
	}

	return &Neo4jStore{
		driver: driver,
		db:     "", // use default database
	}, nil
}

func (n *Neo4jStore) tenantLabels(tenantID string) string {
	safe := nonAlphanumericRe.ReplaceAllString(tenantID, "_")
	return fmt.Sprintf(":%s:Tenant_%s", labelEntity, safe)
}

func (n *Neo4jStore) cypher(ctx context.Context, query string, params map[string]any) ([]map[string]any, error) {
	session := n.driver.NewSession(ctx, neo4j.SessionConfig{DatabaseName: n.db})
	defer session.Close(ctx)

	result, err := session.Run(ctx, query, params)
	if err != nil {
		return nil, err
	}

	var records []map[string]any
	for result.Next(ctx) {
		records = append(records, result.Record().AsMap())
	}

	if err := result.Err(); err != nil {
		return nil, err
	}

	return records, nil
}

// InitializeTenant creates the tenant label structure in Neo4j.
func (n *Neo4jStore) InitializeTenant(ctx context.Context, tenantID string) error {
	lbls := n.tenantLabels(tenantID)

	// Create temporary nodes with relationships to ensure label exists, then delete them
	query := fmt.Sprintf(`
		MERGE (a%s {_init: true})
		SET a.%s = [], a.%s = [], a.%s = ''
		MERGE (b%s {_init_b: true})
		SET b.%s = [], b.%s = [], b.%s = ''
		MERGE (a)-[r:%s]->(b)
		SET r.%s = ''
		WITH a, b, r
		DETACH DELETE a, b
	`, lbls, propAccessKey, propTags, propName, lbls, propAccessKey, propTags, propName, relType, propType)

	_, err := n.cypher(ctx, query, nil)
	if err != nil {
		return fmt.Errorf("initialize tenant %s: %w", tenantID, err)
	}

	slog.Info("initialized Neo4j tenant", "tenant", tenantID)
	return nil
}

// InsertTriplets batch-inserts triplets using UNWIND for efficiency.
func (n *Neo4jStore) InsertTriplets(ctx context.Context, tenantID string, triplets []models.Triplet, documentKey string, tags []string, accessKeys []int) error {
	// Filter out empty triplets
	var clean []map[string]string
	for _, t := range triplets {
		if t.Subject != "" && t.Predicate != "" && t.Object != "" {
			clean = append(clean, map[string]string{
				"subj": t.Subject,
				"pred": t.Predicate,
				"obj":  t.Object,
			})
		}
	}
	if len(clean) == 0 {
		return nil
	}

	lbls := n.tenantLabels(tenantID)

	query := fmt.Sprintf(`
UNWIND $triplets AS t
MERGE (s%s {%s: t.subj})
  ON CREATE SET s.%s = $tags, s.%s = $access_keys
  ON MATCH  SET s.%s = apoc.coll.toSet(coalesce(s.%s, []) + $tags),
                s.%s = apoc.coll.toSet(coalesce(s.%s, []) + $access_keys)
MERGE (o%s {%s: t.obj})
  ON CREATE SET o.%s = $tags, o.%s = $access_keys
  ON MATCH  SET o.%s = apoc.coll.toSet(coalesce(o.%s, []) + $tags),
                o.%s = apoc.coll.toSet(coalesce(o.%s, []) + $access_keys)
FOREACH (_ IN CASE WHEN $dk IS NOT NULL THEN [1] ELSE [] END |
    SET s.%s = $dk, o.%s = $dk
)
MERGE (s)-[r:%s {%s: t.pred}]->(o)
SET r.tenant_id = $tid
FOREACH (_ IN CASE WHEN $dk IS NOT NULL THEN [1] ELSE [] END |
    SET r.%s = $dk
)
`, lbls, propName, propTags, propAccessKey, propTags, propTags, propAccessKey, propAccessKey,
		lbls, propName, propTags, propAccessKey, propTags, propTags, propAccessKey, propAccessKey,
		propDocKey, propDocKey, relType, propType, propDocKey)

	params := map[string]any{
		"triplets":    clean,
		"tags":        tags,
		"access_keys": accessKeys,
		"dk":          documentKey,
		"tid":         tenantID,
	}

	_, err := n.cypher(ctx, query, params)
	if err != nil {
		return fmt.Errorf("insert triplets: %w", err)
	}

	return nil
}

// FuzzyEntitySearch finds entities matching a search term.
func (n *Neo4jStore) FuzzyEntitySearch(ctx context.Context, tenantID, term string, tags []string, userAccess []int) ([]models.Entity, error) {
	lbls := n.tenantLabels(tenantID)
	params := map[string]any{"term": strings.ToLower(term)}

	var conditions []string
	conditions = append(conditions, fmt.Sprintf("toLower(e.%s) CONTAINS $term", propName))

	if len(tags) > 0 {
		conditions = append(conditions, fmt.Sprintf("any(t IN $tags WHERE t IN e.%s)", propTags))
		params["tags"] = tags
	}

	if len(userAccess) > 0 {
		conditions = append(conditions, fmt.Sprintf(
			"(size(e.%s) = 0 OR size([k IN e.%s WHERE k IN $keys]) > 0)",
			propAccessKey, propAccessKey))
		params["keys"] = userAccess
	}

	query := fmt.Sprintf(`
MATCH (e%s)
WHERE %s
RETURN e.%s AS name
ORDER BY name
`, lbls, strings.Join(conditions, " AND "), propName)

	records, err := n.cypher(ctx, query, params)
	if err != nil {
		return nil, fmt.Errorf("entity search: %w", err)
	}

	entities := make([]models.Entity, 0, len(records))
	for _, r := range records {
		if name, ok := r["name"].(string); ok {
			entities = append(entities, models.Entity{Name: name})
		}
	}

	return entities, nil
}

// FuzzyRelationshipSearch finds triplets matching a search term.
func (n *Neo4jStore) FuzzyRelationshipSearch(ctx context.Context, tenantID, term string, tags []string, userAccess []int) ([]models.Triplet, error) {
	// Get all triplets then filter in Go (matching Python behavior)
	allTriplets, err := n.getAllTriplets(ctx, tenantID, tags, userAccess)
	if err != nil {
		return nil, err
	}

	termLower := strings.ToLower(term)
	var matched []models.Triplet
	for _, t := range allTriplets {
		if strings.Contains(strings.ToLower(t.Subject), termLower) ||
			strings.Contains(strings.ToLower(t.Predicate), termLower) ||
			strings.Contains(strings.ToLower(t.Object), termLower) {
			matched = append(matched, t)
		}
	}

	return matched, nil
}

func (n *Neo4jStore) getAllTriplets(ctx context.Context, tenantID string, tags []string, userAccess []int) ([]models.Triplet, error) {
	lbls := n.tenantLabels(tenantID)
	params := map[string]any{}

	var conditions []string

	if len(tags) > 0 {
		conditions = append(conditions, fmt.Sprintf(
			"(any(t IN $tags WHERE t IN s.%s) OR any(t IN $tags WHERE t IN o.%s))",
			propTags, propTags))
		params["tags"] = tags
	}

	if len(userAccess) > 0 {
		conditions = append(conditions, fmt.Sprintf(
			"((size(s.%s) = 0 OR size([k IN s.%s WHERE k IN $keys]) > 0) OR (size(o.%s) = 0 OR size([k IN o.%s WHERE k IN $keys]) > 0))",
			propAccessKey, propAccessKey, propAccessKey, propAccessKey))
		params["keys"] = userAccess
	}

	whereClause := ""
	if len(conditions) > 0 {
		whereClause = "WHERE " + strings.Join(conditions, " AND ")
	}

	query := fmt.Sprintf(`
MATCH (s%s)-[r:%s]->(o%s)
WITH s, r, o
%s
RETURN s.%s AS subj, r.%s AS pred, o.%s AS obj
ORDER BY subj, pred, obj
`, lbls, relType, lbls, whereClause, propName, propType, propName)

	records, err := n.cypher(ctx, query, params)
	if err != nil {
		return nil, fmt.Errorf("get all triplets: %w", err)
	}

	triplets := make([]models.Triplet, 0, len(records))
	for _, r := range records {
		subj, _ := r["subj"].(string)
		pred, _ := r["pred"].(string)
		obj, _ := r["obj"].(string)
		triplets = append(triplets, models.Triplet{
			Subject:   subj,
			Predicate: pred,
			Object:    obj,
		})
	}

	return triplets, nil
}

// DeleteByDocumentKey removes all nodes and relationships for a document.
func (n *Neo4jStore) DeleteByDocumentKey(ctx context.Context, tenantID, documentKey string) error {
	lbls := n.tenantLabels(tenantID)
	params := map[string]any{"dk": documentKey}

	// Delete relationships first
	relQuery := fmt.Sprintf(`
MATCH (n%s)-[r:%s]-(m%s)
WHERE r.%s = $dk
DELETE r
`, lbls, relType, lbls, propDocKey)

	_, err := n.cypher(ctx, relQuery, params)
	if err != nil {
		return fmt.Errorf("delete relationships: %w", err)
	}

	// Delete orphaned nodes
	nodeQuery := fmt.Sprintf(`
MATCH (n%s)
WHERE n.%s = $dk
DETACH DELETE n
`, lbls, propDocKey)

	_, err = n.cypher(ctx, nodeQuery, params)
	if err != nil {
		return fmt.Errorf("delete nodes: %w", err)
	}

	slog.Info("deleted graph data for document", "tenant", tenantID, "document", documentKey)
	return nil
}

// UpdateMetadata updates tags/access_keys on nodes for a document.
func (n *Neo4jStore) UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int) error {
	lbls := n.tenantLabels(tenantID)
	params := map[string]any{"dk": documentKey}

	var setClauses []string
	if tags != nil {
		setClauses = append(setClauses, fmt.Sprintf("v.%s = $tags", propTags))
		params["tags"] = tags
	}
	if accessKeys != nil {
		setClauses = append(setClauses, fmt.Sprintf("v.%s = $access_keys", propAccessKey))
		params["access_keys"] = accessKeys
	}

	if len(setClauses) == 0 {
		return nil
	}

	query := fmt.Sprintf(`
MATCH (v%s)
WHERE v.%s = $dk
SET %s
RETURN count(v) AS updated
`, lbls, propDocKey, strings.Join(setClauses, ", "))

	records, err := n.cypher(ctx, query, params)
	if err != nil {
		return fmt.Errorf("update metadata: %w", err)
	}

	count := int64(0)
	if len(records) > 0 {
		if c, ok := records[0]["updated"].(int64); ok {
			count = c
		}
	}
	slog.Info("updated Neo4j nodes", "count", count, "document", documentKey)

	return nil
}

// DeleteTenant removes all nodes with the tenant's label.
func (n *Neo4jStore) DeleteTenant(ctx context.Context, tenantID string) error {
	lbls := n.tenantLabels(tenantID)

	// Count first
	countQuery := fmt.Sprintf(`MATCH (n%s) RETURN count(n) AS c`, lbls)
	records, err := n.cypher(ctx, countQuery, nil)
	if err != nil {
		return fmt.Errorf("count tenant nodes: %w", err)
	}

	count := int64(0)
	if len(records) > 0 {
		if c, ok := records[0]["c"].(int64); ok {
			count = c
		}
	}

	if count > 0 {
		deleteQuery := fmt.Sprintf(`MATCH (n%s) DETACH DELETE n`, lbls)
		_, err = n.cypher(ctx, deleteQuery, nil)
		if err != nil {
			return fmt.Errorf("delete tenant nodes: %w", err)
		}
	}

	slog.Info("deleted Neo4j tenant", "tenant", tenantID, "nodes", count)
	return nil
}

// Healthy checks if Neo4j is reachable.
func (n *Neo4jStore) Healthy(ctx context.Context) bool {
	err := n.driver.VerifyConnectivity(ctx)
	return err == nil
}

// Close closes the Neo4j driver.
func (n *Neo4jStore) Close(ctx context.Context) error {
	return n.driver.Close(ctx)
}
