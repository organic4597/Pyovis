from __future__ import annotations

import json
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

pytest = importlib.import_module("pytest")

from pyovis.memory.graph_builder import chunk_text, KnowledgeGraphBuilder


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_short_text_single_chunk(self):
        result = chunk_text("Hello world.", max_chars=1500)
        assert len(result) == 1
        assert result[0]["chunk_index"] == 0
        assert result[0]["text"] == "Hello world."

    def test_long_text_splits(self):
        text = ". ".join(f"Sentence number {i}" for i in range(100))
        result = chunk_text(text, max_chars=200, overlap=50)
        assert len(result) > 1
        for i, chunk in enumerate(result):
            assert chunk["chunk_index"] == i

    def test_overlap_preserves_content(self):
        sentences = [f"Sent{i} is here." for i in range(20)]
        text = " ".join(sentences)
        result = chunk_text(text, max_chars=100, overlap=30)
        assert len(result) >= 2
        full_text = " ".join(c["text"] for c in result)
        for s in sentences:
            assert s.replace(".", "") in full_text.replace(".", "")

    def test_empty_text(self):
        result = chunk_text("", max_chars=100)
        assert len(result) == 1
        assert result[0]["text"] == ""


# ---------------------------------------------------------------------------
# _parse_json_array
# ---------------------------------------------------------------------------


class TestParseJsonArray:
    def test_valid_array(self):
        text = '[{"a": 1}, {"b": 2}]'
        assert KnowledgeGraphBuilder._parse_json_array(text) == [{"a": 1}, {"b": 2}]

    def test_strips_think_tags(self):
        text = '<think>some reasoning</think>[{"x": 1}]'
        assert KnowledgeGraphBuilder._parse_json_array(text) == [{"x": 1}]

    def test_no_json_returns_empty(self):
        assert KnowledgeGraphBuilder._parse_json_array("no json here") == []

    def test_invalid_json_returns_empty(self):
        assert KnowledgeGraphBuilder._parse_json_array("[{bad json}]") == []

    def test_json_embedded_in_text(self):
        text = 'Here is the result:\n[{"node": "test"}]\nDone.'
        assert KnowledgeGraphBuilder._parse_json_array(text) == [{"node": "test"}]


# ---------------------------------------------------------------------------
# _keyword_extract
# ---------------------------------------------------------------------------


class TestKeywordExtract:
    def test_filters_stop_words(self):
        result = KnowledgeGraphBuilder._keyword_extract("the quick brown fox")
        assert "the" not in result
        assert "quick" in result
        assert "brown" in result
        assert "fox" in result

    def test_returns_lowercase(self):
        result = KnowledgeGraphBuilder._keyword_extract("Python Machine Learning")
        assert all(t == t.lower() for t in result)

    def test_handles_korean(self):
        result = KnowledgeGraphBuilder._keyword_extract("인공지능과 머신러닝")
        assert "인공지능과" in result or "인공지능" in result

    def test_filters_short_tokens(self):
        result = KnowledgeGraphBuilder._keyword_extract("I am a cat")
        assert "i" not in result
        assert "a" not in result
        assert "am" not in result
        assert "cat" in result


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def builder(tmp_path: Path) -> KnowledgeGraphBuilder:
    return KnowledgeGraphBuilder(
        persist_path=tmp_path / "graph.json",
        llm_base="http://test:8001",
        model="test",
    )


