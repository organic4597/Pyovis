from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GRAPH_PERSIST_PATH = Path(
    os.environ.get("PYOVIS_MEMORY_DIR", "/pyovis_memory")
) / "kg" / "graph.json"

_EXTRACT_PROMPT = """\
You are a knowledge graph extractor. Given a text, extract all concept pairs and their relationships.

Return a JSON array where each element is:
{{"node_1": "<concept A>", "node_2": "<concept B>", "edge": "<relationship>"}}

Rules:
- node_1 and node_2 must be short noun phrases (1-4 words), lowercase
- edge must be a short verb phrase describing the relationship
- extract as many meaningful relationships as possible
- do NOT add explanation outside the JSON array

Text:
{text}
"""

_CONCEPTS_PROMPT = """\
You are a knowledge graph entity extractor. Given a text, list all important concepts.

Return a JSON array where each element is:
{{"entity": "<concept>", "category": "<type>", "importance": <1-5>}}

Rules:
- entity must be a short noun phrase (1-4 words), lowercase
- category: one of [person, place, organization, concept, technology, event, other]
- importance: 1 (peripheral) to 5 (central)
- do NOT add explanation outside the JSON array

Text:
{text}
"""

_COMMUNITY_SUMMARY_PROMPT = """\
You are a knowledge graph analyst. Given a community of related entities and their relationships, \
write a concise 2-3 sentence summary describing what this community represents and its key themes.

Entities in this community:
{entities}

Relationships:
{relations}

Return ONLY the summary text, no JSON, no formatting.
"""

_ENTITY_EXTRACT_PROMPT = """\
Extract the key entities (nouns, named entities) from this query. \
Return a JSON array of lowercase strings.

Query: {query}
"""

# ---------------------------------------------------------------------------
# Sentence-boundary-aware text chunking
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+")


