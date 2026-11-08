"""The planner: one LLM call that decides how to answer a question.

It also resolves follow-ups ("and for how long?") using the conversation, so it replaces the
separate rewrite call: a planned question costs the same two LLM calls as before.
If the plan cannot be parsed, it falls back to a plain search, so planning never breaks answering.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from app.agent.catalog import CatalogEntry, catalog_text
from app.llm import LLMClient, LLMResult, Message

QuestionType = Literal["details", "lookup", "find_all", "compare", "summary", "relations"]
TYPES: tuple[QuestionType, ...] = (
    "details", "lookup", "find_all", "compare", "summary", "relations",
)  # fmt: skip
MAX_QUERIES = 3
MAX_ENTITIES = 8

PLANNER_SYSTEM = """\
You plan how a research assistant answers a question about a library of papers. You do not \
answer it.

Choose the question type:
- "details": about a paper's metadata: author, title, page count, language, file, date added.
- "lookup": a specific fact that one or a few passages can answer (the default).
- "find_all": asks for EVERY instance of something in a paper ("list all", "every sentence that \
uses", "how many times"). Needs the whole paper, not a few passages.
- "compare": compares two or more papers, or the same topic across papers.
- "summary": asks for a summary or overview of a whole paper.
- "relations": how people, things or papers are connected: who did what for whom, how X \
relates to Y, what two papers have in common, everything connected to X.

Also give:
- "papers": keys (P1, P2, ...) of the papers the question is about; [] if it does not name or \
clearly point to any. Use the conversation to resolve "it", "this paper", "the second one".
- "question": the question rewritten to stand alone, resolving references from the \
conversation, in the language it was asked in.
- "queries": 1 to 3 search queries for "lookup" and "compare": the standalone question, plus \
the same question in the language of the relevant papers if that differs (English <-> \
Japanese), plus at most one rephrasing with likely keywords.
- "entities": for "relations", the 1 to 4 people, places, things or concepts the question is \
about, each in English and, if the papers are Japanese, also in Japanese (e.g. "vegetation", \
"植生"); [] otherwise.

Reply with JSON only: {"type": "...", "papers": ["P1"], "question": "...", \
"queries": ["...", "..."], "entities": []}"""


@dataclass
class Plan:
    type: QuestionType
    question: str  # standalone version of the question
    queries: list[str]
    papers: list[CatalogEntry] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)  # for "relations" questions
    fallback: bool = False  # the planner's reply was unusable; plain search is used

    def describe(self) -> dict:
        return {
            "question_type": self.type,  # not "type": that key names the stream event
            "question": self.question,
            "queries": self.queries,
            "papers": [{"id": str(p.id), "title": p.title} for p in self.papers],
            "entities": self.entities,
            "fallback": self.fallback,
        }


async def plan_question(
    llm: LLMClient, question: str, history: list[Message], catalog: list[CatalogEntry]
) -> tuple[Plan, LLMResult | None]:
    conversation = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-6:])
    content = (
        f"<library>\n{catalog_text(catalog)}\n</library>\n"
        f"<conversation>\n{conversation or '(none)'}\n</conversation>\n"
        f"<question>{question}</question>"
    )
    reply = await llm.complete(
        system=PLANNER_SYSTEM, messages=[{"role": "user", "content": content}], max_tokens=2000
    )
    return parse_plan(reply.text, question, catalog), reply


def parse_plan(text: str, question: str, catalog: list[CatalogEntry]) -> Plan:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return Plan(type="lookup", question=question, queries=[question], fallback=True)

    kind = data.get("type") if data.get("type") in TYPES else "lookup"
    standalone = str(data.get("question") or "").strip() or question
    by_key = {e.key: e for e in catalog}
    papers = [by_key[k] for k in dict.fromkeys(map(str, data.get("papers") or [])) if k in by_key]
    queries = [str(q).strip() for q in data.get("queries") or [] if str(q).strip()]
    queries = list(dict.fromkeys([standalone, *queries]))[:MAX_QUERIES]
    entities = [str(e).strip() for e in data.get("entities") or [] if str(e).strip()]
    return Plan(
        type=kind,
        question=standalone,
        queries=queries,
        papers=papers,
        entities=list(dict.fromkeys(entities))[:MAX_ENTITIES],
    )
