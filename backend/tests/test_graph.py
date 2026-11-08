"""Knowledge graph: extraction checks, Neo4j storage (in the "test" namespace), the agent's
relations route, and the API. Neo4j tests are skipped when Neo4j is not running."""

import json
import re
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncGraphDatabase
from sqlalchemy import select

from app.answer.service import AnswerDeps, SearchOptions, answer_events
from app.config import get_settings
from app.db import SessionLocal
from app.graph.build import build_paper_graph
from app.graph.extract import EXTRACT_SYSTEM, entity_key, extract_batch
from app.graph.store import APP_NAMESPACE, GraphStore, get_graph_store
from app.llm import LLMResult, Message
from app.main import app
from app.models import Document
from app.queue import get_queue
from tests.fakes import TokenEmbedder

# --- a fake extractor -------------------------------------------------------------------


class ExtractLLM:
    """Extracts entities from keywords it finds in each passage, like a (very simple) LLM."""

    provider = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, bad: bool = False) -> None:
        self.bad = bad  # also return invalid items, which must be dropped
        self.calls = 0

    async def complete(self, *, system: str, messages: list[Message], max_tokens: int = 4000):
        assert system == EXTRACT_SYSTEM
        self.calls += 1
        passages = re.findall(r'<passage id="(P\d+)">\n(.*?)\n</passage>', messages[-1]["content"],
                              re.DOTALL)  # fmt: skip
        entities: dict[str, dict] = {}
        relations = []

        def add(name: str, kind: str, pid: str, ja: str = "") -> None:
            entities.setdefault(name, {"name": name, "type": kind, "name_ja": ja, "passages": []})
            entities[name]["passages"].append(pid)

        for pid, text in passages:
            if "Tottori" in text or "鳥取" in text:
                add("Tottori sand dunes", "place", pid, "鳥取砂丘")
            if "sites" in text or "地点" in text:
                add("measurement sites", "measurement", pid)
            if "years" in text or "年間" in text:
                add("study duration", "measurement", pid)
            if "hospital" in text:
                add("hospital", "place", pid)
                add("kidney disease patients", "person", pid)
                relations.append({"source": "kidney disease patients", "target": "hospital",
                                  "relation": "visited", "passage": pid,
                                  "evidence": "each hospital visit"})  # fmt: skip
            if "forty sites" in text:
                relations.append({"source": "Tottori sand dunes", "target": "measurement sites",
                                  "relation": "measured at", "passage": pid,
                                  "evidence": "forty sites"})  # fmt: skip
        if self.bad:
            relations.append({"source": "Tottori sand dunes", "target": "nowhere",
                              "relation": "x", "passage": "P1", "evidence": "y"})  # fmt: skip
            relations.append({"source": "Tottori sand dunes", "target": "measurement sites",
                              "relation": "invented", "passage": passages[0][0],
                              "evidence": "this quote is not in the text"})  # fmt: skip
            entities["ghost"] = {"name": "ghost", "type": "person", "passages": ["P99"]}
        reply = {"entities": list(entities.values()), "relations": relations}
        return LLMResult(text=f"```json\n{json.dumps(reply, ensure_ascii=False)}\n```",
                         model=self.model, input_tokens=800, output_tokens=200)  # fmt: skip


# --- extraction (no Neo4j) --------------------------------------------------------------


def test_entity_keys_merge_spellings() -> None:
    assert entity_key("Tottori  Sand-Dunes!") == entity_key("tottori sand dunes")
    assert entity_key("ＴＯＴＴＯＲＩ") == "tottori"


async def test_extraction_drops_unsupported_items() -> None:
    passages = [("c1", "We measured vegetation at forty sites in Tottori over three years.")]
    out = await extract_batch(ExtractLLM(bad=True), passages)
    assert set(out.entities) == {"tottori sand dunes", "measurement sites", "study duration"}
    assert out.entities["tottori sand dunes"].aliases == ["鳥取砂丘"]
    assert [(r.relation, r.evidence) for r in out.relations] == [("measured at", "forty sites")]
    reasons = " | ".join(out.dropped)
    assert "ghost" in reasons and "not quoted" in reasons and "unknown entities" in reasons


# --- Neo4j ------------------------------------------------------------------------------


