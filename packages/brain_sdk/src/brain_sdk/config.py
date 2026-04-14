"""Brain SDK configuration via Pydantic Settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrainSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BRAIN_", env_file=".env", extra="ignore")

    # Qdrant
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str = Field(default="", description="Optional Qdrant Cloud API key")

    # Neo4j
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="")

    # Collections
    chunk_collection_suffix: str = Field(default="chunks")
    document_collection_suffix: str = Field(default="documents")

    # Processing
    max_tokens: int = Field(default=16000)
    default_chunk_size: int = Field(default=500)
    default_overlap: int = Field(default=20)

    # API key for service-to-service auth
    api_key: str = Field(default="", description="Shared secret for brain-api auth")
