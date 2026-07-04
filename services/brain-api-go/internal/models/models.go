// Package models defines shared domain types for the brain-api-go service.
package models

// VectorRecord is a document or chunk payload stored in the vector database.
type VectorRecord struct {
	ID      string
	Vector  []float32
	Payload map[string]any
}

// SearchResult is a single result returned by a vector similarity search.
type SearchResult struct {
	ID      string
	Score   float32
	Payload map[string]any
}

// Triplet is a subject–predicate–object triple stored in the knowledge graph.
type Triplet struct {
	Subject   string
	Predicate string
	Object    string
}

// Entity is a named node in the knowledge graph.
type Entity struct {
	Name string
}
