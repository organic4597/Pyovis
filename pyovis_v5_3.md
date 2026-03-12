# Pyovis v5.3 — Comprehensive Project Documentation

> Status: consolidated technical documentation generated from the current repository state and six focused codebase analyses.
> Project root: `/Pyvis`
> Scope: architecture, modules, execution flow, memory system, Rust core, sandboxing, MCP/skills/tracking, interfaces, tests, scripts, and v5.x roadmap context.

---

## 1. Project Overview

Pyovis is a **local multi-role AI assistant and research agent** built around a single `llama.cpp` inference server with **model hot-swapping** across four specialized roles:

- **Planner** — task decomposition
- **Brain** — analysis, review, escalation, final synthesis
- **Hands** — code generation and revision
- **Judge** — evaluation and verdicting

The system combines:

- dual-GPU local inference
- Python orchestration
- Rust performance primitives via PyO3
- Docker/venv-based execution isolation
- FAISS + NetworkX memory / Graph RAG
- MCP tool integration
- skill extraction and loop tracking
- multiple user interfaces (Telegram, KG web viewer, QnA bot)

Current repo documents describe the system as **v4.0**, while this file (`pyovis_v5_3.md`) is a **new consolidated technical document** that also includes the implemented and planned **v5.x architecture evolution** referenced in `pyovis_v5_architecture.md` and `pyovis_v5_1.md`.

---

## 2. Hardware and Runtime Environment

Source of truth: `config/unified_node.yaml`, `README.md`, `ARCHITECTURE.md`

### 2.1 Hardware

- **CPU**: AMD Ryzen 9 3900X
- **CPU cores configured in YAML**:
  - interface: `[0, 1]`
  - orchestration: `[2, 3]`
  - ai_inference: `[4, 5, 6, 7]`
- **GPU 0**: RTX 4070 SUPER, 12GB VRAM, `sm_89`, split ratio `0.55`
- **GPU 1**: RTX 3060, 12GB VRAM, `sm_86`, split ratio `0.45`
- **Total VRAM**: 24GB
- **RAM**: 32GB
- **Storage**: ~60GB+ for models and workspace data

### 2.2 GPU model serving strategy

Pyovis uses a **single llama.cpp server** and swaps the active role model rather than keeping all role models resident simultaneously.

Key server settings from `config/unified_node.yaml`:

- **Host**: `0.0.0.0`
- **Port**: `8001`
- **split_mode**: `layer`
- **tensor_split**: `0.55,0.45`
- **default n_gpu_layers**: `60`
- **threads**: `4`
- **warmup_timeout**: `120s`

### 2.3 Configured models

| Role | Model | Size | Context | GPU Layers | Notes |
|---|---|---:|---:|---:|---|
| Planner | `GLM-4.7-Flash-Q4_K_M.gguf` | 18GB | 65536 | 60 | Fallback to Brain |
| Brain | `Qwen3-14B-Q5_K_M.gguf` | 10GB | 40960 | 60 | Core reasoning role |
| Hands | `mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf` | 14GB | 65536 | 40 | Uses `--jinja`; fallback to Brain |
| Judge | `DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf` | 9GB | 65536 | 60 | Independent evaluation role |

Additional role-specific runtime settings:

- **Brain** KV cache: `q4_0`
- **Hands** KV cache: `q8_0`
- **Judge** uses fresh context / cache reset semantics
- Model swap blocks requests while swap is in progress

---

## 3. Top-Level Architecture

### 3.1 High-level flow

```text
User Request
    ↓
SessionManager
    ↓
RequestAnalyzer
    ↓
Route:
  - CHAT / direct answer
  - SIMPLE task
  - COMPLEX task → ResearchLoopController

ResearchLoopController
  PLAN → BUILD → CRITIQUE → EVALUATE
                      ↘ REVISE / ENRICH / ESCALATE ↺

On success:
  final review → optional README generation → memory ingestion / tracking
```

### 3.2 Main layers

1. **Interface layer**
   - Telegram bot
   - KG web viewer
   - QnA bot

2. **Orchestration layer**
   - SessionManager
   - RequestAnalyzer
   - ResearchLoopController / loop controller

3. **AI role layer**
   - Planner
   - Brain
   - Hands
   - Judge / EnhancedJudge
   - ModelSwapManager

4. **Execution layer**
   - CriticRunner
   - WorkspaceManager / FileWriter
   - StaticAnalyzer
   - ExecutionPlan
   - Snapshot / rollback helpers
   - Search/Replace patching

5. **Memory layer**
   - KGStore (FAISS)
   - KnowledgeGraphBuilder (NetworkX)
   - ExperienceDB
   - ConversationMemory
   - UserProfile

6. **Infra / integration layer**
   - MCP client / registry / adapters
   - Skill system
   - Loop tracker
   - Monitoring / watchdog
   - Rust core (`pyovis_core`)

### 3.3 Startup and entrypoints

Pyovis has multiple runtime entrypoints depending on how much of the stack you want to launch.

#### `pyovis/cli.py`

This is the console entrypoint registered in `pyproject.toml` as `pyovis = "pyovis.cli:main"`.

The CLI launcher:

- configures logging
- loads `.env`
- kills stale `llama-server` / launcher processes
- creates `PyPriorityQueue`, `ModelSwapManager`, and `LoopTracker`
- starts `SessionManager`
- starts `TelegramBot`
- starts the KG web viewer on port `8502`
- starts the Brain role server in a background thread
- handles signal-driven shutdown

#### `pyovis/main.py`

This is the smaller async bootstrap path. It creates `PyPriorityQueue`, `ModelSwapManager`, starts the KG server task, creates `LoopTracker`, creates `SessionManager`, and runs the main session loop.

