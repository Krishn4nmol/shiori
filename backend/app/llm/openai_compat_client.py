"""Any provider that speaks the OpenAI chat-completions format.

One client covers the free options: Google Gemini (free API key), Groq, OpenRouter, and a
local Ollama server. Only LLM_BASE_URL, LLM_API_KEY and LLM_MODEL change between them.
"""

import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import openai

from app.llm.base import LLMError, LLMResult, Message

# Some open models (Qwen, DeepSeek) write their reasoning inline before the answer.
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class OpenAICompatLLM:
    provider = "openai"

    def __init__(self, *, model: str, base_url: str, api_key: str | None) -> None:
        self.model = model
        host = urlparse(base_url).hostname or ""
        # Cloud APIs need a key; local servers (Ollama) ignore it, but the SDK requires one.
        self._missing_key = not api_key and host not in ("localhost", "127.0.0.1", "::1")
        self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")

    def _check_key(self) -> None:
        if self._missing_key:
            raise LLMError(
                "No LLM API key set. Add a free key as LLM_API_KEY in .env "
                "(Gemini: https://aistudio.google.com/apikey)."
            )

    def _messages(self, system: str, messages: list[Message]) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, *messages]

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult:
        self._check_key()
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(system, messages),
                max_tokens=max_tokens,
            )
        except openai.APIError as err:
            raise _to_llm_error(err) from err
        choice = response.choices[0]
        usage = response.usage
        return LLMResult(
            text=_THINK_BLOCK.sub("", choice.message.content or "").strip(),
            model=response.model or self.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            stop_reason=choice.finish_reason,
        )

    async def stream(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> AsyncIterator[str | LLMResult]:
        think = _ThinkFilter()
        parts: list[str] = []
        usage: Any = None
        finish: str | None = None
        model = self.model
        self._check_key()
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(system, messages),
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                model = chunk.model or model
                if chunk.usage:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                finish = chunk.choices[0].finish_reason or finish
                if text := think.feed(chunk.choices[0].delta.content or ""):
                    parts.append(text)
                    yield text
        except openai.APIError as err:
            raise _to_llm_error(err) from err

        if tail := think.flush():
            parts.append(tail)
            yield tail
        yield LLMResult(
            text="".join(parts).strip(),
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            stop_reason=finish,
        )


class _ThinkFilter:
    """Drops <think>...</think> from a token stream, even when the tags span chunks."""

    def __init__(self) -> None:
        self._buffer = ""
        self._thinking = False

    def feed(self, text: str) -> str:
        self._buffer += text
        out = ""
        while self._buffer:
            if self._thinking:
                end = self._buffer.find("</think>")
                if end < 0:
                    self._buffer = self._buffer[-len("</think>") :]  # keep a possible partial tag
                    return out
                self._buffer = self._buffer[end + len("</think>") :].lstrip()
                self._thinking = False
            else:
                start = self._buffer.find("<think>")
                if start >= 0:
                    out += self._buffer[:start]
                    self._buffer = self._buffer[start + len("<think>") :]
                    self._thinking = True
                    continue
                # Hold back anything that could be the start of "<think>".
                keep = next(
                    (
                        i
                        for i in range(1, len("<think>"))
                        if "<think>".startswith(self._buffer[-i:])
                    ),
                    0,
                )
                cut = len(self._buffer) - keep
                out += self._buffer[:cut]
                self._buffer = self._buffer[cut:]
                return out
        return out

    def flush(self) -> str:
        rest, self._buffer = ("" if self._thinking else self._buffer), ""
        return rest


def _to_llm_error(err: openai.APIError) -> LLMError:
    if isinstance(err, openai.AuthenticationError | openai.PermissionDeniedError):
        return LLMError("LLM API key is missing or invalid. Check LLM_API_KEY in .env.")
    if isinstance(err, openai.NotFoundError):
        return LLMError("Model not found. Check LLM_MODEL and LLM_BASE_URL in .env.")
    if isinstance(err, openai.RateLimitError):
        return LLMError(
            "The free quota for this model is used up for now. Wait a minute (or until tomorrow "
            "for daily limits), or switch LLM_MODEL to another free model.",
            retryable=True,
        )
    if isinstance(err, openai.APIConnectionError):
        return LLMError(
            "Could not reach the LLM server. Check your connection, or that Ollama is running.",
            retryable=True,
        )
    if isinstance(err, openai.APIStatusError):
        return LLMError(f"LLM API error ({err.status_code}).", retryable=err.status_code >= 500)
    return LLMError("Unexpected LLM API error.")
