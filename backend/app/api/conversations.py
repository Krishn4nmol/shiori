import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.answer.service import HISTORY_MESSAGES, AnswerDeps, answer_events
from app.api.search import get_reranker_if_enabled
from app.auth.deps import CurrentUser
from app.db import SessionLocal, get_session
from app.graph.store import GraphStore, get_graph_store
from app.ingest.embedder import Embedder, get_embedder
from app.llm import LLMClient, Message, get_llm
from app.models import Conversation, MessageRole
from app.models import Message as MessageRow
from app.observability.traces import PROMPT_VERSION, record_trace
from app.retrieval.reranker import Reranker

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)

Session = Annotated[AsyncSession, Depends(get_session)]


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None  # omit to start a new conversation
    document_ids: list[uuid.UUID] | None = None  # limit the answer to these papers


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    sources: list[dict[str, Any]] | None
    cited: list[int] | None
    search_query: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    timings_ms: dict[str, float] | None
    error: str | None
    feedback: int | None
    feedback_note: str | None
    created_at: datetime


class FeedbackIn(BaseModel):
    value: Literal[1, -1, 0]  # helpful, not helpful, or clear
    note: str | None = Field(default=None, max_length=2000)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user: CurrentUser,
    llm: Annotated[LLMClient, Depends(get_llm)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    reranker: Annotated[Reranker | None, Depends(get_reranker_if_enabled)],
    graph: Annotated[GraphStore | None, Depends(get_graph_store)],
) -> StreamingResponse:
    """Ask a question; the answer streams back as Server-Sent Events:
    `conversation`, `sources`, `token` (many), then `done` or `error`."""
    # Validate and save the question before streaming, so errors become proper HTTP codes.
    async with SessionLocal() as session:
        conversation, history = await _start_turn(session, body, user.id)

    deps = AnswerDeps(llm=llm, embedder=embedder, reranker=reranker, graph=graph)
    return StreamingResponse(
        _stream(conversation, history, body, deps),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(user: CurrentUser, session: Session) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(rows)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: Session
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: Session
) -> None:
    conversation = await session.get(Conversation, conversation_id)
    # Someone else's conversation looks the same as a missing one.
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "Conversation not found.")
    await session.delete(conversation)
    await session.commit()


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_feedback(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: FeedbackIn,
    user: CurrentUser,
    session: Session,
) -> None:
    """Rate one of your own answers. Ratings appear in Insights and guide evaluation."""
    message = await session.scalar(
        select(MessageRow)
        .join(Conversation, Conversation.id == MessageRow.conversation_id)
        .where(
            MessageRow.id == message_id,
            MessageRow.conversation_id == conversation_id,
            MessageRow.role == MessageRole.assistant,
            Conversation.user_id == user.id,
        )
    )
    if message is None:
        raise HTTPException(404, "Answer not found.")
    message.feedback = body.value or None
    message.feedback_note = (body.note or "").strip() or None if body.value else None
    await session.commit()


async def _start_turn(
    session: AsyncSession, body: ChatRequest, user_id: uuid.UUID
) -> tuple[Conversation, list[Message]]:
    if body.conversation_id is None:
        conversation = Conversation(title=_title(body.content), user_id=user_id)
        session.add(conversation)
        history: list[Message] = []
    else:
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == body.conversation_id, Conversation.user_id == user_id)
            .options(selectinload(Conversation.messages))
        )
        if conversation is None:
            raise HTTPException(404, "Conversation not found.")
        history = [
            {"role": m.role.value, "content": m.content}
            for m in conversation.messages
            if not m.error
        ][-HISTORY_MESSAGES:]
    session.add(MessageRow(conversation=conversation, role=MessageRole.user, content=body.content))
    await session.commit()
    await session.refresh(conversation)
    return conversation, history


async def _stream(
    conversation: Conversation, history: list[Message], body: ChatRequest, deps: AnswerDeps
) -> AsyncIterator[str]:
    yield _sse("conversation", {"id": str(conversation.id), "title": conversation.title})
    parts: list[str] = []
    plan: dict[str, Any] | None = None
    saved = False
    try:
        async with SessionLocal() as session:
            async for event in answer_events(
                session, body.content, history, deps, body.document_ids
            ):
                kind = event.pop("type")
                if kind == "token":
                    parts.append(event["text"])
                elif kind == "plan":
                    plan = dict(event)
                elif kind == "done":
                    record = event["record"]
                    message = await _save_answer(conversation.id, **record)
                    saved = True
                    # Trace before the last event: the client may disconnect right after it.
                    await _trace_chat(conversation, body.content, message.id, record,
                                      event["retrieval"], plan=plan)  # fmt: skip
                    yield _sse("done", {**record, "message_id": str(message.id)})
                    continue
                elif kind == "error":
                    trace = event.pop("trace")
                    message = await _save_answer(
                        conversation.id, "".join(parts), error=event["message"]
                    )
                    saved = True
                    await _trace_chat(
                        conversation, body.content, message.id, trace, trace["retrieval"],
                        error=event["message"], plan=plan,
                    )  # fmt: skip
                yield _sse(kind, event)
    except Exception:
        log.exception("chat stream failed")
        if not saved:
            await asyncio.shield(
                _save_answer(conversation.id, "".join(parts), error="Internal error")
            )
            saved = True
        yield _sse("error", {"message": "Something went wrong while answering."})
    finally:
        if not saved:  # client disconnected mid-answer: keep what was written
            await asyncio.shield(_save_answer(conversation.id, "".join(parts), error="Interrupted"))


async def _trace_chat(
    conversation: Conversation,
    question: str,
    message_id: uuid.UUID,
    record: dict[str, Any],
    retrieval: dict[str, Any] | None,
    error: str | None = None,
    plan: dict[str, Any] | None = None,
) -> None:
    retrieval = retrieval or {}
    timings = record.get("timings_ms") or {}
    await record_trace(
        kind="chat",
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        message_id=message_id,
        query=question,
        search_query=record.get("search_query"),
        answered=error is None and bool(record.get("sources")),
        candidates=retrieval.get("candidates", []),
        n_candidates=retrieval.get("n_candidates", 0),
        filtered_out=retrieval.get("filtered_out", 0),
        best_rerank_score=retrieval.get("best_rerank_score"),
        model=record.get("model"),
        input_tokens=record.get("input_tokens", 0),
        output_tokens=record.get("output_tokens", 0),
        cost_usd=record.get("cost_usd", 0.0),
        latency_ms=timings.get("total", 0.0),
        timings_ms=timings,
        prompt_version=PROMPT_VERSION,
        plan=plan,
        error=error,
    )


async def _save_answer(conversation_id: uuid.UUID, content: str, **fields: Any) -> MessageRow:
    async with SessionLocal() as session:
        message = MessageRow(
            conversation_id=conversation_id,
            role=MessageRole.assistant,
            content=content,
            **fields,
        )
        session.add(message)
        conversation = await session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now().astimezone()
        await session.commit()
        await session.refresh(message)
        return message


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _title(question: str) -> str:
    question = " ".join(question.split())
    return question if len(question) <= 80 else question[:77] + "…"
