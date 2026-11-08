import json
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.answer import prompts
from app.answer.service import extract_citations
from app.api.search import get_reranker_if_enabled
from app.ingest.embedder import get_embedder
from app.llm import LLMError, get_llm
from app.main import app
from app.retrieval.search import Hit
from tests.fakes import FixedReranker, ScriptedLLM, TokenEmbedder

# --- pure helpers -----------------------------------------------------------------------


def test_extract_citations_handles_common_formats() -> None:
    text = "Forty sites [2]. Three years [1][2]. Grain size [1, 3]. 粒径も測定した［4］[3、5]."
    assert extract_citations(text, n_sources=5) == [2, 1, 3, 4, 5]


def test_extract_citations_ignores_out_of_range_numbers() -> None:
    assert extract_citations("See [0], [7] and [2].", n_sources=3) == [2]


def test_sources_are_numbered_and_escaped() -> None:
    hit = Hit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title='Dunes & "Wind"',
        text="We measured forty sites.",
        section="2 Methods",
        page_start=2,
        page_end=3,
        language="en",
    )
    block = prompts.format_sources([hit])
    assert '<source id="1" paper="Dunes &amp; &quot;Wind&quot;" pages="2-3"' in block
    assert 'section="2 Methods"' in block
    assert "We measured forty sites." in block


# --- chat API ---------------------------------------------------------------------------


class BrokenLLM(ScriptedLLM):
    async def stream(self, **kwargs) -> AsyncIterator:
        yield "Partial "
        raise LLMError("Rate limited by the Anthropic API. Try again shortly.", retryable=True)


class JunkReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [0.0001] * len(passages)


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
async def client(library: dict[str, uuid.UUID], llm: ScriptedLLM) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_embedder] = TokenEmbedder
    app.dependency_overrides[get_reranker_if_enabled] = FixedReranker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def ask(client: AsyncClient, content: str, **extra) -> list[tuple[str, dict]]:
    res = await client.post("/api/chat", json={"content": content, **extra})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in res.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def by_type(events: list[tuple[str, dict]], kind: str) -> list[dict]:
    return [data for name, data in events if name == kind]


async def test_answer_streams_with_sources_and_citations(
    client: AsyncClient, llm: ScriptedLLM
) -> None:
    events = await ask(client, "How many sites were measured?")
    names = [name for name, _ in events]
    assert names[0] == "conversation" and names[1] == "sources" and names[-1] == "done"
    assert names.count("token") > 1  # really streamed, not one blob

    sources = by_type(events, "sources")[0]["sources"]
    assert sources[0]["n"] == 1 and sources[0]["page_start"] >= 1
    done = by_type(events, "done")[0]
    assert "".join(e["text"] for e in by_type(events, "token")).strip() == done["content"]
    assert done["cited"] == [1]
    assert done["cost_usd"] == pytest.approx((2000 * 0.10 + 50 * 0.50) / 1_000_000)
    assert {"retrieval", "first_token", "generation", "total"} <= set(done["timings_ms"])

    # The model saw the rules, the numbered sources and the question.
    call = llm.calls[-1]
    assert call["system"] == prompts.ANSWER_SYSTEM
    prompt = call["messages"][-1]["content"]
    assert '<source id="1"' in prompt
    assert "Question: How many sites were measured?" in prompt
    assert prompt.endswith("Write the answer in English.")


async def test_conversation_is_saved(client: AsyncClient) -> None:
    events = await ask(client, "How many sites were measured?")
    conv_id = by_type(events, "conversation")[0]["id"]

    detail = (await client.get(f"/api/conversations/{conv_id}")).json()
    assert detail["title"] == "How many sites were measured?"
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    answer = detail["messages"][1]
    assert answer["cited"] == [1] and answer["sources"] and answer["cost_usd"] > 0

    listed = (await client.get("/api/conversations")).json()
    assert [c["id"] for c in listed] == [conv_id]


async def test_no_relevant_passages_means_no_llm_call(
    client: AsyncClient, llm: ScriptedLLM
) -> None:
    app.dependency_overrides[get_reranker_if_enabled] = JunkReranker
    events = await ask(client, "What is the weather in Osaka?")
    done = by_type(events, "done")[0]
    assert done["content"] == prompts.NOT_FOUND["en"]
    assert done["sources"] == [] and done["cost_usd"] == 0
    assert llm.calls == []

    events = await ask(client, "大阪の天気はどうですか")
    assert by_type(events, "done")[0]["content"] == prompts.NOT_FOUND["ja"]


async def test_follow_up_is_rewritten_using_history(client: AsyncClient, llm: ScriptedLLM) -> None:
    first = await ask(client, "How many sites were measured?")
    conv_id = by_type(first, "conversation")[0]["id"]

    llm.rewrite = "How long were the dune sites measured?"
    second = await ask(client, "and for how long?", conversation_id=conv_id)

    rewrite_call = next(c for c in llm.calls if c["kind"] == "complete")
    assert rewrite_call["system"] == prompts.REWRITE_SYSTEM
    assert "How many sites were measured?" in rewrite_call["messages"][0]["content"]
    assert by_type(second, "sources")[0]["search_query"] == llm.rewrite

    answer_call = llm.calls[-1]
    assert [m["role"] for m in answer_call["messages"]] == ["user", "assistant", "user"]
    assert answer_call["messages"][0]["content"] == "How many sites were measured?"


async def test_answers_can_be_limited_to_one_paper(
    client: AsyncClient, library: dict[str, uuid.UUID]
) -> None:
    events = await ask(
        client, "blood pressure and creatinine", document_ids=[str(library["dunes"])]
    )
    for source in by_type(events, "sources")[0]["sources"]:
        assert source["document_id"] == str(library["dunes"])


async def test_llm_failure_is_reported_and_saved(client: AsyncClient) -> None:
    app.dependency_overrides[get_llm] = BrokenLLM
    events = await ask(client, "How many sites were measured?")
    error = by_type(events, "error")[0]
    assert "Rate limited" in error["message"]

    conv_id = by_type(events, "conversation")[0]["id"]
    answer = (await client.get(f"/api/conversations/{conv_id}")).json()["messages"][1]
    assert answer["content"] == "Partial " and "Rate limited" in answer["error"]


async def test_failed_answers_are_left_out_of_history(client: AsyncClient) -> None:
    app.dependency_overrides[get_llm] = BrokenLLM
    conv_id = by_type(await ask(client, "first question"), "conversation")[0]["id"]

    llm = ScriptedLLM(rewrite="second question rewritten")
    app.dependency_overrides[get_llm] = lambda: llm
    await ask(client, "second question", conversation_id=conv_id)
    history = llm.calls[0]["messages"][0]["content"]
    assert "first question" in history and "Partial" not in history


async def test_unknown_conversation_is_404(client: AsyncClient) -> None:
    res = await client.post(
        "/api/chat", json={"content": "hello", "conversation_id": str(uuid.uuid4())}
    )
    assert res.status_code == 404


async def test_delete_conversation(client: AsyncClient) -> None:
    conv_id = by_type(await ask(client, "How many sites?"), "conversation")[0]["id"]
    assert (await client.delete(f"/api/conversations/{conv_id}")).status_code == 204
    assert (await client.get(f"/api/conversations/{conv_id}")).status_code == 404
