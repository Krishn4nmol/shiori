import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.agent.catalog import CatalogEntry, catalog_text, details_hit
from app.agent.router import PLANNER_SYSTEM, parse_plan
from app.answer import prompts
from app.answer.service import AnswerDeps, SearchOptions, answer_events
from app.api.search import get_reranker_if_enabled
from app.config import get_settings
from app.db import SessionLocal
from app.ingest.embedder import get_embedder
from app.llm import LLMResult, Message, get_llm
from app.main import app
from app.models import Chunk
from app.retrieval.search import search
from tests.fakes import TokenEmbedder

# --- planner parsing (no database) ------------------------------------------------------


def entry(key: str, title: str) -> CatalogEntry:
    from datetime import UTC, datetime

    return CatalogEntry(
        key=key, id=uuid.uuid4(), title=title, authors="山田 花子", page_count=3, language="ja",
        chunk_count=4, filename=f"{title}.pdf", created_at=datetime(2026, 11, 7, tzinfo=UTC),
    )  # fmt: skip


CATALOG = [entry("P1", "Dense retrieval"), entry("P2", "鳥取砂丘の植生調査")]


def test_plan_is_parsed_and_validated() -> None:
    reply = {
        "type": "find_all",
        "papers": ["P2", "P9", "P2"],
        "question": "List every dataset used in the survey",
        "queries": ["a", "a", "b", "c", "d"],
    }
    text = f"Plan:\n```json\n{json.dumps(reply, ensure_ascii=False)}\n```"  # prose + code fence
    plan = parse_plan(text, "list them", CATALOG)
    assert plan.type == "find_all" and not plan.fallback
    assert [p.title for p in plan.papers] == ["鳥取砂丘の植生調査"]  # unknown P9 dropped, deduped
    assert plan.question == "List every dataset used in the survey"
    assert plan.queries == [
        "List every dataset used in the survey",
        "a",
        "b",
    ]  # standalone first, max 3


def test_bad_plan_falls_back_to_plain_search() -> None:
    plan = parse_plan("I think you should search.", "Which retriever was used?", CATALOG)
    assert plan.fallback and plan.type == "lookup"
    assert plan.queries == ["Which retriever was used?"]
    odd = parse_plan('{"type": "poem", "queries": []}', "q", CATALOG)
    assert odd.type == "lookup" and odd.queries == ["q"]


def test_catalog_and_details_source() -> None:
    text = catalog_text(CATALOG)
    assert "P2: 鳥取砂丘の植生調査 | authors: 山田 花子 | 3 pages | Japanese" in text
    hit = details_hit(CATALOG[1])
    assert "Authors: 山田 花子" in hit.text and hit.section == "Document details"
    assert hit.chunk_id == details_hit(CATALOG[1]).chunk_id  # stable id


def test_details_point_to_the_first_page_when_metadata_has_no_authors() -> None:
    bare = replace(CATALOG[0], authors=None, opening="Dense retrieval\nA. Author, B. Author")
    text = details_hit(bare).text
    assert "not recorded" not in text and "A. Author, B. Author" in text
    assert "Authors: not recorded" in details_hit(replace(bare, opening="")).text


# --- multi-query search -----------------------------------------------------------------


async def test_extra_query_in_another_language_finds_more(library: dict[str, uuid.UUID]) -> None:
    async with SessionLocal() as session:
        english = await search(session, "dune survey", embedder=TokenEmbedder(), reranker=None,
                               mode="keyword")  # fmt: skip
        both = await search(session, "dune survey", extra_queries=["鳥取砂丘の調査"],
                            embedder=TokenEmbedder(), reranker=None, mode="keyword")  # fmt: skip
    assert not any(h.language == "ja" for h in english.hits)
    assert any(h.language == "ja" for h in both.hits)


# --- the agent end to end ---------------------------------------------------------------


class PlannerLLM:
    """Returns a scripted plan from the planner prompt, then a fixed answer."""

    provider = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, plan: dict | str) -> None:
        self.plan = plan
        self.calls: list[dict] = []

    async def complete(self, *, system: str, messages: list[Message], max_tokens: int = 4000):
        self.calls.append({"system": system, "content": messages[-1]["content"]})
        assert system == PLANNER_SYSTEM
        text = self.plan if isinstance(self.plan, str) else json.dumps(self.plan)
        return LLMResult(text=text, model=self.model, input_tokens=300, output_tokens=40)

    async def stream(self, *, system: str, messages: list[Message], max_tokens: int = 4000):
        self.calls.append({"system": system, "content": messages[-1]["content"]})
        yield "Answer [1]."
        yield LLMResult(text="Answer [1].", model=self.model, input_tokens=900, output_tokens=5)


class TopReranker:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [0.9 - i * 0.05 for i in range(len(passages))]


async def run_agent(llm: PlannerLLM, question: str, history=None, document_ids=None):
    deps = AnswerDeps(llm=llm, embedder=TokenEmbedder(), reranker=TopReranker())
    events = []
    async with SessionLocal() as session:
        async for event in answer_events(
            session, question, history or [], deps, document_ids, SearchOptions(agent=True)
        ):
            events.append(event)
    return events


