import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUser
from app.config import get_settings
from app.db import get_session
from app.ingest.embedder import Embedder, get_embedder
from app.observability.traces import candidates_payload, record_trace
from app.retrieval.reranker import Reranker, get_reranker
from app.retrieval.search import Mode, SearchFilters, search

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=8, ge=1, le=50)
    mode: Mode = "hybrid"
    rerank: bool = True
    document_ids: list[uuid.UUID] | None = None
    language: Literal["en", "ja"] | None = None
    # Hide reranked results scoring below this. None = server default; 0 = show everything.
    min_score: float | None = Field(default=None, ge=0, le=1)


class HitOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    text: str
    section: str | None
    page_start: int
    page_end: int
    language: str
    vector_rank: int | None
    vector_score: float | None
    keyword_rank: int | None
    keyword_score: float | None
    rrf_score: float
    rerank_score: float | None


class SearchResponse(BaseModel):
    query: str
    mode: Mode
    reranked: bool
    candidates: int
    min_score: float | None  # cutoff applied (None when not reranked)
    filtered_out: int  # results hidden by the cutoff
    best_rerank_score: float | None
    timings_ms: dict[str, float]
    hits: list[HitOut]


def get_reranker_if_enabled() -> Reranker | None:
    return get_reranker() if get_settings().rerank_enabled else None


@router.post("/search", response_model=SearchResponse)
async def search_chunks(
    body: SearchRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    reranker: Annotated[Reranker | None, Depends(get_reranker_if_enabled)],
) -> SearchResponse:
    """Find the passages that best match a question, in English or Japanese."""
    settings = get_settings()
    use_reranker = reranker if body.rerank else None
    min_score = body.min_score if body.min_score is not None else settings.min_rerank_score
    result = await search(
        session,
        body.query,
        embedder=embedder,
        reranker=use_reranker,
        k=body.k,
        mode=body.mode,
        candidates=settings.search_candidates,
        rerank_candidates=settings.rerank_candidates,
        min_rerank_score=min_score,
        filters=SearchFilters(document_ids=body.document_ids, language=body.language),
    )
    await record_trace(
        kind="search",
        user_id=user.id,
        query=body.query,
        search_query=body.query,
        answered=bool(result.hits),
        candidates=candidates_payload(result),
        n_candidates=result.candidates,
        filtered_out=result.filtered_out,
        best_rerank_score=result.best_rerank_score,
        latency_ms=result.timings_ms.get("total", 0.0),
        timings_ms=result.timings_ms,
    )
    return SearchResponse(
        query=body.query,
        mode=body.mode,
        reranked=use_reranker is not None,
        candidates=result.candidates,
        min_score=min_score if use_reranker is not None else None,
        filtered_out=result.filtered_out,
        best_rerank_score=result.best_rerank_score,
        timings_ms=result.timings_ms,
        hits=[HitOut(**vars(hit)) for hit in result.hits],
    )
