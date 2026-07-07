"""Worker configuration.

All env vars are unprefixed (env_prefix=""). Field names map directly to
upper-case env var names.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    brain_api_url: str = Field(default="http://localhost:8020")
    brain_api_key: str = Field(default="", validation_alias="BRAIN_API_KEY")
    max_file_size_mb: int = Field(default=50)
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")

    # Model used for OpenAI vision OCR (the "ai" translation method).
    openai_ocr_model: str = Field(default="gpt-4.1-mini", validation_alias="OPENAI_OCR_MODEL")

    # Extraction (OCR / vision) is slow and, for the AI path, paid — give it a
    # dedicated timeout separate from the brain-api POST timeout.
    extraction_timeout_seconds: int = Field(default=600, validation_alias="EXTRACTION_TIMEOUT_SECONDS")

    # brain-api ingest is asynchronous: the worker SUBMITS a document (fast) then
    # POLLS for the outcome, so a long background pipeline (a whole book can take
    # tens of minutes) is never gated by a single long-held HTTP request.
    # accept: timeout for the submit + each status poll (both are quick calls).
    brain_ingest_accept_timeout_seconds: int = Field(
        default=60, validation_alias="BRAIN_INGEST_ACCEPT_TIMEOUT_SECONDS"
    )
    # poll_interval: seconds between status polls.
    brain_ingest_poll_interval_seconds: int = Field(
        default=5, validation_alias="BRAIN_INGEST_POLL_INTERVAL_SECONDS"
    )
    # max_wait: hard ceiling on total time spent waiting for one ingest before
    # giving up and reporting FAILED (a backstop against a wedged background job).
    brain_ingest_max_wait_seconds: int = Field(
        default=5400, validation_alias="BRAIN_INGEST_MAX_WAIT_SECONDS"
    )

    # Hard cap on PDF pages rendered/OCR'd per document. Bounds memory + cost
    # (and, for the AI path, per-page billing) so a pathological many-page PDF
    # can't OOM a worker or run up a huge bill. Pages beyond the cap are skipped
    # with a warning (never silently).
    max_ocr_pages: int = Field(default=100, validation_alias="MAX_OCR_PAGES")

    # Object storage (MinIO / S3-compatible) for uploaded originals. Same
    # unprefixed STORAGE_* env vars the API reads, so both point at one bucket.
    storage_endpoint: str = Field(default="http://localhost:9000", validation_alias="STORAGE_ENDPOINT")
    storage_access_key: str = Field(default="", validation_alias="STORAGE_ACCESS_KEY")
    storage_secret_key: str = Field(default="", validation_alias="STORAGE_SECRET_KEY")
    storage_bucket: str = Field(default="km-documents", validation_alias="STORAGE_BUCKET")
    storage_region: str = Field(default="us-east-1", validation_alias="STORAGE_REGION")

    # Callback to the api service for processing_status updates.
    # api_url is where the worker POSTs status; internal_api_key
    # authenticates the request against the api service's internal router.
    api_url: str = Field(default="http://localhost:8000", validation_alias="API_URL")
    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")
