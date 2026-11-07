"""Fake models and a sample library shared by the search and chat tests."""

import math
import uuid
from collections.abc import AsyncIterator

import pytest

from app.db import SessionLocal
from app.ingest.pipeline import ingest_document
from app.llm import LLMResult, Message
from app.models import Document
from app.retrieval.tokenize import search_tokens
from tests.pdf_factory import make_paper


class TokenEmbedder:
    """Fake embedder where texts sharing words get similar vectors (feature hashing)."""

    dim = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in search_tokens(text):
                vec[hash(token) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


class ReverseReranker:
    """Scores passages in reverse of the order it receives them, to prove reranking applies."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append(len(passages))
        return [i / len(passages) for i in range(len(passages))]


class FixedReranker:
    """Gives the first passage a strong score and the rest junk scores."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [0.6] + [0.0004] * (len(passages) - 1)


class ScriptedLLM:
    """Streams a fixed answer and records every call, so tests can check the prompts."""

    provider = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, answer: str = "Forty sites were measured [1].", rewrite: str = "") -> None:
        self.answer = answer
        self.rewrite = rewrite
        self.calls: list[dict] = []

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult:
        self.calls.append({"kind": "complete", "system": system, "messages": messages})
        return LLMResult(text=self.rewrite, model=self.model, input_tokens=100, output_tokens=10)

    async def stream(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> AsyncIterator[str | LLMResult]:
        self.calls.append({"kind": "stream", "system": system, "messages": messages})
        for word in self.answer.split(" "):
            yield word + " "
        yield LLMResult(text=self.answer, model=self.model, input_tokens=2000, output_tokens=50)


@pytest.fixture
async def library(clean_db: None, tmp_path) -> dict[str, uuid.UUID]:
    """Two ingested papers: the generated EN/JA dune paper and a short unrelated one."""
    import pymupdf

    other = tmp_path / "other.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_textbox(
        pymupdf.Rect(72, 72, 520, 700),
        "Kidney Disease\n\nChronic kidney disease patients were followed for five years. "
        "Blood pressure and creatinine were recorded at each hospital visit. " * 3,
        fontsize=10,
    )
    pdf.save(other)
    pdf.close()

    ids = {}
    async with SessionLocal() as session:
        for name, path in [("dunes", make_paper(tmp_path / "dunes.pdf")), ("kidney", other)]:
            doc = Document(
                title=name,
                filename=path.name,
                sha256=uuid.uuid4().hex,
                file_path=str(path),
                size_bytes=path.stat().st_size,
            )
            session.add(doc)
            await session.commit()
            ids[name] = doc.id
    for doc_id in ids.values():
        await ingest_document(SessionLocal, doc_id, TokenEmbedder())
    return ids
