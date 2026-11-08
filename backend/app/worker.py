"""Background worker. Run with:  uv run arq app.worker.WorkerSettings

Loads bge-m3 once at startup, then processes one document at a time
(embedding is CPU-bound, so running two at once would only slow both down).
"""

import logging
import uuid
from typing import Any

from app.db import SessionLocal
from app.graph.build import build_paper_graph
from app.graph.store import get_graph_store
from app.ingest.embedder import get_embedder
from app.ingest.pipeline import ingest_document
from app.llm import get_llm
from app.queue import redis_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def startup(ctx: dict[str, Any]) -> None:
    logging.getLogger(__name__).info("loading embedding model...")
    ctx["embedder"] = get_embedder()


async def build_graph(ctx: dict[str, Any], document_id: str) -> None:
    """Extract entities and relations for one paper into the knowledge graph."""
    store = get_graph_store()
    if store is None:
        return
    await build_paper_graph(SessionLocal, uuid.UUID(document_id), get_llm(), store)


async def ingest(ctx: dict[str, Any], document_id: str) -> None:
    await ingest_document(SessionLocal, uuid.UUID(document_id), ctx["embedder"])


class WorkerSettings:
    functions = [ingest, build_graph]
    on_startup = startup
    redis_settings = redis_settings()
    max_jobs = 1
    job_timeout = 60 * 60  # large PDFs on a CPU can take a while
    keep_result = 0
