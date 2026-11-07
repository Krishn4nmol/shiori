"""Hybrid search: vector + keyword, fused with Reciprocal Rank Fusion, then reranked.

Why both? Vector search finds passages with the same meaning in other words and other
languages ("how many sites" -> "四十地点"). Keyword search finds exact terms that vector search
blurs: names, numbers, rare technical words. RRF merges the two ranked lists by position,
so their very different score scales never need to be compared.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Select, bindparam, cast, func, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.embedder import Embedder
from app.models import Chunk, Document, DocumentStatus
from app.retrieval.reranker import Reranker
from app.retrieval.tokenize import tsquery

RRF_K = 60  # standard constant from the RRF paper; damps the advantage of rank 1 vs 2

Mode = Literal["hybrid", "vector", "keyword"]


@dataclass
class SearchFilters:
    document_ids: list[uuid.UUID] | None = None
    language: str | None = None


@dataclass
class Hit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    text: str
    section: str | None
    page_start: int
    page_end: int
    language: str
    vector_rank: int | None = None
    vector_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None


@dataclass
class SearchResult:
    hits: list[Hit]
    timings_ms: dict[str, float] = field(default_factory=dict)
    candidates: int = 0  # distinct chunks found before reranking
    filtered_out: int = 0  # reranked results dropped by the relevance cutoff
    best_rerank_score: float | None = None  # shown when everything was filtered out


class _Timer:
    def __init__(self) -> None:
        self.ms: dict[str, float] = {}
        self._start = time.perf_counter()

    def lap(self, name: str, since: float) -> float:
        now = time.perf_counter()
        self.ms[name] = round((now - since) * 1000, 1)
        return now

    def total(self) -> dict[str, float]:
        self.ms["total"] = round((time.perf_counter() - self._start) * 1000, 1)
        return self.ms


async def search(
    session: AsyncSession,
    query: str,
    *,
    embedder: Embedder,
    reranker: Reranker | None,
    k: int = 8,
    mode: Mode = "hybrid",
    candidates: int = 40,
    rerank_candidates: int = 20,
    min_rerank_score: float | None = None,
    filters: SearchFilters | None = None,
) -> SearchResult:
    filters = filters or SearchFilters()
    timer = _Timer()
    t = time.perf_counter()

    vector_ranked: list[tuple[uuid.UUID, float]] = []
    keyword_ranked: list[tuple[uuid.UUID, float]] = []

    if mode in ("hybrid", "vector"):
        query_vector = (await asyncio.to_thread(embedder.embed, [query]))[0]
        t = timer.lap("embed", t)
        vector_ranked = await _vector_search(session, query_vector, candidates, filters)
        t = timer.lap("vector", t)

    if mode in ("hybrid", "keyword"):
        keyword_ranked = await _keyword_search(session, query, candidates, filters)
        t = timer.lap("keyword", t)

    fused = rrf_fuse([vector_ranked, keyword_ranked])
    hits = await _load_hits(session, [chunk_id for chunk_id, _ in fused])
    by_id = {hit.chunk_id: hit for hit in hits}
    for chunk_id, score in fused:
        if chunk_id in by_id:
            by_id[chunk_id].rrf_score = round(score, 6)
    for rank, (chunk_id, score) in enumerate(vector_ranked, start=1):
        if chunk_id in by_id:
            by_id[chunk_id].vector_rank, by_id[chunk_id].vector_score = rank, round(score, 4)
    for rank, (chunk_id, score) in enumerate(keyword_ranked, start=1):
        if chunk_id in by_id:
            by_id[chunk_id].keyword_rank, by_id[chunk_id].keyword_score = rank, round(score, 4)
    t = timer.lap("fuse", t)

    result = SearchResult(hits=[], candidates=len(hits))
    if reranker is not None and hits:
        pool = hits[:rerank_candidates]
        scores = await asyncio.to_thread(reranker.score, query, [h.text for h in pool])
        for hit, score in zip(pool, scores, strict=True):
            hit.rerank_score = score  # small probabilities: keep full precision
        hits = sorted(pool, key=lambda h: h.rerank_score or 0.0, reverse=True)
        result.best_rerank_score = hits[0].rerank_score
        if min_rerank_score:
            # Only the reranker's scores mean "does this answer the question?" well enough
            # to cut on. Vector and keyword scores just say "somewhat similar".
            kept = [h for h in hits if (h.rerank_score or 0.0) >= min_rerank_score]
            result.filtered_out = len(hits) - len(kept)
            hits = kept
        timer.lap("rerank", t)

    result.hits = hits[:k]
    result.timings_ms = timer.total()
    return result


def rrf_fuse(rankings: list[list[tuple[uuid.UUID, float]]]) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal Rank Fusion: score = sum over lists of 1 / (RRF_K + rank)."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, (chunk_id, _) in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _filtered(stmt: Select, filters: SearchFilters) -> Select:
    stmt = stmt.join(Document, Document.id == Chunk.document_id).where(
        Document.status == DocumentStatus.ready
    )
    if filters.document_ids:
        stmt = stmt.where(Chunk.document_id.in_(filters.document_ids))
    if filters.language:
        stmt = stmt.where(Chunk.language == filters.language)
    return stmt


async def _vector_search(
    session: AsyncSession, vector: list[float], limit: int, filters: SearchFilters
) -> list[tuple[uuid.UUID, float]]:
    distance = Chunk.embedding.cosine_distance(vector)
    stmt = _filtered(select(Chunk.id, (1 - distance).label("score")), filters)
    stmt = stmt.where(Chunk.embedding.is_not(None)).order_by(distance).limit(limit)
    # Search more of the HNSW graph so filters don't leave us with too few results.
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    return [(row.id, float(row.score)) for row in await session.execute(stmt)]


async def _keyword_search(
    session: AsyncSession, query: str, limit: int, filters: SearchFilters
) -> list[tuple[uuid.UUID, float]]:
    raw = tsquery(query)
    if raw is None:
        return []
    q = cast(bindparam("q", raw), TSQUERY)  # raw lexemes, not re-parsed by Postgres
    # Normalization 1: divide by 1 + log(length) so long chunks don't win just by size.
    rank = func.ts_rank(Chunk.search_tsv, q, 1)
    stmt = _filtered(select(Chunk.id, rank.label("score")), filters)
    stmt = stmt.where(Chunk.search_tsv.op("@@")(q)).order_by(rank.desc()).limit(limit)
    rows = await session.execute(stmt)
    return [(row.id, float(row.score)) for row in rows]


async def _load_hits(session: AsyncSession, chunk_ids: list[uuid.UUID]) -> list[Hit]:
    if not chunk_ids:
        return []
    rows = await session.execute(
        select(Chunk, Document.title)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(chunk_ids))
    )
    by_id = {
        chunk.id: Hit(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_title=title,
            text=chunk.text,
            section=chunk.section,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            language=chunk.language,
        )
        for chunk, title in rows
    }
    return [by_id[i] for i in chunk_ids if i in by_id]  # keep fused order
