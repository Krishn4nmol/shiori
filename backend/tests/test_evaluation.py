import json
import re
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.evaluation import wait_for_run
from app.api.search import get_reranker_if_enabled
from app.auth.deps import get_current_user
from app.db import SessionLocal
from app.evaluation.llm_tasks import TaskError, parse_json
from app.evaluation.runner import mark_interrupted_runs
from app.ingest.embedder import get_embedder
from app.llm import LLMResult, Message, get_llm
from app.main import app
from app.models import EvalRun, User, UserRole
from tests.fakes import FixedReranker, TokenEmbedder

EVIDENCE = "forty sites over three years"


class EvalLLM:
    """Plays question writer, answerer and judge, recognising each by its system prompt."""

    provider = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, bad_evidence: bool = False, declines: bool = False) -> None:
        self.bad_evidence = bad_evidence
        self.declines = declines  # answer the unanswerable question with "not in the sources"
        self.judged = 0

    async def complete(
        self, *, system: str, messages: list[Message], max_tokens: int = 4000
    ) -> LLMResult:
        content = messages[-1]["content"]
        if "write ONE question" in system:
            passage = re.search(r"<passage>\n(.*)\n</passage>", content, re.DOTALL).group(1)
            evidence = "not in the passage at all" if self.bad_evidence else passage[10:40]
            reply = {"question": "What does this passage say?", "answer": "Something.",
                     "evidence": evidence}  # fmt: skip
        elif "does NOT answer" in system:
            reply = {"questions": ["Who funded the dune study?", "砂丘の調査費用はいくらですか？"]}
        elif "You grade answers" in system:
            self.judged += 1
            unanswerable = "does not answer this question" in content
            # Like a real judge that grades a decline "correct" but forgets the declined flag.
            verdict = "correct" if not unanswerable or self.declines else "wrong"
            reply = {"correctness": verdict, "faithful": True, "declined": False,
                     "reason": "graded"}  # fmt: skip
        else:
            reply = {}
        return LLMResult(text=f"```json\n{json.dumps(reply)}\n```", model=self.model,
                         input_tokens=500, output_tokens=40)  # fmt: skip

    async def stream(self, *, system: str, messages: list[Message], max_tokens: int = 4000):
        yield "Forty sites over three years [1]."
        yield LLMResult(text="Forty sites over three years [1].", model=self.model,
                        input_tokens=2000, output_tokens=20)  # fmt: skip


@pytest.fixture
def llm() -> EvalLLM:
    return EvalLLM()


@pytest.fixture
async def client(library: dict[str, uuid.UUID], llm: EvalLLM) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_embedder] = TokenEmbedder
    app.dependency_overrides[get_reranker_if_enabled] = FixedReranker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    for dep in (get_llm, get_embedder, get_reranker_if_enabled):
        app.dependency_overrides.pop(dep, None)


async def add_questions(client: AsyncClient, library: dict[str, uuid.UUID]) -> None:
    good = {
        "question": "How many sites were measured, and for how long?",
        "expected_answer": "Forty sites over three years.",
        "document_id": str(library["dunes"]),
        "evidence": EVIDENCE,
    }
    assert (await client.post("/api/eval/questions", json=good)).status_code == 201
    unanswerable = {"question": "Who funded the dune study?"}
    assert (await client.post("/api/eval/questions", json=unanswerable)).status_code == 201


async def run(client: AsyncClient, **body) -> dict:
    res = await client.post("/api/eval/runs", json={"name": "test run", **body})
    assert res.status_code == 201, res.text
    run_id = res.json()["id"]
    await wait_for_run(uuid.UUID(run_id))
    return (await client.get(f"/api/eval/runs/{run_id}")).json()


# --- parsing --------------------------------------------------------------------------


def test_parse_json_tolerates_fences_and_prose() -> None:
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(TaskError):
        parse_json("no json here")


# --- test set -------------------------------------------------------------------------


async def test_evidence_must_be_in_the_paper(
    client: AsyncClient, library: dict[str, uuid.UUID]
) -> None:
    body = {"question": "How many sites?", "expected_answer": "Forty.",
            "document_id": str(library["dunes"])}  # fmt: skip
    missing = await client.post("/api/eval/questions", json=body)
    assert missing.status_code == 422
    wrong = await client.post("/api/eval/questions", json={**body, "evidence": "ninety sites"})
    assert wrong.status_code == 422
    right = await client.post("/api/eval/questions", json={**body, "evidence": "FORTY  sites"})
    assert right.status_code == 201 and right.json()["language"] == "en"