#### `run_unified.py`

This script is a standalone unified launcher variant. It performs process cleanup, creates the core components, starts the Telegram bot, starts the LLM server in the background, and runs the session manager until interrupted.

#### `run_telegram_bot.py`

This is a Telegram-specific launcher that imports and runs the Telegram bot entrypoint directly.

#### `run_qna.py`

This starts the documentation QnA web app and expects the Brain model server to already be live on `localhost:8001`.

---

## 4. Repository Structure

### 4.1 Key directories

```text
/Pyvis/
├── pyovis/
│   ├── ai/
│   ├── orchestration/
│   ├── execution/
│   ├── memory/
│   ├── skill/
│   ├── mcp/
│   ├── tracking/
│   ├── monitoring/
│   └── interface/
├── pyovis_core/
├── config/
├── docker/
├── scripts/
├── tests/
├── qna_bot/
├── README.md
├── ARCHITECTURE.md
├── pyovis_v5_architecture.md
├── pyovis_v5_1.md
└── run_qna.py
```

### 4.2 Key files by responsibility

- `config/unified_node.yaml` — global configuration source
- `pyproject.toml` — Python dependencies and maturin build settings
- `pyovis_core.pyi` — Python stubs for Rust bindings
- `docker/sandbox/Dockerfile` — isolated runtime image
- `run_qna.py` — QnA web app launcher
- `ARCHITECTURE.md` — current architecture reference
- `pyovis_v5_architecture.md`, `pyovis_v5_1.md` — v5.x design documents

---

## 5. AI Role Layer

Directory: `pyovis/ai/`

### 5.1 Planner

Primary purpose:

- decompose complex tasks
- define file-level todo items
- specify pass criteria
- define self-fix scope for Hands

Implementation file:

- `pyovis/ai/planner.py`

Expected output structure described in agent analysis:

- `plan`
- `file_structure`
- `todo_list`
- `pass_criteria`
- `self_fix_scope`

The current Planner implementation explicitly requests **JSON-only** output with those fields.

Behavioral rule from prompt set:

- **tool-first principle** — if the problem can be solved by tools/MCP/fetch instead of code generation, prefer that path

Additional Planner rules verified in the prompt files and implementation:

- file structure must be designed first
- `todo_list` must be ordered by dependency order
- each todo must correspond to exactly one file
- each description must be concrete enough for Hands to implement immediately
- realtime information tasks should prefer `fetch` / MCP tool plans over code generation

The implementation in `planner.py` also performs schema normalization and guardrail logic:

- converts string todo items into structured objects when needed
- auto-fills missing `id` and `file_path`
- strips descriptive suffixes from `file_path` entries such as `"app.py - description"`
- creates fallback `todo_list` when the model returns none
- creates fallback `pass_criteria` when omitted
- normalizes `pass_criteria` keys to strings

Planner prompt rules also define `pass_type` semantics in todo items:

- `exit_only` — successful execution itself is enough
- `output_check` — program output or artifacts must be semantically checked

At runtime, Planner output is consumed directly by the loop controller via:

- `ctx.todo_list`
- `ctx.pass_criteria`
- `ctx.self_fix_scope`

These fields drive build order, Judge evaluation, and the allowed Hands self-fix boundary.

### 5.2 Brain

Primary purpose:

- analyze requests
- review plans
- handle escalations
- synthesize final results
- produce direct answers for simpler flows

Brain is also the role used by the QnA bot against the local OpenAI-compatible endpoint.

Expected Brain responsibilities include:

- plan review and adjustment
- escalation cause classification
- final review / completion response
- markdown/JSON-constrained structured outputs depending on stage

### 5.3 Hands

Primary purpose:

- generate code from the plan
- revise code using Search/Replace blocks
- emit execution hints / execution plan
- optionally declare `pip_packages`

Important conventions from prompts and implementation summary:

- low temperature generation for deterministic code output
- revisions use **Aider-style Search/Replace blocks**
- if precise patching fails, fallback logic avoids destructive rewrites

### 5.4 Judge

Primary purpose:

- evaluate execution output against pass criteria
- classify failures
- return verdict:
  - `PASS`
  - `REVISE`
  - `ENRICH`
  - `ESCALATE`

### 5.5 EnhancedJudge

v5.1 introduces a more explicit evaluation protocol with a **4-step Thought Instruction checklist**:

1. Exit code validation
2. PASS criteria verification
3. Missing symbols detection
4. Error classification

This improves transparency and produces richer evaluation metadata than a single black-box verdict.

### 5.6 Prompt files

Prompt files live under `pyovis/ai/prompts/` and are loaded via `pyovis/ai/prompts/loaders.py`.

Prompt files currently present in the repo:

- `brain_prompt.txt`
- `judge_prompt.txt`
- `planner_prompt.txt`
- `hands_prompt.txt`
- `hands_revise_prompt.txt`
- `planner_behavior.txt`
- `planner_system_v5.3.txt`

These files encode much of the role behavior policy, especially the tool-first planner behavior and the separation between planning, coding, and judging.

### 5.7 Response utilities

`pyovis/ai/response_utils.py` handles parsing and cleanup tasks such as:

- extracting textual message content
- stripping reasoning / CoT blocks such as `<think>...</think>`
- parsing JSON embedded in model output
- summarizing long internal reasoning strings for logging or downstream use

---

## 6. Model Hot-Swap Architecture

Core file: `pyovis/ai/swap_manager.py`

### 6.1 Purpose

`ModelSwapManager` keeps one active llama.cpp server on port `8001` and ensures the correct role model is loaded before inference.

