"""Test setup. Runs before any app module is imported, so these settings take effect."""

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest

TEST_DB = "shiori_test"
_base_url = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://shiori:shiori@127.0.0.1:5432/shiori"
)
os.environ["DATABASE_URL"] = _base_url.rsplit("/", 1)[0] + f"/{TEST_DB}"
os.environ["ENVIRONMENT"] = "test"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="shiori-test-")
os.environ["ANTHROPIC_API_KEY"] = ""  # tests must never call a real LLM API
os.environ["LLM_API_KEY"] = ""
os.environ["ANSWER_MODE"] = "classic"  # agent tests switch it on explicitly
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256-signing"

# Every test runs as this user unless it uses the `real_auth` fixture.
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _prepare_database() -> None:
    import asyncpg
    from sqlalchemy import text

    admin_dsn = _base_url.replace("+asyncpg", "").rsplit("/", 1)[0] + "/postgres"
    conn = await asyncpg.connect(admin_dsn, timeout=5)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()

    from app import models  # noqa: F401  (registers tables)
    from app.db import Base, engine

    async with engine.begin() as db:
        await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await db.run_sync(Base.metadata.drop_all)
        await db.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="session")
def database() -> None:
    """A fresh test database, or skip DB tests when Postgres isn't running."""
    try:
        asyncio.run(_prepare_database())
    except (TimeoutError, OSError) as err:
        pytest.skip(f"Postgres not available ({err}); run `docker compose up -d`")


@pytest.fixture
async def clean_db(database: None) -> AsyncIterator[None]:
    from sqlalchemy import text

    from app.db import engine

    async with engine.begin() as db:
        await db.execute(
            text(
                "TRUNCATE users, documents, chunks, conversations, messages, traces, "
                "eval_questions, eval_runs, eval_results CASCADE"
            )
        )
        # The default test user must exist: papers and conversations reference it.
        await db.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, role, is_active, "
                "session_version) VALUES (:id, 'tester@lab.test', 'Tester', 'x', 'admin', "
                "true, 0)"
            ),
            {"id": TEST_USER_ID},
        )
    yield


def _test_user():
    from app.models import User, UserRole

    return User(
        id=TEST_USER_ID,
        email="tester@lab.test",
        name="Tester",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
        session_version=0,
    )


@pytest.fixture(autouse=True)
def logged_in(request: pytest.FixtureRequest):
    """Log every test in as the test user, except tests that use `real_auth`."""
    from app.auth.deps import get_current_user
    from app.auth.rate_limit import login_limiter
    from app.evaluation import runner
    from app.main import app

    login_limiter.reset()
    runner.RETRY_WAITS = (0, 0)  # never sleep on rate-limit retries in tests
    if "real_auth" not in request.fixturenames:
        app.dependency_overrides[get_current_user] = _test_user
    # Never load the real embedding/reranker models (GBs to download in CI): tests that care
    # about search install their own fakes, which replace these.
    from app.api.search import get_reranker_if_enabled
    from app.ingest.embedder import get_embedder
    from tests.fakes import TokenEmbedder

    app.dependency_overrides[get_embedder] = TokenEmbedder
    app.dependency_overrides[get_reranker_if_enabled] = lambda: None
    # No knowledge graph unless a test asks for one (tests then use the "test" namespace).
    from app.graph.store import get_graph_store

    app.dependency_overrides[get_graph_store] = lambda: None
    yield
    for dep in (get_current_user, get_embedder, get_reranker_if_enabled, get_graph_store):
        app.dependency_overrides.pop(dep, None)


@pytest.fixture
def real_auth() -> None:
    """Use real sessions (cookies) instead of the automatic test login."""


# Shared fixtures. Imported last: they import app modules, which must see the settings above.
from tests.fakes import library  # noqa: E402,F401
