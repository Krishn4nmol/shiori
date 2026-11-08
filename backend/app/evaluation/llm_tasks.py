"""LLM-powered parts of evaluation: writing test questions and judging answers.

Every LLM output is checked before it is trusted: generated questions must quote their
evidence exactly from the passage, and judge verdicts must parse into the expected fields.
"""

from dataclasses import dataclass
from typing import Any

from app.evaluation.metrics import normalize
from app.llm import LLMClient, LLMError
from app.llm.json_utils import parse_json_object

GENERATE_SYSTEM = """\
You write test questions for a search system over research papers and study materials.
Given one passage, write ONE question that this passage answers, the short answer, and an exact \
quote from the passage that proves the answer.

Rules:
- Write the question in {language}. The passage may be in another language: that is intended.
- Ask what a real reader would ask, in their own words; do not copy long phrases from the passage.
- The answer must come from the passage alone.
- "evidence" must be copied character-for-character from the passage: 4 to 60 characters that \
contain the key fact. Include no ellipses and no added words.
- The passage was extracted from a PDF and may contain small reading aids (furigana) merged into \
the text. Never choose evidence that contains them.

Reply with JSON only: {{"question": "...", "answer": "...", "evidence": "..."}}"""

UNANSWERABLE_SYSTEM = """\
You write test questions that a library of papers does NOT answer, to check that a search \
system says "not found" instead of making something up.
Given the titles and short excerpts of every paper in the library, write {count} questions a \
reader of this library might plausibly ask, but that none of these papers answer. Mix English \
and Japanese. Avoid questions that are trivially unrelated (like the weather): stay near the \
papers' topics.

Reply with JSON only: {{"questions": ["...", "..."]}}"""

JUDGE_SYSTEM = """\
You grade answers produced by a research assistant that must answer only from given sources.

Given the question, the expected answer, the assistant's answer and the sources it was given:
- "correctness": "correct" if the answer gives the expected answer (any language, any wording), \
"partial" if it gives part of it or adds wrong details, "wrong" if it does not give it or \
contradicts it.
- "faithful": true if every claim in the answer is supported by the sources; false if anything \
is not in the sources.
- "declined": true if the answer says the sources do not contain the answer.
- "reason": one short sentence explaining the grade.

Reply with JSON only: \
{"correctness": "...", "faithful": true, "declined": false, "reason": "..."}"""


class TaskError(Exception):
    """The LLM's output could not be used; the message says why."""


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in an LLM reply, tolerating code fences and surrounding prose."""
    try:
        return parse_json_object(text)
    except ValueError as err:
        raise TaskError(str(err)) from err


@dataclass
class GeneratedQuestion:
    question: str
    answer: str
    evidence: str


async def generate_question(llm: LLMClient, passage: str, language: str) -> GeneratedQuestion:
    reply = await llm.complete(
        system=GENERATE_SYSTEM.format(language="Japanese" if language == "ja" else "English"),
        messages=[{"role": "user", "content": f"<passage>\n{passage}\n</passage>"}],
        max_tokens=2000,
    )
    data = parse_json(reply.text)
    question = str(data.get("question", "")).strip()
    answer = str(data.get("answer", "")).strip()
    evidence = str(data.get("evidence", "")).strip()
    if not question or not answer or not evidence:
        raise TaskError("missing question, answer or evidence")
    if normalize(evidence) not in normalize(passage):
        raise TaskError(f"evidence is not an exact quote from the passage: {evidence!r}")
    return GeneratedQuestion(question, answer, evidence)


async def generate_unanswerable(llm: LLMClient, library_overview: str, count: int) -> list[str]:
    reply = await llm.complete(
        system=UNANSWERABLE_SYSTEM.format(count=count),
        messages=[{"role": "user", "content": library_overview}],
        max_tokens=3000,
    )
    questions = parse_json(reply.text).get("questions")
    if not isinstance(questions, list):
        raise TaskError("missing questions list")
    return [str(q).strip() for q in questions if str(q).strip()][:count]


@dataclass
class Verdict:
    correctness: str
    faithful: bool
    declined: bool
    reason: str
    input_tokens: int
    output_tokens: int
    model: str


async def judge_answer(
    llm: LLMClient, question: str, expected: str, answer: str, sources: list[str]
) -> Verdict:
    blocks = "\n".join(f'<source id="{i}">\n{s}\n</source>' for i, s in enumerate(sources, 1))
    content = (
        f"<question>{question}</question>\n<expected_answer>{expected}</expected_answer>\n"
        f"<answer>{answer}</answer>\n<sources>\n{blocks}\n</sources>"
    )
    try:
        reply = await llm.complete(
            system=JUDGE_SYSTEM, messages=[{"role": "user", "content": content}], max_tokens=2000
        )
    except LLMError as err:
        raise TaskError(f"judge call failed: {err}") from err
    data = parse_json(reply.text)
    correctness = str(data.get("correctness", "")).lower()
    if correctness not in ("correct", "partial", "wrong"):
        raise TaskError(f"judge gave an unknown grade: {correctness!r}")
    return Verdict(
        correctness=correctness,
        faithful=bool(data.get("faithful")),
        declined=bool(data.get("declined")),
        reason=str(data.get("reason", ""))[:500],
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        model=reply.model,
    )