### 6.2 Swap process

Typical swap flow:

1. Check whether requested role is already active
2. Run health check on current server
3. If wrong role or unhealthy:
   - terminate current process (`SIGTERM`, then `SIGKILL` if needed)
   - free the port if necessary
   - start llama server with the target model
   - poll `/health`
   - optionally verify identity with `/props`
4. Log swap outcome to `swap.jsonl`

### 6.3 Role characteristics

- `Planner`, `Brain`, `Hands`, `Judge` are explicit roles
- Hands may require special launch flags (`--jinja`)
- Brain and Judge are treated as more strict / critical roles
- Requests may be blocked during swaps to avoid inconsistent inference state

### 6.4 Logging

Swap history is recorded under:

- `/pyovis_memory/logs/swap.jsonl`

Typical fields include source role, target role, elapsed time, success status, and swap count.

---

## 7. Orchestration Layer

Directory: `pyovis/orchestration/`

### 7.1 SessionManager

Main responsibilities:

- entrypoint for task routing
- integrate MCP tool availability
- enrich requests with graph/context information
- decide whether to answer directly or invoke the loop controller
- optionally ingest conversations/results into memory

Core routing concept:

- **CHAT** → lightweight direct response
- **SIMPLE** → direct or simplified execution path
- **COMPLEX** → full loop execution

SessionManager also exposes tool discovery / suggestion logic such as:

- live MCP tool lookup
- fallback tool suggestions
- keyword-to-tool mappings

### 7.2 RequestAnalyzer

Main responsibilities:

- classify task complexity
- decide whether clarification is needed
- determine required tools
- mark tool availability state

Analysis outputs include:

- complexity (`CHAT`, `SIMPLE`, `COMPLEX` / similar categorization)
- clarification requirements
- required tools
- tool availability / fallback status
- analyzer reasoning

### 7.3 ResearchLoopController / LoopController

This is the central multi-step execution FSM.

#### Loop states

```text
PLAN
BUILD
CRITIQUE
EVALUATE
REVISE
ENRICH
ESCALATE
COMPLETE
```

#### Core lifecycle

1. **PLAN**
   - Planner/Brain produce plan, todo list, criteria, self-fix scope

2. **BUILD**
   - Hands generates code per todo item
   - execution plan and setup commands may be collected
   - current code is persisted

3. **CRITIQUE**
   - CriticRunner executes generated code/tests
   - stdout/stderr/exit_code/error_type are captured

4. **EVALUATE**
   - Judge scores and returns verdict

5. **REVISE / ENRICH**
   - Hands revises code if self-fix is allowed
   - syntax validation / fallback / rollback logic is applied

6. **ESCALATE**
   - Brain determines whether to revise plan or request human escalation

7. **COMPLETE**
   - final review
   - optional README generation
   - skill evaluation / memory ingestion / tracking finalization

#### Safety controls

- `max_loops: 5`
- `max_consecutive_fails: 3`
- `max_escalations: 2` (described in agent findings)
- `pass_threshold: 90`
- `revise_threshold: 70`
- `sandbox_timeout: 30`
- bounded failure reason and reasoning logs to avoid token bloat

### 7.4 ChatChainController

File: `pyovis/orchestration/chat_chain.py`

This controller implements the v5.1 **consensus loop** mechanism instead of treating every disagreement as a normal build/revise iteration.

Implemented segments:

- **Segment A**: Planner ↔ Brain for design agreement
- **Segment B**: Brain ↔ Hands for revision agreement

Key runtime structures in the file include:

- `TerminationReason`
- `ConsensusResult`
- `HardLimitConfig`
- `ChatChainController`

`ChatChainController.consensus_loop(...)` runs a bounded back-and-forth conversation and returns whether agreement was reached, the final content, the exchanged messages, turn count, termination reason, and optional hard-limit trigger metadata.

### 7.5 Hard Limit interruption system

Files:

- `pyovis/orchestration/chat_chain.py`
- `pyovis/orchestration/hard_limit.py`

The Hard Limit system exists to stop unproductive agreement loops.

Implemented trigger families:

1. `diff_too_small` — repeated minimal change / meaningless repetition
2. `ast_error_repeat` — repeated structural code breakage
3. `clarification_loop` — too many clarification cycles
4. `max_turns` — upper bound reached
5. `sycophancy` — invalid code accepted too quickly / blindly

The dedicated `hard_limit.py` module defines:

- `HardLimitTrigger`
- `EscalationAction`
- `TriggerDefinition`
- `HardLimitState`
- `HardLimitResult`
- `HardLimitChecker`

### 7.6 SymbolExtractor

File: `pyovis/orchestration/symbol_extractor.py`

The Symbol Extractor is a v5.1 context-compression feature. It uses Python AST parsing to summarize dependency files before they are sent into the Hands context.

Extracted symbol categories:

- classes
- functions / async functions
- constants

Important structures:

- `ClassSymbol`
- `FunctionSymbol`
- `ConstantSymbol`
- `SymbolSummary`
- `SymbolExtractor`

The module documentation explicitly states the goal is to reduce Hands context from roughly **58K** to **32K** when extraction succeeds.

As of v5.3, `SymbolExtractor` also exposes `extract_graph()` which produces a structured representation of a Python file suitable for ingestion into the `KnowledgeGraphBuilder` code symbol graph:

```python
{
    "module": {"id": "module:<file_path>", "file_path": ..., "language": "python"},
    "symbols": [...],  # id, name, qualified_name, kind, file_path, line, parent, signature
    "edges": [...],    # source, target, relation, line
}
```

---

## 8. Memory Architecture

Directory: `pyovis/memory/`

