"""Background worker. Run with:  uv run arq app.worker.WorkerSettings

Loads bge-m3 once at startup, then processes one document at a time
(embedding is CPU-bound, so running two at once would only slow both down).
"""

import logging
import uuid
from typing import Any

from app.db import SessionLocal
from app.ingest.embedder import get_embedder
from app.ingest.pipeline import ingest_document
from app.queue import redis_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def startup(ctx: dict[str, Any]) -> None:
    logging.getLogger(__name__).info("loading embedding model...")
    ctx["embedder"] = get_embedder()


async def ingest(ctx: dict[str, Any], document_id: str) -> None:
    await ingest_document(SessionLocal, uuid.UUID(document_id), ctx["embedder"])


class WorkerSettings:
    functions = [ingest]
    on_startup = startup
    redis_settings = redis_settings()
    max_jobs = 1
    job_timeout = 60 * 60  # large PDFs on a CPU can take a while
    keep_result = 0
