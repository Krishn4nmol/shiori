"""Hands ingestion jobs to the background worker through Redis (arq)."""

import uuid
from typing import Protocol

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import get_settings


class JobQueue(Protocol):
    async def enqueue_ingest(self, document_id: uuid.UUID) -> None: ...

    async def enqueue_graph(self, document_id: uuid.UUID) -> None: ...


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class ArqQueue:
    def __init__(self) -> None:
        self._pool: ArqRedis | None = None

    async def enqueue_ingest(self, document_id: uuid.UUID) -> None:
        if self._pool is None:
            self._pool = await create_pool(redis_settings())
        # A fixed job id means a document is never queued twice at the same time.
        await self._pool.enqueue_job("ingest", str(document_id), _job_id=f"ingest:{document_id}")

    async def enqueue_graph(self, document_id: uuid.UUID) -> None:
        if self._pool is None:
            self._pool = await create_pool(redis_settings())
        await self._pool.enqueue_job(
            "build_graph", str(document_id), _job_id=f"graph:{document_id}"
        )


_queue = ArqQueue()


def get_queue() -> JobQueue:
    return _queue
