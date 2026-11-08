"""Database tables."""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

EMBEDDING_DIM = get_settings().embedding_dim


class UserRole(enum.StrEnum):
    admin = "admin"  # manages users, can delete any paper
    member = "member"  # uploads, searches and asks


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lowercase
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(500))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.member
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change or deactivation: invalidates every existing session.
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    # The library is shared by the lab; the uploader (or an admin) may delete a paper.
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # Filled in by the worker
    authors: Mapped[str | None] = mapped_column(String(1000))
    language: Mapped[str | None] = mapped_column(String(8))  # "en", "ja", "mixed"
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # Knowledge graph (Phase 8): none, building, ready, failed
    graph_status: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    graph_entities: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    graph_relations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    graph_error: Mapped[str | None] = mapped_column(Text)

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


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Conversations are private to their owner.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))  # first question, shortened
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="message_role"))
    content: Mapped[str] = mapped_column(Text)

    # Assistant messages only: what the answer was built from and what it cost.
    # sources: the passages shown to the LLM, numbered as cited in `content` ([1], [2], ...)
    sources: Mapped[list[dict] | None] = mapped_column(JSONB)
    cited: Mapped[list[int] | None] = mapped_column(JSONB)  # source numbers actually cited
    search_query: Mapped[str | None] = mapped_column(Text)  # standalone rewrite of follow-ups
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    timings_ms: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    # Reader's rating of an answer: 1 (helpful) or -1 (not helpful), with an optional note
    feedback: Mapped[int | None] = mapped_column(SmallInteger)
    feedback_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Trace(Base):
    """One record per question or search: what was retrieved, what it cost, how long each
    stage took. Feeds the Insights dashboard (Phase 5) and evaluation (Phase 6)."""

    __tablename__ = "traces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20))  # "chat" or "search"
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    query: Mapped[str] = mapped_column(Text)
    search_query: Mapped[str | None] = mapped_column(Text)  # after follow-up rewriting
    answered: Mapped[bool] = mapped_column(Boolean)  # false: nothing passed the cutoff, or error
    # Passages considered, in final order: ranks, scores, kept by the cutoff, cited
    candidates: Mapped[list[dict]] = mapped_column(JSONB)
    n_candidates: Mapped[int] = mapped_column(Integer)
    filtered_out: Mapped[int] = mapped_column(Integer)
    best_rerank_score: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float)
    timings_ms: Mapped[dict] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(String(20))  # hash of the system prompt
    # Agent answers: question type, standalone question, queries, papers, steps taken
    plan: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EvalQuestion(Base):
    """A test question with a known answer, or one the library should NOT answer."""

    __tablename__ = "eval_questions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(8))
    # None: the library does not answer this, and Shiori should say "not found"
    expected_answer: Mapped[str | None] = mapped_column(Text)
    # Where the answer is: a passage counts as relevant if it is from this paper and contains
    # the evidence quote. Matching text (not chunk ids) survives re-chunking.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    evidence: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # manual, generated, trace
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # retrieval (free) or answer (LLM + judge)
    config: Mapped[dict] = mapped_column(JSONB)  # search settings, prompt version, model
    status: Mapped[str] = mapped_column(String(20), default="running")  # running, done, failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_questions.id", ondelete="CASCADE")
    )
    # Rank (1-based) of the first relevant passage in the final ranking, before the cutoff
    relevant_rank: Mapped[int | None] = mapped_column(Integer)
    kept_relevant: Mapped[bool] = mapped_column(Boolean)  # a relevant passage passed the cutoff
    answered: Mapped[bool] = mapped_column(Boolean)  # anything passed the cutoff
    # Ranked passages with scores and relevance: lets any cutoff be re-scored afterwards
    candidates: Mapped[list[dict]] = mapped_column(JSONB)
    answer: Mapped[str | None] = mapped_column(Text)
    judge: Mapped[dict | None] = mapped_column(JSONB)  # correctness, faithfulness, reason
    latency_ms: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
