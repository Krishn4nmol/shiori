"""LLM extraction of entities and relations from passages.

Every relation must quote its evidence exactly from its passage, or it is dropped: the graph
only contains claims that can be traced back to the text. Entity names are canonical English
(with the Japanese form as an alias), so mentions in both languages become one node.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from app.evaluation.metrics import normalize
from app.llm import LLMClient
from app.llm.json_utils import parse_json_object

EntityType = Literal[
    "person", "organization", "place", "concept", "method", "measurement", "object", "other"
]
ENTITY_TYPES: tuple[str, ...] = (
    "person", "organization", "place", "concept", "method", "measurement", "object", "other",
)  # fmt: skip
MAX_ENTITIES_PER_BATCH = 20

EXTRACT_SYSTEM = """\
You build a knowledge graph from passages of research papers and study materials.

From the numbered passages, extract:
- "entities": the important people, organizations, places, concepts, methods, measurements and \
objects. For each: "name" (a short canonical name in English; for Japanese people keep the \
name and add the role, e.g. "Tanaka (professor)"), "name_ja" (the Japanese form if it \
appears in Japanese, else ""), "type" (one of: person, organization, place, concept, method, \
measurement, object, other) and "passages" (ids where it appears).
- "relations": facts that connect two of those entities. For each: "source" and "target" \
(entity names exactly as you wrote them above), "relation" (a short English verb phrase, e.g. \
"trained on", "measured at", "is part of"), "passage" (one id) and "evidence" (an exact quote from \
that passage, 4 to 80 characters, copied character-for-character).

Rules: at most 20 entities. Only facts stated in the passages, but extract every relation \
between two entities that a passage states. Use one name per thing: if the paper defines an \
abbreviation, use it alone ("DPR", not "Dense Passage Retriever (DPR)"). When the authors \
describe their own work ("we", "our", 本研究), make the method or system they describe the \
source of the relation; if there is none, skip the relation. Never create an entity for the \
authors or the paper itself. Skip words that are not things in the text ("sentence", \
"example", "question"). The passages come from PDFs and may contain small reading aids \
(furigana) merged into the text; never quote them as evidence.

Reply with JSON only: {"entities": [...], "relations": [...]}"""


@dataclass
class Entity:
    key: str  # normalized name, unique within a namespace
    name: str
    type: str
    aliases: list[str] = field(default_factory=list)
    chunk_ids: set[str] = field(default_factory=set)


@dataclass
class Relation:
    source: str  # entity keys
    target: str
    relation: str
    chunk_id: str
    evidence: str


@dataclass
class Extraction:
    entities: dict[str, Entity] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # why some items were rejected
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None

    def merge(self, other: "Extraction") -> None:
        for key, entity in other.entities.items():
            mine = self.entities.setdefault(key, entity)
            if mine is not entity:
                mine.chunk_ids |= entity.chunk_ids
                mine.aliases = list(dict.fromkeys([*mine.aliases, *entity.aliases]))
        seen = {(r.source, r.target, r.relation, r.chunk_id) for r in self.relations}
        self.relations.extend(
            r for r in other.relations if (r.source, r.target, r.relation, r.chunk_id) not in seen
        )
        self.dropped.extend(other.dropped)
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.model = other.model or self.model


def entity_key(name: str) -> str:
    """Normalized form used to merge mentions: NFKC, lowercase, punctuation removed."""
    text = unicodedata.normalize("NFKC", name).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def extract_batch(llm: LLMClient, passages: list[tuple[str, str]]) -> Extraction:
    """`passages`: (chunk_id, text). Returns validated entities and relations."""
    labels = {f"P{i}": chunk_id for i, (chunk_id, _) in enumerate(passages, start=1)}
    texts = {chunk_id: text for chunk_id, text in passages}
    content = "\n\n".join(
        f'<passage id="P{i}">\n{text}\n</passage>' for i, (_, text) in enumerate(passages, 1)
    )
    reply = await llm.complete(
        system=EXTRACT_SYSTEM, messages=[{"role": "user", "content": content}], max_tokens=8000
    )
    out = Extraction(
        input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, model=reply.model
    )
    try:
        data = parse_json_object(reply.text)
    except ValueError as err:
        out.dropped.append(f"unreadable reply: {err}")
        return out

    names: dict[str, str] = {}  # name as written by the LLM -> key
    for item in (data.get("entities") or [])[:MAX_ENTITIES_PER_BATCH]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = entity_key(name)
        chunk_ids = {labels[p] for p in item.get("passages") or [] if p in labels}
        if not key or not chunk_ids:
            out.dropped.append(f"entity without name or valid passage: {name!r}")
            continue
        kind = str(item.get("type") or "other").lower()
        alias = str(item.get("name_ja") or "").strip()
        entity = Entity(
            key=key, name=name[:200], type=kind if kind in ENTITY_TYPES else "other",
            aliases=[alias] if alias else [], chunk_ids=chunk_ids,
        )  # fmt: skip
        out.merge(Extraction(entities={key: entity}))
        names[name] = key

    for item in data.get("relations") or []:
        if not isinstance(item, dict):
            continue
        source = names.get(str(item.get("source") or "").strip())
        target = names.get(str(item.get("target") or "").strip())
        chunk_id = labels.get(str(item.get("passage") or ""))
        evidence = str(item.get("evidence") or "").strip()
        relation = str(item.get("relation") or "").strip()[:100]
        if not (source and target and chunk_id and relation) or source == target:
            out.dropped.append(f"relation with unknown entities or passage: {item}")
            continue
        if not evidence or normalize(evidence) not in normalize(texts[chunk_id]):
            out.dropped.append(f"evidence not quoted from the passage: {evidence!r}")
            continue
        out.relations.append(Relation(source, target, relation, chunk_id, evidence[:300]))
    return out
