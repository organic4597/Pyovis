# Pyovis Architecture

## Scope

This document describes the architecture implemented in the current repository state.

The codebase still contains mixed naming from the v4 runtime line and v5.x planning documents. The sections below focus on what is actually wired into the checked-in Python, Rust, runtime scripts, and configuration files.

## System Summary

Pyovis is a local agent platform with four cooperating LLM roles, a bounded execution and revision loop, graph-based memory, MCP tool integration, and multiple user interfaces.

At a high level, the system is built from:

- a Python orchestration layer under `pyovis/`
- a Rust extension module under `pyovis_core/`
- a local llama.cpp server used as the shared inference endpoint
- workspace and sandbox execution infrastructure
- memory services for graph and experience accumulation
- interface layers for Telegram, graph visualization, and project QnA

## High-Level Runtime Topology

```text
       +----------------------+
       |  Telegram Interface  |
       +----------------------+
           |
           |
       +----------------------+
       |  SessionManager      |
       |  - RequestAnalyzer   |
       |  - MCP integration   |
       |  - Graph enrichment  |
       +----------+-----------+
            |
      +-----------------+------------------+
      |                                    |
      v                                    v
  +---------------------+              +----------------------+
  | ResearchLoopControl |              | Direct answer path   |
  | PLAN -> BUILD ->    |              | Brain-only responses |
  | CRITIQUE -> EVAL    |              +----------------------+
  +----------+----------+
       |
       v
      +-----------------------------------+
      | Planner / Hands / Judge / Brain   |
      | via ModelSwapManager              |
      +----------------+------------------+
           |
           v
         +---------------+
         | llama.cpp API |
         | port 8001     |
         +---------------+

       Loop execution path
           |
           v
         +---------------+
         | CriticRunner  |
         | Docker / venv |
         +-------+-------+
           |
           v
      +--------------------------+
      | WorkspaceManager         |
      | FileWriter / Snapshots   |
      +--------------------------+
           |
           v
      +--------------------------+
      | KnowledgeGraphBuilder    |
      | Experience DB            |
      | Conversation Memory      |
      +--------------------------+
           |
           v
      +--------------------------+
      | Optional Neo4j mirror    |
      +--------------------------+
```

## Runtime Entry Points

### Preferred entry point

`pyovis` launches the unified runtime through `pyovis/cli.py`.

That path is the most complete launcher in the repository and starts:

- environment loading
- `SessionManager`
- the Telegram bot
- the KG web viewer on port 8502
- the llama.cpp-backed role server through `scripts/start_model.sh`

### Additional entry points

- `run_unified.py`: legacy unified launcher
- `run_qna.py`: FastAPI-based project QnA app on port 8080
- `python -m pyovis.main`: core session loop plus KG service startup
- `run_telegram_bot.py`: Telegram-only helper script

## Main Technologies

| Layer | Technologies | Notes |
|------|--------------|-------|
| Orchestration | Python, asyncio | Core control flow, long-running session loop |
| Native acceleration | Rust, PyO3, maturin | `pyovis_core` exposes native classes to Python |
| Inference | llama.cpp | Single OpenAI-compatible endpoint, role-specific model swapping |
| Web runtime | FastAPI, Starlette, Uvicorn | QnA app, KG web UI, KG service |
| HTTP client | httpx | Model API calls, streaming, native fetch tool |
| Execution | Docker, virtualenv | Sandboxed execution and dependency installation |
| Graph memory | JSON persistence, NetworkX | Graph storage, traversal, community analysis, visualization |
| Optional graph mirror | Neo4j | Optional persistence and visualization mirror backend |
| Retrieval | FAISS, sentence-transformers | Vector retrieval support in memory services |
| Bot interface | python-telegram-bot | Telegram polling and interaction flow |
| Config and parsing | PyYAML, python-dotenv | YAML runtime config and environment loading |
| Data utilities | numpy, pandas | Data and retrieval-related helpers |
| Testing | pytest, pytest-asyncio | Unit and integration coverage |

## Core Architectural Decisions

### 1. One inference server, multiple roles