Pyovis memory is not a single store; it is a set of cooperating subsystems.

### 8.1 KGStore (FAISS-backed vector store)

Implemented in `pyovis/memory/kg_server.py`.

Key characteristics:

- embedding model: `sentence-transformers/all-MiniLM-L6-v2`
- embedding dimension: `384`
- FAISS index type: `IndexFlatL2`
- stores raw documents alongside vector index
- lazy initialization behavior in the server implementation

Storage paths summarized by analysis:

- `/pyovis_memory/kg/faiss.index`
- `/pyovis_memory/kg/documents.txt`

API shape described by analysis:

- add texts
- search by query with top-k results

### 8.2 KnowledgeGraphBuilder

Implemented in `pyovis/memory/graph_builder.py`.

This is the graph-centric RAG component.

Main capabilities:

- extract triplets from text via LLM
- extract concepts/entities
- build persistent graph with nodes and relations
- query neighbors by depth
- detect communities using NetworkX
- summarize communities
- perform Graph RAG queries
- merge graph context with vector-search context
- produce HTML visualizations

Public API (v5.3):

- `add_text(text, source)` — LLM extracts triplets + concepts and inserts into the graph. Called via `asyncio.create_task()` (fire-and-forget)
- `add_document(...)` — chunks long text and calls `add_text` per chunk
- `extract_triplets(...)` — raw LLM extraction, returns list of dicts
- `extract_concepts(...)` — raw LLM extraction, returns list of dicts
- `add_triplet(subject, predicate, object, origin)` — directly insert a semantic triple. Mirrors to Neo4j. Called via `asyncio.create_task()`
- `add_code_symbols(code, file_path, source)` — ingest a Python file into the code symbol graph via `SymbolExtractor.extract_graph()`. Mirrors to Neo4j. Called via `asyncio.create_task()`
- `query_code_symbols(query, depth)` — traverse the code symbol graph
- `query_graph_rag(...)` — includes `code_results` alongside knowledge graph results
- `hybrid_search(...)` — combines FAISS vector results with graph context
- `detect_communities()` — greedy modularity clustering via NetworkX
- `summarize_communities()` — LLM-generated summary per community
- `visualize(output_path, height, width)` — renders an interactive Pyvis HTML graph. Node styling per `node_type`: `semantic`=blue dot (size 18), `module`=orange box (size 16), `code_symbol`=green diamond (size 12, color by `kind`: function=`#3cb44b`, class=`#2dd4bf`, method=`#a3e635`, constant=`#fbbf24`). Edges with `edge_type="code"` are rendered in green (`#3cb44b`, width 2)
- `to_networkx()` — exports both knowledge and code graph nodes/edges as a `networkx.DiGraph`
- `get_stats()` — returns `total_nodes`, `total_edges`, `total_communities`, `total_code_modules`, `total_code_symbols`, `total_code_edges`, `neo4j_enabled`
### 8.3 Hybrid retrieval flow

```text
User Query
   ├─ Vector retrieval via KGStore / FAISS
   └─ Graph retrieval via KnowledgeGraphBuilder
        ├─ entity extraction
        ├─ neighborhood traversal
        ├─ community lookup
        └─ summary aggregation

Merged context
   → injected into downstream reasoning
```

### 8.4 ExperienceDB

Implemented in `pyovis/memory/experience_db.py`.

Purpose:

- store success/failure experiences
- reuse successful patterns
- analyze failure patterns by task type and error type
- support semantic retrieval of past experiences using FAISS

The repo implementation is more than a placeholder. It includes an `ExperienceEntry` data model, semantic indexing of experiences, success/failure pattern retrieval, and task-type-oriented reuse of prior outcomes.

This means ExperienceDB is present in code today, even though broader v5 learning workflows around it are still evolving.

Storage:

- `/pyovis_memory/experience/experience_faiss.index`
- `/pyovis_memory/experience/experience_metadata.json`

### 8.5 ConversationMemory

Implemented in `pyovis/memory/conversation.py`.

Purpose:

- store per-chat/user conversational turns
- retain up to a bounded history (agent analysis: 30 turns / 60 messages)
- filter relevant history by keyword overlap or referential phrases
- format history for prompt injection

Storage:

- `/pyovis_memory/conversations/chat_{id}.json`

### 8.6 UserProfile

Implemented in `pyovis/memory/user_profile.py`.

Purpose:

- learn user preferences from feedback and code patterns
- persist learned preferences
- inject preference hints back into prompts

Storage:

- `/pyovis_memory/profiles/{user_id}.json`

### 8.7 Code Symbol Graph (v5.3)

As of v5.3, `KnowledgeGraphBuilder` is extended with a **code symbol graph** layer that sits alongside the knowledge (triplet) graph.

This layer is populated automatically whenever Hands generates or modifies a Python file. The loop controller calls `kg_builder.add_code_symbols()` via `asyncio.create_task()` immediately after `_save_current_code()` succeeds — non-blocking fire-and-forget so the build loop is not delayed.

#### Graph schema

```text
(:CodeModule  { id, file_path, language, source })
(:CodeSymbol  { id, name, qualified_name, kind, file_path, line, parent, signature, docstring })

(:CodeModule) -[:DEFINES]->     (:CodeSymbol)
(:CodeSymbol) -[:CODE_RELATION { relation, origin, line }]-> (:CodeSymbol)
```

Relation types emitted by `SymbolExtractor.extract_graph()`:

- `inherits` — class inheritance
- `contains` — method / nested function containment
- `calls` — call-graph edge (best-effort, static analysis)
- `uses` — constant / variable reference

#### Query API

