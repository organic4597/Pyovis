# Pyovis v4.0 — Architecture

## Overview

Pyovis is a local AI assistant and research agent running 4 specialized LLM roles on dual GPUs with model hot-swapping, a self-evaluation loop, persistent knowledge graph memory, and MCP tool integration.

```
┌─────────────────────────────────────────────────────────────┐
│                         User Input                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    SessionManager                           │
│   RequestAnalyzer → Graph RAG Enrichment → LoopController  │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┼────────────────┐
           ▼               ▼                ▼
      ┌─────────┐   ┌─────────────┐  ┌──────────────┐
      │ Planner │   │    Brain    │  │    Hands     │
      │ GLM-4.7 │   │  Qwen3-14B  │  │ Devstral-24B │
      └────┬────┘   └──────┬──────┘  └──────┬───────┘
           │               │                │
           └───────────────┼────────────────┘
                           │
                           ▼
                   ┌───────────────┐
                   │     Judge     │
                   │ DeepSeek-R1   │
                   └───────┬───────┘
                           │
               ┌───────────┼───────────┐
               ▼           ▼           ▼
            PASS         REVISE    ESCALATE
               │           │
               │           └─── Loop (max 5)
               ▼
      ┌────────────────┐
      │  ingest_to_    │
      │  graph (KG)    │
      └────────────────┘
```

---

## Hardware Configuration

| Item | Spec |
|------|------|
| GPU 0 | RTX 3060 12GB (45% VRAM split) |
| GPU 1 | RTX 4070 SUPER 12GB (55% VRAM split) |
| RAM | 32GB |
| Storage | ~60GB model files |
| CUDA driver | 13.1 |
| CUDA nvcc | 12.0 |
| OS | Linux (WSL2 6.6) |

### Model-to-GPU Mapping

| Role | Model | VRAM | Load Time | GPU |
|------|-------|------|-----------|-----|
| Planner | GLM-4.7-Flash 30B | 22.4 GB | 72s | Both |
| Brain | Qwen3-14B | 18.5 GB | 27s | Both |
| Hands | Devstral-24B | 22.2 GB | 27s | Both |
| Judge | DeepSeek-R1-Distill-14B | 14.3 GB | 19s | Both |

Single llama-server on port 8001, roles swap via `/slots` API.

---

## Software Layers

```
┌─────────────────────────────────────────────────────────┐
│  Python (pyovis/)                                       │
│  ┌──────────────┐ ┌───────────────┐ ┌────────────────┐  │
│  │ orchestration│ │     ai/       │ │    memory/     │  │
│  │ SessionMgr   │ │ ModelSwapMgr  │ │ KnowledgeGraph │  │
│  │ LoopCtrl     │ │ Brain/Planner │ │ KGStore(FAISS) │  │
│  │ ReqAnalyzer  │ │ Hands/Judge   │ │                │  │
│  └──────────────┘ └───────────────┘ └────────────────┘  │
│  ┌──────────────┐ ┌───────────────┐ ┌────────────────┐  │
│  │    mcp/      │ │   skill/      │ │  execution/    │  │
│  │ MCPClient    │ │ SkillManager  │ │ CriticRunner   │  │
│  │ ToolAdapter  │ │               │ │ FileWriter     │  │
│  │ MCPRegistry  │ │               │ │                │  │
│  └──────────────┘ └───────────────┘ └────────────────┘  │
│  ┌──────────────┐                                        │
│  │  tracking/   │                                        │
│  │ LoopTracker  │                                        │
│  └──────────────┘                                        │
├─────────────────────────────────────────────────────────┤
│  Rust (pyovis_core/) — PyO3 bindings                    │
│  PyPriorityQueue  |  PyModelSwap  |  ThreadPool         │
├─────────────────────────────────────────────────────────┤
│  llama.cpp server (port 8001, CUDA build)               │
└─────────────────────────────────────────────────────────┘
```

---

## Component Reference

### `pyovis/orchestration/`

#### `SessionManager` (`session_manager.py`, ~470 lines)

Main request dispatcher. Integrates MCP tools, Graph RAG context enrichment, and knowledge ingestion.

