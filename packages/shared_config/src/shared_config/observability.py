"""Observability configuration (logging, tracing, metrics)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    otel_exporter_otlp_endpoint: str = Field(
        default="",
        description="OpenTelemetry OTLP endpoint (empty = disabled)",
    )
    otel_service_name: str = Field(
        default="red-arch-km",
        description="Service name for traces and metrics",
    )
