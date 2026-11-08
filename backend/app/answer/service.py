"""Produces an answer as a stream of events the API forwards to the browser:

sources  -> the numbered passages the answer is built from (sent before any text)
token    -> a piece of answer text
done     -> the final record: full text, cited sources, tokens, cost, timings
error    -> something failed; the message is safe to show
"""

import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentContext, agent_graph
from app.answer import prompts
from app.config import get_settings
from app.graph.store import GraphStore
from app.ingest.embedder import Embedder
from app.ingest.text_utils import question_language
from app.llm import LLMClient, LLMError, LLMResult, Message
from app.llm.pricing import estimate_cost_usd
from app.observability.traces import candidates_payload
from app.retrieval.reranker import Reranker
from app.retrieval.search import Hit, Mode, SearchFilters, SearchResult, search

MAX_SOURCES = 6
HISTORY_MESSAGES = 6  # last 3 question/answer pairs
_CITATION = re.compile(r"[\[［](\d+(?:\s*[,，、]\s*\d+)*)[\]］]")  # ASCII or full-width


@dataclass
class AnswerRecord:
    content: str
    sources: list[dict[str, Any]]
    cited: list[int]
    search_query: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SearchOptions:
    """Search settings for one answer. Defaults come from Settings; evaluation runs override
    them to compare configurations."""

    mode: Mode = "hybrid"
    rerank: bool = True
    min_rerank_score: float | None = None  # None: use Settings.min_rerank_score
    candidates: int | None = None
    rerank_candidates: int | None = None
    agent: bool | None = None  # None: use Settings.answer_mode


@dataclass
class AnswerDeps:
    llm: LLMClient
    embedder: Embedder
    reranker: Reranker | None
    graph: GraphStore | None = None