```python
SessionManager(task_queue, model_swap, tracker, result_callback)

# Key methods
await session.run()                          # Main async loop
await session._handle_request(payload)       # Route to loop or direct answer
await session._enrich_with_graph_rag(payload)# Inject KG context into prompt
await session._ingest_to_graph(req, resp)    # Auto-accumulate conversation to KG
await session.get_mcp_tools() -> list[str]   # Discover live MCP tools
session.suggest_alternative_tools(failed)    # Fallback tool mapping
session.get_tools_for_task(keywords) -> dict # Keyword-based tool selection
```

#### `ResearchLoopController` (`loop_controller.py`, ~250 lines)

Drives the PLAN → BUILD → CRITIQUE → EVALUATE → REVISE/ENRICH/ESCALATE → COMPLETE cycle.

```python
ResearchLoopController(model_swap, critic, skill_mgr, tracker, file_writer)

await controller.run(ctx: LoopContext) -> dict
# LoopContext fields: request, session_id, loop_count, history, ...
# LoopStep: PLAN, BUILD, CRITIQUE, EVALUATE, REVISE, ENRICH, ESCALATE, COMPLETE
# JudgeVerdict: PASS, REVISE, ESCALATE
```

Loop limits:
- `max_loops`: 5 (configurable in `unified_node.yaml`)
- `max_consecutive_failures`: 3

#### `RequestAnalyzer` (`request_analyzer.py`)

Context-aware intent detection. Classifies requests into `TaskComplexity` levels and maps to real MCP tool names.

```python
RequestAnalyzer(model_swap)

await analyzer.analyze(request, context) -> AnalysisResult
# AnalysisResult fields: complexity, needs_clarification, required_tools, ...
# TaskComplexity: SIMPLE, MODERATE, COMPLEX
# ToolStatus: AVAILABLE, UNAVAILABLE, FALLBACK
```

---

### `pyovis/ai/`

#### `ModelSwapManager` (`swap_manager.py`, ~348 lines)

Hot-swaps LLM roles on the single llama-server without restarting the process.

```python
ModelSwapManager(config: SwapManagerConfig)

await mgr.ensure_model(role: ModelRole)   # Load role if not current
await mgr.health_check() -> bool
await mgr.shutdown()
await mgr._run_llm(role, messages, ...) -> dict
```

`ModelRole` enum: `PLANNER`, `BRAIN`, `HANDS`, `JUDGE`

#### `Brain` / `Planner` / `response_utils`

- `Brain`: Reviews plan output, decides escalation
- `Planner`: Decomposes task into steps
- `strip_cot(text)`: Remove `<think>` blocks from CoT models
- `message_text(msg)`: Extract text from message object
- `parse_json_message(msg)`: Parse JSON from LLM response

---

### `pyovis/memory/`

#### `KnowledgeGraphBuilder` (`graph_builder.py`, ~751 lines)

LLM-driven triplet extraction with NetworkX graph + FAISS vector store. Persistent JSON + index files.

```python
KnowledgeGraphBuilder(persist_path, llm_base, model)

# Ingestion
await kb.add_text(text, source) -> dict
await kb.add_document(text, source, max_chars=1500, overlap=200) -> dict

# Extraction (LLM-driven)
await kb.extract_triplets(text) -> list[dict]   # [{"subject","predicate","object"}]
await kb.extract_concepts(text) -> list[dict]   # [{"concept","type","description"}]

# Retrieval
await kb.query_graph_rag(query, depth=2, use_llm_extraction=True) -> dict
await kb.hybrid_search(query, vector_results=5, depth=2) -> dict
kb.query_neighbors(entity, depth=2) -> dict

# Community detection
kb.detect_communities() -> dict[str, list[str]]
await kb.summarize_communities() -> dict[str, str]

# Utilities
kb.to_networkx() -> nx.DiGraph
kb.visualize(output_path) -> str
kb.get_stats() -> dict

# Module-level helper
chunk_text(text, max_chars=1500, overlap=200) -> list[dict]
```

#### `KGStore` (`kg_server.py`)

FAISS-backed vector store with FastAPI server. Uses lazy imports — `fastapi`/`pydantic`/`numpy` loaded only when server starts.

```python
KGStore(index_path, documents_path, model_name)

store.add_documents(texts, sources)
store.search(query, k=5) -> list[dict]
store.save()
store.load()
```

Server endpoints (when `kg_server.py` run standalone):
- `POST /add` — ingest documents
- `POST /search` — vector similarity search
- `GET /stats` — index statistics

---

