"""The ingestion job: parse -> chunk -> embed -> store, reporting progress as it goes."""

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.ingest.chunker import ChunkDraft, chunk_blocks
from app.ingest.embedder import Embedder
from app.ingest.pdf_parser import parse_pdf
from app.models import Chunk, Document, DocumentStatus
from app.retrieval.tokenize import search_text

log = logging.getLogger(__name__)


class IngestError(Exception):
    """A problem with the document itself, shown to the user as-is."""


INTERRUPTED = "Interrupted by a restart. Please try again."


async def mark_interrupted_jobs(sessions: async_sessionmaker[AsyncSession]) -> None:
    """At startup: the job queue does not survive a restart, so unfinished jobs are gone.

    Left as they are, these papers would look busy forever and could not be retried.
    """
    async with sessions() as session:
        await session.execute(
            update(Document)
            .where(Document.status.in_((DocumentStatus.pending, DocumentStatus.processing)))
            .values(status=DocumentStatus.failed, error=INTERRUPTED)
        )
        await session.execute(
            update(Document)
            .where(Document.graph_status == "building")
            .values(graph_status="failed", graph_error=INTERRUPTED)
        )
        await session.commit()


async def ingest_document(
    sessions: async_sessionmaker[AsyncSession], document_id: uuid.UUID, embedder: Embedder
) -> None:
    settings = get_settings()
    try:
        async with sessions() as session:
            doc = await session.get(Document, document_id)
            if doc is None:
                log.warning("document %s no longer exists; skipping", document_id)
                return
            file_path = Path(doc.file_path)
            await _update(session, doc, status=DocumentStatus.processing, progress=5,
                          stage="parsing", error=None)  # fmt: skip

        # CPU-heavy steps run in a thread so the worker's event loop stays responsive.
        parsed = await asyncio.to_thread(parse_pdf, file_path)
        drafts = chunk_blocks(
            parsed.blocks,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )
        if not drafts:
            raise IngestError(
                "No text found. The PDF may be scanned images; OCR is not supported yet."
            )

        async with sessions() as session:
            doc = await session.get(Document, document_id)
            assert doc is not None
            doc.title = parsed.title or doc.title
            doc.authors = parsed.authors
            doc.page_count = parsed.page_count
            doc.language = _document_language(drafts)
            await _update(session, doc, progress=15, stage=f"embedding 0/{len(drafts)}")

            vectors = await _embed_with_progress(session, doc, drafts, embedder)

            # Replace any chunks from an earlier run (re-ingest), all in one transaction.
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            session.add_all(
                Chunk(
                    document_id=document_id,
                    chunk_index=i,
                    text=d.text,
                    section=d.section,
                    page_start=d.page_start,
                    page_end=d.page_end,
                    language=d.language,
                    token_count=d.token_count,
                    embedding=vec,
                    search_text=search_text(embedding_input(doc, d)),
                )
                for i, (d, vec) in enumerate(zip(drafts, vectors, strict=True))
            )
            doc.chunk_count = len(drafts)
            await _update(session, doc, status=DocumentStatus.ready, progress=100, stage=None)
        log.info("ingested %s: %d pages, %d chunks", document_id, parsed.page_count, len(drafts))

    except Exception as err:
        log.exception("ingestion failed for %s", document_id)
        message = str(err) if isinstance(err, IngestError) else f"{type(err).__name__}: {err}"
        async with sessions() as session:
            doc = await session.get(Document, document_id)
            if doc is not None:
                await _update(session, doc, status=DocumentStatus.failed, stage=None,
                              error=message[:2000])  # fmt: skip


def embedding_input(doc: Document, draft: ChunkDraft) -> str:
    """What gets embedded: the chunk plus its title and section, so a chunk that says
    "we used 40 samples" can still be found by a question about the paper's topic."""
    header = " / ".join(x for x in (doc.title, draft.section) if x)
    return f"{header}\n{draft.text}" if header else draft.text


async def _embed_with_progress(
    session: AsyncSession, doc: Document, drafts: list[ChunkDraft], embedder: Embedder
) -> list[list[float]]:
    batch = get_settings().embedding_batch_size * 4
    vectors: list[list[float]] = []
    for start in range(0, len(drafts), batch):
        texts = [embedding_input(doc, d) for d in drafts[start : start + batch]]
        vectors.extend(await asyncio.to_thread(embedder.embed, texts))
        done = len(vectors)
        await _update(session, doc, progress=15 + int(80 * done / len(drafts)),
                      stage=f"embedding {done}/{len(drafts)}")  # fmt: skip
    return vectors


def _document_language(drafts: list[ChunkDraft]) -> str:
    ja = sum(d.token_count for d in drafts if d.language == "ja")
    share = ja / max(1, sum(d.token_count for d in drafts))
    return "ja" if share > 0.7 else "en" if share < 0.3 else "mixed"


async def _update(session: AsyncSession, doc: Document, **fields: object) -> None:
    for key, value in fields.items():
        setattr(doc, key, value)
    await session.commit()
