from functools import lru_cache

from app.config import get_settings
from app.llm.anthropic_client import AnthropicLLM
from app.llm.base import LLMClient
from app.llm.ollama_client import OllamaLLM
from app.llm.openai_compat_client import OpenAICompatLLM


@lru_cache
def get_llm() -> LLMClient:
    """The configured LLM. Switch with LLM_PROVIDER / LLM_MODEL / LLM_EFFORT in .env."""
    settings = get_settings()
    if settings.llm_provider == "openai":
        return OpenAICompatLLM(
            model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key
        )
    if settings.llm_provider == "ollama":
        return OllamaLLM(model=settings.llm_model, base_url=settings.ollama_base_url)
    return AnthropicLLM(
        model=settings.llm_model,
        effort=settings.llm_effort,
        api_key=settings.anthropic_api_key,
    )
