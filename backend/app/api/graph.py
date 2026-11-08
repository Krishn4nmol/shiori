"""Knowledge graph API: build a paper's graph, and browse entities and relations."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import DocumentOut, _check_can_modify, _get_or_404
from app.auth.deps import CurrentUser
from app.db import get_session
from app.graph.store import GraphStore, get_graph_store
from app.models import Document, DocumentStatus
from app.queue import JobQueue, get_queue

router = APIRouter(prefix="/graph", tags=["graph"])
Session = Annotated[AsyncSession, Depends(get_session)]


def require_graph(store: Annotated[GraphStore | None, Depends(get_graph_store)]) -> GraphStore:
    if store is None:
        raise HTTPException(503, "The knowledge graph is turned off (GRAPH_ENABLED=false).")
    return store


Graph = Annotated[GraphStore, Depends(require_graph)]


@router.post(
    "/papers/{document_id}/build",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def build_graph(
    document_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    queue: Annotated[JobQueue, Depends(get_queue)],
    _: Graph,
) -> Document:
    """Extract this paper's entities and relations in the background (about one LLM call per
    four passages). Poll the document's graph_status."""
    doc = await _get_or_404(session, document_id)
    _check_can_modify(doc, user)
    if doc.status != DocumentStatus.ready:
        raise HTTPException(409, "Wait until the paper has finished processing.")
    if doc.graph_status == "building":
        raise HTTPException(409, "The graph for this paper is already being built.")
    doc.graph_status, doc.graph_error = "building", None
    await session.commit()
    await session.refresh(doc)
    await queue.enqueue_graph(doc.id)
    return doc


@router.get("/overview")
async def overview(graph: Graph, document_id: uuid.UUID | None = None) -> dict[str, Any]:
    return await graph.overview(str(document_id) if document_id else None)


@router.get("/entities/{key}")
async def entity(key: str, graph: Graph) -> dict[str, Any]:
    data = await graph.entity(key)
    if data is None:
        raise HTTPException(404, "Entity not found.")
    return data
