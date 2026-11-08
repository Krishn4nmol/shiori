"""Admin API for evaluation: the test set, runs, and their results."""

import asyncio
import random
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.answer.service import AnswerDeps, SearchOptions
from app.api.search import get_reranker_if_enabled
from app.auth.deps import AdminUser, require_admin
from app.db import get_session
from app.evaluation import runner
from app.evaluation.llm_tasks import TaskError, generate_question, generate_unanswerable
from app.evaluation.metrics import normalize
from app.graph.store import GraphStore, get_graph_store
from app.ingest.embedder import Embedder, get_embedder
from app.ingest.text_utils import question_language
from app.llm import LLMClient, LLMError, get_llm
from app.models import Chunk, Document, DocumentStatus, EvalQuestion, EvalResult, EvalRun
from app.retrieval.reranker import Reranker

router = APIRouter(prefix="/eval", tags=["evaluation"], dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]
LLM = Annotated[LLMClient, Depends(get_llm)]


# --- test questions -------------------------------------------------------------------


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    expected_answer: str | None = Field(default=None, max_length=2000)  # None: unanswerable
    document_id: uuid.UUID | None = None
    evidence: str | None = Field(default=None, max_length=500)


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str
    language: str
    expected_answer: str | None
    document_id: uuid.UUID | None
    evidence: str | None
    source: str
    created_at: datetime


class GenerateIn(BaseModel):
    count: int = Field(default=10, ge=1, le=30)  # answerable questions
    unanswerable: int = Field(default=3, ge=0, le=10)


class GenerateOut(BaseModel):
    created: list[QuestionOut]
    skipped: list[str]  # why some generations were rejected


@router.get("/questions", response_model=list[QuestionOut])
async def list_questions(session: Session) -> list[EvalQuestion]:
    return list(await session.scalars(select(EvalQuestion).order_by(EvalQuestion.created_at)))


@router.post("/questions", response_model=QuestionOut, status_code=status.HTTP_201_CREATED)
async def add_question(body: QuestionIn, session: Session) -> EvalQuestion:
    await _check_evidence(session, body)
    question = EvalQuestion(**_question_fields(body), source="manual")
    session.add(question)
    await session.commit()
    await session.refresh(question)
    return question


@router.put("/questions/{question_id}", response_model=QuestionOut)
async def edit_question(question_id: uuid.UUID, body: QuestionIn, session: Session) -> EvalQuestion:
    question = await session.get(EvalQuestion, question_id)
    if question is None:
        raise HTTPException(404, "Question not found.")
    await _check_evidence(session, body)
    for key, value in _question_fields(body).items():
        setattr(question, key, value)
    await session.commit()
    await session.refresh(question)
    return question


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: uuid.UUID, session: Session) -> None:
    question = await session.get(EvalQuestion, question_id)
    if question is None:
        raise HTTPException(404, "Question not found.")
    await session.delete(question)
    await session.commit()


@router.post("/questions/generate", response_model=GenerateOut)
async def generate_questions(body: GenerateIn, session: Session, llm: LLM) -> GenerateOut:
    """Ask the LLM to write test questions from random passages (alternating English and
    Japanese, to test cross-language search), plus questions the library cannot answer.
    Review them: generated questions can be vague."""
    chunks = list(
        await session.execute(
            select(Chunk, Document.title)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.status == DocumentStatus.ready, Chunk.token_count >= 60)
        )
    )
    if not chunks:
        raise HTTPException(400, "Upload and process some papers first.")
    picked = random.sample(chunks, min(body.count, len(chunks)))

    created: list[EvalQuestion] = []
    skipped: list[str] = []
    for i, (chunk, title) in enumerate(picked):
        language = "ja" if i % 2 else "en"
        try:
            g = await generate_question(llm, chunk.text, language)
        except (TaskError, LLMError) as err:
            skipped.append(f"{title} p.{chunk.page_start}: {err}")
            continue
        created.append(EvalQuestion(
            question=g.question, language=question_language(g.question), expected_answer=g.answer,
            document_id=chunk.document_id, evidence=g.evidence, source="generated",
        ))  # fmt: skip

    if body.unanswerable:
        overview = "\n\n".join(
            f"Title: {title}\nExcerpt: {chunk.text[:300]}"
            for chunk, title in {t: (c, t) for c, t in chunks}.values()
        )
        try:
            for q in await generate_unanswerable(llm, overview, body.unanswerable):
                created.append(EvalQuestion(question=q, language=question_language(q),
                                            source="generated"))  # fmt: skip
        except (TaskError, LLMError) as err:
            skipped.append(f"unanswerable questions: {err}")

    session.add_all(created)
    await session.commit()
    for q in created:
        await session.refresh(q)
    return GenerateOut(created=[QuestionOut.model_validate(q) for q in created], skipped=skipped)


