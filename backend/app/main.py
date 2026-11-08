import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    auth,
    conversations,
    documents,
    evaluation,
    graph,
    health,
    insights,
    llm,
    search,
    users,
)
from app.auth.deps import get_current_user
from app.config import get_settings
from app.evaluation.runner import mark_interrupted_runs
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
    if settings.environment != "test":
        await mark_interrupted_runs()
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
# Public: health check and login. Everything else requires a logged-in user.
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")  # admin only (checked in the router)
app.include_router(insights.router, prefix="/api")  # admin only
app.include_router(evaluation.router, prefix="/api")  # admin only
_login_required = [Depends(get_current_user)]
for module in (llm, documents, search, conversations, graph):
    app.include_router(module.router, prefix="/api", dependencies=_login_required)
