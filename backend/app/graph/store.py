"""Neo4j storage for the knowledge graph.

    (:Paper)<-[:PART_OF]-(:Chunk)<-[:MENTIONED_IN]-(:Entity)
    (:Entity)-[:RELATED {relation, evidence, chunk_id, paper_id}]->(:Entity)

Every node carries a namespace (`ns`): the app writes "app", tests write "test" and only ever
delete their own namespace. Neo4j Community has a single database, so this is what keeps tests
away from real data. All queries are fixed templates with parameters; no text from users or
the LLM is ever pasted into Cypher.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import get_settings
from app.graph.extract import Extraction, entity_key

APP_NAMESPACE = "app"

_SCHEMA = [
    "CREATE CONSTRAINT entity_uid IF NOT EXISTS FOR (e:Entity) REQUIRE e.uid IS UNIQUE",
    "CREATE CONSTRAINT paper_uid IF NOT EXISTS FOR (p:Paper) REQUIRE p.uid IS UNIQUE",
    "CREATE CONSTRAINT chunk_uid IF NOT EXISTS FOR (c:Chunk) REQUIRE c.uid IS UNIQUE",
]


@dataclass
class Fact:
    source: str
    relation: str
    target: str
    chunk_id: str
    paper_id: str
    evidence: str

    def line(self) -> str:
        return f"{self.source} —{self.relation}→ {self.target}"


class GraphStore:
    def __init__(self, driver: AsyncDriver, ns: str = APP_NAMESPACE) -> None:
        self.driver = driver
        self.ns = ns

    def _uid(self, value: str) -> str:
        return f"{self.ns}:{value}"

    async def ensure_schema(self) -> None:
        async with self.driver.session() as s:
            for statement in _SCHEMA:
                await s.run(statement)

    # --- writing ------------------------------------------------------------------------

    async def replace_paper(
        self,
        paper_id: str,
        title: str,
        chunks: list[tuple[str, int, int]],  # (chunk_id, page_start, page_end)
        extraction: Extraction,
    ) -> None:
        entities = [
            {
                "uid": self._uid(e.key),
                "key": e.key,
                "name": e.name,
                "type": e.type,
                "aliases": e.aliases,
                "alias_keys": [entity_key(a) for a in e.aliases],
                "chunk_uids": [self._uid(c) for c in e.chunk_ids],
            }
            for e in extraction.entities.values()
        ]
        relations = [
            {
                "a": self._uid(r.source),
                "b": self._uid(r.target),
                "relation": r.relation,
                "chunk_id": r.chunk_id,
                "evidence": r.evidence,
            }  # fmt: skip
            for r in extraction.relations
        ]
        params = {"ns": self.ns, "paper_uid": self._uid(paper_id), "paper_id": paper_id}
        async with self.driver.session() as s:
            await s.execute_write(self._remove_paper, params)
            await s.execute_write(
                self._write_paper, params, title,
                [{"uid": self._uid(c), "id": c, "start": a, "end": b} for c, a, b in chunks],
                entities, relations,
            )  # fmt: skip
            await s.execute_write(self._remove_orphans, params)

    async def delete_paper(self, paper_id: str) -> None:
        params = {"ns": self.ns, "paper_uid": self._uid(paper_id), "paper_id": paper_id}
        async with self.driver.session() as s:
            await s.execute_write(self._remove_paper, params)
            await s.execute_write(
                lambda tx: tx.run("MATCH (p:Paper {uid: $paper_uid}) DETACH DELETE p", params)
            )
            await s.execute_write(self._remove_orphans, params)

    async def clear_namespace(self) -> None:
        async with self.driver.session() as s:
            await s.run("MATCH (n {ns: $ns}) DETACH DELETE n", ns=self.ns)

    @staticmethod
    async def _remove_paper(tx: Any, params: dict) -> None:
        await tx.run("MATCH ()-[r:RELATED {paper_uid: $paper_uid}]->() DELETE r", params)
        await tx.run(
            "MATCH (c:Chunk)-[:PART_OF]->(:Paper {uid: $paper_uid}) DETACH DELETE c", params
        )

    @staticmethod
    async def _write_paper(
        tx: Any, params: dict, title: str, chunks: list, entities: list, relations: list
    ) -> None:
        await tx.run(
            "MERGE (p:Paper {uid: $paper_uid}) SET p.id = $paper_id, p.title = $title, p.ns = $ns",
            {**params, "title": title},
        )
        await tx.run(
            """
            UNWIND $chunks AS c
            MATCH (p:Paper {uid: $paper_uid})
            MERGE (n:Chunk {uid: c.uid})
            SET n.id = c.id, n.page_start = c.start, n.page_end = c.end, n.ns = $ns
            MERGE (n)-[:PART_OF]->(p)
            """,
            {**params, "chunks": chunks},
        )
        await tx.run(
            """
            UNWIND $entities AS ent
            MERGE (e:Entity {uid: ent.uid})
            ON CREATE SET e.key = ent.key, e.name = ent.name, e.type = ent.type, e.ns = $ns,
                          e.aliases = [], e.alias_keys = []
            SET e.aliases = reduce(acc = [], a IN e.aliases + ent.aliases |
                                   CASE WHEN a IN acc THEN acc ELSE acc + a END),
                e.alias_keys = reduce(acc = [], a IN e.alias_keys + ent.alias_keys |
                                      CASE WHEN a IN acc THEN acc ELSE acc + a END)
            WITH e, ent
            UNWIND ent.chunk_uids AS cu
            MATCH (c:Chunk {uid: cu})
            MERGE (e)-[:MENTIONED_IN]->(c)
            """,
            {**params, "entities": entities},
        )
        await tx.run(
            """
            UNWIND $relations AS rel
            MATCH (a:Entity {uid: rel.a}), (b:Entity {uid: rel.b})
            CREATE (a)-[:RELATED {relation: rel.relation, chunk_id: rel.chunk_id,
                                  evidence: rel.evidence, paper_uid: $paper_uid,
                                  paper_id: $paper_id, ns: $ns}]->(b)
            """,
            {**params, "relations": relations},
        )

    @staticmethod
    async def _remove_orphans(tx: Any, params: dict) -> None:
        await tx.run(
            "MATCH (e:Entity {ns: $ns}) WHERE NOT (e)-[:MENTIONED_IN]->() DETACH DELETE e",
            params,
        )

    # --- reading (fixed templates) ------------------------------------------------------

    async def find_entities(
        self, terms: list[str], paper_ids: list[str] | None = None, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Entities whose name or Japanese alias matches any term (substring, both ways)."""
        keys = [k for k in (entity_key(t) for t in terms) if len(k) >= 2]
        if not keys:
            return []
        query = """
            UNWIND $keys AS t
            MATCH (e:Entity {ns: $ns})
            WHERE e.key CONTAINS t OR any(a IN e.alias_keys WHERE a CONTAINS t)
               OR (size(e.key) >= 3 AND t CONTAINS e.key)
               OR any(a IN e.alias_keys WHERE size(a) >= 2 AND t CONTAINS a)
            WITH DISTINCT e
            WHERE $paper_ids IS NULL OR EXISTS {
                MATCH (e)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(p:Paper)
                WHERE p.id IN $paper_ids }
            OPTIONAL MATCH (e)-[r:RELATED]-()
            WITH e, count(r) AS degree
            RETURN e.uid AS uid, e.key AS key, e.name AS name, e.type AS type,
                   e.aliases AS aliases, degree
            ORDER BY degree DESC LIMIT $limit
        """
        async with self.driver.session() as s:
            result = await s.run(query, keys=keys, ns=self.ns, paper_ids=paper_ids, limit=limit)
            return [record.data() async for record in result]

    async def facts_around(self, uids: list[str], limit: int = 40) -> list[Fact]:
        query = """
            MATCH (a:Entity)-[r:RELATED]->(b:Entity)
            WHERE a.uid IN $uids OR b.uid IN $uids
            RETURN a.name AS source, r.relation AS relation, b.name AS target,
                   r.chunk_id AS chunk_id, r.paper_id AS paper_id, r.evidence AS evidence
            LIMIT $limit
        """
        return await self._facts(query, uids=uids, limit=limit)

    async def facts_between(self, uid_a: str, uid_b: str) -> list[Fact]:
        """The relations on the shortest path (up to 4 steps) between two entities."""
        query = """
            MATCH (a:Entity {uid: $a}), (b:Entity {uid: $b})
            MATCH p = shortestPath((a)-[:RELATED*..4]-(b))
            UNWIND relationships(p) AS r
            RETURN startNode(r).name AS source, r.relation AS relation,
                   endNode(r).name AS target, r.chunk_id AS chunk_id,
                   r.paper_id AS paper_id, r.evidence AS evidence
        """
        return await self._facts(query, a=uid_a, b=uid_b)

    async def shared_entities(self, paper_ids: list[str], limit: int = 15) -> list[dict]:
        """Entities mentioned in two or more of the given papers: what connects them."""
        query = """
            MATCH (e:Entity {ns: $ns})-[:MENTIONED_IN]->(c:Chunk)-[:PART_OF]->(p:Paper)
            WHERE p.id IN $paper_ids
            WITH e, collect(DISTINCT p.id) AS papers, collect(DISTINCT c.id) AS chunk_ids
            WHERE size(papers) >= 2
            RETURN e.uid AS uid, e.name AS name, e.type AS type, papers, chunk_ids
            LIMIT $limit
        """
        async with self.driver.session() as s:
            result = await s.run(query, ns=self.ns, paper_ids=paper_ids, limit=limit)
            return [record.data() async for record in result]

    async def overview(self, paper_id: str | None = None, limit: int = 250) -> dict[str, Any]:
        """Nodes and edges for the graph view, most connected entities first."""
        node_query = """
            MATCH (e:Entity {ns: $ns})
            WHERE $paper_id IS NULL OR EXISTS {
                MATCH (e)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(:Paper {id: $paper_id}) }
            OPTIONAL MATCH (e)-[r:RELATED]-()
            WITH e, count(r) AS degree
            OPTIONAL MATCH (e)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(p:Paper)
            RETURN e.key AS key, e.name AS name, e.type AS type, e.aliases AS aliases,
                   degree, collect(DISTINCT p.id) AS papers
            ORDER BY degree DESC LIMIT $limit
        """
        edge_query = """
            MATCH (a:Entity)-[r:RELATED]->(b:Entity)
            WHERE a.key IN $keys AND b.key IN $keys AND r.ns = $ns
            RETURN a.key AS source, b.key AS target, r.relation AS relation,
                   r.chunk_id AS chunk_id, r.paper_id AS paper_id, r.evidence AS evidence
        """
        async with self.driver.session() as s:
            nodes = [r.data() async for r in await s.run(
                node_query, ns=self.ns, paper_id=paper_id, limit=limit)]  # fmt: skip
            keys = [n["key"] for n in nodes]
            edges = [r.data() async for r in await s.run(edge_query, keys=keys, ns=self.ns)]
        return {"nodes": nodes, "edges": edges}

    async def entity(self, key: str) -> dict[str, Any] | None:
        query = """
            MATCH (e:Entity {uid: $uid})
            OPTIONAL MATCH (e)-[:MENTIONED_IN]->(c:Chunk)-[:PART_OF]->(p:Paper)
            WITH e, collect(DISTINCT {chunk_id: c.id, page_start: c.page_start,
                                      paper_id: p.id, paper_title: p.title}) AS mentions
            RETURN e.key AS key, e.name AS name, e.type AS type, e.aliases AS aliases, mentions
        """
        async with self.driver.session() as s:
            record = await (await s.run(query, uid=self._uid(key))).single()
        if record is None:
            return None
        data = record.data()
        data["facts"] = [vars(f) for f in await self.facts_around([self._uid(key)], limit=60)]
        return data

    async def counts(self, paper_id: str) -> tuple[int, int]:
        query = """
            MATCH (p:Paper {uid: $uid})
            OPTIONAL MATCH (e:Entity)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(p)
            WITH p, count(DISTINCT e) AS entities
            OPTIONAL MATCH ()-[r:RELATED {paper_uid: $uid}]->()
            RETURN entities, count(r) AS relations
        """
        async with self.driver.session() as s:
            record = await (await s.run(query, uid=self._uid(paper_id))).single()
        return (record["entities"], record["relations"]) if record else (0, 0)

    async def _facts(self, query: str, **params: Any) -> list[Fact]:
        async with self.driver.session() as s:
            result = await s.run(query, **params)
            return [Fact(**record.data()) async for record in result]


@lru_cache
def get_driver() -> AsyncDriver:
    s = get_settings()
    return AsyncGraphDatabase.driver(s.neo4j_url, auth=(s.neo4j_user, s.neo4j_password))


def get_graph_store() -> GraphStore | None:
    """The app's graph, or None when the graph feature is turned off."""
    if not get_settings().graph_enabled:
        return None
    return GraphStore(get_driver(), APP_NAMESPACE)
