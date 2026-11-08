"""Retrieval and answer metrics. Pure functions over stored results, so any run can be
re-scored (for example at a different cutoff) without re-running it.

Terms:
- answerable question: has an expected answer; one or more passages are *relevant*.
- unanswerable question: the library does not answer it; Shiori should say "not found".
- hit@k: a relevant passage is among the top k results.
- MRR (mean reciprocal rank): average of 1/rank of the first relevant passage (0 if none).
- coverage: a relevant passage passed the relevance cutoff, so the LLM got to see it.
- rejection: for an unanswerable question, nothing passed the cutoff, so no answer was made up.
"""

import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.ingest.text_utils import cjk_ratio

# Cutoffs tried by the sweep: log-spaced, because reranker scores span orders of magnitude.
SWEEP_CUTOFFS = [0.0, 0.00003, 0.0001, 0.0003, 0.001, 0.003, 0.01, 0.03, 0.1, 0.3]
HIT_KS = (1, 3, 5)


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def is_relevant(
    chunk_text: str, chunk_document_id: str, document_id: str | None, evidence: str | None
) -> bool:
    """A passage is relevant if it comes from the expected paper and contains the evidence."""
    if not evidence:
        return False
    if document_id and chunk_document_id != document_id:
        return False
    return normalize(evidence) in normalize(chunk_text)


@dataclass
class ResultView:
    """What metrics need from one question's result."""

    answerable: bool
    relevant_rank: int | None
    kept_relevant: bool
    answered: bool
    scores: list[tuple[float | None, bool]]  # (rerank score, relevant) per ranked passage
    latency_ms: float
    cost_usd: float = 0.0
    judge: dict[str, Any] | None = None
    error: bool = False


def retrieval_metrics(results: list[ResultView]) -> dict[str, Any]:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    out: dict[str, Any] = {
        "questions": len(results),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
    }
    if answerable:
        for k in HIT_KS:
            out[f"hit@{k}"] = _mean(r.relevant_rank is not None and r.relevant_rank <= k
                                    for r in answerable)  # fmt: skip
        out["mrr"] = _mean(1 / r.relevant_rank if r.relevant_rank else 0.0 for r in answerable)
        out["coverage"] = _mean(r.kept_relevant for r in answerable)
    if unanswerable:
        out["rejection"] = _mean(not r.answered for r in unanswerable)
    latencies = [r.latency_ms for r in results]
    if latencies:
        out["latency_p50_ms"] = round(statistics.median(latencies), 1)
    out["cost_usd"] = round(sum(r.cost_usd for r in results), 6)
    out["errors"] = sum(r.error for r in results)
    return out


def cutoff_sweep(results: list[ResultView], k: int = 6) -> dict[str, Any]:
    """How coverage and rejection would change at each cutoff, using stored scores.

    Only meaningful for reranked runs (scores are reranker probabilities).
    """
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    if not any(score is not None for r in results for score, _ in r.scores):
        return {"points": [], "best": None}

    points = []
    for cutoff in SWEEP_CUTOFFS:
        coverage = (
            _mean(_kept_relevant_at(r, cutoff, k) for r in answerable) if answerable else None
        )
        rejection = (
            _mean(not _any_kept_at(r, cutoff) for r in unanswerable) if unanswerable else None
        )
        parts = [v for v in (coverage, rejection) if v is not None]
        points.append(
            {
                "cutoff": cutoff,
                "coverage": coverage,
                "rejection": rejection,
                "balanced": round(sum(parts) / len(parts), 4) if parts else None,
            }
        )
    scored = [p for p in points if p["balanced"] is not None]
    # Highest balanced score; on ties prefer the HIGHER cutoff: it keeps every answer the
    # lower ones keep, and still filters clearly unrelated questions before any LLM call.
    best = max(scored, key=lambda p: (p["balanced"], p["cutoff"])) if scored else None
    return {"points": points, "best": best}


def answer_metrics(results: list[ResultView]) -> dict[str, Any]:
    """Scores from the LLM judge (answer runs only)."""
    judged = [r for r in results if r.judge]
    answerable = [r for r in judged if r.answerable]
    unanswerable = [r for r in judged if not r.answerable]
    out: dict[str, Any] = {"judged": len(judged)}
    if answerable:
        out["correctness"] = _mean(
            _CORRECTNESS.get(r.judge["correctness"], 0.0) for r in answerable
        )
        faithful = [r.judge["faithful"] for r in answerable if r.judge.get("faithful") is not None]
        if faithful:
            out["faithfulness"] = _mean(faithful)
        out["cited_relevant"] = _mean(bool(r.judge.get("cited_relevant")) for r in answerable)
    checked = [r for r in judged if r.judge.get("language_match") is not None]
    if checked:
        out["language_match"] = _mean(r.judge["language_match"] for r in checked)
    if unanswerable:
        out["said_not_found"] = _mean(bool(r.judge.get("said_not_found")) for r in unanswerable)
    return out


_CORRECTNESS = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}


def answer_in_language(question_language: str, answer: str) -> bool:
    """Did the answer use the question's language? Answers quote sources in the other
    language (an English list of Japanese example sentences), so the question's language
    only has to make up a real share (30%) of the letters."""
    latin = sum(1 for c in answer if c.isascii() and c.isalpha())
    cjk = round(cjk_ratio(answer) * sum(1 for c in answer if not c.isspace()))
    total = latin + cjk
    if total == 0:
        return True
    share = latin / total if question_language == "en" else cjk / total
    return share >= 0.3


def _kept_relevant_at(r: ResultView, cutoff: float, k: int) -> bool:
    return any(relevant and (score or 0.0) >= cutoff for score, relevant in r.scores[:k])


def _any_kept_at(r: ResultView, cutoff: float) -> bool:
    return any((score or 0.0) >= cutoff for score, _ in r.scores)


def _mean(values: Any) -> float:
    items = [float(v) for v in values]
    return round(sum(items) / len(items), 4) if items else 0.0
