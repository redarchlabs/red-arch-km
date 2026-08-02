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
    # Point the OpenAI SDK at an OpenAI-compatible server (Ollama, vLLM, llama.cpp)
    # instead of api.openai.com. Empty (the default) keeps the hosted behaviour, so
    # existing deployments are unaffected; a fully-local instance sets it.
    #
    # Covers every **chat** call: SearchService (the robot's live path) plus ingest-time
    # chat in ChunkSummarizer and TripletExtractor. Embeddings are NOT covered — they
    # have their own setting below, because one llama.cpp server cannot serve both chat
    # and embeddings, so the two genuinely need different endpoints.
    #
    # NB this name matches an environment variable the OpenAI SDK itself honours. Every
    # client in this codebase now passes base_url explicitly so that collision cannot
    # silently redirect a call we did not mean to redirect — which is exactly how
    # embeddings once ended up at a chat-only server, failing every ingest with
    # "501 This server does not support embeddings".
    openai_base_url: str = Field(default="", validation_alias="OPENAI_BASE_URL")
    # Per-model endpoint routes ("model=url" pairs, comma/whitespace separated).
    # A model id with a route reaches that endpoint instead of openai_base_url.
    # This is what makes a per-org model pin (orgs.default_llm_model, threaded in
    # on the chat request) actually land on the right server — local llama.cpp
    # for one org, hosted OpenAI for another. Same format as the API service.
    openai_model_routes: str = Field(default="", validation_alias="OPENAI_MODEL_ROUTES")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="OPENAI_EMBEDDING_MODEL",
    )
    # Separate endpoint for embeddings. Empty => OpenAI.
    embedding_base_url: str = Field(default="", validation_alias="EMBEDDING_BASE_URL")
    # Vector width of the embedding model. Required when embedding_base_url is set,
    # since dimensions are only known for OpenAI's own models. Changing this is a
    # MIGRATION, not a config flip: the Qdrant collections and the Neo4j vector index
    # are created at this width, so every stored vector must be re-embedded.
    embedding_dimension: int = Field(default=0, validation_alias="EMBEDDING_DIMENSION")

    # --- reranking ------------------------------------------------------
    # Cross-encoder re-scoring of the dense shortlist. Dense search embeds query
    # and passage separately, so it ranks on topical similarity and misses
    # paraphrase: "how many people can each ship handle" did not retrieve
    # "Standard Crew Complement: 5,500 officers and crew" from the very document
    # that answers it. A cross-encoder reads the pair together and scores it.
    #
    # Empty URL = off, and retrieval behaves exactly as it did before — the same
    # convention as openai_base_url/embedding_base_url. Any Cohere-shaped
    # /rerank endpoint works: llama.cpp --reranking (local), TEI, Jina, Cohere.
    # OpenAI has no rerank API, so a hosted-OpenAI deployment leaves this unset.
    rerank_base_url: str = Field(default="", validation_alias="RERANK_BASE_URL")
    rerank_model: str = Field(default="", validation_alias="RERANK_MODEL")
    rerank_api_key: str = Field(default="", validation_alias="RERANK_API_KEY")
    # How many dense hits are scored before the top `limit` are kept. This is the
    # whole point of the feature: the passage that answers the question has to be
    # IN the shortlist for reranking to promote it, and the ones that were missed
    # sat well below the old top-5. Raising it costs one forward pass each.
    rerank_candidates: int = Field(default=30, validation_alias="RERANK_CANDIDATES")
    rerank_timeout: float = Field(default=20.0, validation_alias="RERANK_TIMEOUT")

    # How many RANKED passages a chat answer is grounded in. Sibling expansion adds
    # more on top (see _expand_top_documents), so this is not the size of the prompt.
    #
    # Was 5, which is too tight for an "each X" question: the passages answering
    # "how many people can each ship handle" live in two different documents, and five
    # slots let the best-ranked one take them all.
    #
    # NOT free to raise without limit, and the ceiling is a QUALITY one, not latency.
    # Measured on the Robots corpus, two runs per setting: at 10, "what ships do we
    # have" reliably gained a seventh, non-existent ship — the 10th slot admitted a
    # template document ("Ship name: **Starship Horizon** (change to your simulator's
    # ship)"), which the model then read as fact. 8 and 9 both answer correctly. 8 is
    # the default rather than 9 to keep a slot of margin below that cliff instead of
    # sitting exactly on one corpus's edge.
    #
    # Latency did NOT drive this choice: alternating runs measured 10 as FASTER than 5
    # (14.2s/7.1s vs 18.4s/10.2s). Wall-clock tracks generation length and llama.cpp's
    # prompt cache, not the ranked-hit count — the prompt is dominated by the ~18
    # chunks same-document expansion adds.
    chat_chunk_limit: int = Field(default=8, validation_alias="CHAT_CHUNK_LIMIT")

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
