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