def chunk_text(
    text: str,
    max_chars: int = 1500,
    overlap: int = 200,
) -> list[dict[str, Any]]:
    """Split *text* into overlapping chunks that respect sentence boundaries.

    Returns a list of dicts::

        [{"chunk_index": 0, "text": "...", "char_start": 0, "char_end": 1480}, ...]
    """
    if len(text) <= max_chars:
        return [{"chunk_index": 0, "text": text, "char_start": 0, "char_end": len(text)}]

    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0
    char_cursor = 0

    for sentence in sentences:
        sent_len = len(sentence)
        if current_len + sent_len > max_chars and current:
            chunk_text_str = " ".join(current)
            chunks.append({
                "chunk_index": len(chunks),
                "text": chunk_text_str,
                "char_start": char_cursor,
                "char_end": char_cursor + len(chunk_text_str),
            })
            # overlap: keep trailing sentences whose combined length <= overlap
            overlap_parts: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > overlap:
                    break
                overlap_parts.insert(0, s)
                overlap_len += len(s)
            char_cursor += len(chunk_text_str) - overlap_len
            current = overlap_parts
            current_len = overlap_len

        current.append(sentence)
        current_len += sent_len

    if current:
        chunk_text_str = " ".join(current)
        chunks.append({
            "chunk_index": len(chunks),
            "text": chunk_text_str,
            "char_start": char_cursor,
            "char_end": char_cursor + len(chunk_text_str),
        })

    return chunks


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class KnowledgeGraphBuilder:
    """Knowledge graph builder with Graph RAG capabilities.

    Implements the rahulnyk/knowledge_graph methodology:
    1. LLM-driven triplet & concept extraction
    2. Text chunking for long documents
    3. Community detection (greedy modularity)
    4. LLM-generated community summaries
    5. Graph RAG query (entity + relation + community context)
    6. Hybrid search (vector + graph)
    7. Interactive HTML visualization
    """

    def __init__(
        self,
        persist_path: Path = _GRAPH_PERSIST_PATH,
        llm_base: str | None = None,
        model: str | None = None,
    ) -> None:
        self._persist_path = persist_path
        self._llm_base = llm_base or os.environ.get(
            "PYOVIS_LLM_BASE_URL", "http://localhost:8001"
        )
        self._model = model or os.environ.get("PYOVIS_BRAIN_MODEL", "brain")
        self._graph: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if self._persist_path.exists():
            try:
                data = json.loads(self._persist_path.read_text(encoding="utf-8"))
                logger.info(
                    "graph_builder: loaded graph — %d nodes, %d edges",
                    len(data.get("nodes", {})),
                    len(data.get("edges", [])),
                )
                return data
            except Exception as exc:
                logger.warning("graph_builder: failed to load graph: %s", exc)
        return {"nodes": {}, "edges": [], "communities": {}, "community_summaries": {}}

    def _save(self) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._graph, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("graph_builder: failed to save graph: %s", exc)

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 2048,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._llm_base}/v1/chat/completions", json=payload
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _parse_json_array(text: str) -> list:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return []

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    async def extract_triplets(self, text: str) -> list[dict]:
        raw = await self._call_llm(_EXTRACT_PROMPT.format(text=text))
        triplets = self._parse_json_array(raw)
        valid = [
            t for t in triplets
            if isinstance(t, dict) and "node_1" in t and "node_2" in t and "edge" in t
        ]
        return valid

    async def extract_concepts(self, text: str) -> list[dict]:
        raw = await self._call_llm(_CONCEPTS_PROMPT.format(text=text))
        concepts = self._parse_json_array(raw)
        valid = [
            c for c in concepts
            if isinstance(c, dict) and "entity" in c
        ]
        return valid

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def add_text(self, text: str, source: str = "") -> dict:
        triplets = await self.extract_triplets(text)
        concepts = await self.extract_concepts(text)

        added_nodes: list[str] = []
        added_edges: list[dict] = []

        for concept in concepts:
            entity = concept["entity"].lower().strip()
            if entity not in self._graph["nodes"]:
                self._graph["nodes"][entity] = {
                    "category": concept.get("category", "other"),
                    "importance": concept.get("importance", 1),
                    "sources": [],
                }
                added_nodes.append(entity)
            if source and source not in self._graph["nodes"][entity]["sources"]:
                self._graph["nodes"][entity]["sources"].append(source)

        for triplet in triplets:
            n1 = triplet["node_1"].lower().strip()
            n2 = triplet["node_2"].lower().strip()
            edge_label = triplet["edge"].lower().strip()

            for node in (n1, n2):
                if node not in self._graph["nodes"]:
                    self._graph["nodes"][node] = {
                        "category": "other",
                        "importance": 1,
                        "sources": [source] if source else [],
                    }
                    added_nodes.append(node)

            edge = {"source": n1, "target": n2, "relation": edge_label, "origin": source}
            self._graph["edges"].append(edge)
            added_edges.append(edge)

        self._save()
        logger.info(
            "graph_builder: added %d nodes, %d edges from source=%r",
            len(added_nodes),
            len(added_edges),
            source,
        )
        return {"added_nodes": len(added_nodes), "added_edges": len(added_edges)}

    async def add_document(
        self,
        text: str,
        source: str = "",
        max_chars: int = 1500,
        overlap: int = 200,
    ) -> dict:
        """Ingest a long document by chunking and extracting triplets per chunk.

        This is the rahulnyk-style batch ingestion pipeline:
        text -> chunk -> extract triplets/concepts per chunk -> merge into graph.
        """
        chunks = chunk_text(text, max_chars=max_chars, overlap=overlap)
        total_nodes = 0
        total_edges = 0

        for chunk in chunks:
            chunk_source = f"{source}#chunk{chunk['chunk_index']}" if source else f"chunk{chunk['chunk_index']}"
            result = await self.add_text(chunk["text"], source=chunk_source)
            total_nodes += result["added_nodes"]
            total_edges += result["added_edges"]

        logger.info(
            "graph_builder: document ingested — %d chunks, %d new nodes, %d new edges",
            len(chunks),
            total_nodes,
            total_edges,
        )
        return {
            "chunks_processed": len(chunks),
            "added_nodes": total_nodes,
            "added_edges": total_edges,
        }

    # ------------------------------------------------------------------
    # Query — neighbors
    # ------------------------------------------------------------------

    def query_neighbors(self, entity: str, depth: int = 1) -> dict:
        entity = entity.lower().strip()
        visited: set[str] = set()
        frontier = {entity}
        result_edges: list[dict] = []

        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in self._graph["edges"]:
                if edge["source"] in frontier and edge["target"] not in visited:
                    result_edges.append(edge)
                    next_frontier.add(edge["target"])
                elif edge["target"] in frontier and edge["source"] not in visited:
                    result_edges.append(edge)
                    next_frontier.add(edge["source"])
            visited.update(frontier)
            frontier = next_frontier - visited

        all_nodes = {e["source"] for e in result_edges} | {e["target"] for e in result_edges}
        all_nodes.add(entity)

        return {
            "center": entity,
            "nodes": {
                n: self._graph["nodes"].get(n, {"category": "other", "importance": 1})
                for n in all_nodes
            },
            "edges": result_edges,
        }

    # ------------------------------------------------------------------
    # Community Detection (greedy modularity)
    # ------------------------------------------------------------------

    def detect_communities(self) -> dict[str, list[str]]:
        """Run greedy modularity community detection on the graph.

        Stores results in ``self._graph["communities"]`` and persists to disk.
        Returns mapping of community_id -> list of member entity names.
        """
        import networkx as nx
        from networkx.algorithms.community import greedy_modularity_communities

        G = self.to_networkx()

        if G.number_of_nodes() == 0:
            self._graph["communities"] = {}
            self._save()
            return {}

        # greedy_modularity_communities needs an undirected graph
        U = G.to_undirected()
        communities_iter = greedy_modularity_communities(U)

        communities: dict[str, list[str]] = {}
        for idx, community_set in enumerate(communities_iter):
            cid = str(idx)
            communities[cid] = sorted(community_set)

        self._graph["communities"] = communities
        self._save()

        logger.info(
            "graph_builder: detected %d communities across %d nodes",
            len(communities),
            G.number_of_nodes(),
        )
        return communities

    def _get_entity_community(self, entity: str) -> str | None:
        """Return the community_id an entity belongs to, or None."""
        entity = entity.lower().strip()
        for cid, members in self._graph.get("communities", {}).items():
            if entity in members:
                return cid
        return None

    # ------------------------------------------------------------------
    # Community Summaries (LLM-generated)
    # ------------------------------------------------------------------

    async def summarize_communities(self) -> dict[str, str]:
        """Generate LLM summaries for each detected community.

        Requires ``detect_communities()`` to have been called first.
        Stores results in ``self._graph["community_summaries"]``.
        """
        communities = self._graph.get("communities", {})
        if not communities:
            logger.warning("graph_builder: no communities detected — run detect_communities() first")
            return {}

        summaries: dict[str, str] = {}

        for cid, members in communities.items():
            # Collect edges within this community
            member_set = set(members)
            internal_edges: list[str] = []
            for edge in self._graph["edges"]:
                if edge["source"] in member_set and edge["target"] in member_set:
                    internal_edges.append(
                        f"{edge['source']} --[{edge['relation']}]--> {edge['target']}"
                    )

            if not internal_edges:
                internal_edges = ["(no direct relationships between members)"]

            entities_str = ", ".join(members)
            relations_str = "\n".join(internal_edges[:50])  # cap to avoid prompt overflow

            prompt = _COMMUNITY_SUMMARY_PROMPT.format(
                entities=entities_str,
                relations=relations_str,
            )

            try:
                raw = await self._call_llm(prompt)
                summary = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                summaries[cid] = summary
            except Exception as exc:
                logger.warning("graph_builder: failed to summarize community %s: %s", cid, exc)
                summaries[cid] = f"Community with entities: {entities_str}"

        self._graph["community_summaries"] = summaries
        self._save()

        logger.info("graph_builder: generated %d community summaries", len(summaries))
        return summaries

    # ------------------------------------------------------------------
    # Graph RAG Query
    # ------------------------------------------------------------------

    async def query_graph_rag(
        self,
        query: str,
        depth: int = 2,
        use_llm_extraction: bool = True,
    ) -> dict[str, Any]:
        """Query the knowledge graph for context relevant to *query*.

        Pipeline:
        1. Extract key entities from query (LLM or keyword fallback)
        2. For each entity in graph, get N-hop neighbors
        3. Find community memberships and retrieve community summaries
        4. Return structured context suitable for LLM prompt injection
        """
        # Step 1: extract key entities from the query
        if use_llm_extraction:
            try:
                raw = await self._call_llm(_ENTITY_EXTRACT_PROMPT.format(query=query))
                query_entities = self._parse_json_array(raw)
                query_entities = [
                    e.lower().strip() for e in query_entities if isinstance(e, str)
                ]
            except Exception:
                query_entities = self._keyword_extract(query)
        else:
            query_entities = self._keyword_extract(query)

        if not query_entities:
            query_entities = self._keyword_extract(query)

        # Step 2: find matching entities in graph + N-hop neighbors
        matched_entities: list[dict] = []
        all_relations: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()
        community_ids: set[str] = set()

        graph_nodes = self._graph.get("nodes", {})

        for qe in query_entities:
            # Exact match
            if qe in graph_nodes:
                node_data = graph_nodes[qe]
                matched_entities.append({
                    "name": qe,
                    "category": node_data.get("category", "other"),
                    "importance": node_data.get("importance", 1),
                })
                neighbors = self.query_neighbors(qe, depth=depth)
                for edge in neighbors["edges"]:
                    edge_key = (edge["source"], edge["target"], edge["relation"])
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        all_relations.append(edge)
                cid = self._get_entity_community(qe)
                if cid is not None:
                    community_ids.add(cid)
            else:
                # Partial match (substring)
                for node_name in graph_nodes:
                    if qe in node_name or node_name in qe:
                        node_data = graph_nodes[node_name]
                        matched_entities.append({
                            "name": node_name,
                            "category": node_data.get("category", "other"),
                            "importance": node_data.get("importance", 1),
                        })
                        neighbors = self.query_neighbors(node_name, depth=depth)
                        for edge in neighbors["edges"]:
                            edge_key = (edge["source"], edge["target"], edge["relation"])
                            if edge_key not in seen_edges:
                                seen_edges.add(edge_key)
                                all_relations.append(edge)
                        cid = self._get_entity_community(node_name)
                        if cid is not None:
                            community_ids.add(cid)

        # Step 3: gather community summaries
        summaries_store = self._graph.get("community_summaries", {})
        relevant_summaries = [
            summaries_store[cid]
            for cid in community_ids
            if cid in summaries_store
        ]

        # Step 4: build context_text for LLM prompt injection
        context_parts: list[str] = []

        if matched_entities:
            entity_lines = [
                f"- {e['name']} ({e['category']}, importance={e['importance']})"
                for e in matched_entities
            ]
            context_parts.append("Related entities:\n" + "\n".join(entity_lines))

        if all_relations:
            rel_lines = [
                f"- {r['source']} --[{r['relation']}]--> {r['target']}"
                for r in all_relations[:30]  # cap for prompt length
            ]
            context_parts.append("Relationships:\n" + "\n".join(rel_lines))

        if relevant_summaries:
            summary_lines = [f"- {s}" for s in relevant_summaries]
            context_parts.append("Community context:\n" + "\n".join(summary_lines))

        context_text = "\n\n".join(context_parts) if context_parts else ""

        return {
            "entities": matched_entities,
            "relations": all_relations,
            "community_summaries": relevant_summaries,
            "context_text": context_text,
        }

    @staticmethod
    def _keyword_extract(query: str) -> list[str]:
        """Simple keyword extraction fallback (no LLM needed)."""
        stop_words = {
            "the", "a", "an", "am", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above",
            "below", "between", "about", "against", "and", "but", "or",
            "nor", "not", "so", "yet", "both", "either", "neither",
            "each", "every", "all", "any", "few", "more", "most",
            "other", "some", "such", "no", "only", "own", "same",
            "than", "too", "very", "just", "because", "if", "when",
            "where", "how", "what", "which", "who", "whom", "this",
            "that", "these", "those", "i", "me", "my", "we", "our",
            "you", "your", "he", "him", "his", "she", "her", "it",
            "its", "they", "them", "their",
        }
        tokens = re.findall(r"[a-z\uac00-\ud7a3]+", query.lower())
        return [t for t in tokens if t not in stop_words and len(t) > 1]

    # ------------------------------------------------------------------
    # Hybrid Search (Vector + Graph)
    # ------------------------------------------------------------------

    async def hybrid_search(
        self,
        query: str,
        vector_results: list[dict] | None = None,
        depth: int = 2,
    ) -> dict[str, Any]:
        """Combine vector search results with graph RAG context.

        Args:
            query: user query string
            vector_results: optional list of dicts from KGStore.search()
                            each with keys: text, distance, index
            depth: N-hop depth for graph traversal

        Returns unified context dict.
        """
        graph_context = await self.query_graph_rag(query, depth=depth)
        vector_context = vector_results or []

        parts: list[str] = []

        if vector_context:
            vec_lines = []
            for vr in vector_context[:10]:
                text_preview = vr.get("text", "")[:200]
                dist = vr.get("distance", 0)
                vec_lines.append(f"- (dist={dist:.3f}) {text_preview}")
            parts.append("Vector search results:\n" + "\n".join(vec_lines))

        if graph_context["context_text"]:
            parts.append("Knowledge graph context:\n" + graph_context["context_text"])

        merged = "\n\n".join(parts) if parts else ""

        return {
            "vector_context": vector_context,
            "graph_context": graph_context,
            "merged_context_text": merged,
        }

    # ------------------------------------------------------------------
    # Stats & Conversion
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "total_nodes": len(self._graph["nodes"]),
            "total_edges": len(self._graph["edges"]),
            "total_communities": len(self._graph.get("communities", {})),
            "has_summaries": bool(self._graph.get("community_summaries")),
        }

    def to_networkx(self):
        import networkx as nx

        G = nx.DiGraph()
        for node, attrs in self._graph["nodes"].items():
            G.add_node(node, **attrs)
        for edge in self._graph["edges"]:
            G.add_edge(edge["source"], edge["target"], relation=edge["relation"])
        return G

    # ------------------------------------------------------------------
    # Visualization (optional — requires pyvis)
    # ------------------------------------------------------------------

    def visualize(
        self,
        output_path: str | Path | None = None,
        height: str = "800px",
        width: str = "100%",
    ) -> str:
        """Generate an interactive HTML visualization of the knowledge graph.

        Nodes are colored by community membership and sized by importance.
        Requires the ``pyvis`` package (pip install pyvis).

        Returns the output file path.
        """
        try:
            from pyvis.network import Network
        except ImportError:
            raise ImportError(
                "pyvis is required for visualization. Install it: pip install pyvis"
            )

        if output_path is None:
            output_path = self._persist_path.parent / "graph.html"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        net = Network(
            height=height,
            width=width,
            directed=True,
            notebook=False,
            cdn_resources="remote",
        )

        # Community colors
        communities = self._graph.get("communities", {})
        entity_to_community: dict[str, str] = {}
        for cid, members in communities.items():
            for m in members:
                entity_to_community[m] = cid

        _COLORS = [
            "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
            "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
            "#469990", "#dcbeff", "#9A6324", "#fffac8", "#800000",
            "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
        ]

        for node_name, attrs in self._graph["nodes"].items():
            importance = attrs.get("importance", 1)
            cid = entity_to_community.get(node_name)
            color = _COLORS[int(cid) % len(_COLORS)] if cid is not None else "#808080"
            size = 10 + importance * 5

            title = (
                f"{node_name}\n"
                f"category: {attrs.get('category', 'other')}\n"
                f"importance: {importance}"
            )
            if cid is not None:
                summary = self._graph.get("community_summaries", {}).get(cid, "")
                if summary:
                    title += f"\ncommunity: {summary[:100]}"

            net.add_node(
                node_name,
                label=node_name,
                title=title,
                color=color,
                size=size,
            )

        for edge in self._graph["edges"]:
            net.add_edge(
                edge["source"],
                edge["target"],
                title=edge["relation"],
                label=edge["relation"],
            )

        net.save_graph(str(output_path))
        logger.info("graph_builder: visualization saved to %s", output_path)
        return str(output_path)