```python
result = kg_builder.query_code_symbols(query="DatabaseManager", depth=1)
# returns: {"symbols": [...], "edges": [...], "modules": [...]}
```

`query_graph_rag()` now merges code symbol results alongside the knowledge-graph neighborhood and vector hits.

### 8.8 Neo4j Graph Mirror (v5.3)

File: `pyovis/memory/neo4j_backend.py`

An optional Neo4j mirroring layer that shadows writes from `KnowledgeGraphBuilder` into a running Neo4j instance. The local JSON file remains the **primary source of truth**; Neo4j is used for richer graph queries (Cypher, PageRank, etc.).

#### Activation

Set all four environment variables to enable:

```bash
PYOVIS_NEO4J_URI=bolt://localhost:7687
PYOVIS_NEO4J_USERNAME=neo4j
PYOVIS_NEO4J_PASSWORD=password
PYOVIS_NEO4J_DATABASE=neo4j   # optional, default: neo4j
```

If the `neo4j` Python package is absent or the env vars are unset, the mirror silently disables itself — no error is raised.

#### Neo4j schema

```cypher
(:Entity  { id, name, kind })
(:Module  { id, path, language, source })
(:CodeSymbol { id, name, qualified_name, kind, file_path, line, parent })

(:Entity)     -[:KG_RELATION   { predicate, origin }]->      (:Entity)
(:Module)     -[:DEFINES]->                                   (:CodeSymbol)
(:CodeSymbol) -[:CODE_RELATION { relation, origin, line }]->  (:CodeSymbol)
```

#### Public API

```python
class Neo4jGraphMirror:
    @classmethod
    def from_environment(cls) -> "Neo4jGraphMirror | None": ...
    def mirror_triplet(self, subject, predicate, object_value, origin="") -> None: ...
    def mirror_code_graph(self, module, symbols, edges) -> None: ...
```

---

## 9. Rust Core (`pyovis_core`)

Rust core provides performance-sensitive primitives exposed to Python through PyO3.

### 9.1 Build chain

From `pyproject.toml`:

- build backend: `maturin`
- exported module name: `pyovis_core`
- manifest path: `pyovis_core/Cargo.toml`

Workspace and release optimization from Cargo configuration:

- `opt-level = 3`
- `lto = true`

### 9.2 Rust dependencies

Agent analysis identified these core crates:

- `pyo3`
- `crossbeam`
- `crossbeam-channel`
- `libc`

### 9.3 Priority queue

Rust file: `pyovis_core/src/queue/priority_queue.rs`

Design:

- lock-free / low-lock queue structure
- based on `SegQueue`
- atomic size tracking
- tiered priorities

Priority tiers described by analysis:

- Stop
- Brain
- Hands
- Judge
- Orchestration
- IO

Python-exposed API from `pyovis_core.pyi`:

```python
class PyPriorityQueue:
    def __init__(self) -> None
    def enqueue(self, priority: int, task_type: str, payload: str) -> None
    def dequeue(self) -> Optional[Tuple[int, str, str]]
    def len(self) -> int
    def is_empty(self) -> bool
```

### 9.4 Model hot-swap primitive

Rust file: `pyovis_core/src/model/hot_swap.rs`

Design:

- atomic role state (`u8` enum representation)
- mutex-serialized switching
- sequential consistency semantics for correctness

Python-exposed API from `pyovis_core.pyi`:

```python
class PyModelSwap:
    def __init__(self) -> None
    def switch_to_planner(self) -> Tuple[str, bool]
    def switch_to_brain(self) -> Tuple[str, bool]
    def switch_to_hands(self) -> Tuple[str, bool]
    def switch_to_judge(self) -> Tuple[str, bool]
    def current_role(self) -> str
```

### 9.5 Thread pool

Rust file: `pyovis_core/src/thread_pool/pool.rs`

Design:

- worker pool backed by channels
- Linux CPU affinity via `libc::sched_setaffinity`
- intended to reduce context switching and improve locality

This component exists in Rust but is not the main Python-exposed surface shown by the stub file.

---

## 10. Execution and Sandboxing

Directory: `pyovis/execution/`

### 10.1 CriticRunner

Main executor for generated code.

Responsibilities:

- create isolated execution environment
- auto-detect dependencies from imports
- install dependencies when needed
- run code / tests / CLI / API checks
- classify failures
- return structured execution results

Execution result fields described by analysis include:

- `stdout`
- `stderr`
- `exit_code`
- `execution_time`
- `error_type`

### 10.2 ExecutionPlan

`pyovis/execution/execution_plan.py` defines execution metadata used by Judge/Critic.

Execution types summarized by agent analysis:

- `python_script`
- `python_module`
- `python_test`
- `function_call`
- `api_server`
- `cli_command`

Related structures:

- `ExecutionPlan`
- `TestCase`

Hands can produce execution instructions that guide how CriticRunner evaluates generated output.

### 10.3 WorkspaceManager and FileWriter

Implemented in `file_writer.py`.

Purpose:

- create isolated per-project workspaces
- manage `.venv`
- safely write/read files
- prevent traversal outside project root
- clean up stale projects

Workspace path family:

- `/pyovis_memory/workspace/project_*`

### 10.4 StaticAnalyzer

Implemented in `static_analyzer.py`.

Purpose:

- run `ruff`
- run `mypy`
- optionally auto-fix certain issues
- catch errors before sandbox execution

### 10.5 Snapshot / rollback

Implemented in `snapshot.py`.

Purpose:

- manage git-based snapshots
- restore previous state after failed attempts

### 10.6 Search/Replace parser

Implemented in `search_replace.py`.

Purpose:

- parse Aider-style search/replace blocks
- apply exact/normalized/fuzzy matching strategies
- support incremental file revisions instead of whole-file rewrites

The matching strategy is important to Hands revision behavior:

- **exact match** first
- **whitespace-normalized match** second
- **fuzzy match** last

This reduces unnecessary whole-file regeneration and makes revision loops more robust when the patch output is slightly misaligned with the current file.

### 10.7 Error classification

The execution layer classifies failures into categories such as:

- `type_error`
- `syntax_error`
- `missing_import`
- `name_error`
- `index_error`
- `key_error`
- `value_error`
- `attribute_error`
- `network_error`
- `install_error`
- `env_error`
- `timeout_error`
- `unknown_error`

Agent analysis described this as a 17-type error classification system, with environment and dependency errors separated from ordinary coding failures.

---

## 11. Docker Sandbox

File: `docker/sandbox/Dockerfile`

### 11.1 Base image

- `python:3.11-slim`

### 11.2 System packages

Installed packages include:

- `xvfb`
- `xauth`
- `libgl1`
- `libgl1-mesa-dri`
- `libglib2.0-0`
- `libsm6`
- `libxext6`
- `libxrender1`
- `libx11-6`

These support headless display / OpenGL-capable workloads.

### 11.3 Preinstalled Python packages

From the Dockerfile:

- `requests`
- `pydantic`
- `fastapi`
- `httpx`
- `numpy`
- `pillow`
- `matplotlib`
- `pandas`
- `scipy`
- `pygame`
- `PyOpenGL`
- `PyOpenGL_accelerate`
- `pytest`
- `colorama`
- `click`
- `rich`

### 11.4 User model

- creates non-root user `sandbox` with UID `1000`
- working directory: `/workspace`
- default command: `python`

### 11.5 Runtime sandbox config from YAML

- type: `docker`
- image: `pyovis-sandbox:latest`
- tmpfs path: `/dev/shm/pyovis_sandbox`
- memory limit: `512m`
- CPU limit: `1.0`
- network enabled: `true`

---

## 12. Skill System

Directory: `pyovis/skill/`

### 12.1 SkillManager

Purpose:

- load verified skills relevant to current task description
- evaluate loop outcomes
- request candidate skill drafts when repeated failure patterns appear
- notify for review / promotion workflow

Storage layout summarized by analysis:

```text
/pyovis_memory/skill_library/
  ├── verified/
  └── candidate/
```

Skill files are markdown documents with YAML frontmatter describing metadata such as:

- `id`
- `status`
- `name`
- `category`
- `tags`
- `when_to_use`

### 12.2 SkillValidator

Purpose:

- decide when a repeated issue should become a reusable skill
- reject categories considered not fixable by a skill
- detect duplicate/overlapping skill cases

Described logic includes thresholds like:

- same failure reason repeated multiple times
- sufficient task diversity
- exclusion of environment-only errors

---

## 13. MCP Tool Integration

Directory: `pyovis/mcp/`

### 13.1 MCPClient

Implements JSON-RPC 2.0 communication with MCP servers.

Capabilities summarized by analysis:

- connect over stdio
- initialize protocol session
- list tools
- call tools
- read resources
- track server capabilities

### 13.2 MCPManager

Purpose:

- manage multiple MCP clients
- add/remove servers
- aggregate available tools
- route tool calls to a specific server

### 13.3 MCPToolAdapter

Purpose:

- bridge MCP tools into OpenAI tool/function schema
- expose native tools and MCP tools in a unified calling interface
- execute tool calls produced by an LLM loop

### 13.4 ToolEnabledLLM

Described by the agent analysis as a multi-iteration tool-calling loop:

1. send user/system prompt with tool schema
2. receive tool calls
3. execute them through adapter
4. feed results back as tool messages
5. continue up to configured max iterations
6. return final content

This is the concrete bridge between LLM reasoning and MCP/native tool execution through `MCPToolAdapter`.

### 13.5 ToolRegistry and ToolInstaller

Additional MCP-supporting files exist and should be considered part of the operator surface:

- `pyovis/mcp/tool_registry.py`
- `pyovis/mcp/tool_installer.py`

`ToolRegistry` provides a lightweight in-memory registry of tool records with name, description, and approval requirement metadata.

`ToolInstaller` provides the approval-gated installation result abstraction. In its current form, if `requires_approval=True`, installation returns an approval-required result instead of silently installing.

### 13.6 Registry / installation

Registry explorer supports discovery of official MCP servers like:

- filesystem
- git
- github
- fetch
- memory
- sequential-thinking
- puppeteer

Approval mode is enabled by default in config:

```yaml
mcp:
  requires_approval: true
```

This prevents automatic installation/use of certain external tool servers without approval gating.

---

## 14. Tracking and Monitoring

### 14.1 LoopTracker

Directory: `pyovis/tracking/`

Purpose:

- start task record
- record loop failures and model switch counts
- finalize task metrics
- persist records in JSONL format

Storage:

- `/pyovis_memory/loop_records/YYYY-MM-DD.jsonl`

Tracked fields summarized by agent analysis include:

- task id / description
- started / finished times
- total loops
- total time
- switch count
- escalation flag
- fail reasons with timestamps
- final quality
- skill patch added flag

### 14.2 LogMonitor

Directory: `pyovis/monitoring/`

Purpose:

- record fine-grained loop metrics
- persist to `loop_metrics.jsonl`
- compute statistics such as average duration, average cost, and success rate

Storage:

- `/pyovis_memory/logs/loop_metrics.jsonl`

### 14.3 Watchdog

Purpose:

- continuously health-check the llama server
- auto-restart if unhealthy
- throttle restarts within a time window