@pytest.fixture
async def graph() -> AsyncIterator[GraphStore]:
    s = get_settings()
    driver = AsyncGraphDatabase.driver(s.neo4j_url, auth=(s.neo4j_user, s.neo4j_password))
    try:
        await driver.verify_connectivity()
    except Exception as err:
        await driver.close()
        pytest.skip(f"Neo4j not available ({err}); run `docker compose up -d neo4j`")
    store = GraphStore(driver, ns="test")
    await store.clear_namespace()
    yield store
    await store.clear_namespace()
    await driver.close()


async def build_all(graph: GraphStore, library: dict[str, uuid.UUID], llm=None) -> None:
    for doc_id in library.values():
        await build_paper_graph(SessionLocal, doc_id, llm or ExtractLLM(), graph)


async def test_build_merges_languages_and_counts(
    graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    llm = ExtractLLM()
    await build_all(graph, library, llm)
    async with SessionLocal() as session:
        dunes = await session.get(Document, library["dunes"])
    assert dunes.graph_status == "ready" and dunes.graph_entities >= 3
    assert dunes.graph_relations >= 1
    assert llm.calls >= 2  # passages are sent in batches, not one call each

    # English and Japanese mentions are one entity, found by either name.
    by_english = await graph.find_entities(["Tottori"])
    by_japanese = await graph.find_entities(["鳥取砂丘"])
    assert by_english[0]["uid"] == by_japanese[0]["uid"]
    assert by_english[0]["aliases"] == ["鳥取砂丘"]


async def test_queries_return_facts_paths_and_shared_entities(
    graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    await build_all(graph, library)
    tottori = (await graph.find_entities(["Tottori"]))[0]["uid"]
    sites = (await graph.find_entities(["measurement sites"]))[0]["uid"]

    facts = await graph.facts_around([tottori])
    assert any(f.relation == "measured at" and f.evidence == "forty sites" for f in facts)
    path = await graph.facts_between(tottori, sites)
    assert [f.relation for f in path] == ["measured at"]

    shared = await graph.shared_entities([str(library["dunes"]), str(library["kidney"])])
    assert [e["name"] for e in shared] == ["study duration"]  # both papers mention years

    only_kidney = await graph.find_entities(["Tottori"], [str(library["kidney"])])
    assert only_kidney == []

    view = await graph.overview()
    assert {n["key"] for n in view["nodes"]} >= {"tottori sand dunes", "hospital"}
    assert view["edges"]
    detail = await graph.entity("tottori sand dunes")
    assert detail["mentions"] and detail["facts"]


async def test_rebuild_and_delete_clean_up(
    graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    await build_all(graph, library)
    await build_paper_graph(SessionLocal, library["dunes"], ExtractLLM(), graph)  # rebuild
    first = await graph.counts(str(library["dunes"]))
    await build_paper_graph(SessionLocal, library["dunes"], ExtractLLM(), graph)
    assert await graph.counts(str(library["dunes"])) == first  # no duplicates

    await graph.delete_paper(str(library["dunes"]))
    assert await graph.counts(str(library["dunes"])) == (0, 0)
    assert await graph.find_entities(["Tottori"]) == []  # orphans removed
    assert await graph.find_entities(["hospital"])  # the other paper is untouched


async def test_test_namespace_never_touches_app_data(
    graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    async def app_nodes() -> int:
        async with graph.driver.session() as s:
            record = await (await s.run("MATCH (n {ns: $ns}) RETURN count(n) AS n",
                                        ns=APP_NAMESPACE)).single()  # fmt: skip
            return record["n"]

    before = await app_nodes()
    await build_all(graph, library)
    await graph.clear_namespace()
    assert await app_nodes() == before


# --- the agent's relations route --------------------------------------------------------


class RelationsPlanner:
    provider = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, plan: dict) -> None:
        self.plan = plan
        self.prompts: list[str] = []

    async def complete(self, *, system: str, messages: list[Message], max_tokens: int = 4000):
        return LLMResult(text=json.dumps(self.plan), model=self.model, input_tokens=100,
                         output_tokens=20)  # fmt: skip

    async def stream(self, *, system: str, messages: list[Message], max_tokens: int = 4000):
        self.prompts.append(messages[-1]["content"])
        yield "They are measured at forty sites [2]."
        yield LLMResult(text="They are measured at forty sites [2].", model=self.model,
                        input_tokens=500, output_tokens=10)  # fmt: skip


async def ask_agent(llm, graph: GraphStore | None, question: str) -> list[dict]:
    deps = AnswerDeps(llm=llm, embedder=TokenEmbedder(), reranker=None, graph=graph)
    async with SessionLocal() as session:
        return [e async for e in answer_events(session, question, [], deps,
                                               options=SearchOptions(agent=True))]  # fmt: skip


async def test_relations_question_uses_the_graph(
    graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    await build_all(graph, library)
    llm = RelationsPlanner({"type": "relations", "papers": [], "question": "q",
                            "entities": ["Tottori", "鳥取砂丘"]})  # fmt: skip
    events = await ask_agent(llm, graph, "What is connected to the Tottori dunes?")
    plan = next(e for e in events if e["type"] == "plan")
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert "Knowledge graph" in plan["steps"][0]
    assert sources[0]["document_title"] == "Knowledge graph"
    assert "Tottori sand dunes —measured at→ measurement sites" in sources[0]["text"]
    assert len(sources) > 1 and all(s["document_title"] != "Knowledge graph" for s in sources[1:])
    assert "knowledge graph" in llm.prompts[0]  # the relations task hint


async def test_relations_question_falls_back_to_search(
    graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    llm = RelationsPlanner({"type": "relations", "papers": [], "question": "dunes",
                            "entities": ["nothing like this"]})  # fmt: skip
    events = await ask_agent(llm, graph, "How are the dunes connected to anything?")
    plan = next(e for e in events if e["type"] == "plan")
    assert "searched the passages instead" in plan["steps"][0]


async def test_without_a_graph_relations_questions_search(library: dict[str, uuid.UUID]) -> None:
    llm = RelationsPlanner({"type": "relations", "papers": [], "question": "dunes",
                            "entities": ["Tottori"]})  # fmt: skip
    events = await ask_agent(llm, None, "What is connected to Tottori?")
    assert next(e for e in events if e["type"] == "plan")["steps"][0].startswith("Searched")


# --- API --------------------------------------------------------------------------------


class RecordingQueue:
    def __init__(self) -> None:
        self.graph_jobs: list[uuid.UUID] = []

    async def enqueue_ingest(self, document_id: uuid.UUID) -> None: ...

    async def enqueue_graph(self, document_id: uuid.UUID) -> None:
        self.graph_jobs.append(document_id)


@pytest.fixture
async def client(graph: GraphStore) -> AsyncIterator[tuple[AsyncClient, RecordingQueue]]:
    queue = RecordingQueue()
    app.dependency_overrides[get_graph_store] = lambda: graph
    app.dependency_overrides[get_queue] = lambda: queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, queue
    app.dependency_overrides.pop(get_queue, None)


async def test_build_endpoint_queues_a_job(client, library: dict[str, uuid.UUID]) -> None:
    c, queue = client
    res = await c.post(f"/api/graph/papers/{library['dunes']}/build")
    assert res.status_code == 202 and res.json()["graph_status"] == "building"
    assert queue.graph_jobs == [library["dunes"]]
    again = await c.post(f"/api/graph/papers/{library['dunes']}/build")
    assert again.status_code == 409


async def test_overview_and_entity_endpoints(
    client, graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    c, _ = client
    await build_all(graph, library)
    view = (await c.get("/api/graph/overview")).json()
    assert view["nodes"] and view["edges"]
    scoped = (await c.get(f"/api/graph/overview?document_id={library['kidney']}")).json()
    assert {n["key"] for n in scoped["nodes"]} == {
        "hospital",
        "kidney disease patients",
        "study duration",
    }
    assert (await c.get("/api/graph/entities/hospital")).json()["facts"]
    assert (await c.get("/api/graph/entities/no-such-thing")).status_code == 404


async def test_graph_endpoints_when_disabled(library: dict[str, uuid.UUID]) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/graph/overview")).status_code == 503


async def test_deleting_a_paper_removes_it_from_the_graph(
    client, graph: GraphStore, library: dict[str, uuid.UUID]
) -> None:
    c, _ = client
    await build_all(graph, library)
    assert (await c.delete(f"/api/documents/{library['dunes']}")).status_code == 204
    assert await graph.find_entities(["Tottori"]) == []
    async with SessionLocal() as session:
        assert (await session.scalars(select(Document))).all()  # the other paper remains
