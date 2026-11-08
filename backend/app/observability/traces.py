"""Builds and stores trace records. Recording never breaks a request: failures are logged."""

import hashlib
import logging
import uuid
from typing import Any

from app.answer.prompts import ANSWER_SYSTEM
from app.db import SessionLocal
from app.models import Trace
from app.retrieval.search import Hit, SearchResult

log = logging.getLogger(__name__)

# Changes whenever the answer prompt changes, so quality can be compared across versions.
PROMPT_VERSION = hashlib.sha256(ANSWER_SYSTEM.encode()).hexdigest()[:10]


def candidates_payload(result: SearchResult, cited_chunk_ids: set[str] | None = None) -> list[dict]:
    kept = {hit.chunk_id for hit in result.hits}
    return [_candidate(hit, hit.chunk_id in kept, cited_chunk_ids) for hit in result.considered]


def _candidate(hit: Hit, kept: bool, cited: set[str] | None) -> dict[str, Any]:
    return {
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "document_title": hit.document_title,
        "section": hit.section,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "language": hit.language,
        "preview": hit.text[:300],
        "vector_rank": hit.vector_rank,
        "vector_score": hit.vector_score,
        "keyword_rank": hit.keyword_rank,
        "rerank_score": hit.rerank_score,
        "kept": kept,
        "cited": bool(cited and str(hit.chunk_id) in cited),
    }


async def record_trace(**fields: Any) -> uuid.UUID | None:
    try:
        async with SessionLocal() as session:
            trace = Trace(**fields)
            session.add(trace)
            await session.commit()
            return trace.id
    except Exception:
        log.exception("could not record trace")
        return None
