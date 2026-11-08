import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.answer import prompts
from app.api.search import get_reranker_if_enabled
from app.auth.deps import get_current_user
from app.db import SessionLocal
from app.ingest.embedder import get_embedder
from app.llm import get_llm
from app.main import app
from app.models import Trace, User, UserRole
from app.observability.traces import PROMPT_VERSION
from tests.fakes import FixedReranker, ScriptedLLM, TokenEmbedder
from tests.test_chat import BrokenLLM, JunkReranker, ask, by_type


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
    for dep in (get_llm, get_embedder, get_reranker_if_enabled):
        app.dependency_overrides.pop(dep, None)


async def traces(kind: str | None = None) -> list[Trace]:
    async with SessionLocal() as session:
        stmt = select(Trace).order_by(Trace.created_at)
        if kind:
            stmt = stmt.where(Trace.kind == kind)
        return list(await session.scalars(stmt))


def as_member(user_id: uuid.UUID | None = None) -> None:
    """Make the next requests come from a (different) non-admin user."""
    app.dependency_overrides[get_current_user] = lambda: User(
        id=user_id or uuid.uuid4(), email="m@lab.test", name="Member", password_hash="x",
        role=UserRole.member, is_active=True, session_version=0,
    )  # fmt: skip


# --- recording --------------------------------------------------------------------------


async def test_answer_records_a_full_trace(client: AsyncClient) -> None:
    events = await ask(client, "How many sites were measured?")
    done = by_type(events, "done")[0]

    [trace] = await traces("chat")
    assert trace.query == "How many sites were measured?"
    assert trace.answered and trace.error is None
    assert str(trace.message_id) == done["message_id"]
    assert trace.prompt_version == PROMPT_VERSION
    assert trace.cost_usd == pytest.approx(done["cost_usd"])
    assert trace.latency_ms == done["timings_ms"]["total"]
    assert {"retrieval", "generation", "search_embed", "search_rerank"} <= set(trace.timings_ms)

    # FixedReranker keeps only the first passage: one kept and cited, the rest dropped.
    kept = [c for c in trace.candidates if c["kept"]]
    assert len(kept) == 1 and kept[0]["cited"] is True
    assert trace.filtered_out == len(trace.candidates) - 1
    assert all(c["rerank_score"] is not None for c in trace.candidates)


async def test_unanswered_question_is_traced(client: AsyncClient) -> None:
    app.dependency_overrides[get_reranker_if_enabled] = JunkReranker
    await ask(client, "What is the weather in Osaka?")
    [trace] = await traces("chat")
    assert trace.answered is False and trace.error is None
    assert trace.candidates and not any(c["kept"] for c in trace.candidates)
    assert trace.cost_usd == 0


async def test_llm_failure_is_traced_with_its_error(client: AsyncClient) -> None:
    app.dependency_overrides[get_llm] = BrokenLLM
    await ask(client, "How many sites were measured?")
    [trace] = await traces("chat")
    assert trace.answered is False
    assert "Rate limited" in trace.error
    assert trace.candidates  # retrieval details survive the failure


async def test_search_is_traced(client: AsyncClient) -> None:
    await client.post("/api/search", json={"query": "creatinine"})
    [trace] = await traces("search")
    assert trace.query == "creatinine" and trace.answered
    assert trace.message_id is None


# --- feedback ---------------------------------------------------------------------------


async def test_feedback_on_own_answer(client: AsyncClient) -> None:
    events = await ask(client, "How many sites were measured?")
    conv = by_type(events, "conversation")[0]["id"]
    msg = by_type(events, "done")[0]["message_id"]
    url = f"/api/conversations/{conv}/messages/{msg}/feedback"

    assert (
        await client.put(url, json={"value": -1, "note": "missed the years"})
    ).status_code == 204
    answer = (await client.get(f"/api/conversations/{conv}")).json()["messages"][1]
    assert (answer["feedback"], answer["feedback_note"]) == (-1, "missed the years")

    await client.put(url, json={"value": 0})
    answer = (await client.get(f"/api/conversations/{conv}")).json()["messages"][1]
    assert answer["feedback"] is None and answer["feedback_note"] is None

    assert (await client.put(url, json={"value": 5})).status_code == 422
    as_member()
    assert (await client.put(url, json={"value": 1})).status_code == 404  # not their answer


# --- insights API -----------------------------------------------------------------------


async def test_summary_numbers(client: AsyncClient) -> None:
    first = await ask(client, "How many sites were measured?")
    await ask(client, "How long was the study?")
    app.dependency_overrides[get_reranker_if_enabled] = JunkReranker
    await ask(client, "What is the weather in Osaka?")
    await client.post("/api/search", json={"query": "dunes"})
    conv = by_type(first, "conversation")[0]["id"]
    msg = by_type(first, "done")[0]["message_id"]
    await client.put(f"/api/conversations/{conv}/messages/{msg}/feedback", json={"value": 1})

    s = (await client.get("/api/insights/summary?days=7")).json()
    assert (s["questions"], s["searches"]) == (3, 1)
    assert s["answered_rate"] == pytest.approx(2 / 3)
    assert s["error_rate"] == 0
    assert s["latency_ms"]["p50"] > 0 and s["latency_ms"]["p95"] >= s["latency_ms"]["p50"]
    assert {"retrieval", "generation"} <= set(s["stage_avg_ms"])
    assert "rewrite" not in s["stage_avg_ms"]  # no follow-ups: never ran, not shown
    assert {"embed", "rerank"} <= set(s["search_stage_avg_ms"])
    assert s["cost_usd"] == pytest.approx(2 * (2000 * 0.10 + 50 * 0.50) / 1_000_000)
    assert (s["feedback_up"], s["feedback_down"]) == (1, 0)
    assert s["unanswered_questions"] == ["What is the weather in Osaka?"]
    assert sum(d["questions"] for d in s["per_day"]) == 3


async def test_trace_list_filters_and_detail(client: AsyncClient) -> None:
    await ask(client, "How many sites were measured?")
    app.dependency_overrides[get_reranker_if_enabled] = JunkReranker
    await ask(client, "What is the weather in Osaka?")

    page = (await client.get("/api/insights/traces")).json()
    assert page["total"] == 2
    assert page["items"][0]["query"] == "What is the weather in Osaka?"  # newest first
    assert page["items"][0]["user_name"] == "Tester"

    unanswered = (await client.get("/api/insights/traces?only=unanswered")).json()
    assert [t["query"] for t in unanswered["items"]] == ["What is the weather in Osaka?"]

    answered_id = page["items"][1]["id"]
    detail = (await client.get(f"/api/insights/traces/{answered_id}")).json()
    assert detail["answer"] == "Forty sites were measured [1]."
    assert detail["prompt_is_current"] and detail["system_prompt"] == prompts.ANSWER_SYSTEM
    assert detail["candidates"][0]["kept"] is True
    assert (await client.get(f"/api/insights/traces/{uuid.uuid4()}")).status_code == 404


async def test_insights_are_admin_only(client: AsyncClient) -> None:
    as_member()
    assert (await client.get("/api/insights/summary")).status_code == 403
    assert (await client.get("/api/insights/traces")).status_code == 403


async def test_summary_with_no_data(client: AsyncClient) -> None:
    s = (await client.get("/api/insights/summary")).json()
    assert s["questions"] == 0 and s["answered_rate"] is None
    assert s["latency_ms"] == {"p50": None, "p95": None}