def first(events, kind):
    return next(e for e in events if e["type"] == kind)


def paper_key(library: dict[str, uuid.UUID], name: str) -> str:
    # Catalog keys follow creation order: dunes was ingested first.
    return "P1" if name == "dunes" else "P2"


async def test_details_question_cites_document_details(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "details", "papers": ["P1"], "question": "Who wrote it?"})
    events = await run_agent(llm, "Who wrote the dune paper?")
    plan, sources = first(events, "plan"), first(events, "sources")["sources"]
    assert plan["question_type"] == "details" and "document details" in plan["steps"][0]
    assert len(sources) == 1 and sources[0]["section"] == "Document details"
    assert "Authors: A. Researcher" in sources[0]["text"]
    assert prompts.TASK_HINTS["details"] in llm.calls[-1]["content"]


async def test_find_all_reads_the_whole_paper(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "find_all", "papers": ["P1"], "question": "List every site detail"})
    events = await run_agent(llm, "List every detail about the sites")
    sources = first(events, "sources")["sources"]
    async with SessionLocal() as session:
        total = await session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == library["dunes"])
        )
    assert len(sources) == total  # every passage, not a top-k
    assert [s["document_id"] for s in sources] == [str(library["dunes"])] * total
    assert "Read all" in first(events, "plan")["steps"][0]
    assert prompts.TASK_HINTS["find_all"] in llm.calls[-1]["content"]


async def test_compare_searches_each_paper(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "compare", "papers": ["P1", "P2"], "question": "Compare them",
                      "queries": ["patients sites years measured"]})  # fmt: skip
    events = await run_agent(llm, "Compare how the two studies collected data")
    docs = {s["document_id"] for s in first(events, "sources")["sources"]}
    assert docs == {str(library["dunes"]), str(library["kidney"])}


async def test_lookup_uses_all_planned_queries(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "lookup", "papers": [], "question": "What was surveyed at the dunes?",
                      "queries": ["鳥取砂丘で何を調査しましたか"]})  # fmt: skip
    events = await run_agent(llm, "What was surveyed at the dunes?")
    plan = first(events, "plan")
    assert plan["queries"] == ["What was surveyed at the dunes?", "鳥取砂丘で何を調査しましたか"]
    assert first(events, "done")["record"]["search_query"] == "What was surveyed at the dunes?"


async def test_planner_failure_still_answers(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM("not json at all")
    events = await run_agent(llm, "creatinine blood pressure")
    assert first(events, "plan")["fallback"] is True
    assert first(events, "sources")["sources"]
    assert first(events, "done")["record"]["content"] == "Answer [1]."


async def test_follow_up_context_reaches_the_planner(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "lookup", "papers": ["P1"], "question": "How long did it last?"})
    history = [{"role": "user", "content": "How many dune sites were measured?"},
               {"role": "assistant", "content": "Forty sites [1]."}]  # fmt: skip
    await run_agent(llm, "and for how long?", history)
    assert "How many dune sites were measured?" in llm.calls[0]["content"]
    assert len(llm.calls) == 2  # plan + answer: no separate rewrite call


async def test_usage_and_timings_include_planning(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "lookup", "papers": [], "question": "dunes"})
    record = first(await run_agent(llm, "dunes"), "done")["record"]
    assert record["input_tokens"] == 300 + 900
    assert {"plan", "retrieval", "generation"} <= set(record["timings_ms"])


async def test_user_paper_filter_limits_the_agent(library: dict[str, uuid.UUID]) -> None:
    llm = PlannerLLM({"type": "find_all", "papers": [], "question": "everything"})
    events = await run_agent(llm, "List everything", document_ids=[library["kidney"]])
    assert {s["document_id"] for s in first(events, "sources")["sources"]} == {
        str(library["kidney"])
    }
    assert "Dunes" not in llm.calls[0]["content"]  # the planner only saw the chosen paper


# --- chat API in agent mode -------------------------------------------------------------


@pytest.fixture
async def agent_client(library, monkeypatch) -> AsyncIterator[tuple[AsyncClient, PlannerLLM]]:
    llm = PlannerLLM({"type": "lookup", "papers": [], "question": "creatinine"})
    monkeypatch.setattr(get_settings(), "answer_mode", "agent")
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_embedder] = TokenEmbedder
    app.dependency_overrides[get_reranker_if_enabled] = TopReranker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, llm
    for dep in (get_llm, get_embedder, get_reranker_if_enabled):
        app.dependency_overrides.pop(dep, None)


async def test_chat_streams_the_plan(agent_client) -> None:
    client, _ = agent_client
    res = await client.post("/api/chat", json={"content": "creatinine"})
    names = [line.split(": ", 1)[1] for line in res.text.splitlines() if line.startswith("event: ")]
    assert names[:3] == ["conversation", "plan", "sources"] and names[-1] == "done"


async def test_trace_keeps_the_plan(agent_client) -> None:
    from app.models import Trace

    client, _ = agent_client
    await client.post("/api/chat", json={"content": "creatinine"})
    async with SessionLocal() as session:
        trace = await session.scalar(select(Trace))
    assert trace.plan["question_type"] == "lookup"
    assert trace.plan["steps"] and "plan" in trace.timings_ms