Restart strategies described by analysis:

- `systemctl restart`
- `docker-compose restart`
- shell fallback script

### 14.4 HealthMonitor

Purpose:

- monitor disk, memory, CPU usage
- monitor loop cost / error thresholds
- send Telegram alerts when thresholds are exceeded

Monitored thresholds include:

- disk usage %
- memory usage %
- CPU usage %
- loop cost
- error count
- loop iteration duration

---

## 15. Interface Layer

### 15.1 Telegram Bot

Files:

- `pyovis/interface/telegram_bot.py`
- `pyovis/interface/telegram_enhanced.py`
- `run_telegram_bot.py`
- `run_unified.py`

#### Core capabilities

- handle chat requests
- route to SessionManager or direct logic depending on complexity
- track escalations
- split long messages for Telegram’s size limit
- expose operational commands

Commands reported by the agent analysis:

- `/start`
- `/help`
- `/status`
- `/tools`
- `/allow`
- `/deny`

#### Enhanced bot features

- Whisper-based voice transcription
- image analysis through a vision endpoint (LLaVA-style integration)
- code sending / formatting helpers
- progress notifications

`telegram_enhanced.py` is a real implementation layer, not just a placeholder. It adds voice file download + Whisper transcription flow, image file download + vision analysis flow, and richer progress/code-formatting helpers for Telegram delivery.

### 15.2 KG Web Viewer

File:

- `pyovis/interface/kg_web.py`

Stack:

- Starlette
- NetworkX
- HTML graph visualization (pyvis-style output)

Endpoints summarized by analysis:

- `GET /`
- `GET /graph.html`
- `GET /api/stats`
- `POST /api/rebuild`
- `POST /api/detect-communities`
- `GET /api/nodes`
- `GET /api/edges`

Default reported port:

- `8502`

### 15.3 QnA Bot

Files:

- `qna_bot/app.py`
- `qna_bot/context_loader.py`
- `qna_bot/llm_client.py`
- `qna_bot/static/index.html`
- `run_qna.py`

#### Purpose

A lightweight web UI for asking questions specifically about the Pyovis project using the **Brain** model on port `8001`.

#### FastAPI endpoints

Verified directly from `qna_bot/app.py`:

- `GET /` → serves `index.html`
- `POST /api/chat` → SSE streaming response
- `GET /api/health` → LLM and context status
- `GET /api/context` → context metadata and preview

#### Startup behavior

On app startup:

- `load_project_context()` loads project docs into a cached string
- startup logs report total loaded context size

#### Context loading

Verified directly from `qna_bot/context_loader.py`.

Files included by default:

- `README.md`
- `ARCHITECTURE.md`
- `IMPROVEMENTS.md`
- `TASK_TYPES_AND_ROUTING.md`
- `TASK_TYPES_INDEX.md`
- `ISSUE_LIST.md`
- `config/unified_node.yaml`

Also appends a generated Python module tree from `pyovis/`.

Per-file truncation limit:

- `8000` characters

#### LLM integration

Verified directly from `qna_bot/llm_client.py`.

- base URL: `http://localhost:8001`
- endpoint: `/v1/chat/completions`
- model field sent: `local`
- `temperature = 0.7`
- `max_tokens = 4096`
- streaming enabled

#### CoT filtering

`stream_brain_response()` removes `<think>...</think>` blocks **while streaming**, so hidden reasoning is not shown in the UI.

#### Frontend

The static UI uses:

- markdown rendering (`marked.js` per prior analysis)
- syntax highlighting (`highlight.js` per prior analysis)
- dark-themed chat layout
- sample question chips
- live health/context status indicators

#### Launch

Verified directly from `run_qna.py`:

```bash
python run_qna.py
python run_qna.py --host 0.0.0.0 --port 8080
python run_qna.py --reload
```

Precondition:

- Brain model server must already be available on `localhost:8001`

---

## 16. Python Dependencies

Verified from `pyproject.toml`.

### 16.1 Runtime dependencies

- `fastapi>=0.100.0`
- `uvicorn[standard]>=0.23.0`
- `httpx>=0.24.0`
- `pydantic>=2.0.0`
- `pyyaml>=6.0`
- `faiss-cpu>=1.7.4`
- `sentence-transformers>=2.2.0`
- `docker>=6.0.0`
- `uvloop>=0.17.0`
- `numpy>=1.24.0`
- `networkx>=3.0`
- `pandas>=2.0.0`

### 16.2 Development dependencies

- `pytest>=7.0.0`
- `pytest-asyncio>=0.21.0`
- `black>=23.0.0`
- `ruff>=0.1.0`
- `mypy>=1.5.0`

### 16.3 Python version constraint

- `requires-python = ">=3.10"`

---

## 17. Tests and Coverage Surface

Directory: `tests/`

Direct repo scan found **15 Python test files** plus `__init__.py`:

- `test_ai_modules.py`
- `test_chat_chain.py`
- `test_e2e_v5_pipeline.py`
- `test_file_writer.py`
- `test_search_replace.py`
- `test_phase5_integration.py`
- `test_task_classification.py`
- `test_judge_enhanced.py`
- `test_symbol_extractor.py`
- `test_hard_limit.py`
- `test_e2e_loop.py`
- `test_graph_builder.py`
- `test_request_analyzer.py`
- `test_infra_modules.py`
- `__init__.py`

The agent summary reported overall status as approximately:

- **249 / 254 tests passing (~98%)**

Coverage areas described by analysis:

- AI modules and response parsing
- request analysis and routing
- graph builder and memory
- infra modules (MCP, skills, tracking, critic)
- file writer / workspace management
- end-to-end loop behavior
- v5.1 additions: chat chain, hard limit, enhanced judge, symbol extraction, search/replace, v5 pipeline integration