Pyovis does not keep all role models resident at the same time. Instead, `ModelSwapManager` ensures the correct role model is loaded into a single llama.cpp endpoint and blocks work during swap windows when required.

This reduces steady-state memory pressure while preserving role separation.

### 2. Bounded self-correction loop

The core execution path is a bounded research loop implemented in `pyovis/orchestration/loop_controller.py`.

Main phases:

- `PLAN`
- `BUILD`
- `CRITIQUE`
- `EVALUATE`
- `REVISE`
- `ENRICH`
- `ESCALATE`
- `COMPLETE`

The loop uses explicit limits such as `max_loops`, `max_consecutive_fails`, and escalation counters to prevent runaway behavior.

### 3. Workspace-first execution

Generated code is written into isolated workspaces under `/pyovis_memory/workspace` through `WorkspaceManager` and `FileWriter`.

This keeps generated artifacts separate from the main repository and supports:

- path traversal protection
- temporary virtual environments
- incomplete/complete markers
- stale workspace cleanup
- execution snapshots and revision flows

### 4. Memory as first-class infrastructure

The memory layer combines several patterns:

- `KnowledgeGraphBuilder` for graph extraction and graph persistence
- `ConversationMemory` for per-chat history
- `ExperienceDB` for reusable success and failure patterns
- optional `Neo4jGraphMirror` for external graph mirroring

Graph persistence is file-backed by default and can be mirrored into Neo4j when the environment is configured.

### 5. Tool calling through MCP and native fallbacks

The MCP layer connects external tools and also supports locally registered native tools. `MCPToolAdapter` converts tool schemas into the OpenAI function-calling format expected by the model endpoint and loops tool results back into the LLM call path.

## Package-Level Structure

### `pyovis/orchestration`

Responsible for request routing and loop coordination.

Important modules:

- `session_manager.py`: central coordinator
- `loop_controller.py`: bounded agent loop
- `request_analyzer.py`: complexity and tool requirement analysis
- `chat_chain.py`: chat-chain style decomposition helpers
- `hard_limit.py`: output and control limits
- `symbol_extractor.py`: code symbol extraction for graph enrichment
- `parallel_generator.py`: multi-step generation helpers

### `pyovis/ai`

Role-specific model wrappers and inference control.

Important modules:

- `swap_manager.py`: role/model swap and health checks
- `planner.py`: task decomposition
- `brain.py`: review and synthesis
- `hands.py`: build and revise path
- `judge.py`, `judge_enhanced.py`: evaluation and escalation decisions
- `response_utils.py`: response cleanup and parsing helpers
- `prompts/`: role prompts and prompt loaders

### `pyovis/execution`

Execution, persistence, and revision support.

Important modules:

- `critic_runner.py`: execution and error classification
- `execution_plan.py`: install/run strategy planning
- `file_writer.py`: workspace lifecycle and file persistence
- `search_replace.py`: structured edits for revision loops
- `snapshot.py`: workspace snapshotting
- `static_analyzer.py`: code analysis support

### `pyovis/memory`

Graph, retrieval, and historical memory.

Important modules:

- `graph_builder.py`: graph extraction, persistence, visualization, communities
- `kg_server.py`: vector-search service with lazy FastAPI import
- `conversation.py`: per-conversation memory
- `experience_db.py`: experience storage and retrieval
- `user_profile.py`: inferred user preference memory
- `neo4j_backend.py`: optional mirror backend

### `pyovis/mcp`

Tool discovery and tool calling.

Important modules:

- `mcp_client.py`: MCP connections and tool invocation
- `tool_adapter.py`: tool schema conversion and tool loop execution
- `mcp_registry.py`: registry exploration and discovery
- `tool_registry.py`, `tool_installer.py`: local registration helpers

### `pyovis/interface`

User-facing runtime surfaces.

Important modules:

- `telegram_bot.py`: Telegram runtime and message workflow
- `telegram_enhanced.py`: enhanced Telegram behaviors
- `kg_web.py`: Starlette-based graph viewer and APIs

### `pyovis/monitoring`

Runtime health and process observation.

Modules:

