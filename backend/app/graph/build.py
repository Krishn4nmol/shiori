"""Builds one paper's part of the knowledge graph: passages in batches -> extraction -> Neo4j."""

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.graph.extract import Extraction, extract_batch
from app.graph.store import GraphStore
from app.llm import LLMClient, LLMError
from app.models import Chunk, Document

log = logging.getLogger(__name__)


async def build_paper_graph(
    sessions: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    llm: LLMClient,
    store: GraphStore,
) -> Extraction:
    async with sessions() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            raise ValueError("Document not found.")
        chunks = list(
            await session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            )
        )
        title = doc.title
        await _set(session, document_id, graph_status="building", graph_error=None)

    try:
        batch = max(1, get_settings().graph_batch_passages)
        extraction = Extraction()
        for start in range(0, len(chunks), batch):
            part = chunks[start : start + batch]
            extraction.merge(await extract_batch(llm, [(str(c.id), c.text) for c in part]))
        await store.ensure_schema()
        await store.replace_paper(
            str(document_id), title, [(str(c.id), c.page_start, c.page_end) for c in chunks],
            extraction,
        )  # fmt: skip
        entities, relations = await store.counts(str(document_id))
        async with sessions() as session:
            await _set(session, document_id, graph_status="ready", graph_entities=entities,
                       graph_relations=relations)  # fmt: skip
        log.info(
            "graph for %s: %d entities, %d relations, %d items dropped",
            document_id, entities, relations, len(extraction.dropped),
        )  # fmt: skip
        for reason in extraction.dropped[:10]:  # why items were rejected, for tuning
            log.info("  dropped: %s", reason[:300])
        return extraction
    except Exception as err:
        log.exception("graph build failed for %s", document_id)
        message = str(err) if isinstance(err, LLMError) else f"{type(err).__name__}: {err}"
        async with sessions() as session:
            await _set(session, document_id, graph_status="failed", graph_error=message[:2000])
        raise


async def _set(session: AsyncSession, document_id: uuid.UUID, **values: object) -> None:
    await session.execute(update(Document).where(Document.id == document_id).values(**values))
    await session.commit()
