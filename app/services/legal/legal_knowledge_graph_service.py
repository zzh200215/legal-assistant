"""Neo4j-backed legal knowledge graph with graceful degradation.

The relational database remains the source of truth. Neo4j stores a derived
graph for legal-source version relations and legal-domain associations, then
returns only evidence for articles already retrieved by lexical/vector recall.
This keeps graph expansion explainable and prevents it from introducing an
unrelated legal source into an answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.legal import LegalArticle, LegalSource

try:  # The application still starts when the optional graph dependency is absent.
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover - exercised in deployments without Neo4j.
    GraphDatabase = None


logger = logging.getLogger(__name__)
settings = get_settings()


def _json_list(raw: str | None) -> list[str | int]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


class LegalKnowledgeGraphService:
    """Synchronize legal metadata to Neo4j and expose relation evidence."""

    def __init__(self, *, driver=None) -> None:
        self._driver = driver
        self._connection_failure_at = 0.0
        self._schema_ready = False

    @property
    def enabled(self) -> bool:
        return bool(settings.NEO4J_ENABLED and settings.NEO4J_URI and settings.NEO4J_PASSWORD)

    def _get_driver(self):
        if self._driver is not None:
            return self._driver
        if not self.enabled or GraphDatabase is None:
            return None
        # Avoid attempting a network connection on every request when Neo4j is down.
        if time.monotonic() - self._connection_failure_at < 60:
            return None
        try:
            driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
                max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
            )
            driver.verify_connectivity()
            self._driver = driver
            return driver
        except Exception as exc:
            self._connection_failure_at = time.monotonic()
            logger.warning("Neo4j legal graph unavailable: %s", type(exc).__name__)
            return None

    @staticmethod
    def _source_key(user_id: int, source_id: int) -> str:
        return f"{user_id}:source:{source_id}"

    @staticmethod
    def _article_key(user_id: int, article_id: int) -> str:
        return f"{user_id}:article:{article_id}"

    @staticmethod
    def _label_key(user_id: int, value: str) -> str:
        return f"{user_id}:label:{value.strip().lower()}"

    async def sync_source(self, db: Session, source_id: int, user_id: int) -> bool:
        """Upsert one source and its articles into the derived graph."""
        source = (
            db.query(LegalSource)
            .filter(LegalSource.id == source_id, LegalSource.user_id == user_id)
            .first()
        )
        if not source:
            return False
        articles = (
            db.query(LegalArticle)
            .filter(LegalArticle.source_id == source.id)
            .order_by(LegalArticle.sequence)
            .all()
        )
        payload = self._source_payload(source, articles)
        return await asyncio.to_thread(self._sync_source_sync, payload)

    async def sync_sources(self, db: Session, user_id: int, source_ids: Iterable[int] | None = None) -> int:
        query = db.query(LegalSource).filter(LegalSource.user_id == user_id)
        if source_ids is not None:
            ids = list(source_ids)
            if not ids:
                return 0
            query = query.filter(LegalSource.id.in_(ids))
        sources = query.order_by(LegalSource.id).all()
        synced = 0
        for source in sources:
            if await self.sync_source(db, source.id, user_id):
                synced += 1
        return synced

    def _source_payload(self, source: LegalSource, articles: list[LegalArticle]) -> dict:
        return {
            "user_id": source.user_id,
            "key": self._source_key(source.user_id, source.id),
            "source_id": source.id,
            "title": source.title,
            "source_type": source.source_type,
            "citation": source.citation or "",
            "jurisdiction": source.jurisdiction or "",
            "version": source.version or "",
            "status": source.status,
            "effective_date": source.effective_date.isoformat() if source.effective_date else None,
            "law_areas": [str(item).strip() for item in _json_list(source.law_area_json) if str(item).strip()],
            "keywords": [str(item).strip() for item in _json_list(source.keywords_json) if str(item).strip()],
            "amends": [int(item) for item in _json_list(source.amends_json) if str(item).isdigit()],
            "amended_by": [int(item) for item in _json_list(source.amended_by_json) if str(item).isdigit()],
            "articles": [
                {
                    "key": self._article_key(source.user_id, article.id),
                    "article_id": article.id,
                    "article_number": article.article_number,
                    "title": article.title or "",
                    "chapter": article.chapter or "",
                    "section": article.section or "",
                    "sequence": article.sequence,
                }
                for article in articles
            ],
        }

    def _sync_source_sync(self, payload: dict) -> bool:
        driver = self._get_driver()
        if driver is None:
            return False
        try:
            self._ensure_schema(driver)
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                session.execute_write(self._upsert_source, payload)
            return True
        except Exception as exc:
            logger.warning("Neo4j legal graph source sync failed: %s", type(exc).__name__)
            return False

    def _ensure_schema(self, driver) -> None:
        if self._schema_ready:
            return
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                for statement in (
                    "CREATE CONSTRAINT legal_source_key IF NOT EXISTS FOR (node:LegalSource) REQUIRE node.key IS UNIQUE",
                    "CREATE CONSTRAINT legal_article_key IF NOT EXISTS FOR (node:LegalArticle) REQUIRE node.key IS UNIQUE",
                    "CREATE CONSTRAINT legal_law_area_key IF NOT EXISTS FOR (node:LegalLawArea) REQUIRE node.key IS UNIQUE",
                    "CREATE CONSTRAINT legal_keyword_key IF NOT EXISTS FOR (node:LegalKeyword) REQUIRE node.key IS UNIQUE",
                ):
                    session.run(statement).consume()
            self._schema_ready = True
        except Exception as exc:
            # Existing deployments may use a Neo4j version that does not support
            # IF NOT EXISTS. Graph writes still work without these constraints.
            logger.warning("Neo4j legal graph schema initialization skipped: %s", type(exc).__name__)

    @classmethod
    def _upsert_source(cls, tx, payload: dict) -> None:
        tx.run(
            """
            MERGE (source:LegalSource {key: $key})
            SET source.user_id = $user_id,
                source.source_id = $source_id,
                source.title = $title,
                source.source_type = $source_type,
                source.citation = $citation,
                source.jurisdiction = $jurisdiction,
                source.version = $version,
                source.status = $status,
                source.effective_date = $effective_date
            WITH source
            OPTIONAL MATCH (source)-[old_area:IN_LAW_AREA|TAGGED_WITH]->()
            DELETE old_area
            WITH source
            UNWIND $law_areas AS law_area
            MERGE (area:LegalLawArea {key: toString($user_id) + ':label:' + toLower(law_area)})
            SET area.user_id = $user_id, area.name = law_area
            MERGE (source)-[:IN_LAW_AREA]->(area)
            """,
            **payload,
        ).consume()
        tx.run(
            """
            MATCH (source:LegalSource {key: $key})
            OPTIONAL MATCH (source)-[old_keyword:TAGGED_WITH]->()
            DELETE old_keyword
            WITH source
            UNWIND $keywords AS keyword
            MERGE (tag:LegalKeyword {key: toString($user_id) + ':label:' + toLower(keyword)})
            SET tag.user_id = $user_id, tag.name = keyword
            MERGE (source)-[:TAGGED_WITH]->(tag)
            """,
            **payload,
        ).consume()
        tx.run(
            """
            MATCH (source:LegalSource {key: $key})
            OPTIONAL MATCH (source)-[:HAS_ARTICLE]->(old_article:LegalArticle)
            WHERE NOT old_article.article_id IN $article_ids
            DETACH DELETE old_article
            WITH source
            UNWIND $articles AS article
            MERGE (node:LegalArticle {key: article.key})
            SET node.user_id = $user_id,
                node.article_id = article.article_id,
                node.article_number = article.article_number,
                node.title = article.title,
                node.chapter = article.chapter,
                node.section = article.section,
                node.sequence = article.sequence
            MERGE (source)-[:HAS_ARTICLE]->(node)
            """,
            article_ids=[article["article_id"] for article in payload["articles"]],
            **payload,
        ).consume()
        tx.run(
            """
            MATCH (source:LegalSource {key: $key})
            OPTIONAL MATCH (source)-[outgoing:AMENDS]->()
            DELETE outgoing
            WITH source
            OPTIONAL MATCH ()-[incoming:AMENDED_BY]->(source)
            DELETE incoming
            """,
            **payload,
        ).consume()
        tx.run(
            """
            MATCH (source:LegalSource {key: $key})
            UNWIND $amends AS related_id
            MATCH (related:LegalSource {key: toString($user_id) + ':source:' + toString(related_id)})
            MERGE (source)-[:AMENDS]->(related)
            MERGE (related)-[:AMENDED_BY]->(source)
            """,
            **payload,
        ).consume()
        tx.run(
            """
            MATCH (source:LegalSource {key: $key})
            UNWIND $amended_by AS related_id
            MATCH (related:LegalSource {key: toString($user_id) + ':source:' + toString(related_id)})
            MERGE (related)-[:AMENDS]->(source)
            MERGE (source)-[:AMENDED_BY]->(related)
            """,
            **payload,
        ).consume()

    async def delete_source(self, source_id: int, user_id: int) -> None:
        await asyncio.to_thread(self._delete_source_sync, source_id, user_id)

    def _delete_source_sync(self, source_id: int, user_id: int) -> None:
        driver = self._get_driver()
        if driver is None:
            return
        try:
            self._ensure_schema(driver)
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                session.run(
                    """
                    MATCH (source:LegalSource {key: $key})
                    OPTIONAL MATCH (source)-[:HAS_ARTICLE]->(article:LegalArticle)
                    WITH source, collect(article) AS articles
                    FOREACH (article IN articles | DETACH DELETE article)
                    DETACH DELETE source
                    """,
                    key=self._source_key(user_id, source_id),
                ).consume()
        except Exception as exc:
            logger.warning("Neo4j legal graph source delete failed: %s", type(exc).__name__)

    async def relation_evidence(self, user_id: int, article_ids: list[int]) -> dict[int, dict]:
        """Return graph relations among already-retrieved article candidates."""
        unique_ids = list(dict.fromkeys(article_ids))
        if len(unique_ids) < 2:
            return {}
        return await asyncio.to_thread(self._relation_evidence_sync, user_id, unique_ids)

    def _relation_evidence_sync(self, user_id: int, article_ids: list[int]) -> dict[int, dict]:
        driver = self._get_driver()
        if driver is None:
            return {}
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                rows = session.run(
                    """
                    UNWIND $article_ids AS article_id
                    MATCH (article:LegalArticle {key: toString($user_id) + ':article:' + toString(article_id)})
                    MATCH (source:LegalSource {user_id: $user_id})-[:HAS_ARTICLE]->(article)
                    OPTIONAL MATCH (source)-[version_relation:AMENDS|AMENDED_BY]-(related_source:LegalSource {user_id: $user_id})-[:HAS_ARTICLE]->(related_article:LegalArticle)
                    WHERE related_source.status <> 'inactive' AND related_article.article_id IN $article_ids
                    WITH article, source, collect(DISTINCT CASE WHEN related_article IS NULL THEN NULL ELSE {article_id: related_article.article_id, relation: type(version_relation)} END) AS version_links
                    OPTIONAL MATCH (source)-[:IN_LAW_AREA]->(area:LegalLawArea {user_id: $user_id})<-[:IN_LAW_AREA]-(area_source:LegalSource {user_id: $user_id})-[:HAS_ARTICLE]->(area_article:LegalArticle)
                    WHERE area_source.key <> source.key AND area_source.status <> 'inactive' AND area_article.article_id IN $article_ids
                    WITH article, [link IN version_links WHERE link IS NOT NULL] AS version_links, collect(DISTINCT area_article.article_id) AS area_links
                    RETURN article.article_id AS article_id, version_links, [item IN area_links WHERE item IS NOT NULL] AS area_links
                    """,
                    user_id=user_id,
                    article_ids=article_ids,
                )
                evidence = {}
                for row in rows:
                    version_links = list(row["version_links"] or [])
                    area_links = list(row["area_links"] or [])
                    support_count = len(version_links) + len(area_links)
                    if support_count:
                        evidence[int(row["article_id"])] = {
                            "version_relations": sorted({item["relation"] for item in version_links}),
                            "related_article_ids": sorted({int(item["article_id"]) for item in version_links + [{"article_id": item} for item in area_links]}),
                            "shared_law_area": bool(area_links),
                            "support_count": support_count,
                        }
                return evidence
        except Exception as exc:
            logger.info("Neo4j legal graph evidence unavailable: %s", type(exc).__name__)
            return {}

    async def source_graph(self, user_id: int, source_id: int, depth: int = 2) -> dict:
        return await asyncio.to_thread(self._source_graph_sync, user_id, source_id, depth)

    def _source_graph_sync(self, user_id: int, source_id: int, depth: int) -> dict:
        driver = self._get_driver()
        if driver is None:
            return {"available": False, "nodes": [], "edges": []}
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                rows = session.run(
                    """
                    MATCH path=(root:LegalSource {key: $key})-[:AMENDS|AMENDED_BY*0..3]-(related:LegalSource {user_id: $user_id})
                    WHERE length(path) <= $depth
                    RETURN nodes(path) AS nodes, relationships(path) AS relationships
                    """,
                    key=self._source_key(user_id, source_id), user_id=user_id, depth=depth,
                )
                nodes: dict[str, dict] = {}
                edges: set[tuple[str, str, str]] = set()
                for row in rows:
                    for node in row["nodes"]:
                        nodes[node["key"]] = {
                            "id": node.get("source_id"),
                            "title": node.get("title"),
                            "status": node.get("status"),
                            "version": node.get("version"),
                        }
                    for relation in row["relationships"]:
                        edges.add((relation.start_node["key"], relation.end_node["key"], relation.type))
                return {
                    "available": True,
                    "nodes": list(nodes.values()),
                    "edges": [{"from_key": start, "to_key": end, "relation": relation} for start, end, relation in sorted(edges)],
                }
        except Exception as exc:
            logger.info("Neo4j legal source graph unavailable: %s", type(exc).__name__)
            return {"available": False, "nodes": [], "edges": []}

    async def health(self) -> dict:
        return await asyncio.to_thread(self._health_sync)

    def _health_sync(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "connected": False, "reason": "Neo4j is not configured"}
        if GraphDatabase is None:
            return {"enabled": True, "connected": False, "reason": "neo4j driver is not installed"}
        driver = self._get_driver()
        if driver is None:
            return {"enabled": True, "connected": False, "reason": "Neo4j is unavailable"}
        return {"enabled": True, "connected": True, "database": settings.NEO4J_DATABASE}


legal_knowledge_graph_service = LegalKnowledgeGraphService()
