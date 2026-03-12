from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _SessionProtocol(Protocol):
    def __enter__(self) -> "_SessionProtocol": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
    def run(self, query: str, **kwargs: Any) -> Any: ...
    def execute_write(self, callback: Any, **kwargs: Any) -> Any: ...


class _DriverProtocol(Protocol):
    def verify_connectivity(self) -> Any: ...
    def close(self) -> Any: ...
    def session(self, *, database: str) -> _SessionProtocol: ...


def neo4j_available() -> bool:
    return importlib.util.find_spec("neo4j") is not None


def _load_graph_database() -> Any:
    return importlib.import_module("neo4j").GraphDatabase


class Neo4jGraphMirror:
    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str = "neo4j",
        driver: _DriverProtocol | None = None,
    ) -> None:
        self._database = database
        self._driver = driver

        if self._driver is None:
            if not neo4j_available():
                raise ImportError(
                    "Neo4j support requires the 'neo4j' package. Install it with: pip install neo4j"
                )

            graph_database = _load_graph_database()
            self._driver = graph_database.driver(uri, auth=(username, password))
            self._driver.verify_connectivity()

        self._ensure_schema()

    @classmethod
    def from_environment(cls) -> "Neo4jGraphMirror | None":
        uri = os.environ.get("PYOVIS_NEO4J_URI")
        username = os.environ.get("PYOVIS_NEO4J_USERNAME")
        password = os.environ.get("PYOVIS_NEO4J_PASSWORD")
        database = os.environ.get("PYOVIS_NEO4J_DATABASE", "neo4j")

        if not uri or not username or not password:
            return None
        if not neo4j_available():
            logger.warning(
                "neo4j_backend: configuration detected but 'neo4j' package is not installed"
            )
            return None

        try:
            return cls(uri=uri, username=username, password=password, database=database)
        except Exception as exc:
            logger.warning(
                "neo4j_backend: failed to initialize mirror backend: %s", exc
            )
            return None

    def is_enabled(self) -> bool:
        return self._driver is not None

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _ensure_schema(self) -> None:
        if self._driver is None:
            return

        statements = [
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT module_id IF NOT EXISTS FOR (n:Module) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT code_symbol_id IF NOT EXISTS FOR (n:CodeSymbol) REQUIRE n.id IS UNIQUE",
        ]

        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement)

    def mirror_triplet(
        self,
        subject: str,
        predicate: str,
        object_value: str,
        origin: str = "",
    ) -> None:
        if self._driver is None:
            return

        with self._driver.session(database=self._database) as session:
            session.execute_write(
                self._mirror_triplet_tx,
                subject=subject,
                predicate=predicate,
                object_value=object_value,
                origin=origin,
            )

    @staticmethod
    def _mirror_triplet_tx(
        tx, *, subject: str, predicate: str, object_value: str, origin: str
    ) -> None:
        tx.run(
            """
            MERGE (s:Entity {id: $subject})
            SET s.name = $subject,
                s.kind = 'semantic_entity'
            MERGE (o:Entity {id: $object_value})
            SET o.name = $object_value,
                o.kind = 'semantic_entity'
            MERGE (s)-[r:KG_RELATION {predicate: $predicate, source_id: $subject, target_id: $object_value}]->(o)
            SET r.origin = $origin
            """,
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            origin=origin,
        )

    def mirror_code_graph(
        self,
        module: dict[str, Any],
        symbols: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        if self._driver is None:
            return

        with self._driver.session(database=self._database) as session:
            session.execute_write(
                self._mirror_code_graph_tx,
                module=module,
                symbols=symbols,
                edges=edges,
            )

    @staticmethod
    def _mirror_code_graph_tx(
        tx,
        *,
        module: dict[str, Any],
        symbols: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        tx.run(
            """
            MERGE (m:Module {id: $module.id})
            SET m.path = $module.file_path,
                m.language = $module.language,
                m.source = $module.source
            """,
            module=module,
        )

        if symbols:
            tx.run(
                """
                UNWIND $symbols AS sym
                MERGE (s:CodeSymbol {id: sym.id})
                SET s.name = sym.name,
                    s.qualified_name = sym.qualified_name,
                    s.kind = sym.kind,
                    s.file_path = sym.file_path,
                    s.line = sym.line,
                    s.parent = sym.parent,
                    s.signature = sym.signature,
                    s.return_type = sym.return_type,
                    s.description = sym.description,
                    s.is_async = sym.is_async,
                    s.external = sym.external
                """,
                symbols=symbols,
            )
            tx.run(
                """
                UNWIND $symbols AS sym
                MATCH (m:Module {id: $module_id})
                MATCH (s:CodeSymbol {id: sym.id})
                MERGE (m)-[:DEFINES]->(s)
                """,
                symbols=symbols,
                module_id=module["id"],
            )

        if edges:
            tx.run(
                """
                UNWIND $edges AS edge
                MATCH (src {id: edge.source})
                MATCH (dst {id: edge.target})
                MERGE (src)-[r:CODE_RELATION {relation: edge.relation, source_id: edge.source, target_id: edge.target}]->(dst)
                SET r.origin = edge.origin,
                    r.line = edge.line
                """,
                edges=edges,
            )