async def answer_events(
    session: AsyncSession,
    question: str,
    history: list[Message],
    deps: AnswerDeps,
    document_ids: list[uuid.UUID] | None = None,
    options: SearchOptions | None = None,
) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    opts = options or SearchOptions()
    min_score = (
        settings.min_rerank_score if opts.min_rerank_score is None else opts.min_rerank_score
    )
    start = time.perf_counter()
    timings: dict[str, float] = {}
    usage = _Usage()

    def lap(name: str, since: float) -> float:
        now = time.perf_counter()
        timings[name] = round((now - since) * 1000, 1)
        return now

    t = start
    language = question_language(question)
    query = question
    retrieval: dict[str, Any] | None = None  # for the trace
    question_type = "lookup"
    try:
        use_agent = settings.answer_mode == "agent" if opts.agent is None else opts.agent
        if use_agent:
            # 1-2 (agent). Plan the question, then gather sources the way it needs.
            ctx = AgentContext(
                session=session, llm=deps.llm, embedder=deps.embedder,
                reranker=deps.reranker if opts.rerank else None, mode=opts.mode,
                min_rerank_score=min_score,
                candidates=opts.candidates or settings.search_candidates,
                rerank_candidates=opts.rerank_candidates or settings.rerank_candidates,
                document_ids=document_ids, graph=deps.graph,
            )  # fmt: skip
            state = await agent_graph.ainvoke(
                {"question": question, "history": history}, context=ctx
            )
            for call in ctx.llm_calls:
                usage.add(call)
            timings.update(ctx.timings_ms)
            t = time.perf_counter()
            plan, result = state["plan"], state["result"]
            query, question_type = plan.question, plan.type
            yield {"type": "plan", **plan.describe(), "steps": state.get("steps", [])}
        else:
            # 1. Follow-ups ("and for how long?") need the conversation to be searchable.
            if history:
                rewritten = await deps.llm.complete(
                    system=prompts.REWRITE_SYSTEM,
                    messages=[
                        {"role": "user", "content": prompts.rewrite_user_message(history, question)}
                    ],
                    max_tokens=1000,
                )
                usage.add(rewritten)
                query = rewritten.text.strip() or question
                t = lap("rewrite", t)

            # 2. Retrieve, with the relevance cutoff so weak matches never reach the model.
            result = await search(
                session,
                query,
                embedder=deps.embedder,
                reranker=deps.reranker if opts.rerank else None,
                k=MAX_SOURCES,
                mode=opts.mode,
                candidates=opts.candidates or settings.search_candidates,
                rerank_candidates=opts.rerank_candidates or settings.rerank_candidates,
                min_rerank_score=min_score,
                filters=SearchFilters(document_ids=document_ids),
            )
            t = lap("retrieval", t)
        hits = result.hits
        sources = [_source_payload(n, hit) for n, hit in enumerate(hits, start=1)]
        timings.update({f"search_{k}": v for k, v in result.timings_ms.items() if k != "total"})
        retrieval = {
            "candidates": candidates_payload(result),
            "n_candidates": result.candidates,
            "filtered_out": result.filtered_out,
            "best_rerank_score": result.best_rerank_score,
        }
        yield {"type": "sources", "sources": sources, "search_query": query}

        # 3. Nothing relevant: say so without calling the model (no cost, no guessing).
        if not hits:
            text = prompts.NOT_FOUND[language]
            yield {"type": "token", "text": text}
            timings["total"] = round((time.perf_counter() - start) * 1000, 1)
            yield _done(
                AnswerRecord(text, [], [], query, *usage.totals(), timings), retrieval, result
            )
            return

        # 4. Stream the answer.
        messages: list[Message] = [
            *history,
            {
                "role": "user",
                "content": prompts.answer_user_message(question, hits, language, question_type),
            },
        ]
        first_token = True
        final: LLMResult | None = None
        async for chunk in deps.llm.stream(
            system=prompts.ANSWER_SYSTEM, messages=messages, max_tokens=4000
        ):
            if isinstance(chunk, LLMResult):
                final = chunk
                continue
            if first_token:
                lap("first_token", t)
                first_token = False
            yield {"type": "token", "text": chunk}
        assert final is not None, "LLM stream ended without a final result"
        usage.add(final)
        lap("generation", t)
        timings["total"] = round((time.perf_counter() - start) * 1000, 1)

        cited = extract_citations(final.text, len(sources))
        cited_ids = {sources[n - 1]["chunk_id"] for n in cited}
        for candidate in retrieval["candidates"]:
            candidate["cited"] = candidate["chunk_id"] in cited_ids
        yield _done(
            AnswerRecord(
                content=final.text,
                sources=sources,
                cited=cited,
                search_query=query,
                model=final.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                timings_ms=timings,
            ),
            retrieval,
            result,
        )
    except LLMError as err:
        timings["total"] = round((time.perf_counter() - start) * 1000, 1)
        yield {
            "type": "error",
            "message": str(err),
            "trace": {"retrieval": retrieval, "search_query": query, "timings_ms": timings},
        }


def extract_citations(text: str, n_sources: int) -> list[int]:
    """Source numbers cited in the text, in first-cited order. Handles [1], [1][2], [1, 2]."""
    seen: dict[int, None] = {}
    for group in _CITATION.findall(text):
        for part in re.split(r"[,，、\s]+", group):
            if part.isdigit() and 1 <= int(part) <= n_sources:
                seen[int(part)] = None
    return list(seen)


def _source_payload(n: int, hit: Hit) -> dict[str, Any]:
    return {
        "n": n,
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "document_title": hit.document_title,
        "section": hit.section,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "language": hit.language,
        "text": hit.text,
        "rerank_score": hit.rerank_score,
    }


def _done(
    record: AnswerRecord, retrieval: dict[str, Any] | None, result: SearchResult
) -> dict[str, Any]:
    # search_result is for in-process callers (evaluation); the API does not send it.
    return {
        "type": "done",
        "record": asdict(record),
        "retrieval": retrieval,
        "search_result": result,
    }


class _Usage:
    """Token and cost totals across the rewrite and answer calls."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0

    def add(self, result: LLMResult) -> None:
        self.model = result.model
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cost_usd += estimate_cost_usd(result.model, result.input_tokens, result.output_tokens)

    def totals(self) -> tuple[str | None, int, int, float]:
        return self.model, self.input_tokens, self.output_tokens, round(self.cost_usd, 6)
