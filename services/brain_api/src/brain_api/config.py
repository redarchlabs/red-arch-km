"""Brain API configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrainAPISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BRAIN_", env_file=".env", extra="ignore")

    api_key: str = Field(description="Service-to-service API key")
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str = Field(default="")
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="")
    chunk_collection_suffix: str = Field(default="chunks")
    document_collection_suffix: str = Field(default="documents")
    max_tokens: int = Field(default=16000)
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO", json_schema_extra={"env": "LOG_LEVEL"})

    # OpenAI
    openai_api_key: str = Field(default="", json_schema_extra={"env": "OPENAI_API_KEY"})
    openai_chat_model: str = Field(default="gpt-4.1-mini", json_schema_extra={"env": "OPENAI_CHAT_MODEL"})
    openai_embedding_model: str = Field(default="text-embedding-3-small", json_schema_extra={"env": "OPENAI_EMBEDDING_MODEL"})
