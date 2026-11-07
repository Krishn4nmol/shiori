import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.ingest.embedder import HashEmbedder
from app.ingest.pipeline import ingest_document
from app.main import app
from app.models import Chunk, Document, DocumentStatus
from app.queue import get_queue
from tests.pdf_factory import make_paper

pytestmark = pytest.mark.usefixtures("clean_db")


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[uuid.UUID] = []

    async def enqueue_ingest(self, document_id: uuid.UUID) -> None:
        self.jobs.append(document_id)


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(queue: FakeQueue) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_queue] = lambda: queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def pdf_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return make_paper(tmp_path_factory.mktemp("pdf") / "dunes.pdf").read_bytes()


async def _upload(client: AsyncClient, data: bytes, name: str = "dune_paper.pdf"):
    return await client.post("/api/documents", files={"file": (name, data, "application/pdf")})


async def test_upload_saves_file_and_queues_job(
    client: AsyncClient, queue: FakeQueue, pdf_bytes: bytes
) -> None:
    res = await _upload(client, pdf_bytes)
    assert res.status_code == 201
    doc = res.json()
    assert doc["status"] == "pending"
    assert doc["title"] == "dune paper"
    assert queue.jobs == [uuid.UUID(doc["id"])]

    file = await client.get(f"/api/documents/{doc['id']}/file")
    assert file.status_code == 200
    assert file.content == pdf_bytes


async def test_duplicate_upload_returns_existing(
    client: AsyncClient, queue: FakeQueue, pdf_bytes: bytes
) -> None:
    first = (await _upload(client, pdf_bytes)).json()
    second = await _upload(client, pdf_bytes, name="renamed.pdf")
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert len(queue.jobs) == 1


async def test_rejects_non_pdf(client: AsyncClient) -> None:
    res = await _upload(client, b"hello, not a pdf", name="notes.pdf")
    assert res.status_code == 415


async def test_full_ingestion_pipeline(client: AsyncClient, pdf_bytes: bytes) -> None:
    doc_id = uuid.UUID((await _upload(client, pdf_bytes)).json()["id"])

    await ingest_document(SessionLocal, doc_id, HashEmbedder(dim=1024))

    doc = (await client.get(f"/api/documents/{doc_id}")).json()
    assert doc["status"] == "ready", doc["error"]
    assert doc["progress"] == 100
    assert doc["title"] == "Seasonal Change of Coastal Dunes"  # from the PDF, not filename
    assert doc["page_count"] == 3
    assert doc["language"] == "en"
    assert doc["chunk_count"] > 3

    chunks = (await client.get(f"/api/documents/{doc_id}/chunks?limit=200")).json()
    assert chunks["total"] == doc["chunk_count"]
    assert [c["chunk_index"] for c in chunks["items"]] == list(range(chunks["total"]))
    assert any(c["language"] == "ja" for c in chunks["items"])

    async with SessionLocal() as session:
        embeddings = (await session.scalars(select(Chunk.embedding))).all()
    assert len(embeddings) == doc["chunk_count"]
    assert all(e is not None and len(e) == 1024 for e in embeddings)


async def test_reingest_replaces_chunks(client: AsyncClient, pdf_bytes: bytes) -> None:
    doc_id = uuid.UUID((await _upload(client, pdf_bytes)).json()["id"])
    embedder = HashEmbedder(dim=1024)
    await ingest_document(SessionLocal, doc_id, embedder)
    first_count = (await client.get(f"/api/documents/{doc_id}")).json()["chunk_count"]

    res = await client.post(f"/api/documents/{doc_id}/reingest")
    assert res.status_code == 200
    assert (await client.post(f"/api/documents/{doc_id}/reingest")).status_code == 409

    await ingest_document(SessionLocal, doc_id, embedder)
    async with SessionLocal() as session:
        stored = (await session.scalars(select(Chunk).where(Chunk.document_id == doc_id))).all()
    assert len(stored) == first_count


async def test_unreadable_pdf_is_marked_failed(client: AsyncClient) -> None:
    broken = b"%PDF-1.7\nthis is not really a pdf"
    doc_id = uuid.UUID((await _upload(client, broken, name="broken.pdf")).json()["id"])

    await ingest_document(SessionLocal, doc_id, HashEmbedder(dim=1024))

    doc = (await client.get(f"/api/documents/{doc_id}")).json()
    assert doc["status"] == "failed"
    assert doc["error"]


async def test_delete_removes_document_chunks_and_file(
    client: AsyncClient, pdf_bytes: bytes
) -> None:
    doc_id = uuid.UUID((await _upload(client, pdf_bytes)).json()["id"])
    await ingest_document(SessionLocal, doc_id, HashEmbedder(dim=1024))
    async with SessionLocal() as session:
        path = Path((await session.get(Document, doc_id)).file_path)
    assert path.exists()

    assert (await client.delete(f"/api/documents/{doc_id}")).status_code == 204
    assert (await client.get(f"/api/documents/{doc_id}")).status_code == 404
    assert not path.exists()
    async with SessionLocal() as session:
        assert not (await session.scalars(select(Chunk))).all()


async def test_list_documents_newest_first(client: AsyncClient, pdf_bytes: bytes) -> None:
    await _upload(client, pdf_bytes, name="first.pdf")
    await _upload(client, pdf_bytes + b"\n%changed", name="second.pdf")
    titles = [d["title"] for d in (await client.get("/api/documents")).json()]
    assert titles == ["second", "first"]
    assert DocumentStatus.pending.value == "pending"