- `health_monitor.py`
- `log_monitor.py`
- `watchdog.py`

### `pyovis_core`

Rust workspace member exposing native types to Python.

Structure:

- `src/lib.rs`
- `src/model/`
- `src/queue/`
- `src/thread_pool/`

The Rust workspace is configured at the repository root through `Cargo.toml`, while maturin builds the Python extension from `pyovis_core/Cargo.toml`.

### `qna_bot`

Standalone FastAPI application for repository-aware QnA.

Responsibilities:

- load project context at startup
- stream Brain model responses through SSE
- expose health and context endpoints
- serve a small browser UI

## Key Control Flow

### Request handling

1. A request arrives through Telegram or another runtime path.
2. `SessionManager` loads or updates conversation context.
3. `RequestAnalyzer` classifies complexity and tool needs.
4. Graph context may be injected from the memory layer.
5. The request is either answered directly or passed into `ResearchLoopController`.
6. Planner, Hands, Brain, and Judge interact through the swap manager.
7. Critic results and judge decisions determine pass, revise, enrich, or escalate.
8. Results and extracted knowledge are written back into memory stores.

### Execution handling

1. Planner returns a todo list, pass criteria, self-fix scope, and optional file structure.
2. `WorkspaceManager` prepares project directories and isolated runtime state.
3. Hands generates code and optional setup commands.
4. `CriticRunner` executes generated output with a dedicated virtual environment and optional network access.
5. Error patterns are classified into install, environment, syntax, import, and runtime families.
6. Judge returns structured verdicts that drive the next loop step.

### Memory handling

1. Graph extraction uses the LLM endpoint to derive triplets, concepts, and query entities.
2. Results are stored in a JSON-backed graph file.
3. Visual HTML output is written to `/pyovis_memory/kg/graph.html`.
4. Optional Neo4j mirroring stores semantic entities, modules, symbols, and relations externally.

## Runtime Configuration

Main configuration file: `config/unified_node.yaml`

Key areas:

- system metadata
- CPU and GPU layout
- model paths and context sizes
- swap timing and blocking policy
- loop limits and thresholds
- sandbox behavior
- storage directories
- logging settings

Current notable settings in that file include:

- single llama.cpp endpoint on port 8001
- dual-GPU tensor split `0.55,0.45`
- `max_loops: 5`
- Docker sandbox execution
- `/pyovis_memory` as the storage root

## External Services And Ports

| Service | Port | Purpose |
|---------|------|---------|
| llama.cpp | 8001 | shared OpenAI-compatible inference endpoint |
| KG web viewer | 8502 | graph visualization and graph API |
| QnA app | 8080 | browser-accessible project QnA |
| Neo4j HTTP | 7474 | optional graph browser |
| Neo4j Bolt | 7687 | optional graph driver endpoint |

## Storage Layout

| Path | Role |
|------|------|
| `/pyovis_memory/models` | GGUF model storage |
| `/pyovis_memory/workspace` | generated projects and execution workspaces |
| `/pyovis_memory/kg` | graph JSON and HTML visualization output |
| `/pyovis_memory/logs` | model server and runtime logs |
| `/pyovis_memory/loop_records` | loop tracking artifacts |

## Build And Packaging

### Python packaging

- build backend: `maturin`
- project metadata: `pyproject.toml`
- Python requirement: `>=3.10`

### Rust packaging

- root workspace: `Cargo.toml`
- Python extension crate: `pyovis_core/Cargo.toml`

## Testing Layout

The repository includes dedicated pytest modules for:

- AI module behavior
- request analysis and task classification
- loop controller and end-to-end pipeline behavior
- file writing and search/replace operations
- graph builder and Neo4j integration
- phase-level integration scenarios

The top-level test directory currently contains 15 focused test modules covering both unit and integration paths.

## Operational Notes

- llama.cpp and GGUF model files are external runtime dependencies and are intentionally not part of the public repository.
- The most complete launcher path is `pyovis` rather than the legacy helper scripts.
- Neo4j is optional. The default graph persistence path does not require it.
- The repository also contains historical planning documents, but the architecture above reflects the runtime code currently checked in.

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
