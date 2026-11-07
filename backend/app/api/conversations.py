import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.answer.service import HISTORY_MESSAGES, AnswerDeps, answer_events
from app.api.search import get_reranker_if_enabled
from app.db import SessionLocal, get_session
from app.ingest.embedder import Embedder, get_embedder
from app.llm import LLMClient, Message, get_llm
from app.models import Conversation, MessageRole
from app.models import Message as MessageRow
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
    created_at: datetime


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
    llm: Annotated[LLMClient, Depends(get_llm)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    reranker: Annotated[Reranker | None, Depends(get_reranker_if_enabled)],
) -> StreamingResponse:
    """Ask a question; the answer streams back as Server-Sent Events:
    `conversation`, `sources`, `token` (many), then `done` or `error`."""
    # Validate and save the question before streaming, so errors become proper HTTP codes.
    async with SessionLocal() as session:
        conversation, history = await _start_turn(session, body)

    deps = AnswerDeps(llm=llm, embedder=embedder, reranker=reranker)
    return StreamingResponse(
        _stream(conversation, history, body, deps),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(session: Session) -> list[Conversation]:
    rows = await session.scalars(select(Conversation).order_by(Conversation.updated_at.desc()))
    return list(rows)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, session: Session) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, session: Session) -> None:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    await session.delete(conversation)
    await session.commit()


async def _start_turn(
    session: AsyncSession, body: ChatRequest
) -> tuple[Conversation, list[Message]]:
    if body.conversation_id is None:
        conversation = Conversation(title=_title(body.content))
        session.add(conversation)
        history: list[Message] = []
    else:
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == body.conversation_id)
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
    saved = False
    try:
        async with SessionLocal() as session:
            async for event in answer_events(
                session, body.content, history, deps, body.document_ids
            ):
                kind = event.pop("type")
                if kind == "token":
                    parts.append(event["text"])
                elif kind == "done":
                    message = await _save_answer(conversation.id, **event["record"])
                    saved = True
                    yield _sse("done", {**event["record"], "message_id": str(message.id)})
                    continue
                elif kind == "error":
                    await _save_answer(conversation.id, "".join(parts), error=event["message"])
                    saved = True
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
