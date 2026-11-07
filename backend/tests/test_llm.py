from collections.abc import AsyncIterator

import anthropic
import httpx
import httpx2
import pytest
from httpx import ASGITransport, AsyncClient

from app.llm import LLMError, LLMResult, Message, get_llm
from app.llm.anthropic_client import AnthropicLLM, _to_llm_error
from app.llm.pricing import estimate_cost_usd
from app.main import app


class FakeLLM:
    provider = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult:
        if self.fail:
            raise LLMError("Anthropic API key is missing or invalid.")
        return LLMResult(
            text="Hello / こんにちは", model=self.model, input_tokens=40, output_tokens=10
        )

    async def stream(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> AsyncIterator[str]:
        yield "Hello"


async def _post_check(llm: FakeLLM) -> httpx.Response:
    app.dependency_overrides[get_llm] = lambda: llm
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/llm/check")
    finally:
        app.dependency_overrides.clear()


def test_haiku_cost() -> None:
    # 1M input tokens at $0.10 + 1M output tokens at $0.50
    assert estimate_cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_unknown_model_costs_nothing() -> None:
    assert estimate_cost_usd("llama3.2:3b", 5000, 500) == 0.0


async def test_check_endpoint_reports_reply_and_cost() -> None:
    res = await _post_check(FakeLLM())
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "Hello / こんにちは"
    assert body["cost_usd"] == pytest.approx((40 * 0.10 + 10 * 0.50) / 1_000_000)


async def test_check_endpoint_surfaces_provider_errors() -> None:
    res = await _post_check(FakeLLM(fail=True))
    assert res.status_code == 503
    assert "API key" in res.json()["detail"]


def _response(status: int) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx2.Response(status, request=request)


def test_auth_error_gets_actionable_message() -> None:
    err = anthropic.AuthenticationError("invalid x-api-key", response=_response(401), body=None)
    assert "ANTHROPIC_API_KEY" in str(_to_llm_error(err))


def test_rate_limit_is_retryable() -> None:
    err = anthropic.RateLimitError("slow down", response=_response(429), body=None)
    assert _to_llm_error(err).retryable


async def test_missing_api_key_gives_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent")  # no saved login profile
    llm = AnthropicLLM(model="claude-haiku-5-5", effort="low", api_key=None)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        await llm.complete(system="test", messages=[{"role": "user", "content": "hi"}])
