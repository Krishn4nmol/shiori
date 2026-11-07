from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or the repo-root .env file."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "Shiori"
    environment: Literal["dev", "test", "prod"] = "dev"

    database_url: str = "postgresql+asyncpg://shiori:shiori@localhost:5432/shiori"
    redis_url: str = "redis://localhost:6379/0"

    # LLM provider: "anthropic" (Anthropic API) or "ollama" (free, local)
    llm_provider: Literal["anthropic", "ollama"] = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    # How hard the model thinks before answering: low | medium | high (Anthropic only)
    llm_effort: Literal["low", "medium", "high"] = "medium"
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Embeddings run locally on CPU
    embedding_model: str = "BAAI/bge-m3"

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
