from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from pyovis.memory.neo4j_backend import Neo4jGraphMirror

logger = logging.getLogger(__name__)

_GRAPH_PERSIST_PATH = (
    Path(os.environ.get("PYOVIS_MEMORY_DIR", "/pyovis_memory")) / "kg" / "graph.json"
)

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
You are a knowledge graph analyst. Given a community of related entities and their relationships, write a concise 2-3 sentence summary describing what this community represents and its key themes.

Entities in this community:
{entities}

Relationships:
{relations}

Return ONLY the summary text, no JSON, no formatting.
"""

_ENTITY_EXTRACT_PROMPT = """\
Extract the key entities (nouns, named entities) from this query. Return a JSON array of lowercase strings.

Query: {query}
"""

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+")


def chunk_text(
    text: str, max_chars: int = 1500, overlap: int = 200
) -> list[dict[str, Any]]:
    if len(text) <= max_chars:
        return [
            {"chunk_index": 0, "text": text, "char_start": 0, "char_end": len(text)}
        ]

    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0
    char_cursor = 0

    for sentence in sentences:
        sent_len = len(sentence)
        if current_len + sent_len > max_chars and current:
            chunk_text_str = " ".join(current)
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "text": chunk_text_str,
                    "char_start": char_cursor,
                    "char_end": char_cursor + len(chunk_text_str),
                }
            )
            overlap_parts: list[str] = []
            overlap_len = 0
            for part in reversed(current):
                if overlap_len + len(part) > overlap:
                    break
                overlap_parts.insert(0, part)
                overlap_len += len(part)
            char_cursor += len(chunk_text_str) - overlap_len
            current = overlap_parts
            current_len = overlap_len

        current.append(sentence)
        current_len += sent_len

    if current:
        chunk_text_str = " ".join(current)
        chunks.append(
            {
                "chunk_index": len(chunks),
                "text": chunk_text_str,
                "char_start": char_cursor,
                "char_end": char_cursor + len(chunk_text_str),
            }
        )

    return chunks


class KnowledgeGraphBuilder:
    def __init__(
        self,
        persist_path: Path = _GRAPH_PERSIST_PATH,
        llm_base: str | None = None,
        model: str | None = None,
        neo4j_mirror: Neo4jGraphMirror | None = None,
        enable_code_graph: bool = True,
    ) -> None:
        self._persist_path = persist_path
        self._llm_base = llm_base or os.environ.get(
            "PYOVIS_LLM_BASE_URL", "http://localhost:8001"
        )
        self._model = model or os.environ.get("PYOVIS_BRAIN_MODEL", "brain")
        self._neo4j_mirror = neo4j_mirror or Neo4jGraphMirror.from_environment()
        self._enable_code_graph = enable_code_graph
        self._graph: dict[str, Any] = self._load()

    @staticmethod
    def _empty_graph() -> dict[str, Any]:
        return {
            "nodes": {},
            "edges": [],
            "communities": {},
            "community_summaries": {},
            "code_modules": {},
            "code_symbols": {},
            "code_symbol_edges": [],
        }

    def _normalize_graph(self, data: dict[str, Any]) -> dict[str, Any]:
        base = self._empty_graph()
        for key, default_value in base.items():
            value = data.get(key, default_value)
            if isinstance(default_value, dict):
                base[key] = value if isinstance(value, dict) else default_value
            elif isinstance(default_value, list):
                base[key] = value if isinstance(value, list) else default_value
            else:
                base[key] = value
        return base

    def _load(self) -> dict[str, Any]:
        if self._persist_path.exists():
            try:
                data = json.loads(self._persist_path.read_text(encoding="utf-8"))
                data = self._normalize_graph(data)
                logger.info(
                    "graph_builder: loaded graph — %d semantic nodes, %d semantic edges, %d code symbols",
                    len(data.get("nodes", {})),
                    len(data.get("edges", [])),
                    len(data.get("code_symbols", {})),
                )
                return data
            except Exception as exc:
                logger.warning("graph_builder: failed to load graph: %s", exc)
        return self._empty_graph()

    def _save(self) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._graph, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("graph_builder: failed to save graph: %s", exc)

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
    def _parse_json_array(text: str) -> list[Any]:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return []

    async def extract_triplets(self, text: str) -> list[dict[str, Any]]:
        raw = await self._call_llm(_EXTRACT_PROMPT.format(text=text))
        triplets = self._parse_json_array(raw)
        return [
            t
            for t in triplets
            if isinstance(t, dict) and "node_1" in t and "node_2" in t and "edge" in t
        ]

    async def extract_concepts(self, text: str) -> list[dict[str, Any]]:
        raw = await self._call_llm(_CONCEPTS_PROMPT.format(text=text))
        concepts = self._parse_json_array(raw)
        return [c for c in concepts if isinstance(c, dict) and "entity" in c]

    def _semantic_edge_exists(
        self, source: str, target: str, relation: str, origin: str
    ) -> bool:
        return any(
            edge["source"] == source
            and edge["target"] == target
            and edge["relation"] == relation
            and edge.get("origin", "") == origin
            for edge in self._graph["edges"]
        )

    def _code_edge_exists(
        self, source: str, target: str, relation: str, origin: str
    ) -> bool:
        return any(
            edge["source"] == source
            and edge["target"] == target
            and edge["relation"] == relation
            and edge.get("origin", "") == origin
            for edge in self._graph["code_symbol_edges"]
        )

    def _ensure_semantic_node(
        self,
        entity: str,
        *,
        category: str = "other",
        importance: int = 1,
        source: str = "",
    ) -> None:
        if entity not in self._graph["nodes"]:
            self._graph["nodes"][entity] = {
                "category": category,
                "importance": importance,
                "sources": [source] if source else [],
            }
            return

        node = self._graph["nodes"][entity]
        node["importance"] = max(int(node.get("importance", 1)), importance)
        if category != "other" and node.get("category", "other") == "other":
            node["category"] = category
        if source and source not in node.setdefault("sources", []):
            node["sources"].append(source)

    def _add_semantic_edge(
        self, source: str, target: str, relation: str, origin: str = ""
    ) -> bool:
        if self._semantic_edge_exists(source, target, relation, origin):
            return False
        self._graph["edges"].append(
            {"source": source, "target": target, "relation": relation, "origin": origin}
        )
        return True

    async def add_text(self, text: str, source: str = "") -> dict[str, int]:
        triplets = await self.extract_triplets(text)
        concepts = await self.extract_concepts(text)

        added_nodes = 0
        added_edges = 0

        for concept in concepts:
            entity = concept["entity"].lower().strip()
            existed = entity in self._graph["nodes"]
            self._ensure_semantic_node(
                entity,
                category=concept.get("category", "other"),
                importance=int(concept.get("importance", 1)),
                source=source,
            )
            if not existed:
                added_nodes += 1

        for triplet in triplets:
            source_node = triplet["node_1"].lower().strip()
            target_node = triplet["node_2"].lower().strip()
            relation = triplet["edge"].lower().strip()
            for node_name in (source_node, target_node):
                existed = node_name in self._graph["nodes"]
                self._ensure_semantic_node(node_name, source=source)
                if not existed:
                    added_nodes += 1
            if self._add_semantic_edge(source_node, target_node, relation, source):
                added_edges += 1

        self._save()
        logger.info(
            "graph_builder: added %d nodes, %d edges from source=%r",
            added_nodes,
            added_edges,
            source,
        )
        return {"added_nodes": added_nodes, "added_edges": added_edges}

    async def add_triplet(
        self, subject: str, predicate: str, object: str, origin: str = ""
    ) -> dict[str, int]:
        source_node = subject.lower().strip()
        target_node = object.lower().strip()
        relation = predicate.lower().strip()

        added_nodes = 0
        for node_name in (source_node, target_node):
            existed = node_name in self._graph["nodes"]
            self._ensure_semantic_node(
                node_name, category="other", importance=1, source=origin
            )
            if not existed:
                added_nodes += 1

        added_edges = (
            1
            if self._add_semantic_edge(source_node, target_node, relation, origin)
            else 0
        )
        self._save()

        if self._neo4j_mirror is not None:
            try:
                self._neo4j_mirror.mirror_triplet(
                    source_node, relation, target_node, origin=origin
                )
            except Exception as exc:
                logger.warning(
                    "graph_builder: failed to mirror semantic triplet to neo4j: %s", exc
                )

        return {"added_nodes": added_nodes, "added_edges": added_edges}

    async def add_document(
        self,
        text: str,
        source: str = "",
        max_chars: int = 1500,
        overlap: int = 200,
    ) -> dict[str, int]:
        chunks = chunk_text(text, max_chars=max_chars, overlap=overlap)
        total_nodes = 0
        total_edges = 0

        for chunk in chunks:
            chunk_source = (
                f"{source}#chunk{chunk['chunk_index']}"
                if source
                else f"chunk{chunk['chunk_index']}"
            )
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

    async def add_code_symbols(
        self, code: str, file_path: str, source: str = ""
    ) -> dict[str, int]:
        if not self._enable_code_graph:
            return {"modules": 0, "symbols": 0, "edges": 0}

        from pyovis.orchestration.symbol_extractor import SymbolExtractor

        extractor = SymbolExtractor()
        graph = extractor.extract_graph(code, file_path)
        module = dict(graph["module"])
        module["source"] = source
        module_id = module["id"]

        added_modules = 0
        if module_id not in self._graph["code_modules"]:
            added_modules = 1
        self._graph["code_modules"][module_id] = module

        added_symbols = 0
        serialized_symbols: list[dict[str, Any]] = []
        for symbol in graph["symbols"]:
            symbol_id = symbol["id"]
            existing = self._graph["code_symbols"].get(symbol_id)
            merged = dict(existing or {})
            merged.update(symbol)
            merged["source"] = source
            if existing is None:
                added_symbols += 1
            self._graph["code_symbols"][symbol_id] = merged
            serialized_symbols.append(merged)

        added_edges = 0
        serialized_edges: list[dict[str, Any]] = []
        for edge in graph["edges"]:
            edge_payload = dict(edge)
            edge_payload["origin"] = source or file_path
            if self._code_edge_exists(
                edge_payload["source"],
                edge_payload["target"],
                edge_payload["relation"],
                edge_payload["origin"],
            ):
                continue
            self._graph["code_symbol_edges"].append(edge_payload)
            serialized_edges.append(edge_payload)
            added_edges += 1

        self._save()

        if self._neo4j_mirror is not None:
            try:
                self._neo4j_mirror.mirror_code_graph(
                    module, serialized_symbols, serialized_edges
                )
            except Exception as exc:
                logger.warning(
                    "graph_builder: failed to mirror code graph to neo4j: %s", exc
                )

        return {
            "modules": added_modules,
            "symbols": added_symbols,
            "edges": added_edges,
        }

    def query_neighbors(self, entity: str, depth: int = 1) -> dict[str, Any]:
        entity = entity.lower().strip()
        visited: set[str] = set()
        frontier = {entity}
        result_edges: list[dict[str, Any]] = []

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

        all_nodes = {edge["source"] for edge in result_edges} | {
            edge["target"] for edge in result_edges
        }
        all_nodes.add(entity)

        return {
            "center": entity,
            "nodes": {
                n: self._graph["nodes"].get(n, {"category": "other", "importance": 1})
                for n in all_nodes
            },
            "edges": result_edges,
        }

    def query_code_symbols(self, query: str, depth: int = 1) -> dict[str, Any]:
        normalized = query.lower().strip()
        matched_ids = {
            symbol_id
            for symbol_id, symbol in self._graph.get("code_symbols", {}).items()
            if normalized in symbol.get("name", "").lower()
            or normalized in symbol.get("qualified_name", "").lower()
            or normalized in symbol.get("file_path", "").lower()
        }

        frontier = set(matched_ids)
        visited = set(matched_ids)
        result_edges: list[dict[str, Any]] = []

        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in self._graph.get("code_symbol_edges", []):
                if edge["source"] in frontier and edge["target"] not in visited:
                    result_edges.append(edge)
                    next_frontier.add(edge["target"])
                elif edge["target"] in frontier and edge["source"] not in visited:
                    result_edges.append(edge)
                    next_frontier.add(edge["source"])
            visited.update(frontier)
            frontier = next_frontier - visited

        related_ids = (
            visited
            | frontier
            | {edge["source"] for edge in result_edges}
            | {edge["target"] for edge in result_edges}
        )
        nodes = {
            symbol_id: self._graph["code_symbols"][symbol_id]
            for symbol_id in related_ids
            if symbol_id in self._graph["code_symbols"]
        }
        modules = {
            module_id: module
            for module_id, module in self._graph.get("code_modules", {}).items()
            if normalized in module.get("file_path", "").lower()
        }

        return {
            "query": query,
            "nodes": nodes,
            "edges": result_edges,
            "modules": modules,
        }

    def detect_communities(self) -> dict[str, list[str]]:
        import networkx as nx
        from networkx.algorithms.community import greedy_modularity_communities

        graph = nx.DiGraph()
        for node_name, attrs in self._graph["nodes"].items():
            graph.add_node(node_name, **attrs)
        for edge in self._graph["edges"]:
            graph.add_edge(edge["source"], edge["target"], relation=edge["relation"])

        if graph.number_of_nodes() == 0:
            self._graph["communities"] = {}
            self._save()
            return {}

        undirected = graph.to_undirected()
        communities_iter = greedy_modularity_communities(undirected)
        communities: dict[str, list[str]] = {}
        for idx, community_set in enumerate(communities_iter):
            communities[str(idx)] = sorted(community_set)
        self._graph["communities"] = communities
        self._save()
        logger.info(
            "graph_builder: detected %d communities across %d semantic nodes",
            len(communities),
            graph.number_of_nodes(),
        )
        return communities

    def _get_entity_community(self, entity: str) -> str | None:
        normalized = entity.lower().strip()
        for community_id, members in self._graph.get("communities", {}).items():
            if normalized in members:
                return community_id
        return None

    async def summarize_communities(self) -> dict[str, str]:
        communities = self._graph.get("communities", {})
        if not communities:
            logger.warning("graph_builder: no communities detected")
            return {}

        summaries: dict[str, str] = {}
        for community_id, members in communities.items():
            member_set = set(members)
            internal_edges: list[str] = []
            for edge in self._graph["edges"]:
                if edge["source"] in member_set and edge["target"] in member_set:
                    internal_edges.append(
                        f"{edge['source']} --[{edge['relation']}]--> {edge['target']}"
                    )

            entities_str = ", ".join(members)
            relations_str = (
                "\n".join(internal_edges[:50])
                if internal_edges
                else "(no direct relationships between members)"
            )
            prompt = _COMMUNITY_SUMMARY_PROMPT.format(
                entities=entities_str, relations=relations_str
            )

            try:
                raw = await self._call_llm(prompt)
                summaries[community_id] = re.sub(
                    r"<think>.*?</think>", "", raw, flags=re.DOTALL
                ).strip()
            except Exception as exc:
                logger.warning(
                    "graph_builder: failed to summarize community %s: %s",
                    community_id,
                    exc,
                )
                summaries[community_id] = f"Community with entities: {entities_str}"

        self._graph["community_summaries"] = summaries
        self._save()
        return summaries

    async def query_graph_rag(
        self, query: str, depth: int = 2, use_llm_extraction: bool = True
    ) -> dict[str, Any]:
        if use_llm_extraction:
            try:
                raw = await self._call_llm(_ENTITY_EXTRACT_PROMPT.format(query=query))
                query_entities = [
                    e.lower().strip()
                    for e in self._parse_json_array(raw)
                    if isinstance(e, str)
                ]
            except Exception:
                query_entities = self._keyword_extract(query)
        else:
            query_entities = self._keyword_extract(query)

        if not query_entities:
            query_entities = self._keyword_extract(query)

        matched_entities: list[dict[str, Any]] = []
        all_relations: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str, str]] = set()
        community_ids: set[str] = set()

        for query_entity in query_entities:
            if query_entity in self._graph["nodes"]:
                node_data = self._graph["nodes"][query_entity]
                matched_entities.append(
                    {
                        "name": query_entity,
                        "category": node_data.get("category", "other"),
                        "importance": node_data.get("importance", 1),
                    }
                )
                neighbors = self.query_neighbors(query_entity, depth=depth)
                for edge in neighbors["edges"]:
                    key = (
                        edge["source"],
                        edge["target"],
                        edge["relation"],
                        edge.get("origin", ""),
                    )
                    if key not in seen_edges:
                        seen_edges.add(key)
                        all_relations.append(edge)
                community_id = self._get_entity_community(query_entity)
                if community_id is not None:
                    community_ids.add(community_id)
            else:
                for node_name, node_data in self._graph["nodes"].items():
                    if query_entity in node_name or node_name in query_entity:
                        matched_entities.append(
                            {
                                "name": node_name,
                                "category": node_data.get("category", "other"),
                                "importance": node_data.get("importance", 1),
                            }
                        )
                        neighbors = self.query_neighbors(node_name, depth=depth)
                        for edge in neighbors["edges"]:
                            key = (
                                edge["source"],
                                edge["target"],
                                edge["relation"],
                                edge.get("origin", ""),
                            )
                            if key not in seen_edges:
                                seen_edges.add(key)
                                all_relations.append(edge)
                        community_id = self._get_entity_community(node_name)
                        if community_id is not None:
                            community_ids.add(community_id)

        summaries_store = self._graph.get("community_summaries", {})
        relevant_summaries = [
            summaries_store[community_id]
            for community_id in community_ids
            if community_id in summaries_store
        ]

        code_results = (
            self.query_code_symbols(query, depth=max(1, min(depth, 2)))
            if self._enable_code_graph
            else {"nodes": {}, "edges": [], "modules": {}}
        )

        context_parts: list[str] = []
        if matched_entities:
            context_parts.append(
                "Related entities:\n"
                + "\n".join(
                    f"- {entity['name']} ({entity['category']}, importance={entity['importance']})"
                    for entity in matched_entities
                )
            )
        if all_relations:
            context_parts.append(
                "Relationships:\n"
                + "\n".join(
                    f"- {relation['source']} --[{relation['relation']}]--> {relation['target']}"
                    for relation in all_relations[:30]
                )
            )
        if relevant_summaries:
            context_parts.append(
                "Community context:\n"
                + "\n".join(f"- {summary}" for summary in relevant_summaries)
            )
        if code_results["nodes"] or code_results["modules"]:
            symbol_lines = [
                f"- {symbol.get('qualified_name', symbol.get('name', symbol_id))} ({symbol.get('kind', 'symbol')})"
                for symbol_id, symbol in list(code_results["nodes"].items())[:15]
            ]
            module_lines = [
                f"- {module.get('file_path', module_id)}"
                for module_id, module in list(code_results["modules"].items())[:10]
            ]
            code_parts: list[str] = []
            if module_lines:
                code_parts.append("Relevant modules:\n" + "\n".join(module_lines))
            if symbol_lines:
                code_parts.append("Relevant code symbols:\n" + "\n".join(symbol_lines))
            if code_results["edges"]:
                code_parts.append(
                    "Code relationships:\n"
                    + "\n".join(
                        f"- {edge['source']} --[{edge['relation']}]--> {edge['target']}"
                        for edge in code_results["edges"][:20]
                    )
                )
            context_parts.append("\n\n".join(code_parts))

        return {
            "entities": matched_entities,
            "relations": all_relations,
            "community_summaries": relevant_summaries,
            "code_results": code_results,
            "context_text": "\n\n".join(context_parts) if context_parts else "",
        }

    @staticmethod
    def _keyword_extract(query: str) -> list[str]:
        stop_words = {
            "the",
            "a",
            "an",
            "am",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "can",
            "shall",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "about",
            "against",
            "and",
            "but",
            "or",
            "nor",
            "not",
            "so",
            "yet",
            "both",
            "either",
            "neither",
            "each",
            "every",
            "all",
            "any",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "only",
            "own",
            "same",
            "than",
            "too",
            "very",
            "just",
            "because",
            "if",
            "when",
            "where",
            "how",
            "what",
            "which",
            "who",
            "whom",
            "this",
            "that",
            "these",
            "those",
            "i",
            "me",
            "my",
            "we",
            "our",
            "you",
            "your",
            "he",
            "him",
            "his",
            "she",
            "her",
            "it",
            "its",
            "they",
            "them",
            "their",
        }
        tokens = re.findall(r"[a-z\uac00-\ud7a3_]+", query.lower())
        return [token for token in tokens if token not in stop_words and len(token) > 1]

    async def hybrid_search(
        self,
        query: str,
        vector_results: list[dict[str, Any]] | None = None,
        depth: int = 2,
    ) -> dict[str, Any]:
        graph_context = await self.query_graph_rag(query, depth=depth)
        vector_context = vector_results or []
        parts: list[str] = []

        if vector_context:
            parts.append(
                "Vector search results:\n"
                + "\n".join(
                    f"- (dist={result.get('distance', 0):.3f}) {result.get('text', '')[:200]}"
                    for result in vector_context[:10]
                )
            )
        if graph_context["context_text"]:
            parts.append("Knowledge graph context:\n" + graph_context["context_text"])

        return {
            "vector_context": vector_context,
            "graph_context": graph_context,
            "merged_context_text": "\n\n".join(parts) if parts else "",
        }

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_nodes": len(self._graph["nodes"]),
            "total_edges": len(self._graph["edges"]),
            "total_communities": len(self._graph.get("communities", {})),
            "has_summaries": bool(self._graph.get("community_summaries")),
            "total_code_modules": len(self._graph.get("code_modules", {})),
            "total_code_symbols": len(self._graph.get("code_symbols", {})),
            "total_code_edges": len(self._graph.get("code_symbol_edges", [])),
            "neo4j_enabled": bool(
                self._neo4j_mirror and self._neo4j_mirror.is_enabled()
            ),
        }

    def to_networkx(self):
        import networkx as nx

        graph = nx.DiGraph()
        for node_name, attrs in self._graph["nodes"].items():
            graph.add_node(node_name, node_type="semantic", **attrs)
        for edge in self._graph["edges"]:
            graph.add_edge(
                edge["source"],
                edge["target"],
                relation=edge["relation"],
                edge_type="semantic",
            )
        for module_id, attrs in self._graph.get("code_modules", {}).items():
            graph.add_node(module_id, node_type="module", **attrs)
        for symbol_id, attrs in self._graph.get("code_symbols", {}).items():
            graph.add_node(symbol_id, node_type="code_symbol", **attrs)
        for edge in self._graph.get("code_symbol_edges", []):
            graph.add_edge(
                edge["source"],
                edge["target"],
                relation=edge["relation"],
                edge_type="code",
            )
        return graph

    def visualize(
        self,
        output_path: str | Path | None = None,
        height: str = "800px",
        width: str = "100%",
    ) -> str:
        try:
            from pyvis.network import Network
        except ImportError as exc:
            raise ImportError(
                "pyvis is required for visualization. Install it: pip install pyvis"
            ) from exc

        if output_path is None:
            output_path = self._persist_path.parent / "graph.html"
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        graph = self.to_networkx()
        net = Network(
            height=height,
            width=width,
            directed=True,
            notebook=False,
            cdn_resources="remote",
        )

        colors = {
            "semantic": "#58a6ff",
            "module": "#f58231",
            "code_symbol": "#3cb44b",
        }
        code_symbol_colors = {
            "function": "#3cb44b",
            "class": "#2dd4bf",
            "method": "#a3e635",
            "constant": "#fbbf24",
        }
        shapes = {
            "semantic": "dot",
            "module": "box",
            "code_symbol": "diamond",
        }
        sizes = {
            "semantic": 18,
            "module": 16,
            "code_symbol": 12,
        }

        for node_name, attrs in graph.nodes(data=True):
            node_type = attrs.get("node_type", "semantic")
            label = attrs.get("name") or attrs.get("qualified_name") or node_name
            title = "\n".join(
                f"{key}: {value}" for key, value in attrs.items() if key != "sources"
            )
            if node_type == "code_symbol":
                kind = attrs.get("kind", "function")
                color = code_symbol_colors.get(kind, "#3cb44b")
            else:
                color = colors.get(node_type, "#808080")
            net.add_node(
                node_name,
                label=label,
                title=title,
                color=color,
                size=sizes.get(node_type, 14),
                shape=shapes.get(node_type, "dot"),
            )

        for source, target, attrs in graph.edges(data=True):
            relation = attrs.get("relation", "related_to")
            edge_type = attrs.get("edge_type", "semantic")
            edge_kwargs: dict = {"label": relation, "title": relation}
            if edge_type == "code":
                edge_kwargs["color"] = "#3cb44b"
                edge_kwargs["width"] = 2
            net.add_edge(source, target, **edge_kwargs)

        net.save_graph(str(output))
        logger.info("graph_builder: visualization saved to %s", output)
        return str(output)
