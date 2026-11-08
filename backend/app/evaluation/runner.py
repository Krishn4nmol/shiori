"""Runs the test set through search (retrieval runs) or the full answer pipeline plus an LLM
judge (answer runs), storing one result per question and the run's metrics.

Runs execute as background tasks in the API process, where the search models are already
loaded. A run interrupted by a restart is marked failed at the next startup.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from app.answer.prompts import NOT_FOUND
from app.answer.service import AnswerDeps, SearchOptions, answer_events
from app.config import get_settings
from app.db import SessionLocal
from app.evaluation.llm_tasks import TaskError, judge_answer
from app.evaluation.metrics import (
    ResultView,
    answer_in_language,
    answer_metrics,
    cutoff_sweep,
    is_relevant,
    retrieval_metrics,
)
from app.llm.pricing import estimate_cost_usd
from app.models import EvalQuestion, EvalResult, EvalRun
from app.observability.traces import PROMPT_VERSION
from app.retrieval.search import SearchFilters, SearchResult, search

log = logging.getLogger(__name__)

RETRY_WAITS = (20, 60)  # seconds to wait after a rate limit before retrying
tasks: dict[uuid.UUID, asyncio.Task] = {}  # running runs; also lets tests await them


def run_config(options: SearchOptions, deps: AnswerDeps, kind: str) -> dict[str, Any]:
    s = get_settings()
    return {
        "mode": options.mode,
        "rerank": options.rerank and deps.reranker is not None,
        "min_rerank_score": s.min_rerank_score
        if options.min_rerank_score is None
        else options.min_rerank_score,
        "candidates": options.candidates or s.search_candidates,
        "rerank_candidates": options.rerank_candidates or s.rerank_candidates,
        "embedding_model": s.embedding_model,
        "reranker_model": s.reranker_model,
        "llm_model": deps.llm.model if kind == "answer" else None,
        "prompt_version": PROMPT_VERSION if kind == "answer" else None,
        # Search runs always measure plain search; answer runs may plan with the agent.
        "agent": kind == "answer"
        and (s.answer_mode == "agent" if options.agent is None else options.agent),
    }


def start_run(run_id: uuid.UUID, kind: str, options: SearchOptions, deps: AnswerDeps) -> None:
    task = asyncio.create_task(run_eval(run_id, kind, options, deps))
    tasks[run_id] = task
    task.add_done_callback(lambda _: tasks.pop(run_id, None))


async def run_eval(run_id: uuid.UUID, kind: str, options: SearchOptions, deps: AnswerDeps) -> None:
    try:
        async with SessionLocal() as session:
            questions = list(await session.scalars(select(EvalQuestion)))
            run = await session.get(EvalRun, run_id)
            assert run is not None
            run.total = len(questions)
            await session.commit()

        views: list[ResultView] = []
        for done, question in enumerate(questions, start=1):
            if kind == "answer":
                result = await _answer_one(question, options, deps)
            else:
                result = await _retrieve_one(question, options, deps)
            result.run_id = run_id
            async with SessionLocal() as session:
                session.add(result)
                await session.execute(
                    update(EvalRun).where(EvalRun.id == run_id).values(progress=done)
                )
                await session.commit()
            views.append(_view(question, result))

        metrics = {**retrieval_metrics(views), "sweep": cutoff_sweep(views)}
        if kind == "answer":
            metrics.update(answer_metrics(views))
        await _finish(run_id, status="done", metrics=metrics)
    except Exception as err:
        log.exception("evaluation run %s failed", run_id)
        await _finish(run_id, status="failed", error=f"{type(err).__name__}: {err}")


async def mark_interrupted_runs() -> None:
    """At startup: runs still 'running' were cut off by a restart."""
    async with SessionLocal() as session:
        await session.execute(
            update(EvalRun)
            .where(EvalRun.status == "running")
            .values(status="failed", error="Interrupted by a server restart.")
        )
        await session.commit()


async def _finish(run_id: uuid.UUID, **values: Any) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(EvalRun)
            .where(EvalRun.id == run_id)
            .values(finished_at=datetime.now(UTC), **values)
        )
        await session.commit()


# --- one question ---------------------------------------------------------------------


async def _retrieve_one(q: EvalQuestion, options: SearchOptions, deps: AnswerDeps) -> EvalResult:
    s = get_settings()
    min_score = s.min_rerank_score if options.min_rerank_score is None else options.min_rerank_score
    start = time.perf_counter()
    async with SessionLocal() as session:
        result = await search(
            session,
            q.question,
            embedder=deps.embedder,
            reranker=deps.reranker if options.rerank else None,
            k=6,
            mode=options.mode,
            candidates=options.candidates or s.search_candidates,
            rerank_candidates=options.rerank_candidates or s.rerank_candidates,
            min_rerank_score=min_score,
            filters=SearchFilters(),
        )
    latency = (time.perf_counter() - start) * 1000
    return _result_from_search(q, result, latency)


async def _answer_one(q: EvalQuestion, options: SearchOptions, deps: AnswerDeps) -> EvalResult:
    done: dict[str, Any] | None = None
    error: str | None = None
    for attempt in range(len(RETRY_WAITS) + 1):
        done, error = None, None
        start = time.perf_counter()
        async with SessionLocal() as session:
            async for event in answer_events(session, q.question, [], deps, options=options):
                if event["type"] == "done":
                    done = event
                elif event["type"] == "error":
                    error = event["message"]
        latency = (time.perf_counter() - start) * 1000
        if done or attempt == len(RETRY_WAITS) or not _is_rate_limit(error):
            break
        await asyncio.sleep(RETRY_WAITS[attempt])

    if done is None:
        return EvalResult(
            question_id=q.id, relevant_rank=None, kept_relevant=False, answered=False,
            candidates=[], latency_ms=latency, error=error,
        )  # fmt: skip

    record = done["record"]
    result = _result_from_search(q, done["search_result"], latency)
    result.answer = record["content"]
    result.cost_usd = record["cost_usd"]

    relevant_ids = {c["chunk_id"] for c in result.candidates if c["relevant"]}
    cited_ids = {record["sources"][n - 1]["chunk_id"] for n in record["cited"]}
    judge: dict[str, Any] = {"cited_relevant": bool(cited_ids & relevant_ids)}
    if record["sources"]:  # an LLM answer (not the fixed "not found" message)
        judge["language_match"] = answer_in_language(q.language, record["content"])

    if not record["sources"]:  # the pipeline said "not found" without calling the LLM
        judge.update(correctness="wrong", faithful=None, declined=True,
                     reason="No passage passed the relevance cutoff.")  # fmt: skip
    else:
        try:
            verdict = await _judge_with_retry(q, record, deps)
            judge.update({k: v for k, v in asdict(verdict).items()
                          if k in ("correctness", "faithful", "declined", "reason")})  # fmt: skip
            result.cost_usd += estimate_cost_usd(
                verdict.model, verdict.input_tokens, verdict.output_tokens
            )
        except TaskError as err:
            result.error = str(err)
            judge = {}
    if q.expected_answer is None:
        # The expected answer here is "the library does not answer this", so a "correct" grade
        # also means it declined. Judges sometimes grade correctly but forget the declined flag.
        said_not_found = (
            not record["sources"]
            or judge.get("declined")
            or judge.get("correctness") == "correct"
            or (record["content"].strip() in NOT_FOUND.values())
        )
        judge = {"said_not_found": bool(said_not_found), **judge}
    result.judge = judge or None
    return result


async def _judge_with_retry(q: EvalQuestion, record: dict[str, Any], deps: AnswerDeps):
    expected = q.expected_answer or "(The library does not answer this question.)"
    sources = [s["text"] for s in record["sources"]]
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return await judge_answer(deps.llm, q.question, expected, record["content"], sources)
        except TaskError as err:
            if attempt == len(RETRY_WAITS) or not _is_rate_limit(str(err)):
                raise
            await asyncio.sleep(RETRY_WAITS[attempt])
    raise AssertionError("unreachable")


def _is_rate_limit(message: str | None) -> bool:
    text = (message or "").lower()
    return "quota" in text or "rate limit" in text


def _result_from_search(q: EvalQuestion, result: SearchResult, latency: float) -> EvalResult:
    doc_id = str(q.document_id) if q.document_id else None
    kept = {hit.chunk_id for hit in result.hits}
    candidates = []
    relevant_rank = None
    for rank, hit in enumerate(result.considered, start=1):
        relevant = is_relevant(hit.text, str(hit.document_id), doc_id, q.evidence)
        if relevant and relevant_rank is None:
            relevant_rank = rank
        candidates.append({
            "chunk_id": str(hit.chunk_id), "document_title": hit.document_title,
            "page_start": hit.page_start, "preview": hit.text[:200],
            "vector_rank": hit.vector_rank, "keyword_rank": hit.keyword_rank,
            "rerank_score": hit.rerank_score, "relevant": relevant, "kept": hit.chunk_id in kept,
        })  # fmt: skip
    return EvalResult(
        question_id=q.id,
        relevant_rank=relevant_rank,
        kept_relevant=any(c["relevant"] and c["kept"] for c in candidates),
        answered=bool(result.hits),
        candidates=candidates,
        latency_ms=round(latency, 1),
        cost_usd=0.0,
    )


def _view(q: EvalQuestion, r: EvalResult) -> ResultView:
    return ResultView(
        answerable=q.expected_answer is not None,
        relevant_rank=r.relevant_rank,
        kept_relevant=r.kept_relevant,
        answered=r.answered,
        scores=[(c["rerank_score"], c["relevant"]) for c in r.candidates],
        latency_ms=r.latency_ms,
        cost_usd=r.cost_usd,
        judge=r.judge,
        error=r.error is not None,
    )


def views_for(results: list[tuple[EvalQuestion, EvalResult]]) -> list[ResultView]:
    return [_view(q, r) for q, r in results]