@pytest.fixture
def populated_builder(tmp_path: Path) -> KnowledgeGraphBuilder:
    b = KnowledgeGraphBuilder(
        persist_path=tmp_path / "graph.json",
        llm_base="http://test:8001",
        model="test",
    )
    b._graph = {
        "nodes": {
            "python": {"category": "technology", "importance": 5, "sources": ["doc1"]},
            "machine learning": {
                "category": "concept",
                "importance": 4,
                "sources": ["doc1"],
            },
            "neural network": {
                "category": "concept",
                "importance": 3,
                "sources": ["doc1"],
            },
            "tensorflow": {
                "category": "technology",
                "importance": 3,
                "sources": ["doc2"],
            },
            "data science": {
                "category": "concept",
                "importance": 2,
                "sources": ["doc2"],
            },
        },
        "edges": [
            {
                "source": "python",
                "target": "machine learning",
                "relation": "used for",
                "origin": "doc1",
            },
            {
                "source": "machine learning",
                "target": "neural network",
                "relation": "includes",
                "origin": "doc1",
            },
            {
                "source": "tensorflow",
                "target": "neural network",
                "relation": "implements",
                "origin": "doc2",
            },
            {
                "source": "python",
                "target": "tensorflow",
                "relation": "runs",
                "origin": "doc2",
            },
            {
                "source": "data science",
                "target": "python",
                "relation": "uses",
                "origin": "doc2",
            },
        ],
        "communities": {},
        "community_summaries": {},
    }
    return b


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_empty_graph(self, builder: KnowledgeGraphBuilder):
        stats = builder.get_stats()
        assert stats["total_nodes"] == 0
        assert stats["total_edges"] == 0
        assert stats["total_communities"] == 0
        assert stats["has_summaries"] is False

    def test_populated_graph(self, populated_builder: KnowledgeGraphBuilder):
        stats = populated_builder.get_stats()
        assert stats["total_nodes"] == 5
        assert stats["total_edges"] == 5
        assert stats["total_code_symbols"] == 0


# ---------------------------------------------------------------------------
# query_neighbors
# ---------------------------------------------------------------------------


class TestQueryNeighbors:
    def test_missing_entity_returns_center_only(self, builder: KnowledgeGraphBuilder):
        result = builder.query_neighbors("nonexistent")
        assert result["center"] == "nonexistent"
        assert "nonexistent" in result["nodes"]
        assert result["edges"] == []

    def test_entity_with_edges(self, populated_builder: KnowledgeGraphBuilder):
        result = populated_builder.query_neighbors("python", depth=1)
        assert result["center"] == "python"
        assert len(result["edges"]) > 0
        neighbor_names = set(result["nodes"].keys())
        assert "machine learning" in neighbor_names

    def test_depth_2_expands(self, populated_builder: KnowledgeGraphBuilder):
        depth1 = populated_builder.query_neighbors("python", depth=1)
        depth2 = populated_builder.query_neighbors("python", depth=2)
        assert len(depth2["edges"]) >= len(depth1["edges"])


# ---------------------------------------------------------------------------
# to_networkx
# ---------------------------------------------------------------------------


class TestToNetworkx:
    def test_empty_graph(self, builder: KnowledgeGraphBuilder):
        G = builder.to_networkx()
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_populated_graph(self, populated_builder: KnowledgeGraphBuilder):
        G = populated_builder.to_networkx()
        assert G.number_of_nodes() == 5
        assert G.number_of_edges() == 5
        assert G.has_node("python")
        assert G.has_edge("python", "machine learning")


# ---------------------------------------------------------------------------
# detect_communities
# ---------------------------------------------------------------------------


class TestDetectCommunities:
    def test_empty_graph(self, builder: KnowledgeGraphBuilder):
        result = builder.detect_communities()
        assert result == {}

    def test_populated_graph(self, populated_builder: KnowledgeGraphBuilder):
        result = populated_builder.detect_communities()
        assert len(result) >= 1
        all_members = []
        for members in result.values():
            all_members.extend(members)
        assert "python" in all_members

    def test_communities_persisted(self, populated_builder: KnowledgeGraphBuilder):
        populated_builder.detect_communities()
        assert "communities" in populated_builder._graph
        assert len(populated_builder._graph["communities"]) >= 1


# ---------------------------------------------------------------------------
# Async: extract_triplets
# ---------------------------------------------------------------------------


