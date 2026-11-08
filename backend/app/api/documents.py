import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUser
from app.config import get_settings
from app.db import get_session
from app.graph.store import GraphStore, get_graph_store
from app.models import Chunk, Document, DocumentStatus, User, UserRole
from app.queue import JobQueue, get_queue

router = APIRouter(prefix="/documents", tags=["documents"])

Session = Annotated[AsyncSession, Depends(get_session)]
Queue = Annotated[JobQueue, Depends(get_queue)]
Graph = Annotated[GraphStore | None, Depends(get_graph_store)]
log = logging.getLogger(__name__)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    filename: str
    size_bytes: int
    authors: str | None
    language: str | None
    page_count: int | None
    chunk_count: int
    status: DocumentStatus
    progress: int
    stage: str | None
    error: str | None
    uploaded_by_id: uuid.UUID | None
    graph_status: str
    graph_entities: int
    graph_relations: int
    graph_error: str | None
    created_at: datetime
    updated_at: datetime


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    text: str
    section: str | None
    page_start: int
    page_end: int
    language: str
    token_count: int


class ChunkPage(BaseModel):
    total: int
    items: list[ChunkOut]


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile, user: CurrentUser, session: Session, queue: Queue, response: Response
) -> Document:
    """Upload a PDF. Processing happens in the background; poll the document for progress.
    Uploading a file that is already in the library returns the existing document (200)."""
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "Only PDF files are supported.")

    sha256 = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(select(Document).where(Document.sha256 == sha256))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    path = settings.storage_dir / f"{sha256}.pdf"
    path.write_bytes(data)

    filename = Path(file.filename or "document.pdf").name
    doc = Document(
        title=Path(filename).stem.replace("_", " "),
        filename=filename,
        sha256=sha256,
        file_path=str(path),
        size_bytes=len(data),
        uploaded_by_id=user.id,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    await queue.enqueue_ingest(doc.id)
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(session: Session) -> list[Document]:
    result = await session.scalars(select(Document).order_by(Document.created_at.desc()))
    return list(result)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, session: Session) -> Document:
    return await _get_or_404(session, document_id)


@router.get("/{document_id}/chunks", response_model=ChunkPage)
async def list_chunks(
    document_id: uuid.UUID,
    session: Session,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ChunkPage:
    """The chunks a document was split into. Useful for checking chunking quality."""
    await _get_or_404(session, document_id)
    where = Chunk.document_id == document_id
    total = await session.scalar(select(func.count()).select_from(Chunk).where(where))
    rows = await session.scalars(
        select(Chunk).where(where).order_by(Chunk.chunk_index).offset(offset).limit(limit)
    )
    return ChunkPage(total=total or 0, items=[ChunkOut.model_validate(c) for c in rows])


@router.get("/{document_id}/file")
async def get_document_file(document_id: uuid.UUID, session: Session) -> FileResponse:
    doc = await _get_or_404(session, document_id)
    return FileResponse(doc.file_path, media_type="application/pdf", filename=doc.filename)


@router.post("/{document_id}/reingest", response_model=DocumentOut)
async def reingest_document(
    document_id: uuid.UUID, user: CurrentUser, session: Session, queue: Queue, graph: Graph
) -> Document:
    """Process the document again, e.g. after changing the chunking settings."""
    doc = await _get_or_404(session, document_id)
    _check_can_modify(doc, user)
    if doc.status in (DocumentStatus.pending, DocumentStatus.processing):
        raise HTTPException(409, "This document is already being processed.")
    doc.status, doc.progress, doc.stage, doc.error = DocumentStatus.pending, 0, None, None
    # New chunks get new ids, so the old graph would point at passages that no longer exist.
    doc.graph_status, doc.graph_entities, doc.graph_relations = "none", 0, 0
    await _remove_from_graph(graph, doc.id)
    await session.commit()
    await session.refresh(doc)
    await queue.enqueue_ingest(doc.id)
    return doc


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: CurrentUser, session: Session, graph: Graph
) -> None:
    doc = await _get_or_404(session, document_id)
    _check_can_modify(doc, user)
    await _remove_from_graph(graph, doc.id)
    path = Path(doc.file_path)
    await session.delete(doc)  # chunks are removed by ON DELETE CASCADE
    await session.commit()
    path.unlink(missing_ok=True)


async def _remove_from_graph(graph: GraphStore | None, document_id: uuid.UUID) -> None:
    if graph is None:
        return
    try:
        await graph.delete_paper(str(document_id))
    except Exception:  # the graph is optional; never block deleting a paper
        log.exception("could not remove %s from the knowledge graph", document_id)


def _check_can_modify(doc: Document, user: User) -> None:
    """The library is shared, but only the uploader or an admin may change or delete a paper."""
    if user.role != UserRole.admin and doc.uploaded_by_id != user.id:
        raise HTTPException(403, "Only the person who uploaded this paper or an admin can do this.")


async def _get_or_404(session: AsyncSession, document_id: uuid.UUID) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    return doc
