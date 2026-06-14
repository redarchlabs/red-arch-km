// Package config provides Brain API service configuration.
package config

import (
	"github.com/redarchlabs/red-arch-km-2/packages/shared/config"
)

// Config holds Brain API service configuration.
type Config struct {
	config.BaseConfig

	// Server settings
	Port int

	// Qdrant vector store
	QdrantURL              string
	QdrantAPIKey           string
	ChunkCollectionSuffix  string
	DocCollectionSuffix    string

	// Neo4j graph database
	Neo4jURI      string
	Neo4jUser     string
	Neo4jPassword string

	// OpenAI
	OpenAIAPIKey       string
	OpenAIEmbeddingModel string
	OpenAIChatModel    string

	// API authentication
	APIKey string

	// Pipeline settings
	ChunkSize    int
	ChunkOverlap int
}

// Load loads configuration from environment variables.
func Load() Config {
	base := config.LoadBaseConfig()

	return Config{
		BaseConfig: base,

		Port: config.GetEnvInt("BRAIN_API_PORT", 8020),

		QdrantURL:             config.GetEnv("QDRANT_URL", "http://localhost:6333"),
		QdrantAPIKey:          config.GetEnv("QDRANT_API_KEY", ""),
		ChunkCollectionSuffix: config.GetEnv("CHUNK_COLLECTION_SUFFIX", "chunks"),
		DocCollectionSuffix:   config.GetEnv("DOCUMENT_COLLECTION_SUFFIX", "documents"),

		Neo4jURI:      config.GetEnv("NEO4J_URI", "bolt://localhost:7687"),
		Neo4jUser:     config.GetEnv("NEO4J_USER", "neo4j"),
		Neo4jPassword: config.GetEnv("NEO4J_PASSWORD", ""),

		OpenAIAPIKey:         config.GetEnv("OPENAI_API_KEY", ""),
		OpenAIEmbeddingModel: config.GetEnv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
		OpenAIChatModel:      config.GetEnv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),

		APIKey: config.GetEnv("BRAIN_API_KEY", ""),

		ChunkSize:    config.GetEnvInt("CHUNK_SIZE", 1000),
		ChunkOverlap: config.GetEnvInt("CHUNK_OVERLAP", 200),
	}
}
