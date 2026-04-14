"""OpenAI API configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", env_file=".env", extra="ignore")

    api_key: SecretStr = Field(description="OpenAI API key")
    chat_model: str = Field(default="gpt-5-mini", description="Chat completion model")
    embedding_model: str = Field(
        default="text-embedding-3-small", description="Embedding model"
    )
