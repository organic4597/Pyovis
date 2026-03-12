"""Integration tests for Neo4jGraphMirror against a live Neo4j instance.

Requires:
  - neo4j Python package  (pip install neo4j)
  - Docker container:
      docker run -d --name pyovis-neo4j-test \
        -e NEO4J_AUTH=neo4j/testpassword \
        -p 7687:7687 neo4j:5-community

Run:
  PYOVIS_NEO4J_URI=bolt://localhost:7687 \
  PYOVIS_NEO4J_USERNAME=neo4j \
  PYOVIS_NEO4J_PASSWORD=testpassword \
  pytest tests/test_neo4j_integration.py -v

Skipped automatically when env vars are absent or neo4j package is missing.
"""

from __future__ import annotations

import os

import pytest

NEO4J_URI = os.environ.get("PYOVIS_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("PYOVIS_NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.environ.get("PYOVIS_NEO4J_PASSWORD", "testpassword")
NEO4J_DB = os.environ.get("PYOVIS_NEO4J_DATABASE", "neo4j")

# ---------------------------------------------------------------------------
# Skip guard — skip the entire module when neo4j package is unavailable
# ---------------------------------------------------------------------------
neo4j_pkg = pytest.importorskip("neo4j", reason="neo4j package not installed")


def _mirror():
    from pyovis.memory.neo4j_backend import Neo4jGraphMirror

    return Neo4jGraphMirror(
        uri=NEO4J_URI,
        username=NEO4J_USER,
        password=NEO4J_PASS,
        database=NEO4J_DB,
    )


def _clear_db(mirror) -> None:
    with mirror._driver.session(database=NEO4J_DB) as session:
        session.run("MATCH (n) DETACH DELETE n")


@pytest.fixture()
def mirror():
    m = _mirror()
    _clear_db(m)
    yield m
    _clear_db(m)
    m.close()


# ---------------------------------------------------------------------------
# Helpers to read back data from Neo4j
# ---------------------------------------------------------------------------


def _count_nodes(mirror, label: str) -> int:
    with mirror._driver.session(database=NEO4J_DB) as session:
        result = session.run(f"MATCH (n:{label}) RETURN count(n) AS c")
        return result.single()["c"]


def _count_rels(mirror, rel_type: str) -> int:
    with mirror._driver.session(database=NEO4J_DB) as session:
        result = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c")
        return result.single()["c"]


def _fetch_entity(mirror, entity_id: str) -> dict | None:
    with mirror._driver.session(database=NEO4J_DB) as session:
        result = session.run("MATCH (n:Entity {id: $id}) RETURN n", id=entity_id)
        record = result.single()
        return dict(record["n"]) if record else None


def _fetch_symbol(mirror, symbol_id: str) -> dict | None:
    with mirror._driver.session(database=NEO4J_DB) as session:
        result = session.run("MATCH (n:CodeSymbol {id: $id}) RETURN n", id=symbol_id)
        record = result.single()
        return dict(record["n"]) if record else None


# ===========================================================================
# Tests: from_environment()
# ===========================================================================


class TestFromEnvironment:
    def test_returns_none_when_vars_missing(self, monkeypatch):
        from pyovis.memory.neo4j_backend import Neo4jGraphMirror

        monkeypatch.delenv("PYOVIS_NEO4J_URI", raising=False)
        monkeypatch.delenv("PYOVIS_NEO4J_USERNAME", raising=False)
        monkeypatch.delenv("PYOVIS_NEO4J_PASSWORD", raising=False)
        assert Neo4jGraphMirror.from_environment() is None

    def test_returns_instance_when_vars_set(self, monkeypatch):
        from pyovis.memory.neo4j_backend import Neo4jGraphMirror

        monkeypatch.setenv("PYOVIS_NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("PYOVIS_NEO4J_USERNAME", NEO4J_USER)
        monkeypatch.setenv("PYOVIS_NEO4J_PASSWORD", NEO4J_PASS)
        m = Neo4jGraphMirror.from_environment()
        assert m is not None
        assert m.is_enabled()
        m.close()

    def test_returns_none_on_bad_credentials(self, monkeypatch):
        from pyovis.memory.neo4j_backend import Neo4jGraphMirror

        monkeypatch.setenv("PYOVIS_NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("PYOVIS_NEO4J_USERNAME", "wrong")
        monkeypatch.setenv("PYOVIS_NEO4J_PASSWORD", "wrong")
        # from_environment() must swallow the exception and return None
        result = Neo4jGraphMirror.from_environment()
        assert result is None


# ===========================================================================
# Tests: mirror_triplet()
# ===========================================================================


class TestMirrorTriplet:
    def test_creates_entities_and_relation(self, mirror):
        mirror.mirror_triplet("PyovisAgent", "uses", "KnowledgeGraph", origin="test")
        assert _count_nodes(mirror, "Entity") == 2
        assert _count_rels(mirror, "KG_RELATION") == 1

    def test_entity_properties_stored(self, mirror):
        mirror.mirror_triplet("Planner", "delegates_to", "Hands", origin="arch")
        node = _fetch_entity(mirror, "Planner")
        assert node is not None
        assert node["name"] == "Planner"
        assert node["kind"] == "semantic_entity"

    def test_idempotent_upsert(self, mirror):
        for _ in range(3):
            mirror.mirror_triplet("A", "rel", "B", origin="x")
        assert _count_nodes(mirror, "Entity") == 2
        assert _count_rels(mirror, "KG_RELATION") == 1

    def test_multiple_predicates_between_same_nodes(self, mirror):
        mirror.mirror_triplet("X", "uses", "Y")
        mirror.mirror_triplet("X", "extends", "Y")
        assert _count_rels(mirror, "KG_RELATION") == 2


# ===========================================================================
# Tests: mirror_code_graph()
# ===========================================================================


class TestMirrorCodeGraph:
    _MODULE = {
        "id": "pyovis.memory.graph_builder",
        "file_path": "pyovis/memory/graph_builder.py",
        "language": "python",
        "source": "ingested",
    }
    _SYMBOLS = [
        {
            "id": "pyovis.memory.graph_builder::KnowledgeGraphBuilder",
            "name": "KnowledgeGraphBuilder",
            "qualified_name": "pyovis.memory.graph_builder.KnowledgeGraphBuilder",
            "kind": "class",
            "file_path": "pyovis/memory/graph_builder.py",
            "line": 50,
            "parent": "",
            "signature": "class KnowledgeGraphBuilder",
            "return_type": "",
            "description": "Main KG builder",
            "is_async": False,
            "external": False,
        },
        {
            "id": "pyovis.memory.graph_builder::KnowledgeGraphBuilder.visualize",
            "name": "visualize",
            "qualified_name": "pyovis.memory.graph_builder.KnowledgeGraphBuilder.visualize",
            "kind": "method",
            "file_path": "pyovis/memory/graph_builder.py",
            "line": 940,
            "parent": "KnowledgeGraphBuilder",
            "signature": "def visualize(self, output_path=None, height='800px', width='100%')",
            "return_type": "str",
            "description": "Render graph as HTML",
            "is_async": False,
            "external": False,
        },
    ]
    _EDGES = [
        {
            "source": "pyovis.memory.graph_builder::KnowledgeGraphBuilder",
            "target": "pyovis.memory.graph_builder::KnowledgeGraphBuilder.visualize",
            "relation": "defines",
            "origin": "pyovis.memory.graph_builder",
            "line": 940,
        }
    ]

    def test_module_node_created(self, mirror):
        mirror.mirror_code_graph(self._MODULE, self._SYMBOLS, self._EDGES)
        assert _count_nodes(mirror, "Module") == 1

    def test_symbol_nodes_created(self, mirror):
        mirror.mirror_code_graph(self._MODULE, self._SYMBOLS, self._EDGES)
        assert _count_nodes(mirror, "CodeSymbol") == 2

    def test_defines_edges_created(self, mirror):
        mirror.mirror_code_graph(self._MODULE, self._SYMBOLS, self._EDGES)
        assert _count_rels(mirror, "DEFINES") == 2

    def test_code_relation_edge_created(self, mirror):
        mirror.mirror_code_graph(self._MODULE, self._SYMBOLS, self._EDGES)
        assert _count_rels(mirror, "CODE_RELATION") == 1

    def test_symbol_properties_stored(self, mirror):
        mirror.mirror_code_graph(self._MODULE, self._SYMBOLS, self._EDGES)
        sym = _fetch_symbol(
            mirror, "pyovis.memory.graph_builder::KnowledgeGraphBuilder"
        )
        assert sym is not None
        assert sym["name"] == "KnowledgeGraphBuilder"
        assert sym["kind"] == "class"

    def test_idempotent_upsert(self, mirror):
        for _ in range(3):
            mirror.mirror_code_graph(self._MODULE, self._SYMBOLS, self._EDGES)
        assert _count_nodes(mirror, "Module") == 1
        assert _count_nodes(mirror, "CodeSymbol") == 2

    def test_empty_symbols_and_edges_ok(self, mirror):
        mirror.mirror_code_graph(self._MODULE, [], [])
        assert _count_nodes(mirror, "Module") == 1
        assert _count_nodes(mirror, "CodeSymbol") == 0


# ===========================================================================
# Tests: schema constraints (idempotent _ensure_schema)
# ===========================================================================


class TestSchema:
    def test_reconnect_reinits_schema_without_error(self):
        m1 = _mirror()
        m1.close()
        m2 = _mirror()
        assert m2.is_enabled()
        m2.close()

    def test_close_sets_driver_to_none(self):
        m = _mirror()
        m.close()
        assert not m.is_enabled()