### `pyovis/mcp/`

#### `MCPClient` / `MCPManager` (`mcp_client.py`)

Manages MCP (Model Context Protocol) server connections and tool invocation.

```python
MCPManager(configs: list[MCPServerConfig])

await mgr.connect_all()
await mgr.call_tool(name, arguments) -> ToolCallResult
mgr.list_tools() -> list[MCPTool]
```

#### `MCPToolAdapter` / `ToolEnabledLLM` (`tool_adapter.py`)

Wraps LLM calls with tool-use loop. Handles tool_calls in response, executes via MCPManager, re-submits results.

#### `MCPRegistryExplorer` (`mcp_registry.py`)

Discovers available MCP servers from npm registry and local installation.

---

### `pyovis/skill/`

#### `SkillManager` (`skill_manager.py`)

Manages a library of verified and candidate skills extracted from past successful loop runs.

```python
SkillManager(skills_dir)

mgr.load_verified() -> list[Skill]
await mgr.evaluate_and_patch(skill, context) -> Skill
mgr.promote(skill_id)
mgr.list_skills() -> list[Skill]
```

---

### `pyovis/execution/`

#### `CriticRunner` (`critic_runner.py`)

Executes code in a Docker sandbox and classifies errors.

```python
CriticRunner(docker_image, timeout)

await runner.run(code, language) -> CriticResult
# CriticResult: stdout, stderr, exit_code, error_type, traceback
```

Docker image: `docker/sandbox/Dockerfile`

#### `FileWriter` / `WorkspaceManager` (`file_writer.py`)

Safe file operations within a workspace directory. Tracks written files per session.

---

### `pyovis/tracking/`

#### `LoopTracker` (`loop_tracker.py`)

Appends JSONL records per loop with token counts, model roles, latency, and cost estimates.

```python
LoopTracker(log_path)

tracker.record(loop_record: LoopRecord)
tracker.load_history() -> list[LoopRecord]
tracker.summary() -> dict
```

---

### `pyovis_core/` (Rust — PyO3)

High-performance primitives exposed to Python.

#### `PyPriorityQueue`

Lock-free priority queue for task scheduling.

```python
from pyovis_core import PyPriorityQueue

q = PyPriorityQueue()
q.enqueue(priority=1, task_type="AiBrain", payload="{...}")
item = q.dequeue()   # -> dict | None
len(q)
q.is_empty()
```

Priority levels (lower = higher priority):
| Level | Name |
|-------|------|
| 0 | Stop |
| 1 | AiBrain |
| 2 | AiHands |
| 3 | AiJudge |
| 4 | Orchestration |
| 5 | Io |

#### `PyModelSwap`

Atomic model role state tracker.

```python
from pyovis_core import PyModelSwap

swap = PyModelSwap()
swap.switch_to_planner()
swap.switch_to_brain()
swap.switch_to_hands()
swap.switch_to_judge()
swap.current_role()  # -> str
```

---

## Data Flow

### Request Lifecycle

```
1. User input arrives → SessionManager._handle_request(payload)
2. RequestAnalyzer.analyze() → TaskComplexity + required_tools
3. SessionManager._enrich_with_graph_rag(payload)
     → KnowledgeGraphBuilder.hybrid_search(query)
     → Injects relevant KG context into prompt
4. ResearchLoopController.run(LoopContext)
     a. PLAN  — Planner decomposes task
     b. BUILD — Hands generates code/answer
     c. CRITIQUE — CriticRunner executes in Docker sandbox
     d. EVALUATE — Judge scores result (PASS / REVISE / ESCALATE)
     e. REVISE — Hands re-generates with error context
        or ENRICH — Brain adds context
        or ESCALATE — Brain takes over
     f. COMPLETE — Result returned
5. SessionManager._ingest_to_graph(request, response)
     → KnowledgeGraphBuilder.add_document(conversation)
     → Triplets + concepts extracted and stored persistently
6. LoopTracker.record(loop_record) — JSONL cost log
```

### Model Hot-Swap Flow

```
ensure_model(HANDS)
  → if current_role != HANDS:
      POST /slots/0 {"model": "devstral-24b.gguf", ...}
      wait for load confirmation
      PyModelSwap.switch_to_hands()
  → _run_llm(HANDS, messages) → OpenAI-compat /v1/chat/completions
```

---

## Configuration

