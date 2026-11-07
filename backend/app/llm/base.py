from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None


class LLMError(Exception):
    """A provider failure, with a message that is safe to show to the user."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    provider: str
    model: str

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult: ...

    def stream(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> AsyncIterator[str | LLMResult]:
        """Yields text deltas as they arrive, then one final LLMResult with the full text
        and token usage."""
        ...