async def test_generate_questions_checks_evidence(client: AsyncClient, llm: EvalLLM) -> None:
    res = (
        await client.post("/api/eval/questions/generate", json={"count": 3, "unanswerable": 2})
    ).json()
    answerable = [q for q in res["created"] if q["expected_answer"]]
    unanswerable = [q for q in res["created"] if not q["expected_answer"]]
    assert len(answerable) == 3 and all(q["evidence"] and q["document_id"] for q in answerable)
    assert [q["language"] for q in unanswerable] == ["en", "ja"]

    llm.bad_evidence = True
    res = (
        await client.post("/api/eval/questions/generate", json={"count": 2, "unanswerable": 0})
    ).json()
    assert res["created"] == [] and len(res["skipped"]) == 2
    assert "not an exact quote" in res["skipped"][0]


# --- runs -----------------------------------------------------------------------------


async def test_retrieval_run_measures_search(
    client: AsyncClient, library: dict[str, uuid.UUID], llm: EvalLLM
) -> None:
    await add_questions(client, library)
    result = await run(client, kind="retrieval")

    assert result["status"] == "done" and result["progress"] == result["total"] == 2
    m = result["metrics"]
    assert (m["answerable"], m["unanswerable"]) == (1, 1)
    assert m["hit@1"] == 1.0 and m["mrr"] == 1.0
    assert m["coverage"] == 1.0
    assert m["rejection"] == 0.0  # FixedReranker always keeps one passage
    assert len(m["sweep"]["points"]) == 10 and m["sweep"]["best"] is not None
    assert result["config"]["rerank"] is True and result["config"]["llm_model"] is None
    assert llm.judged == 0  # retrieval runs never call the LLM

    answerable = next(r for r in result["results"] if r["expected_answer"])
    assert answerable["relevant_rank"] == 1
    assert any(c["relevant"] and c["kept"] for c in answerable["candidates"])


async def test_answer_run_is_judged(
    client: AsyncClient, library: dict[str, uuid.UUID], llm: EvalLLM
) -> None:
    await add_questions(client, library)
    result = await run(client, kind="answer")
    m = result["metrics"]
    assert m["correctness"] == 1.0 and m["faithfulness"] == 1.0
    assert m["cited_relevant"] == 1.0
    assert m["said_not_found"] == 0.0  # it answered the unanswerable question
    assert m["cost_usd"] > 0
    assert result["config"]["prompt_version"] and result["config"]["llm_model"]
    assert llm.judged == 2


async def test_run_without_reranker_has_no_cutoff(
    client: AsyncClient, library: dict[str, uuid.UUID]
) -> None:
    await add_questions(client, library)
    result = await run(client, kind="retrieval", rerank=False)
    assert result["config"]["rerank"] is False
    assert result["config"]["min_rerank_score"] == 0.0
    assert result["metrics"]["sweep"] == {"points": [], "best": None}


async def test_run_needs_questions(client: AsyncClient) -> None:
    res = await client.post("/api/eval/runs", json={"name": "empty"})
    assert res.status_code == 400


async def test_interrupted_runs_are_marked_failed(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        stale = EvalRun(name="stale", kind="retrieval", config={}, status="running",
                        progress=1, total=5)  # fmt: skip
        session.add(stale)
        await session.commit()
        run_id = stale.id
    await mark_interrupted_runs()
    detail = (await client.get(f"/api/eval/runs/{run_id}")).json()
    assert detail["status"] == "failed" and "restart" in detail["error"]
    assert (await client.delete(f"/api/eval/runs/{run_id}")).status_code == 204


async def test_evaluation_is_admin_only(client: AsyncClient) -> None:
    app.dependency_overrides[get_current_user] = lambda: User(
        id=uuid.uuid4(), email="m@lab.test", name="M", password_hash="x",
        role=UserRole.member, is_active=True, session_version=0,
    )  # fmt: skip
    assert (await client.get("/api/eval/questions")).status_code == 403
    assert (await client.post("/api/eval/runs", json={"name": "x"})).status_code == 403


async def test_correct_grade_on_unanswerable_counts_as_not_found(
    client: AsyncClient, library: dict[str, uuid.UUID], llm: EvalLLM
) -> None:
    llm.declines = True
    await add_questions(client, library)
    result = await run(client, kind="answer")
    assert result["metrics"]["said_not_found"] == 1.0
