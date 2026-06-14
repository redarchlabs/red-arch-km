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
	QdrantURL        string
	QdrantCollection string

	// Neo4j graph database
	Neo4jURI      string
	Neo4jUser     string
	Neo4jPassword string

	// OpenAI for embeddings
	OpenAIAPIKey string

	// API authentication
	APIKey string
}

// Load loads configuration from environment variables.
func Load() Config {
	base := config.LoadBaseConfig()

	return Config{
		BaseConfig: base,

		Port: config.GetEnvInt("BRAIN_API_PORT", 8020),

		QdrantURL:        config.GetEnv("QDRANT_URL", "http://localhost:6333"),
		QdrantCollection: config.GetEnv("QDRANT_COLLECTION", "documents"),

		Neo4jURI:      config.GetEnv("NEO4J_URI", "bolt://localhost:7687"),
		Neo4jUser:     config.GetEnv("NEO4J_USER", "neo4j"),
		Neo4jPassword: config.GetEnv("NEO4J_PASSWORD", ""),

		OpenAIAPIKey: config.GetEnv("OPENAI_API_KEY", ""),

		APIKey: config.GetEnv("BRAIN_API_KEY", ""),
	}
}
