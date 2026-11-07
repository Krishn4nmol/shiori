"""LLM layer: one interface, two providers (Anthropic API or local Ollama)."""

from app.llm.base import LLMClient, LLMError, LLMResult, Message
from app.llm.factory import get_llm

__all__ = ["LLMClient", "LLMError", "LLMResult", "Message", "get_llm"]
