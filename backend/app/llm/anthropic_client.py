from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.llm.base import LLMError, LLMResult, Message

REFUSAL_MESSAGE = "The model declined to answer this request."
MISSING_KEY_MESSAGE = "Anthropic API key is missing or invalid. Set ANTHROPIC_API_KEY in .env."


class AnthropicLLM:
    """Anthropic models via the official SDK."""

    provider = "anthropic"

    def __init__(self, *, model: str, effort: str, api_key: str | None = None) -> None:
        self.model = model
        self.effort = effort
        self._api_key = api_key or None
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        # Created on first use, so a missing key fails a request instead of app startup.
        # With api_key=None the SDK reads ANTHROPIC_API_KEY from the environment.
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    def _params(self, system: str, messages: list[Message], max_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult:
        try:
            response = await self.client.messages.create(
                **self._params(system, messages, max_tokens)
            )
        except anthropic.APIError as err:
            raise _to_llm_error(err) from err
        except (TypeError, anthropic.CredentialsError) as err:
            if _is_missing_credentials(err):
                raise LLMError(MISSING_KEY_MESSAGE) from err
            raise

        if response.stop_reason == "refusal":
            raise LLMError(REFUSAL_MESSAGE)

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )

    async def stream(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> AsyncIterator[str | LLMResult]:
        try:
            async with self.client.messages.stream(
                **self._params(system, messages, max_tokens)
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except anthropic.APIError as err:
            raise _to_llm_error(err) from err
        except (TypeError, anthropic.CredentialsError) as err:
            if _is_missing_credentials(err):
                raise LLMError(MISSING_KEY_MESSAGE) from err
            raise

        if final.stop_reason == "refusal":
            raise LLMError(REFUSAL_MESSAGE)
        yield LLMResult(
            text="".join(block.text for block in final.content if block.type == "text"),
            model=final.model,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            stop_reason=final.stop_reason,
        )


def _is_missing_credentials(err: Exception) -> bool:
    """The SDK signals missing credentials with CredentialsError, or a TypeError saying it
    could not resolve an authentication method. Any other TypeError is a real bug."""
    return isinstance(err, anthropic.CredentialsError) or "authentication" in str(err)


def _to_llm_error(err: anthropic.APIError) -> LLMError:
    """Turn SDK errors into messages a user can act on. Most specific classes first."""
    if isinstance(err, anthropic.AuthenticationError):
        return LLMError(MISSING_KEY_MESSAGE)
    if isinstance(err, anthropic.PermissionDeniedError):
        return LLMError("This API key cannot use the configured model.")
    if isinstance(err, anthropic.NotFoundError):
        return LLMError("Model not found. Check LLM_MODEL in .env.")
    if isinstance(err, anthropic.RateLimitError):
        return LLMError("Rate limited by the Anthropic API. Try again shortly.", retryable=True)
    if isinstance(err, anthropic.APIStatusError):
        if err.status_code == 400 and "credit" in str(err.message).lower():
            return LLMError("Anthropic API credit balance is too low. Add credit in the Console.")
        return LLMError(
            f"Anthropic API error ({err.status_code}).", retryable=err.status_code >= 500
        )
    if isinstance(err, anthropic.APIConnectionError):
        return LLMError("Could not reach the Anthropic API. Check your connection.", retryable=True)
    return LLMError("Unexpected Anthropic API error.")
