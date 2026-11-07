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

from app.answer import prompts
from app.config import get_settings
from app.ingest.embedder import Embedder
from app.ingest.text_utils import detect_language
from app.llm import LLMClient, LLMError, LLMResult, Message
from app.llm.pricing import estimate_cost_usd
from app.retrieval.reranker import Reranker
from app.retrieval.search import Hit, SearchFilters, search

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
class AnswerDeps:
    llm: LLMClient
    embedder: Embedder
    reranker: Reranker | None


async def answer_events(
    session: AsyncSession,
    question: str,
    history: list[Message],
    deps: AnswerDeps,
    document_ids: list[uuid.UUID] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    start = time.perf_counter()
    timings: dict[str, float] = {}
    usage = _Usage()

    def lap(name: str, since: float) -> float:
        now = time.perf_counter()
        timings[name] = round((now - since) * 1000, 1)
        return now

    t = start
    language = detect_language(question)
    query = question
    try:
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
            reranker=deps.reranker,
            k=MAX_SOURCES,
            candidates=settings.search_candidates,
            rerank_candidates=settings.rerank_candidates,
            min_rerank_score=settings.min_rerank_score,
            filters=SearchFilters(document_ids=document_ids),
        )
        hits = result.hits
        sources = [_source_payload(n, hit) for n, hit in enumerate(hits, start=1)]
        t = lap("retrieval", t)
        yield {"type": "sources", "sources": sources, "search_query": query}

        # 3. Nothing relevant: say so without calling the model (no cost, no guessing).
        if not hits:
            text = prompts.NOT_FOUND[language]
            yield {"type": "token", "text": text}
            timings["total"] = round((time.perf_counter() - start) * 1000, 1)
            yield _done(AnswerRecord(text, [], [], query, *usage.totals(), timings))
            return

        # 4. Stream the answer.
        messages: list[Message] = [
            *history,
            {"role": "user", "content": prompts.answer_user_message(question, hits)},
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

        yield _done(
            AnswerRecord(
                content=final.text,
                sources=sources,
                cited=extract_citations(final.text, len(sources)),
                search_query=query,
                model=final.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                timings_ms=timings,
            )
        )
    except LLMError as err:
        yield {"type": "error", "message": str(err)}


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


def _done(record: AnswerRecord) -> dict[str, Any]:
    return {"type": "done", "record": asdict(record)}


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
