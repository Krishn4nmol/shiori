"""Database tables."""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

EMBEDDING_DIM = get_settings().embedding_dim


class DocumentStatus(enum.StrEnum):
    pending = "pending"  # uploaded, waiting for the worker
    processing = "processing"  # being parsed / chunked / embedded
    ready = "ready"  # searchable
    failed = "failed"  # see Document.error


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500))
    filename: Mapped[str] = mapped_column(String(500))
    # SHA-256 of the file bytes: uploading the same PDF twice returns the existing document
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    file_path: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int] = mapped_column(Integer)

    # Filled in by the worker
    authors: Mapped[str | None] = mapped_column(String(1000))
    language: Mapped[str | None] = mapped_column(String(8))  # "en", "ja", "mixed"
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"), default=DocumentStatus.pending
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    stage: Mapped[str | None] = mapped_column(String(50))  # e.g. "embedding 12/40"
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)  # order within the document
    text: Mapped[str] = mapped_column(Text)
    # Heading path, e.g. "3 Method > 3.2 Data collection"
    section: Mapped[str | None] = mapped_column(String(1000))
    page_start: Mapped[int] = mapped_column(Integer)  # 1-based, for citations
    page_end: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(8))
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # Keyword search: tokens from app.retrieval.tokenize, indexed as-is (no Postgres parsing)
    search_text: Mapped[str] = mapped_column(Text, server_default="")
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("array_to_tsvector(string_to_array(search_text, ' '))", persisted=True)
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        # Approximate nearest-neighbour index for cosine similarity search (Phase 2)
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_search_tsv", "search_tsv", postgresql_using="gin"),
    )
