"""Insights for admins: how well Shiori answers, how fast, and at what cost.

Admins can read the questions people asked (needed to find and fix weak answers); the
conversations themselves stay private in the Ask page.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.answer import prompts
from app.auth.deps import require_admin
from app.db import get_session
from app.models import Message, Trace, User
from app.observability.traces import PROMPT_VERSION

router = APIRouter(prefix="/insights", tags=["insights"], dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]

STAGES = ["rewrite", "retrieval", "first_token", "generation"]
SEARCH_STAGES = ["embed", "vector", "keyword", "fuse", "rerank"]


class Percentiles(BaseModel):
    p50: float | None
    p95: float | None


class DayCount(BaseModel):
    day: str
    questions: int
    unanswered: int


class Summary(BaseModel):
    days: int
    questions: int  # chat traces
    searches: int
    answered_rate: float | None
    error_rate: float | None
    latency_ms: Percentiles  # chat, end to end
    first_token_ms: Percentiles
    stage_avg_ms: dict[str, float]  # chat stages
    search_stage_avg_ms: dict[str, float]  # inside retrieval
    cost_usd: float
    input_tokens: int
    output_tokens: int
    feedback_up: int
    feedback_down: int
    per_day: list[DayCount]
    unanswered_questions: list[str]  # recent questions nothing answered: gaps in the library


class TraceRow(BaseModel):
    id: uuid.UUID
    kind: str
    created_at: datetime
    user_name: str | None
    query: str
    answered: bool
    error: str | None
    n_candidates: int
    filtered_out: int
    best_rerank_score: float | None
    latency_ms: float
    cost_usd: float
    feedback: int | None


class TracePage(BaseModel):
    total: int
    items: list[TraceRow]


class TraceDetail(TraceRow):
    search_query: str | None
    conversation_id: uuid.UUID | None
    candidates: list[dict[str, Any]]
    timings_ms: dict[str, float]
    model: str | None
    input_tokens: int
    output_tokens: int
    prompt_version: str | None
    plan: dict[str, Any] | None
    prompt_is_current: bool
    system_prompt: str | None  # current prompt text, shown when the version matches
    answer: str | None
    feedback_note: str | None


@router.get("/summary", response_model=Summary)
async def summary(session: Session, days: Annotated[int, Query(ge=1, le=365)] = 7) -> Summary:
    since = datetime.now(UTC) - timedelta(days=days)
    recent = Trace.created_at >= since
    chat = and_(recent, Trace.kind == "chat")

    counts = (
        await session.execute(
            select(
                func.count().filter(Trace.kind == "chat"),
                func.count().filter(Trace.kind == "search"),
                func.count().filter(Trace.kind == "chat", Trace.answered.is_(True)),
                func.count().filter(Trace.kind == "chat", Trace.error.is_not(None)),
                func.coalesce(func.sum(Trace.cost_usd), 0.0),
                func.coalesce(func.sum(Trace.input_tokens), 0),
                func.coalesce(func.sum(Trace.output_tokens), 0),
            ).where(recent)
        )
    ).one()
    questions, searches, answered, errors, cost, tokens_in, tokens_out = counts

    latency = await _percentiles(session, Trace.latency_ms, chat)
    first_token = await _percentiles(session, Trace.timings_ms["first_token"].as_float(), chat)

    stage_avg = await _stage_averages(session, chat, STAGES)
    search_avg = await _stage_averages(
        session, chat, [f"search_{s}" for s in SEARCH_STAGES], strip="search_"
    )

    feedback = (
        await session.execute(
            select(
                func.count().filter(Message.feedback == 1),
                func.count().filter(Message.feedback == -1),
            )
            .select_from(Trace)
            .join(Message, Message.id == Trace.message_id)
            .where(chat)
        )
    ).one()

    day = func.date_trunc("day", Trace.created_at)
    per_day = [
        DayCount(day=row.day.date().isoformat(), questions=row.questions, unanswered=row.unanswered)
        for row in await session.execute(
            select(
                day.label("day"),
                func.count().label("questions"),
                func.count().filter(Trace.answered.is_(False)).label("unanswered"),
            )
            .where(chat)
            .group_by(day)
            .order_by(day)
        )
    ]

    unanswered = list(
        await session.scalars(
            select(Trace.query)
            .where(chat, Trace.answered.is_(False), Trace.error.is_(None))
            .order_by(Trace.created_at.desc())
            .limit(10)
        )
    )

    return Summary(
        days=days,
        questions=questions,
        searches=searches,
        answered_rate=answered / questions if questions else None,
        error_rate=errors / questions if questions else None,
        latency_ms=latency,
        first_token_ms=first_token,
        stage_avg_ms=stage_avg,
        search_stage_avg_ms=search_avg,
        cost_usd=round(float(cost), 6),
        input_tokens=int(tokens_in),
        output_tokens=int(tokens_out),
        feedback_up=feedback[0],
        feedback_down=feedback[1],
        per_day=per_day,
        unanswered_questions=unanswered,
    )


@router.get("/traces", response_model=TracePage)
async def list_traces(
    session: Session,
    kind: Literal["chat", "search"] | None = None,
    only: Literal["unanswered", "errors", "negative"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TracePage:
    stmt = _trace_rows()
    if kind:
        stmt = stmt.where(Trace.kind == kind)
    if only == "unanswered":
        stmt = stmt.where(Trace.answered.is_(False), Trace.error.is_(None))
    elif only == "errors":
        stmt = stmt.where(Trace.error.is_not(None))
    elif only == "negative":
        stmt = stmt.where(Message.feedback == -1)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.execute(stmt.order_by(Trace.created_at.desc()).offset(offset).limit(limit))
    return TracePage(total=total or 0, items=[_row(r) for r in rows])


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: uuid.UUID, session: Session) -> TraceDetail:
    row = (
        await session.execute(
            _trace_rows()
            .add_columns(Message.content, Message.feedback_note)
            .where(Trace.id == trace_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Trace not found.")
    trace: Trace = row.Trace
    current = trace.prompt_version == PROMPT_VERSION
    return TraceDetail(
        **_row(row).model_dump(),
        search_query=trace.search_query,
        conversation_id=trace.conversation_id,
        candidates=trace.candidates,
        timings_ms=trace.timings_ms,
        model=trace.model,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        prompt_version=trace.prompt_version,
        plan=trace.plan,
        prompt_is_current=current,
        system_prompt=prompts.ANSWER_SYSTEM if current and trace.kind == "chat" else None,
        answer=row.content,
        feedback_note=row.feedback_note,
    )


def _trace_rows() -> Select:
    return (
        select(Trace, User.name.label("user_name"), Message.feedback.label("feedback"))
        .outerjoin(User, User.id == Trace.user_id)
        .outerjoin(Message, Message.id == Trace.message_id)
    )


def _row(row: Any) -> TraceRow:
    t: Trace = row.Trace
    return TraceRow(
        id=t.id,
        kind=t.kind,
        created_at=t.created_at,
        user_name=row.user_name,
        query=t.query,
        answered=t.answered,
        error=t.error,
        n_candidates=t.n_candidates,
        filtered_out=t.filtered_out,
        best_rerank_score=t.best_rerank_score,
        latency_ms=t.latency_ms,
        cost_usd=t.cost_usd,
        feedback=row.feedback,
    )


async def _percentiles(session: AsyncSession, column: Any, where: Any) -> Percentiles:
    p50, p95 = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(column),
                func.percentile_cont(0.95).within_group(column),
            ).where(where, column.is_not(None))
        )
    ).one()
    return Percentiles(
        p50=round(p50, 1) if p50 is not None else None,
        p95=round(p95, 1) if p95 is not None else None,
    )


async def _stage_averages(
    session: AsyncSession, where: Any, keys: list[str], strip: str = ""
) -> dict[str, float]:
    # A stage that didn't run (no follow-up to rewrite, no LLM call) counts as 0 ms, so the
    # stage averages add up to the average answer time.
    columns = [func.avg(func.coalesce(Trace.timings_ms[key].as_float(), 0.0)) for key in keys]
    values = (await session.execute(select(*columns).where(where))).one()
    return {
        key.removeprefix(strip): round(float(v), 1)
        for key, v in zip(keys, values, strict=True)
        if v  # skip stages that never ran in this period
    }
