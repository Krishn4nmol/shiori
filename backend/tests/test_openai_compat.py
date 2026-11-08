from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.llm import LLMError, LLMResult
from app.llm.openai_compat_client import OpenAICompatLLM, _ThinkFilter, _to_llm_error

# --- <think> filter ---------------------------------------------------------------------


def run_filter(chunks: list[str]) -> str:
    f = _ThinkFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_filter_passes_plain_text() -> None:
    assert run_filter(["Forty ", "sites [1]."]) == "Forty sites [1]."


def test_filter_removes_think_block() -> None:
    assert run_filter(["<think>plan the answer</think>Forty sites [1]."]) == "Forty sites [1]."


def test_filter_handles_tags_split_across_chunks() -> None:
    chunks = ["<th", "ink>let me ", "check</th", "ink>\n\n四十", "地点 [1]。"]
    assert run_filter(chunks) == "四十地点 [1]。"


def test_filter_does_not_swallow_a_lone_angle_bracket() -> None:
    assert run_filter(["a < b", " and 3 > 2"]) == "a < b and 3 > 2"


def test_unclosed_think_block_produces_no_text() -> None:
    assert run_filter(["<think>still thinking"]) == ""


# --- client ---------------------------------------------------------------------------


def chunk(text: str | None = None, usage=None, finish=None):
    choices = (
        []
        if text is None
        else [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)]
    )
    return SimpleNamespace(model="gemini-3.5-flash-lite", choices=choices, usage=usage)


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self.chunks:
            yield c


def make_llm(monkeypatch: pytest.MonkeyPatch, chunks) -> tuple[OpenAICompatLLM, dict]:
    llm = OpenAICompatLLM(
        model="gemini-3.5-flash-lite", base_url="https://example.test/v1", api_key="k"
    )
    sent: dict = {}

    async def create(**kwargs):
        sent.update(kwargs)
        return FakeStream(chunks)

    monkeypatch.setattr(llm._client.chat.completions, "create", create)
    return llm, sent


async def test_stream_yields_text_then_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=40)
    llm, sent = make_llm(
        monkeypatch,
        [chunk("<think>hmm</think>Forty "), chunk("sites [1].", finish="stop"), chunk(usage=usage)],
    )
    out = [c async for c in llm.stream(system="rules", messages=[{"role": "user", "content": "q"}])]

    assert "".join(c for c in out if isinstance(c, str)) == "Forty sites [1]."
    final = out[-1]
    assert isinstance(final, LLMResult)
    assert (final.input_tokens, final.output_tokens, final.stop_reason) == (1200, 40, "stop")
    assert sent["messages"][0] == {"role": "system", "content": "rules"}
    assert sent["stream_options"] == {"include_usage": True}


async def test_missing_key_for_cloud_api_fails_before_any_request() -> None:
    llm = OpenAICompatLLM(
        model="gemini-3.5-flash-lite",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=None,
    )
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        await llm.complete(system="s", messages=[{"role": "user", "content": "q"}])


def test_local_server_needs_no_key() -> None:
    llm = OpenAICompatLLM(model="qwen3:4b", base_url="http://localhost:11434/v1", api_key=None)
    llm._check_key()  # does not raise


def _status_error(cls, status: int):
    request = httpx2.Request("POST", "https://example.test/v1/chat/completions")
    return cls("error", response=httpx2.Response(status, request=request), body=None)


def test_rate_limit_explains_free_quota() -> None:
    err = _to_llm_error(_status_error(openai.RateLimitError, 429))
    assert "free quota" in str(err) and err.retryable


def test_bad_key_message() -> None:
    assert "LLM_API_KEY" in str(_to_llm_error(_status_error(openai.AuthenticationError, 401)))