def _question_fields(body: QuestionIn) -> dict[str, Any]:
    answerable = bool(body.expected_answer and body.expected_answer.strip())
    return {
        "question": body.question.strip(),
        "language": question_language(body.question),
        "expected_answer": body.expected_answer.strip() if answerable else None,
        "document_id": body.document_id if answerable else None,
        "evidence": (body.evidence or "").strip() or None if answerable else None,
    }


async def _check_evidence(session: AsyncSession, body: QuestionIn) -> None:
    """Evidence must appear in the chosen paper, or no passage could ever count as relevant."""
    if not (body.expected_answer and body.evidence and body.document_id):
        if body.expected_answer and not body.evidence:
            raise HTTPException(422, "Add an evidence quote: a short exact phrase from the paper.")
        return
    texts = list(
        await session.scalars(select(Chunk.text).where(Chunk.document_id == body.document_id))
    )
    doc = await session.get(Document, body.document_id)
    if doc is not None:  # questions about the paper itself ("who wrote it?")
        texts.append(" ".join(x for x in (doc.title, doc.authors, doc.filename) if x))
    if not any(normalize(body.evidence) in normalize(t) for t in texts):
        raise HTTPException(
            422, "The evidence quote was not found in that paper's text or details."
        )


# --- runs -----------------------------------------------------------------------------


class RunIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["retrieval", "answer"] = "retrieval"
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"
    rerank: bool = True
    min_rerank_score: float | None = Field(default=None, ge=0, le=1)
    # Answer runs: plan each question with the agent (None: the server's ANSWER_MODE)
    agent: bool | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    config: dict[str, Any]
    status: str
    progress: int
    total: int
    metrics: dict[str, Any] | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class ResultOut(BaseModel):
    question_id: uuid.UUID
    question: str
    expected_answer: str | None
    evidence: str | None
    relevant_rank: int | None
    kept_relevant: bool
    answered: bool
    candidates: list[dict[str, Any]]
    answer: str | None
    judge: dict[str, Any] | None
    latency_ms: float
    cost_usd: float
    error: str | None


class RunDetail(RunOut):
    results: list[ResultOut]


@router.get("/runs", response_model=list[RunOut])
async def list_runs(session: Session) -> list[EvalRun]:
    return list(await session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc())))


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def start_run(
    body: RunIn,
    admin: AdminUser,
    session: Session,
    llm: LLM,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    reranker: Annotated[Reranker | None, Depends(get_reranker_if_enabled)],
    graph: Annotated[GraphStore | None, Depends(get_graph_store)],
) -> EvalRun:
    """Start a run in the background; poll GET /eval/runs/{id} for progress."""
    count = await session.scalar(select(func.count()).select_from(EvalQuestion))
    if not count:
        raise HTTPException(400, "Add some test questions first.")
    if await session.scalar(select(func.count()).where(EvalRun.status == "running")):
        raise HTTPException(409, "Another run is in progress. Wait for it to finish.")
    # Without the reranker there are no relevance scores, so there is no cutoff either.
    cutoff = body.min_rerank_score if body.rerank else 0.0
    options = SearchOptions(
        mode=body.mode, rerank=body.rerank, min_rerank_score=cutoff, agent=body.agent
    )
    deps = AnswerDeps(llm=llm, embedder=embedder, reranker=reranker, graph=graph)
    run = EvalRun(
        name=body.name.strip(), kind=body.kind, config=runner.run_config(options, deps, body.kind),
        status="running", progress=0, total=count, created_by_id=admin.id,
    )  # fmt: skip
    session.add(run)
    await session.commit()
    await session.refresh(run)
    runner.start_run(run.id, body.kind, options, deps)
    return run


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, session: Session) -> RunDetail:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    rows = await session.execute(
        select(EvalResult, EvalQuestion)
        .join(EvalQuestion, EvalQuestion.id == EvalResult.question_id)
        .where(EvalResult.run_id == run_id)
    )
    results = [
        ResultOut(
            question_id=q.id,
            question=q.question,
            expected_answer=q.expected_answer,
            evidence=q.evidence,
            relevant_rank=r.relevant_rank,
            kept_relevant=r.kept_relevant,
            answered=r.answered,
            candidates=r.candidates,
            answer=r.answer,
            judge=r.judge,
            latency_ms=r.latency_ms,
            cost_usd=r.cost_usd,
            error=r.error,
        )  # fmt: skip
        for r, q in rows
    ]
    return RunDetail(**RunOut.model_validate(run).model_dump(), results=results)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: uuid.UUID, session: Session) -> None:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    if run.status == "running":
        raise HTTPException(409, "Wait for the run to finish before deleting it.")
    await session.delete(run)
    await session.commit()


# Exposed for tests: wait until a background run finishes.
async def wait_for_run(run_id: uuid.UUID) -> None:
    task = runner.tasks.get(run_id)
    if task is not None:
        await asyncio.shield(task)
