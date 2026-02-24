# PYVIS v4.0
## Personal AI Assistant & Research Agent
### Implementation Design Specification — For Opus 4.6 Implementation

> **Version**: 4.0  
> **Goal**: Jarvis-type Personal AI Assistant + Research Agent  
> **Implementation Lead**: Claude Opus 4.6  
> **Languages**: Rust (performance-critical) + Python (business logic)  
> **Hardware**: RTX 4070 12GB + RTX 3060 12GB | 32GB RAM | NVMe 1TB

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Hardware Specs & Resource Allocation](#2-hardware-specs--resource-allocation)
3. [Model Specs & GPU Placement](#3-model-specs--gpu-placement)
4. [Project Directory Structure](#4-project-directory-structure)
5. [Rust Core Layer](#5-rust-core-layer)
6. [Python Orchestration Layer](#6-python-orchestration-layer)
7. [AI Engine — Brain / Hands / Judge](#7-ai-engine--brain--hands--judge)
8. [Self-Evaluation Loop Design](#8-self-evaluation-loop-design)
9. [Critic Sandbox Execution Engine](#9-critic-sandbox-execution-engine)
10. [Skill Library System](#10-skill-library-system)
11. [Loop Cost Tracking + Selective Skill Reinforcement](#11-loop-cost-tracking--selective-skill-reinforcement)
12. [MCP Autonomous Tool Installation](#12-mcp-autonomous-tool-installation)
13. [Long-Term Memory System](#13-long-term-memory-system)
14. [Interface Layer (Phase 4 Reserved)](#14-interface-layer-phase-4-reserved)
15. [Configuration File](#15-configuration-file)
16. [Implementation Roadmap & Phase-by-Phase Tasks](#16-implementation-roadmap--phase-by-phase-tasks)
17. [System Prompt Definitions](#17-system-prompt-definitions)
18. [Risk Factors & Mitigations](#18-risk-factors--mitigations)

---

## 1. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        PYVIS v4.0                               │
├─────────────────────────────────────────────────────────────────┤
│  Layer 0: Interface (Phase 4 Reserved)                          │
│  ├── Audio Module (STT/TTS) — Whisper                           │
│  ├── Vision Module (Screen Capture)                             │
│  ├── Telegram Bot                                               │
│  └── WebSocket Server                                           │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1: Rust Core (pyvis_core)                                │
│  ├── Lock-Free Task Queue (crossbeam)                           │
│  ├── Thread Pool + CPU Affinity                                  │
│  ├── Priority Handler (P0: STOP / P1: AI / P2: IO)             │
│  ├── Model Hot-Swap Controller                                   │
│  └── Python Bindings (PyO3)                                     │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: Python Orchestration                                  │
│  ├── Session Manager                                            │
│  ├── Research Loop Controller                                   │
│  ├── Tool Executor (MCP)                                        │
│  ├── Skill Manager                                              │
│  ├── Loop Cost Tracker                                          │
│  └── Storage Controller (SoT)                                  │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: AI Engine (llama.cpp)                                 │
│  ├── Planner: GLM-4.7-Flash-30B (Planning Only)                │
│  ├── Brain: Qwen3-14B (Review/Escalation)                       │
│  ├── Hands: Devstral-24B (Builder)                              │
│  └── Judge: R1-Distill-14B (Eval, 512 tokens)                   │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4: Execution Engine                                      │
│  ├── Critic: Docker Sandbox (/dev/shm tmpfs)                   │
│  └── Code Validator                                             │
├─────────────────────────────────────────────────────────────────┤
│  Layer 5: Memory & Storage                                      │
│  ├── FAISS KG (CPU RAM — Hot Memory)                            │
│  └── NVMe SSD /pyvis_memory/ (Cold Storage)                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Hardware Specs & Resource Allocation

### 2.1 Hardware Specs

| Component | Specification | Notes |
|---|---|---|
| GPU 0 (llama Device 0) | NVIDIA RTX 4070 SUPER 12GB | Ada Lovelace, sm_89 |
| GPU 1 (llama Device 1) | NVIDIA RTX 3060 12GB | Ampere, sm_86 |
| Total VRAM | 24GB | **Dual GPU parallel** (split-mode layer) |
| Motherboard | Gigabyte X570 AORUS PRO | PCIe 4.0, x8/x8 |
| PCIe Bandwidth | 16 GB/s unidirectional (per slot) | |
| System RAM | 32GB DDR4/5 | |
| CPU | AMD Ryzen 9 3900X | 6 cores, 12 threads |
| System NVMe | OS + model files | |
| Long-Term Memory NVMe | 1TB dedicated partition | `/pyvis_memory/` |

> **GPU Operation Mode**: 32B Q4_K_S models (~18GB) cannot fit entirely on a single GPU (12GB).
> Two GPUs are used in parallel with `--split-mode layer --tensor-split 0.55,0.45`,
> loading only one model at a time. Brain ↔ Hands/Judge switching requires server restart (swap).

### 2.2 CPU Core Allocation (Based on 6 Cores, 12 Threads)

| Core | Responsibility | Process |
|---|---|---|
| 0~1 | Interface / IO | FastAPI, KG Server, FAISS |
| 2~3 | Orchestration | Loop Controller, Skill Manager, Tracker |
| 4~7 | AI Inference | llama.cpp (Dual GPU, Single Model) |
| 8~11 | System Reserve | OS, Docker, Background |

```yaml
# taskset configuration
interface_cores: "0,1"
orchestration_cores: "2,3"
ai_cores: "4,5,6,7"
llama_threads: 4  # Same as AI core count
```

### 2.3 RAM Allocation

| Item | Capacity | Notes |
|---|---|---|
| OS + Background | ~5.0 GB | |
| Active Model RAM Offload | ~2.0 GB | Most loaded into 24GB VRAM, only remainder offloaded |
| FAISS Hot Memory | ~2.0 GB | Resident in CPU RAM |
| Docker Runtime | ~1.0 GB | Critic Sandbox |
| Rust Core Runtime | ~0.5 GB | |
| Reserve | ~21.5 GB | Buffer (single model structure, not simultaneous) |
| **Total** | **~32 GB** | ✅ |

> The original design planned 16GB RAM usage with two models resident simultaneously,
> but after switching to the dual-GPU parallel architecture, only one model is loaded
> at a time, significantly increasing RAM headroom.

### 2.4 VRAM Allocation (Dual GPU Combined 24GB, Single Model)

**When Active Model is Loaded (e.g., DeepSeek-R1 Q4_K_S ~18GB)**

| GPU | Item | Capacity |
|---|---|---|
| Device 0 (RTX 4070S) | Model Layers 55% | ~9.9 GB |
| Device 0 (RTX 4070S) | KV Cache (Primary) | ~1.5 GB |
| Device 1 (RTX 3060) | Model Layers 45% | ~8.1 GB |
| Device 1 (RTX 3060) | KV Cache (Secondary) | ~1.5 GB |
| **Total** | | **~21.0 GB / 24 GB ✅** |

> ⚠️ Only a single model is loaded, so model switching requires server restart.
> Switching cost: 30~90 seconds for model load (NVMe→VRAM).
> Brain → Hands/Judge switching occurs at least 2 times per loop.
> KV Cache is automatically initialized on server restart.

---

## 3. Model Specs & GPU Placement

### 3.1 Model Information

| Role | Model | HuggingFace ID | Quantization | GPU | Context |
|---|---|---|---|---|---|
| Planner | GLM-4.7-Flash | unsloth/GLM-4.7-Flash-GGUF | Q4_K_M | Dual GPU (0+1) | 80K |
| Brain | Qwen3-14B | Qwen/Qwen3-14B-GGUF | Q5_K_M | Dual GPU (0+1) | 114K |
| Hands | Devstral-24B | bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF | Q4_K_M | Dual GPU (0+1) | 114K |
| Judge | DeepSeek-R1-Distill-Qwen-14B | bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF | Q4_K_M | Dual GPU (0+1) | 80K |

> Brain and Hands/Judge cannot be resident simultaneously. Switching is done via model swap.
> Judge uses the same model as Hands but is always called with fresh context (KV Cache reset).

### 3.2 Role Definitions (Absolute Rules)

| Role | Responsibilities | Must Never Do |
|---|---|---|
| Brain | Task analysis, plan creation, TODO List, PASS criteria, self-fix scope, escalation handling, final review, Skill reinforcement decisions | Generate code directly |
| Hands | Builder persona, code generation based on plan, regeneration upon revision instructions | Plan creation, evaluation |
| Judge | Judge persona, independent evaluation after KV Cache reset, PASS/FAIL/ESCALATE verdict | Code modification, plan changes |
| Critic | Docker sandbox code execution, result collection, report creation | Code modification, evaluation |

### 3.3 llama.cpp Execution (Dual GPU Parallel, Single Model Swap)

**Build (Mixed CUDA Architecture)**
```bash
cmake .. -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES="86;89"
make -j$(nproc)
```

**Unified Server Script (`scripts/start_model.sh`)**

Only one model loaded at a time. Distributed across two GPUs with `split-mode layer`.
Single port 8001. Brain ↔ Hands/Judge switching via server restart.

```bash
# Load Planner model
./scripts/start_model.sh planner

# Load Brain model
./scripts/start_model.sh brain

# Load Hands model (auto-stops existing server before restart)
./scripts/start_model.sh hands

# Load Judge model (auto-stops existing server before restart)
./scripts/start_model.sh judge

# Switch between current model and opposite model
./scripts/start_model.sh swap

# Check status
./scripts/start_model.sh status
```

**Common Server Options**
```bash
taskset -c 4,5,6,7 ./llama-server \
  -m <MODEL_PATH> \
  -ngl 99 \
  --ctx-size <ROLE_CTX> \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --split-mode layer \
  --tensor-split 0.55,0.45 \
  --parallel 1 \
  --threads 4 \
  --port 8001
```

> `--split-mode layer`: Distribute layers across GPUs
> `--tensor-split 0.55,0.45`: 55% to RTX 4070S (Device 0), 45% to RTX 3060 (Device 1)
> `-ngl 99`: Load as many layers as possible to GPU (auto-offload when VRAM limit reached)
> Model switching cost: 30~90 seconds (NVMe→VRAM load)
> KV Cache is automatically initialized on server restart (guarantees Judge fresh context)

### 3.4 CoT Preprocessing (Required for Brain Output)

```python
import re

def strip_cot(text: str) -> str:
    """Remove <think> blocks from Brain output"""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
```

---

## 4. Project Directory Structure

```
pyvis/
├── Cargo.toml                    # Rust workspace
├── pyproject.toml                # Python package
├── config/
│   └── unified_node.yaml         # System-wide configuration
├── pyvis_core/                   # Rust crate (PyO3 bindings)
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs                # PyO3 module entry point
│       ├── queue/
│       │   ├── mod.rs
│       │   └── priority_queue.rs # Lock-Free priority queue
│       ├── thread_pool/
│       │   ├── mod.rs
│       │   └── pool.rs           # CPU Affinity thread pool
│       └── model/
│           ├── mod.rs
│           └── hot_swap.rs       # Model switch controller
├── pyvis/                        # Python package
│   ├── __init__.py
│   ├── main.py                   # Entry point
│   ├── orchestration/
│   │   ├── __init__.py
│   │   ├── session_manager.py    # Session management
│   │   ├── loop_controller.py    # Self-evaluation loop
│   │   └── escalation.py        # Escalation handling
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── brain.py              # Brain interface
│   │   ├── hands.py              # Hands interface
│   │   ├── judge.py              # Judge interface
│   │   └── prompts/
│   │       ├── brain_prompt.txt
│   │       ├── hands_prompt.txt
│   │       └── judge_prompt.txt
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── critic_runner.py      # Docker sandbox
│   │   └── result_parser.py     # Execution result parsing
│   ├── skill/
│   │   ├── __init__.py
│   │   ├── skill_manager.py     # Skill load/save/management
│   │   └── skill_validator.py   # Additional condition verification
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── tool_registry.py     # Installed tool registry
│   │   └── tool_installer.py    # Autonomous installation logic
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── kg_server.py         # FastAPI FAISS KG server
│   │   ├── hot_memory.py        # RAM-resident hot memory
│   │   └── cold_storage.py      # SSD cold memory
│   └── tracking/
│       ├── __init__.py
│       └── loop_tracker.py      # Loop cost tracking
├── system/
│   └── prompts/                 # System prompt files
├── /pyvis_memory/               # NVMe mount point
│   ├── models/                  # GGUF model files
│   ├── user_profile/
│   ├── conversation_log/
│   ├── project_history/
│   ├── knowledge_graph/
│   ├── skill_library/
│   │   ├── verified/            # Verified Skills
│   │   └── candidate/           # Review-pending Skills
│   ├── loop_records/            # Loop cost logs (JSONL)
│   └── research_cache/
└── docker/
    └── sandbox/
        └── Dockerfile           # Critic sandbox image
```

---

## 5. Rust Core Layer

### 5.1 Cargo.toml Dependencies

```toml
[package]
name = "pyvis_core"
version = "0.1.0"
edition = "2021"

[lib]
name = "pyvis_core"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.20", features = ["extension-module"] }
crossbeam = "0.8"
crossbeam-channel = "0.5"
rayon = "1.8"
libc = "0.2"

[profile.release]
opt-level = 3
lto = true
```

### 5.2 Lock-Free Priority Queue (`queue/priority_queue.rs`)

```rust
use crossbeam::queue::SegQueue;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug, Clone, PartialEq)]
pub enum TaskPriority {
    Stop = 0,        // P0: Emergency stop (always highest priority)
    AiBrain = 1,     // P1: Brain inference
    AiHands = 2,     // P2: Hands code generation
    AiJudge = 3,     // P3: Judge evaluation
    Orchestration = 4, // P4: Orchestration
    Io = 5,          // P5: IO operations
}

pub struct PriorityTaskQueue {
    stop_queue: SegQueue<Task>,
    ai_queue: SegQueue<Task>,
    io_queue: SegQueue<Task>,
    total_size: AtomicUsize,
}

impl PriorityTaskQueue {
    pub fn new() -> Self {
        Self {
            stop_queue: SegQueue::new(),
            ai_queue: SegQueue::new(),
            io_queue: SegQueue::new(),
            total_size: AtomicUsize::new(0),
        }
    }

    pub fn enqueue(&self, task: Task) {
        self.total_size.fetch_add(1, Ordering::Relaxed);
        match task.priority {
            TaskPriority::Stop => self.stop_queue.push(task),
            TaskPriority::AiBrain
            | TaskPriority::AiHands
            | TaskPriority::AiJudge
            | TaskPriority::Orchestration => self.ai_queue.push(task),
            TaskPriority::Io => self.io_queue.push(task),
        }
    }

    /// Dequeue in P0 → P1 → P2 priority order
    pub fn dequeue(&self) -> Option<Task> {
        self.stop_queue.pop()
            .or_else(|| self.ai_queue.pop())
            .or_else(|| self.io_queue.pop())
            .map(|task| {
                self.total_size.fetch_sub(1, Ordering::Relaxed);
                task
            })
    }

    pub fn len(&self) -> usize {
        self.total_size.load(Ordering::Relaxed)
    }
}
```

### 5.3 CPU Affinity Thread Pool (`thread_pool/pool.rs`)

```rust
use std::thread;
use std::sync::Arc;
use crossbeam_channel::{bounded, Sender, Receiver};

pub struct ThreadPool {
    workers: Vec<Worker>,
    sender: Sender<Job>,
}

impl ThreadPool {
    /// core_ids: List of CPU cores assigned to this pool
    pub fn new(size: usize, core_ids: Vec<usize>) -> Self {
        let (sender, receiver) = bounded(1024);
        let receiver = Arc::new(receiver);
        let mut workers = Vec::with_capacity(size);

        for (i, core_id) in core_ids.iter().enumerate().take(size) {
            workers.push(Worker::new(i, Arc::clone(&receiver), *core_id));
        }

        ThreadPool { workers, sender }
    }

    pub fn execute<F>(&self, f: F)
    where
        F: FnOnce() + Send + 'static,
    {
        self.sender.send(Box::new(f)).expect("Thread pool send failed");
    }
}

struct Worker {
    id: usize,
    thread: Option<thread::JoinHandle<()>>,
}

impl Worker {
    fn new(id: usize, receiver: Arc<Receiver<Job>>, core_id: usize) -> Worker {
        let thread = thread::spawn(move || {
            // CPU Affinity setup (Linux)
            #[cfg(target_os = "linux")]
            {
                let mut cpuset = libc::cpu_set_t::default();
                unsafe {
                    libc::CPU_SET(core_id, &mut cpuset);
                    libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &cpuset);
                }
            }

            loop {
                match receiver.recv() {
                    Ok(job) => job(),
                    Err(_) => break,
                }
            }
        });

        Worker { id, thread: Some(thread) }
    }
}

type Job = Box<dyn FnOnce() + Send + 'static>;
```

### 5.4 Model Hot-Swap Controller (`model/hot_swap.rs`)

```rust
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Mutex;

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ModelRole {
    Brain = 0,
    Hands = 1,
    Judge = 2,
}

pub struct ModelHotSwap {
    current_role: AtomicU8,
    switch_lock: Mutex<()>,
}

impl ModelHotSwap {
    pub fn new() -> Self {
        Self {
            current_role: AtomicU8::new(ModelRole::Brain as u8),
            switch_lock: Mutex::new(()),
        }
    }

    /// Switch role. Returns KV Cache reset signal when switching to Judge
    pub fn switch_role(&self, new_role: ModelRole) -> SwitchResult {
        let _lock = self.switch_lock.lock().unwrap();
        let prev = self.current_role.swap(new_role as u8, Ordering::SeqCst);

        SwitchResult {
            previous_role: ModelRole::from(prev),
            new_role,
            requires_kv_reset: new_role == ModelRole::Judge,
        }
    }

    pub fn current_role(&self) -> ModelRole {
        ModelRole::from(self.current_role.load(Ordering::SeqCst))
    }
}

pub struct SwitchResult {
    pub previous_role: ModelRole,
    pub new_role: ModelRole,
    pub requires_kv_reset: bool,  // True when switching to Judge
}
```

### 5.5 PyO3 Bindings (`lib.rs`)

```rust
use pyo3::prelude::*;

mod queue;
mod thread_pool;
mod model;

use queue::priority_queue::{PriorityTaskQueue, TaskPriority};
use model::hot_swap::{ModelHotSwap, ModelRole};

#[pyclass]
struct PyPriorityQueue {
    inner: PriorityTaskQueue,
}

#[pymethods]
impl PyPriorityQueue {
    #[new]
    fn new() -> Self {
        Self { inner: PriorityTaskQueue::new() }
    }

    fn enqueue(&self, task_type: &str, payload: &str) {
        // Submit task from Python
    }

    fn dequeue(&self) -> Option<String> {
        // Return task to Python
        None
    }

    fn len(&self) -> usize {
        self.inner.len()
    }
}

#[pyclass]
struct PyModelSwap {
    inner: ModelHotSwap,
}

#[pymethods]
impl PyModelSwap {
    #[new]
    fn new() -> Self {
        Self { inner: ModelHotSwap::new() }
    }

    fn switch_to_brain(&self) -> bool {
        let result = self.inner.switch_role(ModelRole::Brain);
        result.requires_kv_reset
    }

    fn switch_to_hands(&self) -> bool {
        let result = self.inner.switch_role(ModelRole::Hands);
        result.requires_kv_reset
    }

    fn switch_to_judge(&self) -> bool {
        // Judge switch always requires KV Cache reset
        let result = self.inner.switch_role(ModelRole::Judge);
        result.requires_kv_reset  // Always True
    }

    fn current_role(&self) -> String {
        format!("{:?}", self.inner.current_role())
    }
}

#[pymodule]
fn pyvis_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyPriorityQueue>()?;
    m.add_class::<PyModelSwap>()?;
    Ok(())
}
```
---

## 6. Python Orchestration Layer

### 6.1 Main Entry Point (`pyvis/main.py`)

```python
import asyncio
import uvloop
from pyvis.orchestration.session_manager import SessionManager
from pyvis.memory.kg_server import start_kg_server
from pyvis.tracking.loop_tracker import LoopTracker
import pyvis_core  # Rust bindings

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

async def main():
    # Initialize Rust core
    task_queue = pyvis_core.PyPriorityQueue()
    model_swap = pyvis_core.PyModelSwap()

    # Start KG server (isolated on cores 0,1)
    kg_task = asyncio.create_task(start_kg_server())

    # Initialize loop tracker
    tracker = LoopTracker()

    # Start session manager
    session = SessionManager(task_queue, model_swap, tracker)
    await session.run()

if __name__ == "__main__":
    uvloop.run(main())
```

### 6.2 Loop Controller (`orchestration/loop_controller.py`)

```python
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class LoopStep(Enum):
    PLAN      = "plan"
    BUILD     = "build"
    CRITIQUE  = "critique"
    EVALUATE  = "evaluate"
    REVISE    = "revise"
    ENRICH    = "enrich"
    COMPLETE  = "complete"
    ESCALATE  = "escalate"

class JudgeVerdict(Enum):
    PASS      = "PASS"       # Score 90 or above
    REVISE    = "REVISE"     # Score 70~90
    ENRICH    = "ENRICH"     # Score below 70
    ESCALATE  = "ESCALATE"   # Unable to judge or exceeded N attempts

@dataclass
class LoopContext:
    task_id: str
    task_description: str
    plan: Optional[str] = None
    todo_list: list = field(default_factory=list)
    pass_criteria: dict = field(default_factory=dict)
    self_fix_scope: dict = field(default_factory=dict)
    current_task_index: int = 0
    loop_count: int = 0
    max_loops: int = 5           # Default, configurable via config
    consecutive_fails: int = 0
    max_consecutive_fails: int = 3
    fail_reasons: list = field(default_factory=list)
    current_step: LoopStep = LoopStep.PLAN
    score: int = 0

class ResearchLoopController:
    def __init__(self, brain, hands, judge, critic, tracker, skill_manager):
        self.brain = brain
        self.hands = hands
        self.judge = judge
        self.critic = critic
        self.tracker = tracker
        self.skill_manager = skill_manager

    async def run(self, ctx: LoopContext) -> dict:
        """
        Main loop.
        Brain only appears at the beginning (PLAN) and end (COMPLETE/ESCALATE).
        Intermediate loops are handled autonomously by Hands + Critic + Judge.
        """
        self.tracker.start(ctx.task_id, ctx.task_description)

        while ctx.current_step != LoopStep.COMPLETE:

            # ── PLAN: Brain call (first time only) ──────────────────────
            if ctx.current_step == LoopStep.PLAN:
                plan_output = await self.brain.plan(ctx)
                ctx.plan = plan_output["plan"]
                ctx.todo_list = plan_output["todo_list"]
                ctx.pass_criteria = plan_output["pass_criteria"]
                ctx.self_fix_scope = plan_output["self_fix_scope"]
                ctx.current_step = LoopStep.BUILD
                # Brain → Hands switch (1 time)
                self.tracker.record_switch("brain_to_hands")

            # ── BUILD: Hands code generation ───────────────────────────
            elif ctx.current_step == LoopStep.BUILD:
                current_task = ctx.todo_list[ctx.current_task_index]
                skill_context = self.skill_manager.load_verified(ctx.task_description)
                code = await self.hands.build(current_task, ctx.plan, skill_context)
                ctx.current_code = code
                ctx.current_step = LoopStep.CRITIQUE

            # ── CRITIQUE: Critic execution ────────────────────────────
            elif ctx.current_step == LoopStep.CRITIQUE:
                result = await self.critic.execute(ctx.current_code)
                ctx.critic_result = result
                ctx.current_step = LoopStep.EVALUATE

            # ── EVALUATE: Judge evaluation (after KV Cache reset) ────
            elif ctx.current_step == LoopStep.EVALUATE:
                verdict = await self.judge.evaluate(
                    task=ctx.todo_list[ctx.current_task_index],
                    pass_criteria=ctx.pass_criteria,
                    critic_result=ctx.critic_result,
                    loop_count=ctx.loop_count
                )
                ctx.score = verdict.score
                ctx.loop_count += 1

                if verdict.verdict == JudgeVerdict.PASS:
                    ctx.current_task_index += 1
                    ctx.consecutive_fails = 0
                    if ctx.current_task_index >= len(ctx.todo_list):
                        ctx.current_step = LoopStep.COMPLETE
                    else:
                        ctx.current_step = LoopStep.BUILD

                elif verdict.verdict == JudgeVerdict.REVISE:
                    ctx.consecutive_fails += 1
                    ctx.fail_reasons.append(verdict.reason)
                    ctx.current_step = self._check_escalation(ctx)

                elif verdict.verdict == JudgeVerdict.ENRICH:
                    ctx.consecutive_fails += 1
                    ctx.fail_reasons.append(verdict.reason)
                    ctx.current_step = self._check_escalation(ctx)

                elif verdict.verdict == JudgeVerdict.ESCALATE:
                    ctx.current_step = LoopStep.ESCALATE

            # ── REVISE/ENRICH: Hands autonomous revision ──────────────
            elif ctx.current_step in (LoopStep.REVISE, LoopStep.ENRICH):
                # Hands revises without Brain involvement
                current_task = ctx.todo_list[ctx.current_task_index]
                can_self_fix = self._can_self_fix(ctx)

                if can_self_fix:
                    code = await self.hands.revise(
                        current_task, ctx.current_code,
                        ctx.critic_result, ctx.self_fix_scope
                    )
                    ctx.current_code = code
                    ctx.current_step = LoopStep.CRITIQUE
                else:
                    ctx.current_step = LoopStep.ESCALATE

            # ── ESCALATE: Brain re-invocation ───────────────────────────
            elif ctx.current_step == LoopStep.ESCALATE:
                if ctx.loop_count >= ctx.max_loops:
                    # Report to human
                    return self._human_escalation(ctx)

                # Brain classifies the cause and revises the plan
                escalation_result = await self.brain.handle_escalation(ctx)
                if escalation_result["action"] == "revise_plan":
                    ctx.plan = escalation_result["new_plan"]
                    ctx.todo_list = escalation_result["new_todo"]
                    ctx.pass_criteria = escalation_result["new_criteria"]
                    ctx.consecutive_fails = 0
                    ctx.current_step = LoopStep.BUILD
                else:
                    return self._human_escalation(ctx)

        # ── COMPLETE: Brain final review ────────────────────────────
        # Hands/Judge → Brain switch (1 time)
        self.tracker.record_switch("hands_to_brain")
        final_result = await self.brain.final_review(ctx)

        # Save loop record + Skill reinforcement decision
        self.tracker.finish(ctx, final_result)
        await self.skill_manager.evaluate_and_patch(ctx, self.tracker.get_record(ctx.task_id))

        return final_result

    def _check_escalation(self, ctx: LoopContext) -> LoopStep:
        if ctx.consecutive_fails >= ctx.max_consecutive_fails:
            return LoopStep.ESCALATE
        if ctx.loop_count >= ctx.max_loops:
            return LoopStep.ESCALATE
        return LoopStep.REVISE

    def _can_self_fix(self, ctx: LoopContext) -> bool:
        """Check if the issue is within the self-fix scope"""
        error_type = ctx.critic_result.get("error_type", "")
        return error_type in ctx.self_fix_scope.get("allowed", [])

    def _human_escalation(self, ctx: LoopContext) -> dict:
        return {
            "status": "escalated",
            "task_id": ctx.task_id,
            "loop_count": ctx.loop_count,
            "fail_reasons": ctx.fail_reasons,
            "message": "Unable to resolve automatically. Human judgment is required."
        }
```

---

## 7. AI Engine — Brain / Hands / Judge

### 7.1 Brain (`ai/brain.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt
from pyvis.utils import stri[CORRUPTED]port json

BRAIN_API = "http://localhost:8001/v1/chat/completions"

class Brain:
    def __init__(self):
        self.system_prompt = load_prompt("brain_prompt.txt")
        self.client = httpx.AsyncClient(timeout=120.0)

    async def plan(self, ctx) -> dict:
        """
        Brain initial output:
        1. Plan document
        2. TODO List
        3. PASS criteria per Task
        4. Self-fix scope (items Hands can autonomously fix)
        """
        user_message = f"""
Task: {ctx.task_descripti[CORRUPTED] You must respond only in the following JSON format:
{{
  "plan": "Overall architecture and implementation plan (Markdown)",
  "todo_list": [
    {{"id": 1, "title": "Task title", "description": "Detailed description"}}
  ],
  "pass_criteria": {{
    "1": ["condition1", "condition2"],
    "2": ["condition1"]
  }},
  "self_fix_scope": {{
    "allowed": ["type_error", "syntax_error", "missing_import"],
    "escalate": ["architecture_change", "schema_change"]
  }}
}}
"""
        response = await self._call(user_message)
     [CORRUPTED]lean = strip_cot(response)
        return json.loads(clean)

    async def handle_escalation(self, ctx) -> dict:
        """Escalation cause analysis and plan revision"""
        user_message = f"""
Original plan: {ctx.plan}
Failure cause list: {json.dumps(ctx.fail_reasons, ensure_ascii=False)}
Loop count: {ctx.loop_count}
Last error: {ctx.critic_result.get('stderr', '')}

Classify the cause and respond in the following format:
{{
  "cause_type": "plan_error | implementation_error | environmen[CORRUPTED]error",
  "action": "revise_plan | human_escalation",
  "analysis": "Analysis content",
  "new_plan": "Revised plan (when action is revise_plan)",
  "new_todo": [...],
  "new_criteria": {{...}}
}}
"""
        response = await self._call(user_message)
        clean = strip_cot(response)
        return json.loads(clean)

    async def final_review(self, ctx) -> dict:
        """Final review"""
        response = await self._call(
            f"Review and summarize the final deliverables for the following task: {ctx.task_description}"
        )
        return {"status": "complete", "review": strip_cot(response)}

    async def _call(self, user_message: str) -> str:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.7,
            "max_tokens": 4096
        }
        resp = await self.client.post(BRAIN_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]
```

### 7.2 Hands (`ai/hands.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt

HANDS_API = "http://localhost:8002/v1/chat/completions"

class Hands:
    def __init__(self):
        self.system_prompt = load_prompt("hands_prompt.txt")
        self.client = httpx.AsyncClient(timeout=180.0)

    async def build(self, task: dict, plan: str, skill_context: str) -> str:
        """Code generation based on plan"""
        user_messe[CORRUPTED] = f"""
Full plan:
{plan}

Current Task to implement:
{task['title']}: {task['description']}

Skill rules to apply:
{skill_context}

Implement only the code corresponding to the current Task in the plan above.
"""
        return await self._call(user_message)

    async def revise(self, task: dict, prev_code: str,
                     critic_result: dict, self_fix_scope: dict) -> str:
        """Code regeneration based on revision instructions"""
        user_message = f"""
Task: {task['title']}
Previous code:
{prev_cod[CORRUPTED]

Execution error:
{critic_result.get('stderr', 'None')}

Standard output:
{critic_result.get('stdout', 'None')}

Allowed self-fix scope: {self_fix_scope.get('allowed', [])}
Fix the above error. Changes outside the allowed scope are prohibited.
"""
        return await self._call(user_message)

    async def _call(self, user_message: str) -> str:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role[CORRUPTED]content": user_message}
            ],
            "temperature": 0.2,
            "max_tokens": 8192
        }
        resp = await self.client.post(HANDS_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]
```

### 7.3 Judge (`ai/judge.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt
from dataclasses import dataclass
import json
import re

JUDGE_API = "http://localhost:8002/v1/chat/completions"

@dataclass
class JudgeResult:
    verdict: str      # PASS / REVISE / ENRICH / ESCALATE
    score: int        # 0~100
    reason: str
    error_type: str   # For determining Hands autonomous fix eligibility

class Judge:
    def __init__(self):
        self.system_prompt = load_prompt("judge_prompt.txt")
        self.client = httpx.AsyncClient(timeout=60.0)

    async def evaluate(self, task: dict, pass_criteria: dict,
                       critic_result: dict, loop_count: int) -> JudgeResult:
        """
        Key point: No previous conversation history. Fresh context every time.
        [CORRUPTED]       Does not include Hands' code or thought process.
        Judges solely based on plan requirements + execution results.
        """
        criteria = pass_criteria.get(str(task["id"]), [])

        user_message = f"""
Task: {task['title']}
PASS criteria:
{chr(10).join(f'- {c}' for c in criteria)}

Execution results:
- Exit code: {critic_result.get('exit_code', -1)}
- Execution time: {critic_result.get('execution_time', 0):.2f} seconds
- Standard output: {critic_result.get('stdout', 'None')[:500]}
- Error: [CORRUPTED]lt.get('stderr', 'None')[:500]}
- Current loop count: {loop_count}

If all PASS criteria are met, verdict is PASS.
If partially unmet, REVISE (score 70 or above) or ENRICH (score below 70).
If unable to judge or repeated failures, ESCALATE.

You must respond only in the following JSON format:
{{"verdict": "PASS|REVISE|ENRICH|ESCALATE", "score": 0-100,
  "reason": "Judgment basis", "error_type": "Error type (null if none)"}}
"""
        # Call with fresh context, no previous conversation
        response = await sel[CORRUPTED]_call_fresh(user_message)
        return self._parse(response)

    async def _call_fresh(self, user_message: str) -> str:
        """Fresh context every time — no previous conversation history"""
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message}
            ],
            "temperature": 0.1,
            "max_tokens": 512
        }
        resp = await se[CORRUPTED]lient.post(JUDGE_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]

    def _parse(self, response: str) -> JudgeResult:
        try:
            data = json.loads(re.sub(r'```json|```', '', response).strip())
            return JudgeResult(
                verdict=data["verdict"],
                score=int(data["score"]),
                reason=data["reason"],
                error_type=data.get("error_type")
            )
        except Exception:
            return JudgeResult(verdict="ESCALATE", score=0,
                               reason="Failed to parse Judge response", error_type=None)
```

---

## 8. Self-Evaluation Loop Design

### 8.1 Overall Flow Diagram

```
Human: Task Input
        │
        ▼
[Brain] Plan + TODO + PASS Criteria + Fix Scope (1 time)
        │
        ▼ ← Switch 1 time (Brain → Hands/Judge)
        │
┌──────────────────────────────────────[CORRUPTED]───┐
│           Autonomous Loop (No Brain)               │
│                                                    │
│  For each Task in TODO:                            │
│                                                    │
│  [Hands/Builder] Generate Task N code              │
│         │                                          │
│         ▼                                          │
│  [Critic] Docker Sandbox Execution                 │
│         │
│         ▼                                          │
│  [Judge] Memory Reset → Compare against PASS criteria │
│         │                                          │
│   ┌─────┼──────────┬──────────┐                    │
│  PASS  REVISE   ENRICH   ESCALATE                  │
│   │     │         │          │                     │
│  Next  Self-fix? Self-fix?  Call Brain             │
│  Task  ┌─┴──┐    ┌─┴[CORRUPTED]   │               │
│       Yes  No   Yes  No   (Exception switch)       │
│       │    │    │    │                              │
│     Regen ESC  Regen ESC                           │
│                                                    │
│  All Tasks PASS → Loop Exit                        │
└────────────────────────────────────────────────────┘
        │
        ▼ ← Switch 1 time (Hands/Judge → Brain)
        │
[Brain] Final Review + Loop Record Analysis + Skill Reinforcement Decision
        │
        ▼
Long-Term Memory Storage → Deliver to Human

Total model switches: Minimum 2 fixed (+ 1 per escalation)
```

### 8.2 Scoring Criteria

| Score | Verdict | Action |
|---|---|---|
| 90~100 | PASS | Proceed to next Task |
| 70~89 | REVISE | Hands autonomous fix (within fix scope) |
| 0~69 | ENRICH | Hands autonomous fix (within scope) or Brain escalation |
| - | ESCE[CORRUPTED] | Brain re-invocation |

### 8.3 Escalation Conditions

| Condition | Criteria | Action |
|---|---|---|
| Consecutive Failures | Same Task fails 3 times consecutively | Call Brain → Cause classification |
| Max Loops | Total loops exceed 5 | Report to human |
| Unable to Judge | Judge ESCALATE | Call Brain |
| Fix Scope Exceeded | Architecture change deemed necessary | Call Brain |
---

## 9. Critic Sandbox Execution Engine

### 9.1 CriticRunner (`execution/critic_runner.py`)

```python
import docker
import temp[CORRUPTED]import os
import time
from dataclasses import dataclass

@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_type: str = None  # For Judge's autonomous fix decision

class CriticRunner:
    SANDBOX_PATH = "/dev/shm/pyvis_sandbox"
    ERROR_PATTERNS = {
        "type_error":       "TypeError",
        "syntax_error":     "SyntaxError",
        "missing_import":   "ModuleNotFoundError",
        "name_error":       "NameError",
        "index_error":      "IndexError",
        "key_error":        "KeyError",
        "value_error":      "ValueError",
        "attribute_error":  "AttributeError",
    }

    def __init__(self):
        self.client = docker.from_env()
        os.makedirs(self.SANDBOX_PATH, exist_ok=True)

    async def execute(self, code: str,
                      timeout: int = 30,
                      allow_network: bool = False) -> ExecutionResult:
        """
        Execute code in Docker sandbox.
        Uses /dev/shm tmpfs to minimize disk I/O.
        """
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py',
            dir=self.SANDBOX_PATH, delete=False
        ) as f:
            f.write(code)
            temp_file = f.name

        start_time = time.time()
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                f"python /workspace/{os.path.basename(temp_file)}",
                volumes={self.SANDBOX_PATH: {'bind': '/workspace'}},
                network_mode="none" if not allow_network else "bridge",
                mem_limit="512m",
                cpu_quota=100000,   # 1 CPU core
                timeout=timeout,
                remove=True,
                stdout=True,
                stderr=True
            )
            elapsed = time.time() - start_time
            output = container.decode() if isinstance(container, bytes) else str(container)
            return ExecutionResult(
                stdout=output, stderr="",
            exit_code=0, execution_time=elapsed
            )

        except docker.errors.ContainerError as e:
            elapsed = time.time() - start_time
            stderr = e.stderr.decode() if e.stderr else str(e)
            return ExecutionResult(
                stdout="", stderr=stderr,
                exit_code=e.exit_status,
                execution_time=elapsed,
                error_type=self._classify_error(stderr)
            )

        except docker.errors.APIError as e:
            return ExecutionResult(
                stdout="", stderr=str(e),
                exit_code=-1, execution_time=0,
                error_type="docker_error"
            )
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def _classify_error(self, stderr: str) -> str:
        """Classify error type — used for determining whether Hands can autonomously fix"""
        for error_type, pattern in self.ERROR_PATTERNS.items():
            if pattern in stderr:
                re[CORRUPTED] error_type
        return "unknown_error"

    def format_report(self, result: ExecutionResult, task_title: str,
                      loop_count: int) -> str:
        return f"""## Execution Result Report
- Task: {task_title}
- Loop iteration: {loop_count}
- Exit code: {result.exit_code} ({'normal' if result.exit_code == 0 else 'abnormal'})
- Execution time: {result.execution_time:.2f}s
- Error type: {result.error_type or 'none'}
- Stdout: {result.stdout[:500] or 'none'}
- Stderr[CORRUPTED]: {result.stderr[:500] or 'none'}"""
```

### 9.2 Docker Sandbox Image (`docker/sandbox/Dockerfile`)

```dockerfile
FROM python:3.11-slim

# Security: remove root privileges
RUN useradd -m -u 1000 sandbox
WORKDIR /workspace
USER sandbox

# Install only essential packages
RUN pip install --no-cache-dir \
    requests \
    pydantic \
    fastapi \
    httpx

# Execution time limit
CMD ["python"]
```

---

## 10. Skill Library System

### 10.1 Skill File Format

```markdown
---
id: skill_001[CORRUPTED]FastAPI Type Safety
status: verified          # verified | candidate
category: web_backend
created_at: 2025-XX-XX
source_task_ids: [001, 003, 007]
fail_count: 4             # Number of times a task failed due to lacking this Skill
reviewed_by: human        # human | auto
---

## Application Conditions
Always apply when implementing FastAPI endpoints

## Rules
- Specify type hints on all function parameters
- Use explicit casting for inputs where int/str mixing is possible
- Use Pydantic BaseModel for request/response schemas
- Use Optional[T] = None format for optional parameters

## Application Example
\```python
from pydantic import BaseModel
from typing import Optional

class UserRequest(BaseModel):
    user_id: int
    name: str
    email: Optional[str] = None
\```

## Prohibited Patterns
- Function parameters without type hints
- Handling requests/responses with raw dict
```

### 10.2 SkillManager (`skill/skill_manager.py`)

```python
import os
import yaml
from pathlib import Path
from typing import Optional

SKILL_BASE = Path("/pyvis_memory/skill_library")
VE[CORRUPTED]RIFIED_DIR = SKILL_BASE / "verified"
CANDIDATE_DIR = SKILL_BASE / "candidate"

class SkillManager:

    def load_verified(self, task_description: str) -> str:
        """
        Load only verified Skills and insert into prompt.
        Candidate skills are not used.
        """
        relevant = self._find_relevant(task_description, status="verified")
        if not relevant:
            return "# No applicable Skill found"
        return "\n\n".join(skill["content"] for skill in relevant)

    [CORRUPTED]def _find_relevant(self, task_description: str, status: str) -> list:
        """Keyword-based relevant Skill search (can be upgraded to FAISS embedding search later)"""
        skill_dir = VERIFIED_DIR if status == "verified" else CANDIDATE_DIR
        results = []
        for skill_file in skill_dir.glob("*.md"):
            with open(skill_file) as f:
                content = f.read()
            # Simple keyword matching (initial implementation)
            if any(kw.lower() in task_description.lower()
               for kw in self._extract_keywords(content)):
                results.append({"file": skill_file.name, "content": content})
        return results

    def _extract_keywords(self, skill_content: str) -> list:
        """Extract keywords from Skill file's category and name"""
        keywords = []
        for line in skill_content.split('\n')[:20]:
            if 'category:' in line:
                keywords.extend(line.split(':')[1].strip().split('_'))
            if 'name:' in line:
                k[CORRUPTED]keywords.extend(line.split(':')[1].strip().split())
        return keywords

    async def evaluate_and_patch(self, ctx, loop_record: dict):
        """After loop completion, determine whether a Skill needs to be added"""
        from pyvis.skill.skill_validator import SkillValidator
        validator = SkillValidator()
        needs_skill = validator.should_add_skill(loop_record, self._get_history())
        if needs_skill:
            await self._create_candidate(loop_record)

    def _get_history(self) -> list:
        """Recent [CORRUPTED]       records_dir = Path("/pyvis_memory/loop_records")
        records = []
        for f in sorted(records_dir.glob("*.jsonl"))[-50:]:  # Last 50 records
            with open(f) as fh:
                import json
                for line in fh:
                    records.append(json.loads(line))
        return records

    async def _create_candidate(self, loop_record: dict):
        """Brain drafts Skill and saves to candidate"""
        # Request Skill draft from Brain
        skill_dra[CORRUPTED]ft = await self._request_skill_draft(loop_record)
        candidate_path = CANDIDATE_DIR / f"skill_{loop_record['task_id']}.md"
        with open(candidate_path, 'w') as f:
            f.write(skill_draft)
        # Notify human for review
        self._notify_review_needed(candidate_path)
```

---

## 11. Loop Cost Tracking + Selective Skill Reinforcement

### 11.1 LoopTracker (`tracking/loop_tracker.py`)

```python
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict[CORRUPTED], field
from datetime import datetime

RECORDS_DIR = Path("/pyvis_memory/loop_records")

@dataclass
class LoopRecord:
    task_id: str
    task_description: str
    task_category: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_loops: int = 0
    total_time_sec: float = 0.0
    switch_count: int = 0          # Model switch count
    escalated: bool = False
    fail_reasons: list = field(default_factory=list)
    final_quality: str = ""        # PASS | ESCALATED
    skill_patch_added: bool = False

class Loop[CORRUPTED]Tracker:
    def __init__(self):
        self._records: dict[str, LoopRecord] = {}
        self._start_times: dict[str, float] = {}
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    def start(self, task_id: str, task_description: str):
        self._records[task_id] = LoopRecord(
            task_id=task_id,
            task_description=task_description,
            started_at=datetime.now().isoformat()
        )
        self._start_times[task_id] = time.time()

    def record_switch(self, switch_type: str, task_id: str = None):
        if task_id and task_id in self._records:
            self._records[task_id].switch_count += 1

    def record_fail(self, task_id: str, reason: str):
        if task_id in self._records:
            self._records[task_id].fail_reasons.append({
                "reason": reason,
                "timestamp": datetime.now().isoformat()
            })
            self._records[task_id].total_loops += 1

    def finish(self, ctx, final_result: dict):
        record = self._records.get(ctx.task_id)
        if not record:
            return
        record.finished_at = datetime.now().isoformat()
        record.total_time_sec = time.time() - self._start_times[ctx.task_id]
        record.total_loops = ctx.loop_count
        record.escalated = final_result.get("status") == "escalated"
        record.final_quality = "ESCALATED" if record.escalated else "PASS"
        self._save(record)

    def get_record(self, task_id: str) -> dict:
        record = self._records.get(task_id)
        return asdict(record) if record else {}

    def _save(self, record: LoopRecord):
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = RECORDS_DIR / f"{date_str}.jsonl"
        with open(log_file, 'a') as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + '\n')
```

### 11.2 SkillValidator — Selective Reinforcement Decision (`skill/skill_validator.py`)

```python
from collections import Counter

class SkillValidator:
    """
    Skill addition conditions (all 4 must be met[CORRUPTED]):
    1. Recurrence: Same type of mistake occurs across 3+ different tasks
    2. Generality: Not a one-off exception specific to a particular task
    3. Correctability: The type of error can actually be prevented by a Skill
    4. No duplicates: Content not already covered by an existing Skill
    """

    NOT_FIXABLE_BY_SKILL = {
        "docker_error", "unknown_error", "environment_error", "network_error"
    }

    def should_add_skill(self, current_record: dict, history: list) -> bool:
        [CORRUPTED]fail_reasons = [f["reason"] for f in current_record.get("fail_reasons", [])]
        if not fail_reasons:
            return False

        for reason in set(fail_reasons):
            if self._check_all_conditions(reason, current_record, history):
                return True
        return False

    def _check_all_conditions(self, reason: str, current: dict,
                               history: list) -> bool:
        # 1. Recurrence: 3+ occurrences across different tasks
        other_task_count = sum(
            [CORRUPTED]1 for record in history
            if record["task_id"] != current["task_id"]
            and any(reason in f["reason"] for f in record.get("fail_reasons", []))
        )
        if other_task_count < 2:  # Including current = 3 total
            return False

        # 2. Generality: not tied to a single specific task_id
        task_ids_with_reason = [
            record["task_id"] for record in history
            if any(reason in f["reason"] for f in record.get("fail_reasons", []))
        ]
        if le[CORRUPTED]n(set(task_ids_with_reason)) < 3:
            return False

        # 3. Correctability
        if reason in self.NOT_FIXABLE_BY_SKILL:
            return False

        # 4. No duplicates (simple check based on existing Skill file names)
        if self._already_exists(reason):
            return False

        return True

    def _already_exists(self, reason: str) -> bool:
        from pathlib import Path
        skill_dir = Path("/pyvis_memory/skill_library/verified")
        return any(reason.lower().replace(" ", "_") in [CORRUPTED] f.stem
                   for f in skill_dir.glob("*.md"))
```

---

## 12. MCP Autonomous Tool Installation

### 12.1 ToolRegistry (`mcp/tool_registry.py`)

```python
import json
from pathlib import Path

REGISTRY_FILE = Path("/pyvis_memory/mcp_registry.json")

class ToolRegistry:
    def __init__(self):
        self._tools = self._load()

    def is_installed(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def get_all(self) -> dict:
        return self._tools.copy()

    def register(sel[CORRUPTED]f, tool_name: str, tool_meta: dict):
        self._tools[tool_name] = tool_meta
        self._save()

    def _load(self) -> dict:
        if REGISTRY_FILE.exists():
            return json.loads(REGISTRY_FILE.read_text())
        return {}

    def _save(self):
        REGISTRY_FILE.write_text(json.dumps(self._tools, indent=2))
```

### 12.2 ToolInstaller (`mcp/tool_installer.py`)

```python
import subprocess
from pyvis.mcp.tool_registry import ToolRegistry

class ToolInstaller:
    """
    Brain auto-installs when needed.
    Approval mode: if requires_approval=True, installation requires human confirmation.
    """

    def __init__(self, requires_approval: bool = True):
        self.registry = ToolRegistry()
        self.requires_approval = requires_approval

    async def prepare_tools(self, required_tools: list) -> dict:
        results = {}
        for tool in required_tools:
            if self.registry.is_installed(tool["name"]):
                results[tool["name"]] = "already_installed"
            elif self.requires_approval:
                results[tool["name"]] = "pending_approval"
                self._request_approval(tool)
            else:
                success = self._install(tool)
                results[tool["name"]] = "installed" if success else "failed"
        return results

    def _install(self, tool: dict) -> bool:
        try:
            cmd = tool.get("install_cmd", f"pip install {tool['name']}")
            result = subprocess.run(cmd.split(), capture_output=True, timeout=60)
            if result.returncode == 0:
                self.registry.register(tool["name"], tool)
                return True
        except Exception as e:
            print(f"Tool installation failed: {tool['name']} — {e}")
        return False

    def _request_approval(self, tool: dict):
        # Notify human (Telegram, logs, etc.)
        print(f"[Approval Required] Tool installation needed: {tool['name']} — {tool.get('reason', '')}")
```

---

## 13. Long-Term Memory System

### 13.1 KG Server (`memory/kg_server.py`)

```python
from fastapi import FastAPI, BackgroundTasks
import faiss
import numpy as np
import pickle
from pathlib import Path

app = FastAPI()
KG_PATH = Path("/pyvis_memory/knowledge_graph")

class CPUKnowledgeGraph:
    def __init__(self):
        self.dim = 384  # Embedding dimension
        self.index = faiss.IndexFlatL2(self.dim)
        self.metadata = []
        self._load()

    def add(self, text: str, meta: dict):
        embedding = self._embed(text)
        self.index.add(np.array([embedding], dtype=np.float32)[CORRUPTED]) 
        self.metadata.append(meta)

    def search(self, query: str, k: int = 5) -> list:
        embedding = self._embed(query)
        D, I = self.index.search(np.array([embedding], dtype=np.float32), k)
        return [self.metadata[i] for i in I[0] if i < len(self.metadata)]

    def _embed(self, text: str) -> list:
        # Use lightweight embedding model (e.g., sentence-transformers/all-MiniLM-L6-v2)
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM[CORRUPTED]-L6-v2')
        return model.encode(text).tolist()

    def _load(self):
        index_file = KG_PATH / "index.faiss"
        meta_file = KG_PATH / "metadata.pkl"
        if index_file.exists():
            self.index = faiss.read_index(str(index_file))
        if meta_file.exists():
            with open(meta_file, 'rb') as f:
                self.metadata = pickle.load(f)

    def save(self):
        KG_PATH.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(KG_PATH / "index.faiss"))
        with open(KG_PATH / "metadata.pkl", 'wb') as f:
            pickle.dump(self.metadata, f)

kg = CPUKnowledgeGraph()

@app.post("/add")
async def add_knowledge(text: str, meta: dict, background_tasks: BackgroundTasks):
    background_tasks.add_task(kg.add, text, meta)
    return {"status": "accepted"}

@app.get("/search")
async def search_knowledge(query: str, k: int = 5):
    return {"results": kg.search(query, k)}

async def start_kg_server():
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=8003, log_level="error")
    server = uvicorn.Server(config)
    await server.serve()
```

### 13.2 Storage Structure

```
/pyvis_memory/
├── models/                          # ~50GB: GGUF model files
│   ├── DeepSeek-R1-Distill-Qwen-32B-Q4_K_S.gguf
│   └── Qwen2.5-Coder-32B-Instruct-Q4_K_S.gguf
├── user_profile/
│   └── profile.json                 # User preferences, tendencies, stack
├── conversation_log/
│   └── YYYY-MM-DD.jsonl             # Per-session conversation history
├── project_history/
│   └── {task_id}/                   # Per-project decision history
├── knowledge_graph/
│   ├── index.faiss                  # FAISS index
│   └── metadata.pkl                 # Vector metadata
├── skill_library/
│   ├── verified/                    # Verified Skills (auto-applied)
│   └── candidate/                   # Pending review Skills (not applied)
├── loop_rec[CORRUPTED]ords/
│   └── YYYY-MM-DD.jsonl             # Loop cost tracking logs
└── research_cache/
    └── {query_hash}.json            # Web search result cache
```

---

## 14. Interface Layer (Reserved for Phase 4)

> Paused — implementation deferred. Only system signatures defined here.

```python
# interface/audio.py (Phase 4 implementation planned)
class AudioModule:
    """Whisper-based STT/TTS"""
    wake_word: str = "hey pyvis"
    sample_rate: int = 16000

# interface/vision.py (Phase 4 implementation planned[CORRUPTED])
class VisionModule:
    """Screen capture and analysis"""
    port: int = 9999

# interface/telegram_bot.py (Phase 4 implementation planned)
class TelegramBot:
    """Telegram bot interface"""
    webhook_url: str = "http://localhost:8080/webhook"
```

---

## 15. Configuration File

> **NOTE**: The YAML configuration embedded below is **STALE** — it reflects the original 2-GPU, 2-port architecture design (GPU 0 on port 8001, GPU 1 on port 8002) with older model choices (DeepSeek-R1-Distill-Qwen-32B, Qwen2.5-Coder-32B). The **current** production configuration uses a single-server swap architecture on port 8001 with updated models (GLM-4.7-Flash, Qwen3-14B, Devstral-24B, DeepSeek-R1-Distill-Qwen-14B). See `config/unified_node.yaml` for the authoritative, up-to-date configuration.

### `config/unified_node.yaml`

```yaml
system:
  name: "Pyvis"
  version: "4.0.0"

hardware:
  cpu:
    cores: 8
    affinity:
      interface:      [0, 1]
      orchestration:  [2, 3]
      ai_inference:   [4, 5, 6, 7]
  gpu:
    - id: 0
      name: "RTX 407[CORRUPTED]0"
      vram_gb: 12
      role: "brain"
      model: "DeepSeek-R1-Distill-Qwen-32B-Q4_K_S.gguf"
      context_size: 32768
      n_gpu_layers: 40
      port: 8001
    - id: 1
      name: "RTX 3060"
      vram_gb: 12
      role: "hands_judge"
      model: "Qwen2.5-Coder-32B-Instruct-Q4_K_S.gguf"
      context_size: 65536
      n_gpu_layers: 40
      port: 8002

ai:
  brain:
    system_prompt: "system/brain_prompt.txt"
    temperature: 0.7
    max_tokens: 4096
    cot_strip: true            # Strip <think> blocks[CORRUPTED]
  hands:
    system_prompt: "system/hands_prompt.txt"
    temperature: 0.2
    max_tokens: 8192
  judge:
    system_prompt: "system/judge_prompt.txt"
    temperature: 0.1
    max_tokens: 512
    kv_cache_reset: true       # KV Cache reset required every time
    fresh_context: true        # No previous conversation history

research_loop:
  max_loops: 5
  max_consecutive_fails: 3
  pass_threshold: 90           # 90+ = PASS
  revise_threshold: 70         # 70+ = REVISE, below = ENRICH
  sandbox_timeou[CORRUPTED]t: 30
  min_repeat_count: 3          # Minimum repeated failure count for Skill addition
  min_task_diversity: 3        # Minimum number of distinct tasks
  requires_human_review: true  # Human review required before verified promotion

mcp:
  requires_approval: true      # Human approval required before tool installation

storage:
  base_path: "/pyvis_memory"
  models_path: "/pyvis_memory/models"
  workspace_path: "/pyvis_memory/workspace"
  logs_path: "/pyvis_memory/loop_records"

sandbox:
  type: "docker"
  image: "pyvis-sandbox:latest"
  tmpfs_path: "/dev/shm/pyvis_sandbox"
  memory_limit: "512m"
  cpu_limit: 1.0
  network_enabled: false

logging:
  level: "INFO"
  format: "json"
  rotation: "daily"
  retention: "30d"
```

---

## 16. Implementation Roadmap and Phase-by-Phase Tasks

### Phase 1: Rust Core (Weeks 1-2)

```
- [ ] Cargo.toml workspace setup
- [ ] Implement and test crossbeam-based lock-free priority queue
- [ ] Implement CPU Affinity thread pool
- [ ] Implement Model Hot-Swap controller (ModelHotSwap)
- [ ] Build via PyO3 Python b[CORRUPTED]indings and verify Python import
- [ ] Unit tests (cargo test)
```

### Phase 2: AI Engine (Weeks 3-4)

```
- [ ] llama.cpp CUDA build (sm_86 + sm_89)
- [ ] Launch and verify Brain server (GPU 0, port 8001)
- [ ] Launch and verify Hands/Judge server (GPU 1, port 8002)
- [ ] Measure VRAM usage and determine optimal n_gpu_layers values
- [ ] Implement Brain client and verify CoT preprocessing
- [ ] Implement Hands client
- [ ] Implement Judge client (KV Cache reset verification required)
- [ ] Write 3 system prompts (brain/hands/judge_prompt.txt)
```

### Phase 3: Orchestration (Weeks 5-6)

```
- [ ] Build and test Docker sandbox image
  - macOS support: document OrbStack as Docker alternative with execution procedures
- [ ] Implement CriticRunner and verify error classification
- [ ] Implement LoopController (full loop state machine)
- [ ] Implement LoopTracker and verify JSONL persistence
- [ ] Implement SkillManager (verified/candidate separation)
- [ ] Implement SkillValidator (4-condition verification)
- [ ] Implement MCP ToolRegistry + ToolInstaller
- [ ] Implement FastAPI KG server (FAISS CPU)
- [ ] Implement Session Manager
- [ ] End-to-End integration test (verify full loop with simple task)
```

### Phase 4: Stabilization (Weeks 7-8)

```
- [ ] Memory leak detection (Valgrind, heaptrack)
- [ ] Stress test (10 consecutive loops)
- [ ] Escalation scenario testing
- [ ] Loop cost tracking → Skill reinforcement pipeline verification
- [ ] Performance profiling (model switch latency measurement)
- [ ] Configuration file-based behavior verification

Phase 5 and beyond (reserved):
- [ ] Interface layer[CORRUPTED] (STT/TTS, Vision, Telegram)
- [ ] Web service expansion
```

---

## 17. System Prompt Definitions

### `system/brain_prompt.txt`

```
You are Pyvis's Brain.

Role:
- Analyze tasks and produce actionable plans
- Clearly define TODO List, PASS criteria, and fix scope
- Analyze escalation causes and revise plans
- Review final deliverables

Absolute Rules:
- You do not generate code directly
- All implementation[CORRUPTED] is delegated to Hands
- Respond only in the requested format (JSON)
```

### `system/hands_prompt.txt`

```
You are Pyvis's Hands.

Role:
- Implement code based on Brain's plan
- When given fix instructions, modify only within the allowed scope

Absolute Rules:
- Do not make design decisions not specified in the plan
- Do not make changes outside the fix scope
- Output code only. Minimize explanations.
```

### `system/judge_prompt.txt`

```
You are Pyvis's Judge.

Role:
- Judge solely based on the plan's PASS criteria and execution results
- You have no knowledge of the code implementation method or process
- Evaluate only whether deliverables meet requirements

Absolute Rules:
- Do not praise
- Issue exactly one verdict: PASS / REVISE / ENRICH / ESCALATE
- Respond only in JSON format
- You have no previous conversation history. Judge only what you see now
```

---

## 18. Risk Factors and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| VRAM shortage | High | Adjust n_gpu_layers; determine optimal values after actual measurement |
| Rust-Python boundary bugs | Medium | PyO3 strict type checking, unit tests |
| Judge self-rationalization | Medium | KV Cache reset + context isolation + forced fresh_context |
| Infinite loops | Medium | max_loops hard cap at 5, timeout settings |
| Skill contamination | Medium | candidate/verified separation, human review required[CORRUPTED] |
| Sandbox security | High | network=none, mem_limit, cpu_limit, root privileges removed |
| Model switch latency | Medium | Keep both models resident in RAM; use context switch only |
| FAISS index corruption | Low | Call save() on session end, periodic backups |

---

## Performance Goals

| Metric | Target |
|---|---|
| Brain inference speed | 15-25 t/s |
| Hands/Judge inference speed | 15-25 t/s |
| Model switch latency | < 100ms (context switch) |
| Critic execution latency | < 30s (timeout) |
| K[CORRUPTED]G search latency | < 1ms |
| Single loop duration | 2-5 min |
| Total model switches | Minimum 2 (when no escalation) |

---

*— Pyvis v4.0 Implementation Design Document End —*  
*Implementation by: Claude Opus 4.6*

---

## 19. Implementation Status (Updated: Feb 21, 2026)

### Completed Phases

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1: Rust Core | ✅ Complete | Lock-free queue, thread pool, model hot-swap, PyO3 bindings |
| Phase 2: AI Engine | ✅ Complete | Planner/Brain/Hands/Judge clients, llama.cpp CUDA build |
| Phase 3: Orchestration | ✅ Complete | LoopController, CriticRunner, SkillManager, MCP tools |
| Phase 3.5: Integration | ✅ Complete | 101 tests passing, hardware validated |
| Phase 4: Interface | ⏳ Reserved | Audio/Vision/Telegram/Web UI |

### Hardware Validation Summary

**Test Environment:**
- GPU0: RTX 3060 12GB + GPU1: RTX 4070 SUPER 12GB
- Total VRAM: ~24.5GB
- CPU: AMD Ryzen 9 3900X (12 threads)

**Model Performance (All 4/4 PASSED):**

| Role | Model | Context | VRAM | Load | Inference |
|------|-------|---------|------|------|-----------|
| Planner | GLM-4.7-Flash-Q4_K_M | 64K | 22.4 GB | 72s | 2.0s |
| Brain | Qwen3-14B-Q5_K_M | 40K | 18.5 GB | 27s | 2.5s |
| Hands | Devstral-24B-Q4_K_M | 80K | 22.2 GB | 27s | 3.8s |
| Judge | DeepSeek-R1-14B-Q4_K_M | 64K | 14.3 GB | 19s | 2.2s |

**Swap Latency:**
- Planner (cold): ~74s
- Brain/Hands/Judge (warm): 8-17s
- Stress test: 100% success (12/12 swaps)

### Implemented Features

1. **Model Hot-Swap System**
   - Single-server architecture on port 8001
   - Dual GPU parallel inference (split-mode layer)
   - Per-role context sizes (32K-114K)
   - Automatic fallback chain: Hands → Brain

2. **AI Role Clients**
   - **Planner**: Task decomposition, plan generation
   - **Brain**: Plan review, escalation handling, CoT stripping
   - **Hands**: Code generation, revision loop
   - **Judge**: Independent evaluation, PASS/REVISE/ESCALATE verdicts

3. **Self-Evaluation Loop**
   - State machine: PLAN → BUILD → CRITIQUE → EVALUATE → REVISE/ENRICH → COMPLETE/ESCALATE
   - Max 5 loops before human escalation
   - Automatic error classification (8 patterns)

4. **Docker Sandbox Execution**
   - Isolated code execution (network=none, resource limits)
   - 30s timeout, stdout/stderr capture
   - Error classification for revision guidance

5. **Skill Library System**
   - Verified/Candidate split
   - 4-condition validation (recurrence, generality, correctability, no-duplicate)
   - Automatic skill reinforcement from successful loops

6. **MCP Tool Integration**
   - ToolRegistry + ToolInstaller
   - Approval mode for external tool installation
   - kg_server (FAISS + FastAPI) on port 8003

### Test Coverage

| Module | Tests | Status |
|--------|-------|--------|
| test_e2e_loop.py | 19 | ✅ All passing |
| test_ai_modules.py | 42 | ✅ All passing |
| test_infra_modules.py | 40 | ✅ All passing |
| **Total** | **101** | ✅ **All passing** |

### Known Limitations

- Planner model (18GB) has longest cold load (~74s)
- Single model loaded at a time (server restart required for role switch)
- No memory leak detection yet (Valgrind/heaptrack pending)

---

## 22. Future Enhancements (Roadmap)

### 22.1 단기 (현재 구현됨)
- ✅ Planner가 단일 계획 생성
- ✅ Judge가 PASS/REVISE/ESCALATE 판정
- ✅ Brain이 에스컬레이션 처리
- ✅ MCP Tools + LLM Tool Calling
- ✅ Skill 자동 강화

### 22.2 중기 (ToT - Tree of Thoughts)

Planner가 복수 경로를 평가 후 선택:

```python
class ToTPlanner:
    async def generate_branches(self, task: str, n: int = 3) -> list[Plan]:
        branches = []
        for i in range(n):
            plan = await self.plan(task, temperature=0.3 + i*0.2)
            branches.append(plan)
        return branches
    
    async def evaluate_branches(self, branches: list[Plan]) -> tuple[Plan, list[Plan]]:
        scores = []
        for plan in branches:
            score = await self.judge.evaluate_plan(plan)
            scores.append((score, plan))
        scores.sort(reverse=True)
        return scores[0][1], [p for _, p in scores[1:]]

# Loop:
# 1. planner.generate_branches(task, n=3) → [plan_a, plan_b, plan_c]
# 2. judge.evaluate_branches(branches) → (best_plan, fallbacks)
# 3. execute(best_plan)
# 4. if fail: backtrack to fallback[0]
```

### 22.3 장기 (ReAct Tree)

서브골 트리 자동 구성:

```
                    [Main Goal]
                    /    |    \
              [Sub1]  [Sub2]  [Sub3]
               /  \      |       
          [Sub1a][Sub1b][Sub2a]   
```

### 22.4 Planner CoT (Chain of Thought)

```
[Enhanced with CoT]
User: "Create a web scraper"
Planner:
  1. [THOUGHT] Requirements analysis
  2. [REASONING] Technology selection
  3. [PLAN] file_structure + todo_list
```

---

*End of Document*