---

## 18. Operational Scripts

Directory: `scripts/`

Agent analysis identified five main scripts:

| Script | Type | Purpose |
|---|---|---|
| `start_model.sh` | Bash | Start/stop/status llama server by role |
| `validate_hardware.sh` | Bash | Validate hardware and role loading |
| `profile_swap.sh` | Bash | Measure swap performance over multiple cycles |
| `e2e_test.py` | Python | End-to-end loop test with real model flow |
| `stress_test.py` | Python | Stability / swap-cycle stress testing |

These scripts support operational verification of model serving, hardware viability, and system stability.

---

## 19. Logging and Storage Layout

Common paths derived from config and analysis:

```text
/pyovis_memory/
├── models/
├── workspace/
├── loop_records/
├── logs/
│   ├── swap.jsonl
│   └── loop_metrics.jsonl
├── kg/
├── experience/
├── conversations/
├── profiles/
├── skill_library/
│   ├── verified/
│   └── candidate/
└── mcp_servers/
```

---

## 20. Security / Reliability Notes

The agent summary from `ISSUE_LIST.md` reported categories such as:

- hardcoded sensitive values / secrets exposure risks
- `eval` / `exec` usage in some utilities
- blocking `time.sleep()` in async contexts
- blocking `input()` patterns in tests or tooling
- bare `except:` and debug-print style cleanup issues

This means the project has substantial architecture implemented, but documentation and audit notes indicate there are still hardening tasks beyond the main core system.

---

## 21. v5.x Evolution Context

Based on `pyovis_v5_architecture.md` and `pyovis_v5_1.md`, the v5 line introduces a more explicit autonomous coding architecture on top of the v4 base.

### 21.1 Major v5.1 themes

- **Chat Chain** consensus loops in two stages:
  - Planner ↔ Brain
  - Brain ↔ Hands
- **Hard Limit** interruption logic to stop unproductive loops
- **Communicative Dehallucination** so Hands asks for clarification/constraints implicitly via process design rather than hallucinating missing details
- **Enhanced Judge** with transparent evaluation checklist
- **Execution Plan** generated by Hands for better downstream evaluation
- **Hands context policy** improvements
- **Graph retrieval upgrades** including FAISS → PageRank style refinement concepts
- **Experience DB** planned as a stronger learning layer

### 21.2 Implementation status snapshot

The design documents describe direction, but the repository now contains a mix of implemented features, stubs, and still-evolving areas.

| Feature | Status in repo | Evidence |
|---|---|---|
| Chat Chain | Implemented | `pyovis/orchestration/chat_chain.py` |
| Hard Limit | Implemented | `pyovis/orchestration/hard_limit.py` |
| Symbol Extractor | Implemented | `pyovis/orchestration/symbol_extractor.py` |
| Enhanced Judge checklist | Implemented | `pyovis/ai/judge_enhanced.py` |
| Execution Plan | Implemented | `pyovis/execution/execution_plan.py` |
| ExperienceDB | Implemented, broader learning flow still evolving | `pyovis/memory/experience_db.py` |
| ToolEnabledLLM / MCP adapter flow | Implemented | `pyovis/mcp/tool_adapter.py` |
| Test generator | Stub / partial | `pyovis/ai/test_generator.py` |
| Parallel generator | Stub / partial | `pyovis/orchestration/parallel_generator.py` |
| Code Symbol Graph + Neo4j mirror | Implemented | `pyovis/memory/graph_builder.py`, `pyovis/memory/neo4j_backend.py` |

One important nuance: some v5.x themes are represented more as prompt/process strategy than as a single standalone runtime module. `Communicative Dehallucination` falls into that category in the current repo state.

### 21.3 Phase status reported by prior analysis

- Phase 1: Rust core — complete
- Phase 2: AI engine — complete
- Phase 3: orchestration / loop features — substantially complete
- Phase 4: enhancements such as ExperienceDB and additional robustness — in progress
- Phase 5: broader interface layer evolution — planned / partially reserved

---

## 22. QnA Bot Appendix (Current Implementation Snapshot)

This repo now contains a dedicated web QnA app.

### 22.1 Runtime structure

```text
Browser
  ↓
FastAPI (`qna_bot/app.py`)
  ├─ load_project_context() on startup
  ├─ /api/chat → stream_brain_response()
  ├─ /api/health
  └─ /api/context

stream_brain_response()
  ↓
httpx streaming POST
  ↓
llama.cpp OpenAI-compatible endpoint on :8001
  ↓
Qwen3 Brain response
  ↓
<think> filtering
  ↓
SSE to browser
```

### 22.2 Key implementation details verified in code

- startup caches the full project context in `_CONTEXT`
- `POST /api/chat` yields SSE messages of the form `data: {"text": ...}`
- `[DONE]` terminates the stream
- health endpoint exposes:
  - `status`
  - `llm_server`
  - `context_loaded`
  - `context_chars`

### 22.3 Why this app exists

It provides a low-friction way to ask documentation- and codebase-grounded questions about Pyovis without going through the full autonomous loop pipeline.

---

## 23. Summary

Pyovis is a **multi-layer local autonomous AI system** that combines:

- role-specialized LLM orchestration
- explicit execution/evaluation loops
- graph + vector memory
- Rust-backed concurrency primitives
- isolated code execution
- MCP tool use
- skill extraction and operational tracking
- multiple interface surfaces

The repository already contains a substantial amount of implemented infrastructure, and the v5.x documents show a clear direction toward a more rigorous, transparent, and self-correcting autonomous development workflow.
