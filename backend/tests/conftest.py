"""Test setup. Runs before any app module is imported, so these settings take effect."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator

import pytest

TEST_DB = "shiori_test"
_base_url = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://shiori:shiori@127.0.0.1:5432/shiori"
)
os.environ["DATABASE_URL"] = _base_url.rsplit("/", 1)[0] + f"/{TEST_DB}"
os.environ["ENVIRONMENT"] = "test"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="shiori-test-")
os.environ["ANTHROPIC_API_KEY"] = ""  # tests must never call the real API


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
        await db.execute(text("TRUNCATE documents, chunks, conversations, messages CASCADE"))
    yield


# Shared fixtures. Imported last: they import app modules, which must see the settings above.
from tests.fakes import library  # noqa: E402,F401
