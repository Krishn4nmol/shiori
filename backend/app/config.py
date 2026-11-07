from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """App settings, read from environment variables or the repo-root .env file."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "Shiori"
    environment: Literal["dev", "test", "prod"] = "dev"

    database_url: str = "postgresql+asyncpg://shiori:shiori@127.0.0.1:5432/shiori"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # LLM provider: "anthropic" (Anthropic API) or "ollama" (free, local)
    llm_provider: Literal["anthropic", "ollama"] = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    # How hard the model thinks before answering: low | medium | high (Anthropic only)
    llm_effort: Literal["low", "medium", "high"] = "medium"
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Embeddings run locally on CPU. embedding_dim must match the model (bge-m3: 1024)
    # and the vector column in the database.
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 8
    embedding_max_seq_length: int = 512

    # Retrieval
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_enabled: bool = True
    search_candidates: int = 40  # per method (vector, keyword) before fusion
    # Top fused results the reranker re-scores. On a laptop CPU each costs ~0.15 s.
    rerank_candidates: int = 12
    # bfloat16 halves reranking time on modern CPUs with the same ranking quality.
    reranker_bf16: bool = True
    # Reranker scores below this mean "does not answer the question". Measured on real
    # queries: right answers scored 0.05-0.8, unrelated passages below 0.001.
    min_rerank_score: float = 0.01
    preload_models: bool = True  # load models at API startup instead of on first search

    # Ingestion
    storage_dir: Path = REPO_ROOT / "data" / "uploads"
    max_upload_mb: int = 50
    chunk_target_tokens: int = 350
    chunk_overlap_tokens: int = 50

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
