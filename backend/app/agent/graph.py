"""The answer agent as a LangGraph graph:

    plan ──▶ details   (paper metadata as sources)
         ├─▶ search    (multi-query hybrid search; per paper for comparisons)
         └─▶ read      (whole papers in order, for "find all" and summaries)

The graph gathers sources; the answer itself is streamed afterwards by the answer service, so
the browser sees tokens as they arrive. LLM usage and per-step timings are collected on the
context so cost and latency stay visible in traces.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.catalog import CatalogEntry, details_hit, load_catalog
from app.agent.router import Plan, plan_question
from app.graph.store import Fact, GraphStore
from app.ingest.embedder import Embedder
from app.llm import LLMClient, LLMResult, Message
from app.models import Chunk, Document
from app.retrieval.reranker import Reranker
from app.retrieval.search import Hit, Mode, SearchFilters, SearchResult, _load_hits, search

MAX_READ_CHUNKS = 40  # passages read for "find all" / summaries (~14k tokens)
MAX_READ_TOKENS = 16_000
PER_PAPER_SOURCES = 4  # for comparisons
GRAPH_FACT_LIMIT = 30  # relations given to the answer
GRAPH_PASSAGES = 8  # passages behind those relations


@dataclass
class AgentContext:
    session: AsyncSession
    llm: LLMClient
    embedder: Embedder
    reranker: Reranker | None
    mode: Mode
    min_rerank_score: float
    candidates: int
    rerank_candidates: int
    document_ids: list[uuid.UUID] | None = None  # the user's "answer from" filter
    graph: GraphStore | None = None  # knowledge graph, when enabled
    llm_calls: list[LLMResult] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


class AgentState(TypedDict, total=False):
    question: str
    history: list[Message]
    catalog: list[CatalogEntry]
    plan: Plan
    graph_available: bool
    result: SearchResult
    steps: list[str]  # human-readable, shown under the answer


async def plan_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    start = time.perf_counter()
    catalog = await load_catalog(ctx.session, ctx.document_ids)
    plan, reply = await plan_question(ctx.llm, state["question"], state["history"], catalog)
    if reply is not None:
        ctx.llm_calls.append(reply)
    _lap(ctx, "plan", start)
    return {"catalog": catalog, "plan": plan, "graph_available": ctx.graph is not None}


def choose_branch(state: AgentState) -> str:
    kind = state["plan"].type
    if kind == "details":
        return "details"
    if kind in ("find_all", "summary"):
        return "read"
    if kind == "relations" and state.get("graph_available"):
        return "graph"
    return "search"


async def details_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    plan = state["plan"]
    entries = plan.papers or state["catalog"][:10]
    hits = [details_hit(e) for e in entries]
    titles = ", ".join(f"“{e.title}”" for e in entries) or "no papers"
    return {
        "result": SearchResult(hits=hits, considered=hits, candidates=len(hits)),
        "steps": [f"Looked up the document details of {titles}"],
    }


async def search_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    plan = state["plan"]
    start = time.perf_counter()
    if plan.type == "compare" and len(plan.papers) >= 2:
        results = [await _search(ctx, plan, [p.id], k=PER_PAPER_SOURCES) for p in plan.papers]
        result = _merge(results)
        steps = [f"Searched each paper separately: {', '.join(p.title for p in plan.papers)}"]
    else:
        scope = [p.id for p in plan.papers] or None
        result = await _search(ctx, plan, scope, k=6)
        if not result.hits and scope:  # the planner may have picked the wrong paper
            result = await _search(ctx, plan, None, k=6)
        steps = ["Searched for: " + " · ".join(f"“{q}”" for q in plan.queries)]
    _lap(ctx, "retrieval", start)
    return {"result": result, "steps": steps}


async def read_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    plan = state["plan"]
    start = time.perf_counter()
    papers = plan.papers
    if not papers:
        if len(state["catalog"]) == 1:
            papers = state["catalog"]
        else:  # not named: read the papers the best search results come from
            found = await _search(ctx, plan, None, k=3, cutoff=0.0)
            ids = list(dict.fromkeys(h.document_id for h in found.hits))[:2]
            papers = [e for e in state["catalog"] if e.id in ids]

    hits, total = await _read_papers(ctx.session, [p.id for p in papers])
    titles = ", ".join(f"“{p.title}”" for p in papers) or "no papers"
    step = (
        f"Read all {len(hits)} passages of {titles}"
        if len(hits) == total
        else f"Read the first {len(hits)} of {total} passages of {titles} (too long to read whole)"
    )
    _lap(ctx, "retrieval", start)
    return {
        "result": SearchResult(hits=hits, considered=hits, candidates=total),
        "steps": [step],
    }


async def graph_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Relations from the knowledge graph, plus the passages they were extracted from."""
    ctx = runtime.context
    plan = state["plan"]
    assert ctx.graph is not None
    start = time.perf_counter()
    paper_ids = [str(p.id) for p in plan.papers] or (
        [str(d) for d in ctx.document_ids] if ctx.document_ids else None
    )
    facts: list[Fact] = []
    lines: list[str] = []
    found = await ctx.graph.find_entities(plan.entities or [plan.question], paper_ids)
    if found:
        uids = [e["uid"] for e in found]
        if len(uids) >= 2:  # how the first two are connected, if they are
            facts += await ctx.graph.facts_between(uids[0], uids[1])
        facts += await ctx.graph.facts_around(uids[:4], limit=GRAPH_FACT_LIMIT)
    if len(plan.papers) >= 2:  # what the papers have in common
        titles = {str(p.id): p.title for p in plan.papers}
        for item in await ctx.graph.shared_entities([str(p.id) for p in plan.papers]):
            shared_in = ", ".join(f"“{titles.get(pid, pid)}”" for pid in item["papers"])
            lines.append(f"{item['name']} ({item['type']}) appears in {shared_in}")

    facts = list({(f.source, f.relation, f.target, f.chunk_id): f for f in facts}.values())
    facts = facts[:GRAPH_FACT_LIMIT]
    if not facts and not lines:
        result = await _search(ctx, plan, [p.id for p in plan.papers] or None, k=6)
        _lap(ctx, "retrieval", start)
        return {
            "result": result,
            "steps": ["The knowledge graph had nothing on this; searched the passages instead"],
        }

    # The passages the facts come from, most-used first, so answers cite real text.
    counts: dict[str, int] = {}
    for f in facts:
        counts[f.chunk_id] = counts.get(f.chunk_id, 0) + 1
    chunk_ids = [uuid.UUID(c) for c in sorted(counts, key=counts.__getitem__, reverse=True)]
    passages = await _load_hits(ctx.session, chunk_ids[:GRAPH_PASSAGES])
    lines += [f"{f.line()} (evidence: “{f.evidence}”)" for f in facts]
    first_doc = passages[0].document_id if passages else plan.papers[0].id
    facts_hit = Hit(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_URL, "shiori-graph:" + "|".join(lines)),
        document_id=first_doc, document_title="Knowledge graph", text="\n".join(lines),
        section="Relations extracted from the papers", page_start=1, page_end=1, language="en",
    )  # fmt: skip
    hits = [facts_hit, *passages]
    names = ", ".join(e["name"] for e in found[:4]) or "the papers' shared entities"
    _lap(ctx, "retrieval", start)
    return {
        "result": SearchResult(hits=hits, considered=hits, candidates=len(hits)),
        "steps": [
            f"Knowledge graph: {names} · {len(facts)} relations from {len(passages)} passages"
        ],
    }