`config/unified_node.yaml`:

```yaml
hardware:
  gpu_split: "45,55"          # RTX3060 45% | RTX4070S 55%
  n_gpu_layers: -1             # Full GPU offload

models:
  planner: glm-4.7-flash-30b
  brain:   qwen3-14b
  hands:   devstral-24b
  judge:   deepseek-r1-distill-14b

server:
  port: 8001
  context_sizes:
    planner: 65536
    brain:   40960
    hands:   81920
    judge:   65536

memory:
  storage_path: /pyvis_memory/

loop:
  max_loops: 5
  max_consecutive_failures: 3
```

Environment variables (override config):

| Variable | Default | Purpose |
|----------|---------|---------|
| `PYOVIS_LLM_BASE_URL` | `http://localhost:8001` | llama-server endpoint |
| `PYOVIS_BRAIN_MODEL` | (from config) | Override Brain model |
| `PYOVIS_MEMORY_DIR` | `/pyvis_memory/` | KG + FAISS persistence root |

---

## Test Coverage

| File | Tests | Domain |
|------|-------|--------|
| `test_ai_modules.py` | 43 | ModelSwapManager, Brain, Planner, response_utils |
| `test_e2e_loop.py` | 23 | ResearchLoopController end-to-end |
| `test_file_writer.py` | 20 | FileWriter, WorkspaceManager |
| `test_graph_builder.py` | 43 | KnowledgeGraphBuilder full pipeline |
| `test_infra_modules.py` | 43 | MCP, SkillManager, CriticRunner, LoopTracker |
| `test_request_analyzer.py` | 14 | RequestAnalyzer intent detection |
| **Total Python** | **186 test functions → 172 collected** | |
| `pyovis_core` (Rust) | 8 | PyPriorityQueue, PyModelSwap |

Run all tests:
```bash
cd /Pyvis
python3 -m pytest tests/ -q
cargo test --workspace
```

---

## Directory Structure

```
/Pyvis/
├── pyproject.toml              # name=pyovis, Python packaging
├── Cargo.toml                  # Rust workspace (members=["pyovis_core"])
├── config/
│   └── unified_node.yaml       # Hardware + model + server config
├── docker/
│   └── sandbox/Dockerfile      # Critic sandbox image
├── llama.cpp/                  # CUDA build (llama-server binary)
├── pyovis/                     # Main Python package
│   ├── ai/
│   │   ├── swap_manager.py     # ModelSwapManager, ModelRole, SwapManagerConfig
│   │   ├── brain.py
│   │   ├── planner.py
│   │   └── response_utils.py   # strip_cot, message_text, parse_json_message
│   ├── orchestration/
│   │   ├── loop_controller.py  # ResearchLoopController, LoopContext, JudgeVerdict
│   │   ├── session_manager.py  # SessionManager (Graph RAG integrated)
│   │   └── request_analyzer.py # RequestAnalyzer, TaskComplexity, AnalysisResult
│   ├── memory/
│   │   ├── graph_builder.py    # KnowledgeGraphBuilder (~751 lines)
│   │   ├── kg_server.py        # KGStore (FAISS) + FastAPI (lazy imports)
│   │   └── __init__.py         # lazy __getattr__ export
│   ├── mcp/
│   │   ├── mcp_client.py       # MCPClient, MCPManager, MCPTool
│   │   ├── tool_adapter.py     # MCPToolAdapter, ToolEnabledLLM
│   │   └── mcp_registry.py     # MCPRegistryExplorer
│   ├── skill/
│   │   └── skill_manager.py    # SkillManager
│   ├── tracking/
│   │   └── loop_tracker.py     # LoopTracker, LoopRecord
│   ├── execution/
│   │   ├── critic_runner.py    # CriticRunner (Docker)
│   │   └── file_writer.py      # FileWriter, WorkspaceManager
│   └── main.py
├── pyovis_core/                # Rust crate (PyO3 bindings)
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs              # PyPriorityQueue, PyModelSwap (public API)
│       ├── queue/priority_queue.rs
│       ├── model/hot_swap.rs
│       └── thread_pool/
└── tests/
    ├── test_ai_modules.py
    ├── test_e2e_loop.py
    ├── test_file_writer.py
    ├── test_graph_builder.py
    ├── test_infra_modules.py
    └── test_request_analyzer.py
```
