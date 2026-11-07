import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import documents, health, llm, search
from app.config import get_settings
from app.ingest.embedder import get_embedder
from app.retrieval.reranker import get_reranker

settings = get_settings()
log = logging.getLogger("shiori")


def _preload_models() -> None:
    get_embedder()
    if settings.rerank_enabled:
        get_reranker()
    log.warning("search models loaded")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Load the models in the background: the API answers immediately, and the first
    # search does not pay the ~20 s model load.
    task = None
    if settings.preload_models and settings.environment != "test":
        task = asyncio.create_task(asyncio.to_thread(_preload_models))
    yield
    if task is not None and not task.done():
        task.cancel()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router, prefix="/api")
app.include_router(llm.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(search.router, prefix="/api")
