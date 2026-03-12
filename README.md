# Pyovis

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Rust](https://img.shields.io/badge/Rust-PyO3-black?logo=rust)](https://www.rust-lang.org/)
[![CUDA](https://img.shields.io/badge/CUDA-Dual_GPU-76B900?logo=nvidia)](https://developer.nvidia.com/cuda-toolkit)
[![llama.cpp](https://img.shields.io/badge/Inference-llama.cpp-orange)](https://github.com/ggml-org/llama.cpp)

Local multi-role AI agent for research and code execution.

Pyovis orchestrates specialized LLM roles on a single local inference stack, adds a critique and evaluation loop, persists knowledge into graph memory, and integrates external tools through MCP.

## What It Does

- Runs specialized roles for planning, coding, reviewing, and judging.
- Swaps models dynamically on one local inference server.
- Executes code in an isolated sandbox and feeds failures back into the loop.
- Stores conversational and document knowledge into graph memory for retrieval.
- Maps tasks to MCP tools and proposes fallbacks when tools are unavailable.

## Core Roles

| Role | Model | Responsibility | Context |
|------|-------|----------------|---------|
| Planner | GLM-4.7-Flash 30B | Task decomposition and routing | 64K |
| Brain | Qwen3-14B | Review, escalation, synthesis | 40K |
| Hands | Devstral-24B | Code generation and edits | 80K |
| Judge | DeepSeek-R1-Distill-14B | PASS / REVISE / ESCALATE decisions | 64K |

## Request Flow

```text
User Request
    -> Request Analysis
    -> Planner
    -> Hands
    -> Critic Sandbox
    -> Judge
       -> PASS
       -> REVISE
       -> ESCALATE
    -> Knowledge Graph Ingestion
```

## Why This Project Exists

Most local-agent setups stop at prompt routing or a single chat loop. Pyovis is structured as an execution system:

- orchestration for multi-step work
- evaluation for self-correction
- memory for retrieval and accumulation
- tool access for external actions
- native acceleration for queueing and model-swap support

The goal is not just answering questions, but handling iterative work with local infrastructure.

## Key Capabilities

### 1. Multi-role orchestration

The system separates planning, generation, critique, and judging so each stage can use a different model and responsibility boundary.

### 2. Local model hot-swap

Pyovis uses a single llama.cpp server and swaps roles on demand instead of keeping every model loaded at once.

### 3. Critique and revision loop

Generated output is executed in a sandbox, reviewed, and revised up to a bounded number of iterations.

### 4. Knowledge graph memory

Conversation and extracted facts are ingested into graph memory to support Graph RAG enrichment on later requests.

### 5. MCP integration

Tasks can be mapped to real tools, with fallback suggestions when the preferred tool is unavailable.

### 6. Rust-accelerated core

Performance-sensitive pieces such as queueing, swap helpers, and worker infrastructure live in the Rust extension module.

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                         User Input                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    SessionManager                           │
│   RequestAnalyzer -> Graph RAG -> LoopController           │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┼────────────────┐
           ▼               ▼                ▼
      ┌─────────┐   ┌─────────────┐  ┌──────────────┐
      │ Planner │   │    Brain    │  │    Hands     │
      └────┬────┘   └──────┬──────┘  └──────┬───────┘
           │               │                │
           └───────────────┼────────────────┘
                           │
                           ▼
                   ┌───────────────┐
                   │     Judge     │
                   └───────┬───────┘
                           │
               ┌───────────┼───────────┐
               ▼           ▼           ▼
            PASS         REVISE    ESCALATE
               │
               ▼
        Knowledge Graph Memory
```

More detail is in `ARCHITECTURE.md`.

## Hardware Profile

Validated on a dual-GPU local machine.

| Item | Spec |
|------|------|
| GPU 0 | RTX 3060 12GB |
| GPU 1 | RTX 4070 SUPER 12GB |
| Total VRAM | 24GB |
| RAM | 32GB |
| Storage | ~60GB for local models |
| CUDA | 12.x |
| Python | 3.12+ |
| Rust | 1.70+ |

## Quick Start

### 1. Clone the project

```bash
git clone https://github.com/organic4597/Pyovis.git
cd Pyovis
```

### 2. Prepare the external inference dependency

This repository intentionally excludes third-party llama.cpp source snapshots and local GGUF model files.

```bash
git clone https://github.com/ggml-org/llama.cpp /Pyvis/llama.cpp
```

### 3. Start a model role

```bash
./scripts/start_model.sh brain
./scripts/start_model.sh hands
./scripts/start_model.sh stop
```

### 4. Validate the environment

```bash
./scripts/validate_hardware.sh all
./scripts/profile_swap.sh 3
python3 scripts/stress_test.py --cycles 3
```

### 5. Run tests

```bash
pytest tests/ -v
cargo test --workspace
```

## Repository Layout

```text
pyovis/            Python orchestration, AI roles, execution, memory, MCP
pyovis_core/       Rust extension module with PyO3 bindings
config/            Runtime configuration
docker/            Sandbox container assets
tests/             Python and integration tests
scripts/           Server startup, validation, profiling helpers
ARCHITECTURE.md    Detailed architecture reference
```

## Current Status

- Core orchestration pipeline implemented
- Local model swap workflow implemented
- Sandbox critique loop implemented
- Graph memory integration implemented
- MCP tool mapping integrated
- Rust core integrated through PyO3

## Documentation

- `ARCHITECTURE.md` for system structure and component details
- `pyovis_v5_3.md` for the current spec snapshot
- `pyovis_v5_3_ko.md` for the Korean spec snapshot

## Notes For GitHub Distribution

The repository excludes the following on purpose:

- local model binaries
- llama.cpp vendor snapshot
- local secrets and chat identifiers
- archived planning and investigation files
- build artifacts and cache directories

That keeps the public repository focused on the core codebase and reproducible project structure.
