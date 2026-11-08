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

    # LLM provider:
    #   "openai"    any OpenAI-compatible API: Google Gemini (free key), Groq, OpenRouter,
    #               or local Ollama. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL.
    #   "anthropic" Anthropic API (paid)
    #   "ollama"    local Ollama through its native API
    # Default: Google Gemini's free tier.
    llm_provider: Literal["openai", "anthropic", "ollama"] = "openai"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_api_key: str | None = None
    llm_model: str = "gemini-3.5-flash-lite"
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
    # Reranker scores below this mean "does not answer the question". Chosen by evaluation:
    # at 0.01 one real answer (a sentence buried in a long Japanese passage, scored 0.0005) was
    # cut; 0.0003 kept every answer (correctness 89% -> 100%) while unrelated questions such
    # as "What is the weather in Osaka?" (~0.00002) are still rejected without an LLM call.
    min_rerank_score: float = 0.0003
    # "agent": plan each question (details / search / read whole paper), see app/agent.
    # "classic": always one search. Both are kept so evaluation can compare them.
    answer_mode: Literal["agent", "classic"] = "agent"
    preload_models: bool = True  # load models at API startup instead of on first search

    # Ingestion
    storage_dir: Path = REPO_ROOT / "data" / "uploads"
    max_upload_mb: int = 50
    chunk_target_tokens: int = 350
    chunk_overlap_tokens: int = 50

    # Knowledge graph (Neo4j). Disable to run without it; the agent then skips graph questions.
    graph_enabled: bool = True
    neo4j_url: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "shiori-graph"
    graph_batch_passages: int = 4  # passages per extraction call (fewer calls on free tiers)

    # Auth. SECRET_KEY signs session cookies: keep it secret, and stable across restarts
    # (changing it logs everyone out). Generate one with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    secret_key: str | None = None
    session_days: int = 7
    cookie_secure: bool = False  # set true when served over HTTPS

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
