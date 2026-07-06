"""Brain API configuration.

Fields in the BRAIN_ namespace (api_key, collection suffixes, max_tokens)
use the env_prefix; fields that map to shared infrastructure env vars
(OpenAI, Qdrant, Neo4j, log level) use explicit validation_alias.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrainAPISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BRAIN_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # Service-scoped config (BRAIN_*)
    api_key: str = Field(description="Service-to-service API key")
    chunk_collection_suffix: str = Field(default="chunks")
    document_collection_suffix: str = Field(default="documents")
    max_tokens: int = Field(default=16000)
    debug: bool = Field(default=False)

    # Shared infrastructure (unprefixed)
    qdrant_url: str = Field(default="http://localhost:6333", validation_alias="QDRANT_URL")
    qdrant_api_key: str = Field(default="", validation_alias="QDRANT_API_KEY")
    neo4j_uri: str = Field(default="bolt://localhost:7687", validation_alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", validation_alias="NEO4J_USER")
    neo4j_password: str = Field(default="", validation_alias="NEO4J_PASSWORD")

    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_chat_model: str = Field(default="gpt-5-mini", validation_alias="OPENAI_CHAT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="OPENAI_EMBEDDING_MODEL",
    )

    # Agentic fact engine (provider-agnostic; OpenAI is the default provider).
    # `use_fact_engine` gates the new reified-claim ingest + agentic query path;
    # the legacy top-K RAG path stays available regardless.
    use_fact_engine: bool = Field(default=False, validation_alias="USE_FACT_ENGINE")
    llm_provider: str = Field(default="openai", validation_alias="LLM_PROVIDER")
    agent_model: str = Field(default="", validation_alias="AGENT_MODEL")
    agent_api_key: str = Field(default="", validation_alias="AGENT_API_KEY")
    agent_max_iterations: int = Field(default=6, validation_alias="AGENT_MAX_ITERATIONS")

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @property
    def resolved_agent_model(self) -> str:
        """Agent/extraction model, falling back to the chat model."""
        return self.agent_model or self.openai_chat_model

    @property
    def resolved_agent_api_key(self) -> str:
        """Provider key for the agent LLM, falling back to the OpenAI key."""
        return self.agent_api_key or self.openai_api_key
