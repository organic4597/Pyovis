# Pyovis v4.0

**Personal AI Assistant & Research Agent** — A local AI system with multi-role orchestration.

## Overview

Pyovis runs 4 specialized LLM roles on dual GPUs with model hot-swapping:

| Role | Model | Purpose | Context |
|------|-------|---------|---------|
| **Planner** | GLM-4.7-Flash 30B | Task decomposition | 64K |
| **Brain** | Qwen3-14B | Review, escalation | 40K |
| **Hands** | Devstral-24B | Code generation | 80K |
| **Judge** | DeepSeek-R1-Distill-14B | Evaluation | 64K |

## Architecture

```
User Request
    |
    v
+------------------+
|   Planner        | <- Task decomposition
+--------+---------+
         |
         v
+------------------+
|    Hands         | <- Code generation
+--------+---------+
         |
         v
+------------------+
|   Critic         | <- Docker sandbox execution
+--------+---------+
         |
         v
+------------------+
|    Judge         | <- PASS / REVISE / ESCALATE
+--------+---------+
         |
    +----+----+
    v         v
 PASS      REVISE/ESCALATE
    |         |
    v         v
 Done    -> Loop (max 5)
```

## Quick Start

```bash
# Start a model role
./scripts/start_model.sh brain    # Load Brain on port 8001
./scripts/start_model.sh hands    # Swap to Hands
./scripts/start_model.sh stop     # Stop server

# Hardware validation
./scripts/validate_hardware.sh all
./scripts/profile_swap.sh 3
python3 scripts/stress_test.py --cycles 3

# Run tests
pytest tests/ -v
cargo test --workspace
```

## Requirements

- **GPU**: 2x NVIDIA GPU with 12GB+ VRAM each (24GB total)
- **RAM**: 32GB
- **Storage**: ~60GB for models
- **CUDA**: 12.x
- **Rust**: 1.70+
- **Python**: 3.12+
- **maturin**: for Rust-Python binding build

## Hardware Validation (RTX 3060 + 4070 SUPER)

| Role | VRAM | Load Time |
|------|------|-----------|
| Planner | 22.4 GB | 72s |
| Brain | 18.5 GB | 27s |
| Hands | 22.2 GB | 27s |
| Judge | 14.3 GB | 19s |

## Project Structure

```
/Pyvis/
+-- pyovis/
|   +-- ai/                 # AI role clients (Planner, Brain, Hands, Judge)
|   +-- orchestration/      # LoopController, SessionManager, RequestAnalyzer
|   +-- execution/          # CriticRunner (Docker sandbox)
|   +-- skill/              # SkillManager, SkillValidator
|   +-- mcp/                # MCPClient, MCPRegistry, ToolAdapter
|   +-- memory/             # KGStore (FAISS), KnowledgeGraphBuilder (Graph RAG)
|   +-- tracking/           # LoopTracker (JSONL cost tracking)
+-- pyovis_core/            # Rust core (priority queue, hot-swap, thread pool)
+-- config/
|   +-- unified_node.yaml   # Per-role configuration
+-- docker/
|   +-- sandbox/Dockerfile  # Critic sandbox image
+-- tests/
|   +-- test_e2e_loop.py         # 23 tests
|   +-- test_ai_modules.py       # 43 tests
|   +-- test_infra_modules.py    # 43 tests
|   +-- test_file_writer.py      # 20 tests
|   +-- test_request_analyzer.py # 14 tests
|   +-- test_graph_builder.py    # 43 tests
+-- ARCHITECTURE.md         # Full architecture + API reference
```

## Implemented Features

1. **Model Hot-Swap**: Single-server, dual-GPU inference with role switching
2. **Self-Evaluation Loop**: PLAN -> BUILD -> CRITIQUE -> EVALUATE -> REVISE
3. **Docker Sandbox**: Isolated code execution with error classification
4. **Skill Library**: Automatic skill extraction and reinforcement
5. **MCP Tools**: Tool installation with approval mode and alternative tool suggestion
6. **Knowledge Graph RAG**: LLM-driven triplet + concept extraction, NetworkX graph, hybrid search
7. **FAISS Vector Store**: Document embedding with persistence (index + documents)
8. **Request Analysis**: Context-aware intent detection with real MCP tool mapping
9. **Rust Core**: Lock-free priority queue, atomic model hot-swap, CPU-affinity thread pool
10. **Graph RAG Integration**: Auto-enrichment of prompts + auto-ingestion of conversations into KG

## Status

- Phase 1-3: Complete (**172 Python tests + 8 Rust tests passing**)
- Rust core: Built and verified (maturin + PyO3)
- Phase 4: Interface layer (Audio/Vision/Telegram) -- reserved

## Documentation

- `ARCHITECTURE.md` -- Full system architecture and API reference
- `Pyvis_v4.md` -- Full design specification
- `IMPROVEMENTS.md` -- Improvement log and next steps
- `TODO.md` / `TODO_kr.md` -- Implementation checklist