class TestExtractTriplets:
    @pytest.mark.asyncio
    async def test_valid_response(self, builder: KnowledgeGraphBuilder):
        mock_response = '[{"node_1": "python", "node_2": "ml", "edge": "used for"}]'
        with patch.object(
            builder, "_call_llm", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await builder.extract_triplets("Python is used for ML")
        assert len(result) == 1
        assert result[0]["node_1"] == "python"
        assert result[0]["edge"] == "used for"

    @pytest.mark.asyncio
    async def test_filters_invalid_entries(self, builder: KnowledgeGraphBuilder):
        mock_response = (
            '[{"node_1": "a", "node_2": "b", "edge": "c"}, {"bad": "entry"}]'
        )
        with patch.object(
            builder, "_call_llm", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await builder.extract_triplets("test")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Async: extract_concepts
# ---------------------------------------------------------------------------


class TestExtractConcepts:
    @pytest.mark.asyncio
    async def test_valid_response(self, builder: KnowledgeGraphBuilder):
        mock_response = (
            '[{"entity": "python", "category": "technology", "importance": 5}]'
        )
        with patch.object(
            builder, "_call_llm", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await builder.extract_concepts("Python programming")
        assert len(result) == 1
        assert result[0]["entity"] == "python"

    @pytest.mark.asyncio
    async def test_filters_invalid(self, builder: KnowledgeGraphBuilder):
        mock_response = '[{"entity": "valid"}, {"no_entity": true}]'
        with patch.object(
            builder, "_call_llm", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await builder.extract_concepts("test")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Async: add_text
# ---------------------------------------------------------------------------


class TestAddText:
    @pytest.mark.asyncio
    async def test_adds_nodes_and_edges(self, builder: KnowledgeGraphBuilder):
        with (
            patch.object(builder, "extract_triplets", new_callable=AsyncMock) as mock_t,
            patch.object(builder, "extract_concepts", new_callable=AsyncMock) as mock_c,
        ):
            mock_t.return_value = [{"node_1": "a", "node_2": "b", "edge": "relates"}]
            mock_c.return_value = [
                {"entity": "a", "category": "concept", "importance": 3}
            ]
            result = await builder.add_text("test text", source="src1")

        assert result["added_nodes"] >= 1
        assert result["added_edges"] == 1
        assert "a" in builder._graph["nodes"]
        assert len(builder._graph["edges"]) == 1

    @pytest.mark.asyncio
    async def test_source_tracked(self, builder: KnowledgeGraphBuilder):
        with (
            patch.object(
                builder, "extract_triplets", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(builder, "extract_concepts", new_callable=AsyncMock) as mock_c,
        ):
            mock_c.return_value = [
                {"entity": "x", "category": "other", "importance": 1}
            ]
            await builder.add_text("text", source="mysource")

        assert "mysource" in builder._graph["nodes"]["x"]["sources"]


# ---------------------------------------------------------------------------
# Async: add_document
# ---------------------------------------------------------------------------


class TestAddDocument:
    @pytest.mark.asyncio
    async def test_chunks_and_aggregates(self, builder: KnowledgeGraphBuilder):
        with patch.object(builder, "add_text", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = {"added_nodes": 2, "added_edges": 1}
            long_text = ". ".join(f"This is sentence {i}" for i in range(100))
            result = await builder.add_document(long_text, source="doc", max_chars=200)

        assert result["chunks_processed"] > 1
        assert result["added_nodes"] == 2 * mock_add.call_count
        assert result["added_edges"] == 1 * mock_add.call_count


class TestAddTriplet:
    @pytest.mark.asyncio
    async def test_add_triplet_dedupes_edges(self, builder: KnowledgeGraphBuilder):
        first = await builder.add_triplet("Task_A", "verdict", "PASS", origin="judge")
        second = await builder.add_triplet("Task_A", "verdict", "PASS", origin="judge")

        assert first["added_edges"] == 1
        assert second["added_edges"] == 0
        assert len(builder._graph["edges"]) == 1


class TestCodeSymbolGraph:
    @pytest.mark.asyncio
    async def test_add_code_symbols_persists_symbols_and_edges(
        self, builder: KnowledgeGraphBuilder
    ):
        code = """
class Service:
    def run(self):
        pass

async def fetch_data(url: str) -> str:
    return url

MAX_RETRIES = 3
"""
        result = await builder.add_code_symbols(
            code, file_path="service.py", source="task:test"
        )

        assert result["modules"] == 1
        assert result["symbols"] >= 4
        assert result["edges"] >= 4
        assert "module:service.py" in builder._graph["code_modules"]
        assert any(
            symbol["name"] == "Service"
            for symbol in builder._graph["code_symbols"].values()
        )

    @pytest.mark.asyncio
    async def test_add_code_symbols_uses_neo4j_mirror_when_present(
        self, tmp_path: Path
    ):
        mirror = Mock()
        builder = KnowledgeGraphBuilder(
            persist_path=tmp_path / "graph.json",
            llm_base="http://test:8001",
            model="test",
            neo4j_mirror=mirror,
        )

        await builder.add_code_symbols(
            "def run():\n    return 1\n", file_path="job.py", source="task:test"
        )

        mirror.mirror_code_graph.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_triplet_uses_neo4j_mirror_when_present(self, tmp_path: Path):
        mirror = Mock()
        builder = KnowledgeGraphBuilder(
            persist_path=tmp_path / "graph.json",
            llm_base="http://test:8001",
            model="test",
            neo4j_mirror=mirror,
        )

        await builder.add_triplet("task_1", "verdict", "PASS", origin="judge")

        mirror.mirror_triplet.assert_called_once_with(
            "task_1", "verdict", "pass", origin="judge"
        )

    @pytest.mark.asyncio
    async def test_query_graph_rag_includes_code_results(
        self, builder: KnowledgeGraphBuilder
    ):
        await builder.add_code_symbols(
            "def fetch_user():\n    return {}\n", file_path="api.py", source="task:test"
        )

        result = await builder.query_graph_rag("fetch_user", use_llm_extraction=False)

        assert "code_results" in result
        assert result["code_results"]["nodes"]
        assert "Relevant code symbols" in result["context_text"]

    def test_to_networkx_includes_code_graph(self, builder: KnowledgeGraphBuilder):
        builder._graph["code_modules"]["module:api.py"] = {
            "id": "module:api.py",
            "file_path": "api.py",
            "language": "python",
            "source": "task:test",
        }
        builder._graph["code_symbols"]["symbol:api.py:fetch_user"] = {
            "id": "symbol:api.py:fetch_user",
            "name": "fetch_user",
            "qualified_name": "api.py:fetch_user",
            "kind": "function",
            "file_path": "api.py",
            "line": 0,
            "parent": None,
            "signature": "()",
            "return_type": "",
            "description": "",
            "is_async": False,
            "external": False,
            "source": "task:test",
        }
        builder._graph["code_symbol_edges"].append(
            {
                "source": "module:api.py",
                "target": "symbol:api.py:fetch_user",
                "relation": "defines",
                "origin": "task:test",
                "line": 0,
            }
        )

        graph = builder.to_networkx()

        assert graph.has_node("module:api.py")
        assert graph.has_node("symbol:api.py:fetch_user")
        assert graph.has_edge("module:api.py", "symbol:api.py:fetch_user")


# ---------------------------------------------------------------------------
# Async: query_graph_rag
# ---------------------------------------------------------------------------


class TestQueryGraphRag:
    @pytest.mark.asyncio
    async def test_returns_structured_context(
        self, populated_builder: KnowledgeGraphBuilder
    ):
        result = await populated_builder.query_graph_rag(
            "Tell me about python programming",
            use_llm_extraction=False,
        )
        assert "entities" in result
        assert "relations" in result
        assert "community_summaries" in result
        assert "context_text" in result
        assert any(e["name"] == "python" for e in result["entities"])

    @pytest.mark.asyncio
    async def test_empty_graph_returns_empty(self, builder: KnowledgeGraphBuilder):
        result = await builder.query_graph_rag("anything", use_llm_extraction=False)
        assert result["entities"] == []
        assert result["relations"] == []
        assert result["context_text"] == ""

    @pytest.mark.asyncio
    async def test_with_community_summaries(
        self, populated_builder: KnowledgeGraphBuilder
    ):
        populated_builder.detect_communities()
        populated_builder._graph["community_summaries"] = {"0": "A tech community."}
        result = await populated_builder.query_graph_rag(
            "python",
            use_llm_extraction=False,
        )
        assert isinstance(result["community_summaries"], list)


# ---------------------------------------------------------------------------
# Async: hybrid_search
# ---------------------------------------------------------------------------


class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_combines_vector_and_graph(
        self, populated_builder: KnowledgeGraphBuilder
    ):
        vector_results = [
            {"text": "Python is great", "distance": 0.1, "index": 0},
            {"text": "ML with Python", "distance": 0.2, "index": 1},
        ]
        result = await populated_builder.hybrid_search(
            "python",
            vector_results=vector_results,
        )
        assert "vector_context" in result
        assert "graph_context" in result
        assert "merged_context_text" in result
        assert len(result["vector_context"]) == 2
        assert "Vector search results" in result["merged_context_text"]

    @pytest.mark.asyncio
    async def test_no_vector_results(self, populated_builder: KnowledgeGraphBuilder):
        result = await populated_builder.hybrid_search("python")
        assert result["vector_context"] == []
        assert "Knowledge graph context" in result["merged_context_text"]


# ---------------------------------------------------------------------------
# Async: summarize_communities
# ---------------------------------------------------------------------------


class TestSummarizeCommunities:
    @pytest.mark.asyncio
    async def test_no_communities_returns_empty(self, builder: KnowledgeGraphBuilder):
        result = await builder.summarize_communities()
        assert result == {}

    @pytest.mark.asyncio
    async def test_generates_summaries(self, populated_builder: KnowledgeGraphBuilder):
        populated_builder.detect_communities()
        with patch.object(
            populated_builder,
            "_call_llm",
            new_callable=AsyncMock,
            return_value="This community is about technology.",
        ):
            result = await populated_builder.summarize_communities()
        assert len(result) >= 1
        assert all(isinstance(v, str) for v in result.values())
        assert "community_summaries" in populated_builder._graph

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(
        self, populated_builder: KnowledgeGraphBuilder
    ):
        populated_builder.detect_communities()
        with patch.object(
            populated_builder,
            "_call_llm",
            new_callable=AsyncMock,
            side_effect=Exception("LLM down"),
        ):
            result = await populated_builder.summarize_communities()
        assert len(result) >= 1
        assert all("Community with entities" in v for v in result.values())


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load(self, tmp_path: Path):
        path = tmp_path / "kg" / "graph.json"
        b1 = KnowledgeGraphBuilder(
            persist_path=path, llm_base="http://test:8001", model="test"
        )
        b1._graph["nodes"]["test"] = {
            "category": "other",
            "importance": 1,
            "sources": [],
        }
        b1._save()

        b2 = KnowledgeGraphBuilder(
            persist_path=path, llm_base="http://test:8001", model="test"
        )
        assert "test" in b2._graph["nodes"]

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "graph.json"
        path.write_text("not json", encoding="utf-8")
        b = KnowledgeGraphBuilder(
            persist_path=path, llm_base="http://test:8001", model="test"
        )
        assert b._graph["nodes"] == {}

    def test_communities_persisted_after_detection(
        self, tmp_path: Path, populated_builder: KnowledgeGraphBuilder
    ):
        populated_builder._persist_path = tmp_path / "g.json"
        populated_builder.detect_communities()

        loaded = json.loads((tmp_path / "g.json").read_text(encoding="utf-8"))
        assert "communities" in loaded
        assert len(loaded["communities"]) >= 1
