"""The library catalog: what papers exist, and their details as citable sources.

Questions like "Who wrote the DPR paper?" are answered by a paper's metadata, not its text,
so search can never find them. The catalog turns each paper's details into a pseudo-passage
the answer can cite like any other source.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, DocumentStatus
from app.retrieval.search import Hit

_DETAILS_NAMESPACE = uuid.UUID("5b1c2a40-7e0f-4f6a-9c1e-3d2b8a6f4e10")
LANGUAGES = {"en": "English", "ja": "Japanese", "mixed": "English and Japanese"}


@dataclass
class CatalogEntry:
    key: str  # short id shown to the LLM: P1, P2, ...
    id: uuid.UUID
    title: str
    authors: str | None
    page_count: int | None
    language: str | None
    chunk_count: int
    filename: str
    created_at: datetime
    # Start of the first page (title and author block): many PDFs, arXiv ones included, have no
    # author metadata, but the authors are printed right under the title.
    opening: str = ""


async def load_catalog(
    session: AsyncSession, document_ids: list[uuid.UUID] | None = None
) -> list[CatalogEntry]:
    stmt = select(Document).where(Document.status == DocumentStatus.ready)
    if document_ids:
        stmt = stmt.where(Document.id.in_(document_ids))
    docs = (await session.scalars(stmt.order_by(Document.created_at))).all()
    openings = dict(
        (
            await session.execute(
                select(Chunk.document_id, Chunk.text).where(
                    Chunk.chunk_index == 0, Chunk.document_id.in_([d.id for d in docs])
                )
            )
        ).all()
    )
    return [
        CatalogEntry(
            key=f"P{i}",
            id=d.id,
            title=d.title,
            authors=d.authors,
            page_count=d.page_count,
            language=d.language,
            chunk_count=d.chunk_count,
            filename=d.filename,
            created_at=d.created_at,
            opening=(openings.get(d.id) or "")[:600],
        )  # fmt: skip
        for i, d in enumerate(docs, start=1)
    ]


def catalog_text(entries: list[CatalogEntry]) -> str:
    """One line per paper, for the planner."""
    if not entries:
        return "(the library is empty)"
    return "\n".join(
        f"{e.key}: {e.title} | authors: {e.authors or 'unknown'} | "
        f"{e.page_count or '?'} pages | {LANGUAGES.get(e.language or '', 'unknown language')}"
        for e in entries
    )


def details_hit(entry: CatalogEntry) -> Hit:
    """A paper's details as a source the answer can cite."""
    # Saying "not recorded" next to a first page that names them makes the LLM report no authors.
    unknown = "see the beginning of the first page below" if entry.opening else "not recorded"
    text = "\n".join(
        [
            f"Title: {entry.title}",
            f"Authors: {entry.authors or unknown}",
            f"Pages: {entry.page_count or 'unknown'}",
            f"Language: {LANGUAGES.get(entry.language or '', 'unknown')}",
            f"File name: {entry.filename}",
            f"Added to the library: {entry.created_at:%Y-%m-%d}",
            f"Passages indexed: {entry.chunk_count}",
            f"Beginning of the first page: {entry.opening}" if entry.opening else "",
        ]
    ).strip()
    return Hit(
        chunk_id=uuid.uuid5(_DETAILS_NAMESPACE, str(entry.id)),
        document_id=entry.id,
        document_title=entry.title,
        text=text,
        section="Document details",
        page_start=1,
        page_end=1,
        language="en",
    )