async def _search(
    ctx: AgentContext,
    plan: Plan,
    document_ids: list[uuid.UUID] | None,
    k: int,
    cutoff: float | None = None,
) -> SearchResult:
    scope = document_ids or ctx.document_ids
    return await search(
        ctx.session,
        plan.question,
        extra_queries=plan.queries,
        embedder=ctx.embedder,
        reranker=ctx.reranker,
        k=k,
        mode=ctx.mode,
        candidates=ctx.candidates,
        rerank_candidates=ctx.rerank_candidates,
        min_rerank_score=ctx.min_rerank_score if cutoff is None else cutoff,
        filters=SearchFilters(document_ids=scope),
    )


def _merge(results: list[SearchResult]) -> SearchResult:
    """Interleave per-paper results so every paper is represented near the top."""
    hits: list[Hit] = []
    for rank in range(max((len(r.hits) for r in results), default=0)):
        hits.extend(r.hits[rank] for r in results if rank < len(r.hits))
    scores = [r.best_rerank_score for r in results if r.best_rerank_score is not None]
    return SearchResult(
        hits=hits,
        considered=[h for r in results for h in r.considered],
        candidates=sum(r.candidates for r in results),
        filtered_out=sum(r.filtered_out for r in results),
        best_rerank_score=max(scores) if scores else None,
    )


async def _read_papers(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> tuple[list[Hit], int]:
    if not document_ids:
        return [], 0
    rows = (
        await session.execute(
            select(Chunk, Document.title)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.document_id.in_(document_ids))
            .order_by(Document.created_at, Chunk.chunk_index)
        )
    ).all()
    hits: list[Hit] = []
    tokens = 0
    for chunk, title in rows:
        if len(hits) >= MAX_READ_CHUNKS or tokens + chunk.token_count > MAX_READ_TOKENS:
            break
        tokens += chunk.token_count
        hits.append(
            Hit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                text=chunk.text,
                section=chunk.section,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                language=chunk.language,
            )  # fmt: skip
        )
    return hits, len(rows)


def _lap(ctx: AgentContext, name: str, start: float) -> None:
    ctx.timings_ms[name] = round(
        ctx.timings_ms.get(name, 0.0) + (time.perf_counter() - start) * 1000, 1
    )


def build_graph():
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("plan", plan_node)
    graph.add_node("details", details_node)
    graph.add_node("search", search_node)
    graph.add_node("read", read_node)
    graph.add_node("graph", graph_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        choose_branch,
        {"details": "details", "search": "search", "read": "read", "graph": "graph"},
    )
    for node in ("details", "search", "read", "graph"):
        graph.add_edge(node, END)
    return graph.compile()


agent_graph = build_graph()
