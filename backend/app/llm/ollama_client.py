import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import LLMError, LLMResult, Message


class OllamaLLM:
    """A local model served by Ollama: free, but slow on a CPU-only laptop."""

    provider = "ollama"

    def __init__(self, *, model: str, base_url: str) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")

    def _body(
        self, system: str, messages: list[Message], max_tokens: int, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": stream,
            "options": {"num_predict": max_tokens},
        }

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult:
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                res = await client.post(
                    f"{self._base_url}/api/chat",
                    json=self._body(system, messages, max_tokens, stream=False),
                )
                res.raise_for_status()
        except httpx.HTTPError as err:
            raise LLMError(f"Ollama request failed: {err}", retryable=True) from err

        data = res.json()
        return LLMResult(
            text=data["message"]["content"],
            model=self.model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            stop_reason=data.get("done_reason"),
        )

    async def stream(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> AsyncIterator[str]:
        try:
            async with (
                httpx.AsyncClient(timeout=300) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=self._body(system, messages, max_tokens, stream=True),
                ) as res,
            ):
                res.raise_for_status()
                async for line in res.aiter_lines():
                    if line and (text := json.loads(line).get("message", {}).get("content")):
                        yield text
        except httpx.HTTPError as err:
            raise LLMError(f"Ollama request failed: {err}", retryable=True) from err
