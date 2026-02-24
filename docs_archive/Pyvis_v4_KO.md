# PYVIS v4.0
## 개인용 AI 어시스턴트 & 리서치 에이전트
### 구현 설계 명세서 — Opus 4.6 구현용

> **버전**: 4.0  
> **목표**: Jarvis형 개인 AI 어시스턴트 + 리서치 에이전트  
> **구현 담당**: Claude Opus 4.6  
> **언어**: Rust (성능 핵심 영역) + Python (비즈니스 로직)  
> **하드웨어**: RTX 4070 12GB + RTX 3060 12GB | 32GB RAM | NVMe 1TB

---

## 목차

1. [시스템 아키텍처 개요](#1-시스템-아키텍처-개요)
2. [하드웨어 사양 및 리소스 할당](#2-하드웨어-사양-및-리소스-할당)
3. [모델 사양 및 GPU 배치](#3-모델-사양-및-gpu-배치)
4. [프로젝트 디렉토리 구조](#4-프로젝트-디렉토리-구조)
5. [Rust 코어 레이어](#5-rust-코어-레이어)
6. [Python 오케스트레이션 레이어](#6-python-오케스트레이션-레이어)
7. [AI 엔진 — Brain / Hands / Judge](#7-ai-엔진--brain--hands--judge)
8. [자기 평가 루프 설계](#8-자기-평가-루프-설계)
9. [Critic 샌드박스 실행 엔진](#9-critic-샌드박스-실행-엔진)
10. [스킬 라이브러리 시스템](#10-스킬-라이브러리-시스템)
11. [루프 비용 추적 + 선택적 스킬 강화](#11-루프-비용-추적--선택적-스킬-강화)
12. [MCP 자율 툴 설치](#12-mcp-자율-툴-설치)
13. [장기 메모리 시스템](#13-장기-메모리-시스템)
14. [인터페이스 레이어 (4단계 예약)](#14-인터페이스-레이어-4단계-예약)
15. [설정 파일](#15-설정-파일)
16. [구현 로드맵 및 단계별 작업](#16-구현-로드맵-및-단계별-작업)
17. [시스템 프롬프트 정의](#17-시스템-프롬프트-정의)
18. [리스크 요소 및 대응 방안](#18-리스크-요소-및-대응-방안)

---

## 1. 시스템 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                        PYVIS v4.0                               │
├─────────────────────────────────────────────────────────────────┤
│  Layer 0: 인터페이스 (4단계 예약)                                │
│  ├── 오디오 모듈 (STT/TTS) — Whisper                            │
│  ├── 비전 모듈 (화면 캡처)                                       │
│  ├── 텔레그램 봇                                                 │
│  └── WebSocket 서버                                              │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1: Rust 코어 (pyvis_core)                                │
│  ├── 락-프리 작업 큐 (crossbeam)                                 │
│  ├── 스레드 풀 + CPU 어피니티                                    │
│  ├── 우선순위 핸들러 (P0: 정지 / P1: AI / P2: IO)               │
│  ├── 모델 핫스왑 컨트롤러                                        │
│  └── Python 바인딩 (PyO3)                                        │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: Python 오케스트레이션                                  │
│  ├── 세션 매니저                                                 │
│  ├── 리서치 루프 컨트롤러                                        │
│  ├── 툴 실행기 (MCP)                                             │
│  ├── 스킬 매니저                                                 │
│  ├── 루프 비용 트래커                                            │
│  └── 스토리지 컨트롤러 (SoT)                                    │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: AI 엔진 (llama.cpp)                                   │
│  ├── Planner: GLM-4.7-Flash-30B (계획 전담)                     │
│  ├── Brain: Qwen3-14B (검토/에스컬레이션)                        │
│  ├── Hands: Devstral-24B (빌더)                                  │
│  └── Judge: R1-Distill-14B (평가, 512 토큰)                      │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4: 실행 엔진                                              │
│  ├── Critic: Docker 샌드박스 (/dev/shm tmpfs)                   │
│  └── 코드 검증기                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Layer 5: 메모리 & 스토리지                                      │
│  ├── FAISS KG (CPU RAM — 핫 메모리)                              │
│  └── NVMe SSD /pyvis_memory/ (콜드 스토리지)                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 하드웨어 사양 및 리소스 할당

### 2.1 하드웨어 사양

| 구성 요소 | 사양 | 비고 |
|---|---|---|
| GPU 0 (llama Device 0) | NVIDIA RTX 4070 SUPER 12GB | Ada Lovelace, sm_89 |
| GPU 1 (llama Device 1) | NVIDIA RTX 3060 12GB | Ampere, sm_86 |
| 총 VRAM | 24GB | **듀얼 GPU 병렬** (분할 모드 레이어) |
| 메인보드 | Gigabyte X570 AORUS PRO | PCIe 4.0, x8/x8 |
| PCIe 대역폭 | 단방향 16 GB/s (슬롯당) | |
| 시스템 RAM | 32GB DDR4/5 | |
| CPU | AMD Ryzen 9 3900X | 6코어, 12스레드 |
| 시스템 NVMe | OS + 모델 파일 | |
| 장기 메모리 NVMe | 1TB 전용 파티션 | `/pyvis_memory/` |

> **GPU 동작 방식**: 32B Q4_K_S 모델(~18GB)은 단일 GPU(12GB)에 완전히 올라가지 않습니다.
> `--split-mode layer --tensor-split 0.55,0.45` 옵션으로 두 GPU를 병렬 사용하며,
> 한 번에 하나의 모델만 로드합니다. Brain ↔ Hands/Judge 전환 시 서버 재시작(스왑)이 필요합니다.

### 2.2 CPU 코어 할당 (6코어 12스레드 기준)

| 코어 | 담당 역할 | 프로세스 |
|---|---|---|
| 0~1 | 인터페이스 / IO | FastAPI, KG 서버, FAISS |
| 2~3 | 오케스트레이션 | 루프 컨트롤러, 스킬 매니저, 트래커 |
| 4~7 | AI 추론 | llama.cpp (듀얼 GPU, 단일 모델) |
| 8~11 | 시스템 예비 | OS, Docker, 백그라운드 |

```yaml
# taskset 설정
interface_cores: "0,1"
orchestration_cores: "2,3"
ai_cores: "4,5,6,7"
llama_threads: 4  # AI 코어 수와 동일
```

### 2.3 RAM 할당

| 항목 | 용량 | 비고 |
|---|---|---|
| OS + 백그라운드 | ~5.0 GB | |
| 활성 모델 RAM 오프로드 | ~2.0 GB | 대부분 24GB VRAM에 로드, 나머지만 오프로드 |
| FAISS 핫 메모리 | ~2.0 GB | CPU RAM 상주 |
| Docker 런타임 | ~1.0 GB | Critic 샌드박스 |
| Rust 코어 런타임 | ~0.5 GB | |
| 예비 | ~21.5 GB | 버퍼 (단일 모델 구조, 동시 로드 없음) |
| **합계** | **~32 GB** | ✅ |

> 원래 설계는 두 모델을 동시에 상주시켜 16GB RAM을 사용하는 방식이었으나,
> 듀얼 GPU 병렬 아키텍처로 전환하면서 한 번에 하나의 모델만 로드하게 되어
> RAM 여유 공간이 크게 늘었습니다.

### 2.4 VRAM 할당 (듀얼 GPU 합산 24GB, 단일 모델)

**활성 모델 로드 시 (예: DeepSeek-R1 Q4_K_S ~18GB)**

| GPU | 항목 | 용량 |
|---|---|---|
| Device 0 (RTX 4070S) | 모델 레이어 55% | ~9.9 GB |
| Device 0 (RTX 4070S) | KV 캐시 (주) | ~1.5 GB |
| Device 1 (RTX 3060) | 모델 레이어 45% | ~8.1 GB |
| Device 1 (RTX 3060) | KV 캐시 (부) | ~1.5 GB |
| **합계** | | **~21.0 GB / 24 GB ✅** |

> ⚠️ 단일 모델만 로드되므로, 모델 전환 시 서버 재시작이 필요합니다.
> 전환 비용: 모델 로드 30~90초 (NVMe→VRAM).
> Brain → Hands/Judge 전환은 루프당 최소 2회 발생합니다.
> KV 캐시는 서버 재시작 시 자동으로 초기화됩니다.

---

## 3. 모델 사양 및 GPU 배치

### 3.1 모델 정보

| 역할 | 모델 | HuggingFace ID | 양자화 | GPU | 컨텍스트 |
|---|---|---|---|---|---|
| Planner | GLM-4.7-Flash | unsloth/GLM-4.7-Flash-GGUF | Q4_K_M | 듀얼 GPU (0+1) | 80K |
| Brain | Qwen3-14B | Qwen/Qwen3-14B-GGUF | Q5_K_M | 듀얼 GPU (0+1) | 114K |
| Hands | Devstral-24B | bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF | Q4_K_M | 듀얼 GPU (0+1) | 114K |
| Judge | DeepSeek-R1-Distill-Qwen-14B | bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF | Q4_K_M | 듀얼 GPU (0+1) | 80K |

> Brain과 Hands/Judge는 동시 상주가 불가합니다. 전환은 모델 스왑으로 처리합니다.
> Judge는 Hands와 동일한 모델을 사용하지만, 항상 새 컨텍스트로 호출됩니다 (KV 캐시 초기화).

### 3.2 역할 정의 (절대 규칙)

| 역할 | 담당 업무 | 절대 금지 |
|---|---|---|
| Brain | 작업 분석, 계획 생성, TODO List, PASS 기준, 자기 수정 범위, 에스컬레이션 처리, 최종 검토, 스킬 강화 결정 | 코드 직접 생성 |
| Hands | 빌더 페르소나, 계획 기반 코드 생성, 수정 지시에 따른 재생성 | 계획 수립, 평가 |
| Judge | 저지 페르소나, KV 캐시 초기화 후 독립 평가, PASS/FAIL/ESCALATE 판정 | 코드 수정, 계획 변경 |
| Critic | Docker 샌드박스 코드 실행, 결과 수집, 보고서 생성 | 코드 수정, 평가 |

### 3.3 llama.cpp 실행 (듀얼 GPU 병렬, 단일 모델 스왑)

**빌드 (CUDA 혼합 아키텍처)**
```bash
cmake .. -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES="86;89"
make -j$(nproc)
```

**통합 서버 스크립트 (`scripts/start_model.sh`)**

한 번에 하나의 모델만 로드. `split-mode layer`로 두 GPU에 분산.
단일 포트 8001. Brain ↔ Hands/Judge 전환은 서버 재시작으로 수행.

```bash
# Planner 모델 로드
./scripts/start_model.sh planner

# Brain 모델 로드
./scripts/start_model.sh brain

# Hands 모델 로드 (기존 서버 자동 종료 후 재시작)
./scripts/start_model.sh hands

# Judge 모델 로드 (기존 서버 자동 종료 후 재시작)
./scripts/start_model.sh judge

# 현재 모델과 반대 모델 간 전환
./scripts/start_model.sh swap

# 상태 확인
./scripts/start_model.sh status
```

**공통 서버 옵션**
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

> `--split-mode layer`: GPU 간 레이어 분산
> `--tensor-split 0.55,0.45`: RTX 4070S(Device 0)에 55%, RTX 3060(Device 1)에 45%
> `-ngl 99`: 가능한 많은 레이어를 GPU에 로드 (VRAM 한계 도달 시 자동 오프로드)
> 모델 전환 비용: 30~90초 (NVMe→VRAM 로드)
> KV 캐시는 서버 재시작 시 자동으로 초기화됨 (Judge 신규 컨텍스트 보장)

### 3.4 CoT 전처리 (Brain 출력에 필수)

```python
import re

def strip_cot(text: str) -> str:
    """Brain 출력에서 <think> 블록 제거"""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
```

---

## 4. 프로젝트 디렉토리 구조

```
pyvis/
├── Cargo.toml                    # Rust 워크스페이스
├── pyproject.toml                # Python 패키지
├── config/
│   └── unified_node.yaml         # 시스템 전체 설정
├── pyvis_core/                   # Rust crate (PyO3 바인딩)
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs                # PyO3 모듈 진입점
│       ├── queue/
│       │   ├── mod.rs
│       │   └── priority_queue.rs # 락-프리 우선순위 큐
│       ├── thread_pool/
│       │   ├── mod.rs
│       │   └── pool.rs           # CPU 어피니티 스레드 풀
│       └── model/
│           ├── mod.rs
│           └── hot_swap.rs       # 모델 전환 컨트롤러
├── pyvis/                        # Python 패키지
│   ├── __init__.py
│   ├── main.py                   # 진입점
│   ├── orchestration/
│   │   ├── __init__.py
│   │   ├── session_manager.py    # 세션 관리
│   │   ├── loop_controller.py    # 자기 평가 루프
│   │   └── escalation.py        # 에스컬레이션 처리
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── brain.py              # Brain 인터페이스
│   │   ├── hands.py              # Hands 인터페이스
│   │   ├── judge.py              # Judge 인터페이스
│   │   └── prompts/
│   │       ├── brain_prompt.txt
│   │       ├── hands_prompt.txt
│   │       └── judge_prompt.txt
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── critic_runner.py      # Docker 샌드박스
│   │   └── result_parser.py     # 실행 결과 파싱
│   ├── skill/
│   │   ├── __init__.py
│   │   ├── skill_manager.py     # 스킬 로드/저장/관리
│   │   └── skill_validator.py   # 추가 조건 검증
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── tool_registry.py     # 설치된 툴 레지스트리
│   │   └── tool_installer.py    # 자율 설치 로직
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── kg_server.py         # FastAPI FAISS KG 서버
│   │   ├── hot_memory.py        # RAM 상주 핫 메모리
│   │   └── cold_storage.py      # SSD 콜드 메모리
│   └── tracking/
│       ├── __init__.py
│       └── loop_tracker.py      # 루프 비용 추적
├── system/
│   └── prompts/                 # 시스템 프롬프트 파일
├── /pyvis_memory/               # NVMe 마운트 포인트
│   ├── models/                  # GGUF 모델 파일
│   ├── user_profile/
│   ├── conversation_log/
│   ├── project_history/
│   ├── knowledge_graph/
│   ├── skill_library/
│   │   ├── verified/            # 검증된 스킬
│   │   └── candidate/           # 검토 대기 스킬
│   ├── loop_records/            # 루프 비용 로그 (JSONL)
│   └── research_cache/
└── docker/
    └── sandbox/
        └── Dockerfile           # Critic 샌드박스 이미지
```

---

## 5. Rust 코어 레이어

### 5.1 Cargo.toml 의존성

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

### 5.2 락-프리 우선순위 큐 (`queue/priority_queue.rs`)

```rust
use crossbeam::queue::SegQueue;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug, Clone, PartialEq)]
pub enum TaskPriority {
    Stop = 0,        // P0: 긴급 정지 (항상 최우선)
    AiBrain = 1,     // P1: Brain 추론
    AiHands = 2,     // P2: Hands 코드 생성
    AiJudge = 3,     // P3: Judge 평가
    Orchestration = 4, // P4: 오케스트레이션
    Io = 5,          // P5: IO 작업
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

    /// P0 → P1 → P2 우선순위 순으로 디큐
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

### 5.3 CPU 어피니티 스레드 풀 (`thread_pool/pool.rs`)

```rust
use std::thread;
use std::sync::Arc;
use crossbeam_channel::{bounded, Sender, Receiver};

pub struct ThreadPool {
    workers: Vec<Worker>,
    sender: Sender<Job>,
}

impl ThreadPool {
    /// core_ids: 이 풀에 할당된 CPU 코어 목록
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
        self.sender.send(Box::new(f)).expect("스레드 풀 전송 실패");
    }
}

struct Worker {
    id: usize,
    thread: Option<thread::JoinHandle<()>>,
}

impl Worker {
    fn new(id: usize, receiver: Arc<Receiver<Job>>, core_id: usize) -> Worker {
        let thread = thread::spawn(move || {
            // CPU 어피니티 설정 (Linux)
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
        // ...
    }
}
```

### 5.4 모델 핫스왑 컨트롤러 (`model/hot_swap.rs`)

```rust
#[derive(Debug, Clone, PartialEq)]
pub enum ModelRole {
    Planner,
    Brain,
    Hands,
    Judge,
}

pub struct SwitchResult {
    pub success: bool,
    pub requires_kv_reset: bool,   // Judge는 항상 true
    pub switch_time_ms: u64,
}

pub struct ModelHotSwap {
    current_role: std::sync::Mutex<Option<ModelRole>>,
}

impl ModelHotSwap {
    pub fn new() -> Self {
        Self { current_role: std::sync::Mutex::new(None) }
    }

    pub fn switch_role(&self, target: ModelRole) -> SwitchResult {
        let mut current = self.current_role.lock().unwrap();
        let requires_kv_reset = target == ModelRole::Judge;

        // 실제 구현: start_model.sh 스크립트 호출
        *current = Some(target);

        SwitchResult {
            success: true,
            requires_kv_reset,
            switch_time_ms: 0, // 실제 측정값으로 채울 것
        }
    }

    pub fn current_role(&self) -> Option<ModelRole> {
        self.current_role.lock().unwrap().clone()
    }
}
```

### 5.5 PyO3 바인딩 (`lib.rs`)

```rust
use pyo3::prelude::*;

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

    fn enqueue(&self, task_json: String) {
        // Python에서 작업 제출
    }

    fn dequeue(&self) -> Option<String> {
        // Python으로 작업 반환
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
        // Judge 전환은 항상 KV 캐시 초기화 필요
        let result = self.inner.switch_role(ModelRole::Judge);
        result.requires_kv_reset  // 항상 True
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

## 6. Python 오케스트레이션 레이어

### 6.1 메인 진입점 (`pyvis/main.py`)

```python
import asyncio
import uvloop
from pyvis.orchestration.session_manager import SessionManager
from pyvis.memory.kg_server import start_kg_server
from pyvis.tracking.loop_tracker import LoopTracker
import pyvis_core  # Rust 바인딩

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

async def main():
    # Rust 코어 초기화
    task_queue = pyvis_core.PyPriorityQueue()
    model_swap = pyvis_core.PyModelSwap()

    # KG 서버 시작 (코어 0,1에 격리)
    kg_task = asyncio.create_task(start_kg_server())

    # 루프 트래커 초기화
    tracker = LoopTracker()

    # 세션 매니저 시작
    session = SessionManager(task_queue, model_swap, tracker)
    await session.run()

if __name__ == "__main__":
    uvloop.run(main())
```

### 6.2 루프 컨트롤러 (`orchestration/loop_controller.py`)

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
    PASS      = "PASS"       # 90점 이상
    REVISE    = "REVISE"     # 70~90점
    ENRICH    = "ENRICH"     # 70점 미만
    ESCALATE  = "ESCALATE"   # 판정 불가 또는 N회 초과

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
    max_loops: int = 5           # 기본값, config로 조정 가능
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
        메인 루프.
        Brain은 시작(PLAN)과 끝(COMPLETE/ESCALATE)에만 등장.
        중간 루프는 Hands + Critic + Judge가 자율적으로 처리.
        """
        self.tracker.start(ctx.task_id, ctx.task_description)

        while ctx.current_step != LoopStep.COMPLETE:

            # ── PLAN: Brain 호출 (최초 1회) ──────────────────────
            if ctx.current_step == LoopStep.PLAN:
                plan_output = await self.brain.plan(ctx)
                ctx.plan = plan_output["plan"]
                ctx.todo_list = plan_output["todo_list"]
                ctx.pass_criteria = plan_output["pass_criteria"]
                ctx.self_fix_scope = plan_output["self_fix_scope"]
                ctx.current_step = LoopStep.BUILD
                # Brain → Hands 전환 (1회)
                self.tracker.record_switch("brain_to_hands")

            # ── BUILD: Hands 코드 생성 ───────────────────────────
            elif ctx.current_step == LoopStep.BUILD:
                current_task = ctx.todo_list[ctx.current_task_index]
                skill_context = self.skill_manager.load_verified(ctx.task_description)
                code = await self.hands.build(current_task, ctx.plan, skill_context)
                ctx.current_code = code
                ctx.current_step = LoopStep.CRITIQUE

            # ── CRITIQUE: Critic 실행 ─────────────────────────────
            elif ctx.current_step == LoopStep.CRITIQUE:
                result = await self.critic.execute(ctx.current_code)
                ctx.critic_result = result
                ctx.current_step = LoopStep.EVALUATE

            # ── EVALUATE: Judge 평가 (KV 캐시 초기화 후) ─────────
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

            # ── REVISE/ENRICH: Hands 자율 수정 ───────────────────
            elif ctx.current_step in (LoopStep.REVISE, LoopStep.ENRICH):
                # Hands가 Brain 개입 없이 수정
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

            # ── ESCALATE: Brain 재호출 ──────────────────────────
            elif ctx.current_step == LoopStep.ESCALATE:
                if ctx.loop_count >= ctx.max_loops:
                    # 사람에게 보고
                    return self._human_escalation(ctx)

                # Brain이 원인 분류 후 계획 수정
                escalation_result = await self.brain.handle_escalation(ctx)
                if escalation_result["action"] == "revise_plan":
                    ctx.plan = escalation_result["new_plan"]
                    ctx.todo_list = escalation_result["new_todo"]
                    ctx.pass_criteria = escalation_result["new_criteria"]
                    ctx.consecutive_fails = 0
                    ctx.current_step = LoopStep.BUILD
                else:
                    return self._human_escalation(ctx)

        # ── COMPLETE: Brain 최종 검토 ───────────────────────────
        # Hands/Judge → Brain 전환 (1회)
        self.tracker.record_switch("hands_to_brain")
        final_result = await self.brain.final_review(ctx)

        # 루프 기록 저장 + 스킬 강화 결정
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
        """문제가 자기 수정 범위 내에 있는지 확인"""
        error_type = ctx.critic_result.get("error_type", "")
        return error_type in ctx.self_fix_scope.get("allowed", [])

    def _human_escalation(self, ctx: LoopContext) -> dict:
        return {
            "status": "escalated",
            "task_id": ctx.task_id,
            "loop_count": ctx.loop_count,
            "fail_reasons": ctx.fail_reasons,
            "message": "자동으로 해결할 수 없습니다. 사람의 판단이 필요합니다."
        }
```

---

## 7. AI 엔진 — Brain / Hands / Judge

### 7.1 Brain (`ai/brain.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt
from pyvis.utils import strip_cot
import json

BRAIN_API = "http://localhost:8001/v1/chat/completions"

class Brain:
    def __init__(self):
        self.system_prompt = load_prompt("brain_prompt.txt")
        self.client = httpx.AsyncClient(timeout=120.0)

    async def plan(self, ctx) -> dict:
        """
        Brain 초기 출력:
        1. 계획 문서
        2. TODO List
        3. 태스크별 PASS 기준
        4. 자기 수정 범위 (Hands가 자율적으로 수정 가능한 항목)
        """
        user_message = f"""
태스크: {ctx.task_description}
다음 JSON 형식으로만 응답해야 합니다:
{{
  "plan": "전체 아키텍처 및 구현 계획 (Markdown)",
  "todo_list": [
    {{"id": 1, "title": "태스크 제목", "description": "상세 설명"}}
  ],
  "pass_criteria": {{
    "1": ["조건1", "조건2"],
    "2": ["조건1"]
  }},
  "self_fix_scope": {{
    "allowed": ["type_error", "syntax_error", "missing_import"],
    "escalate": ["architecture_change", "schema_change"]
  }}
}}
"""
        response = await self._call(user_message)
        clean = strip_cot(response)
        return json.loads(clean)

    async def handle_escalation(self, ctx) -> dict:
        """에스컬레이션 원인 분석 및 계획 수정"""
        user_message = f"""
원래 계획: {ctx.plan}
실패 원인 목록: {json.dumps(ctx.fail_reasons, ensure_ascii=False)}
루프 횟수: {ctx.loop_count}
마지막 오류: {ctx.critic_result.get('stderr', '')}

원인을 분류하고 다음 형식으로 응답하세요:
{{
  "cause_type": "plan_error | implementation_error | environment_error",
  "action": "revise_plan | human_escalation",
  "analysis": "분석 내용",
  "new_plan": "수정된 계획 (action이 revise_plan인 경우)",
  "new_todo": [...],
  "new_criteria": {{...}}
}}
"""
        response = await self._call(user_message)
        clean = strip_cot(response)
        return json.loads(clean)

    async def final_review(self, ctx) -> dict:
        """최종 검토"""
        response = await self._call(
            f"다음 태스크의 최종 결과물을 검토하고 요약하세요: {ctx.task_description}"
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
        """계획 기반 코드 생성"""
        user_message = f"""
전체 계획:
{plan}

현재 구현할 태스크:
{task['title']}: {task['description']}

적용할 스킬 규칙:
{skill_context}

위 계획에서 현재 태스크에 해당하는 코드만 구현하세요.
"""
        return await self._call(user_message)

    async def revise(self, task: dict, prev_code: str,
                     critic_result: dict, self_fix_scope: dict) -> str:
        """수정 지시에 따른 코드 재생성"""
        user_message = f"""
태스크: {task['title']}
이전 코드:
{prev_code}

실행 오류:
{critic_result.get('stderr', '없음')}

표준 출력:
{critic_result.get('stdout', '없음')}

허용된 자기 수정 범위: {self_fix_scope.get('allowed', [])}
위 오류를 수정하세요. 허용 범위 외의 변경은 금지합니다.
"""
        return await self._call(user_message)

    async def _call(self, user_message: str) -> str:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
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
    error_type: str   # Hands 자율 수정 가능 여부 판단용

class Judge:
    def __init__(self):
        self.system_prompt = load_prompt("judge_prompt.txt")
        self.client = httpx.AsyncClient(timeout=60.0)

    async def evaluate(self, task: dict, pass_criteria: dict,
                       critic_result: dict, loop_count: int) -> JudgeResult:
        """
        핵심: 이전 대화 기록 없음. 항상 새 컨텍스트.
        Hands의 코드나 사고 과정 포함하지 않음.
        계획 요구사항 + 실행 결과만으로 판정.
        """
        criteria = pass_criteria.get(str(task["id"]), [])

        user_message = f"""
태스크: {task['title']}
PASS 기준:
{chr(10).join(f'- {c}' for c in criteria)}

실행 결과:
- 종료 코드: {critic_result.get('exit_code', -1)}
- 실행 시간: {critic_result.get('execution_time', 0):.2f}초
- 표준 출력: {critic_result.get('stdout', '없음')[:500]}
- 오류: {critic_result.get('stderr', '없음')[:500]}
- 현재 루프 횟수: {loop_count}

모든 PASS 기준을 충족하면 PASS.
일부 미충족 시 REVISE (70점 이상) 또는 ENRICH (70점 미만).
판정 불가 또는 반복 실패 시 ESCALATE.

다음 JSON 형식으로만 응답하세요:
{{"verdict": "PASS|REVISE|ENRICH|ESCALATE", "score": 0-100,
  "reason": "판정 근거", "error_type": "오류 유형 (없으면 null)"}}
"""
        # 새 컨텍스트로 호출, 이전 대화 없음
        response = await self._call_fresh(user_message)
        return self._parse(response)

    async def _call_fresh(self, user_message: str) -> str:
        """항상 새 컨텍스트 — 이전 대화 기록 없음"""
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message}
            ],
            "temperature": 0.1,
            "max_tokens": 512
        }
        resp = await self.client.post(JUDGE_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]

    def _parse(self, response: str) -> JudgeResult:
        try:
            data = json.loads(response)
            return JudgeResult(
                verdict=data["verdict"],
                score=data["score"],
                reason=data["reason"],
                error_type=data.get("error_type", "")
            )
        except Exception:
            return JudgeResult(verdict="ESCALATE", score=0,
                               reason="파싱 실패", error_type="parse_error")
```

---

## 8. 자기 평가 루프 설계

### 8.1 루프 상태 머신

```
[PLAN] ──Brain──→ [BUILD] ──Hands──→ [CRITIQUE] ──Critic──→ [EVALUATE] ──Judge──→
                                                                    │
                                          ┌─────────────────────────┤
                                          ↓                         ↓
                                       PASS (90+)            REVISE/ENRICH
                                          │                    (자율 수정)
                                          ↓                         │
                                   다음 태스크 or               [REVISE]──→ [CRITIQUE]
                                    [COMPLETE]                        │
                                          │              범위 초과 or 횟수 초과
                                          ↓                         ↓
                                   Brain 최종 검토            [ESCALATE]──Brain──→ 계획 수정
                                                                               or 사람에게 보고
```

### 8.2 점수 기준

| 점수 | 판정 | 행동 |
|---|---|---|
| 90~100 | PASS | 다음 태스크로 진행 |
| 70~89 | REVISE | Hands 자율 수정 (수정 범위 내) |
| 0~69 | ENRICH | Hands 자율 수정 (범위 내) 또는 Brain 에스컬레이션 |
| - | ESCALATE | Brain 재호출 |

### 8.3 에스컬레이션 조건

| 조건 | 기준 | 행동 |
|---|---|---|
| 연속 실패 | 같은 태스크가 3회 연속 실패 | Brain 호출 → 원인 분류 |
| 최대 루프 | 총 루프가 5회 초과 | 사람에게 보고 |
| 판정 불가 | Judge ESCALATE | Brain 호출 |
| 수정 범위 초과 | 아키텍처 변경이 필요하다고 판단 | Brain 호출 |

---

## 9. Critic 샌드박스 실행 엔진

### 9.1 CriticRunner (`execution/critic_runner.py`)

```python
import docker
import tempfile
import os
import time
from dataclasses import dataclass

@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_type: str = None  # Judge의 자율 수정 가능 여부 판단용

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
        Docker 샌드박스에서 코드 실행.
        /dev/shm tmpfs를 사용하여 디스크 I/O 최소화.
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
                cpu_quota=100000,   # CPU 코어 1개
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
        """오류 유형 분류 — Hands의 자율 수정 가능 여부 판단에 사용"""
        for error_type, pattern in self.ERROR_PATTERNS.items():
            if pattern in stderr:
                return error_type
        return "unknown_error"

    def format_report(self, result: ExecutionResult, task_title: str,
                      loop_count: int) -> str:
        return f"""## 실행 결과 보고서
- 태스크: {task_title}
- 루프 반복: {loop_count}
- 종료 코드: {result.exit_code} ({'정상' if result.exit_code == 0 else '비정상'})
- 실행 시간: {result.execution_time:.2f}s
- 오류 유형: {result.error_type or '없음'}
- 표준 출력: {result.stdout[:500] or '없음'}
- 표준 오류: {result.stderr[:500] or '없음'}"""
```

### 9.2 Docker 샌드박스 이미지 (`docker/sandbox/Dockerfile`)

```dockerfile
FROM python:3.11-slim

# 보안: 루트 권한 제거
RUN useradd -m -u 1000 sandbox
WORKDIR /workspace
USER sandbox

# 필수 패키지만 설치
RUN pip install --no-cache-dir \
    requests \
    pydantic \
    fastapi \
    httpx

# 실행 시간 제한
CMD ["python"]
```

---

## 10. 스킬 라이브러리 시스템

### 10.1 스킬 파일 형식

```markdown
---
id: skill_001
name: FastAPI 타입 안전성
status: verified          # verified | candidate
category: web_backend
created_at: 2025-XX-XX
source_task_ids: [001, 003, 007]
fail_count: 4             # 이 스킬 부재로 태스크가 실패한 횟수
reviewed_by: human        # human | auto
---

## 적용 조건
FastAPI 엔드포인트 구현 시 항상 적용

## 규칙
- 모든 함수 파라미터에 타입 힌트 지정
- int/str 혼용이 가능한 입력에는 명시적 캐스팅 사용
- 요청/응답 스키마에 Pydantic BaseModel 사용
- 선택적 파라미터는 Optional[T] = None 형식 사용

## 적용 예시
\```python
from pydantic import BaseModel
from typing import Optional

class UserRequest(BaseModel):
    user_id: int
    name: str
    email: Optional[str] = None
\```

## 금지 패턴
- 타입 힌트 없는 함수 파라미터
- 원시 dict로 요청/응답 처리
```

### 10.2 SkillManager (`skill/skill_manager.py`)

```python
import os
import yaml
from pathlib import Path
from typing import Optional

SKILL_BASE = Path("/pyvis_memory/skill_library")
VERIFIED_DIR = SKILL_BASE / "verified"
CANDIDATE_DIR = SKILL_BASE / "candidate"

class SkillManager:

    def load_verified(self, task_description: str) -> str:
        """
        검증된 스킬만 로드하여 프롬프트에 삽입.
        후보 스킬은 사용하지 않음.
        """
        relevant = self._find_relevant(task_description, status="verified")
        if not relevant:
            return "# 적용 가능한 스킬 없음"
        return "\n\n".join(skill["content"] for skill in relevant)

    def _find_relevant(self, task_description: str, status: str) -> list:
        """키워드 기반 관련 스킬 검색 (추후 FAISS 임베딩 검색으로 업그레이드 가능)"""
        skill_dir = VERIFIED_DIR if status == "verified" else CANDIDATE_DIR
        results = []
        for skill_file in skill_dir.glob("*.md"):
            with open(skill_file) as f:
                content = f.read()
            # 단순 키워드 매칭 (초기 구현)
            if any(kw.lower() in task_description.lower()
               for kw in self._extract_keywords(content)):
                results.append({"file": skill_file.name, "content": content})
        return results

    def _extract_keywords(self, skill_content: str) -> list:
        """스킬 파일의 category와 name에서 키워드 추출"""
        keywords = []
        for line in skill_content.split('\n')[:20]:
            if 'category:' in line:
                keywords.extend(line.split(':')[1].strip().split('_'))
            if 'name:' in line:
                keywords.extend(line.split(':')[1].strip().split())
        return keywords

    async def evaluate_and_patch(self, ctx, loop_record: dict):
        """루프 완료 후 스킬 추가 필요 여부 판단"""
        from pyvis.skill.skill_validator import SkillValidator
        validator = SkillValidator()
        needs_skill = validator.should_add_skill(loop_record, self._get_history())
        if needs_skill:
            await self._create_candidate(loop_record)

    def _get_history(self) -> list:
        """최근 루프 기록"""
        records_dir = Path("/pyvis_memory/loop_records")
        records = []
        for f in sorted(records_dir.glob("*.jsonl"))[-50:]:  # 최근 50개
            with open(f) as fh:
                import json
                for line in fh:
                    records.append(json.loads(line))
        return records

    async def _create_candidate(self, loop_record: dict):
        """Brain이 스킬 초안 작성 후 candidate에 저장"""
        # Brain에 스킬 초안 요청
        skill_draft = await self._request_skill_draft(loop_record)
        candidate_path = CANDIDATE_DIR / f"skill_{loop_record['task_id']}.md"
        with open(candidate_path, 'w') as f:
            f.write(skill_draft)
        # 사람에게 검토 알림
        self._notify_review_needed(candidate_path)
```

---

## 11. 루프 비용 추적 + 선택적 스킬 강화

### 11.1 LoopTracker (`tracking/loop_tracker.py`)

```python
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict, field
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
    switch_count: int = 0          # 모델 전환 횟수
    escalated: bool = False
    fail_reasons: list = field(default_factory=list)
    final_quality: str = ""        # PASS | ESCALATED
    skill_patch_added: bool = False

class LoopTracker:
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

### 11.2 SkillValidator — 선택적 강화 결정 (`skill/skill_validator.py`)

```python
from collections import Counter

class SkillValidator:
    """
    스킬 추가 조건 (4가지 모두 충족해야 함):
    1. 반복성: 서로 다른 3개 이상의 태스크에서 같은 유형의 실수가 발생
    2. 범용성: 특정 태스크에만 국한된 일회성 예외가 아닐 것
    3. 수정 가능성: 해당 오류 유형이 스킬로 실제로 예방 가능할 것
    4. 중복 없음: 기존 스킬에서 이미 다루지 않는 내용일 것
    """

    NOT_FIXABLE_BY_SKILL = {
        "docker_error", "unknown_error", "environment_error", "network_error"
    }

    def should_add_skill(self, current_record: dict, history: list) -> bool:
        fail_reasons = [f["reason"] for f in current_record.get("fail_reasons", [])]
        if not fail_reasons:
            return False

        for reason in set(fail_reasons):
            if self._check_all_conditions(reason, current_record, history):
                return True
        return False

    def _check_all_conditions(self, reason: str, current: dict,
                               history: list) -> bool:
        # 1. 반복성: 서로 다른 태스크에서 3회 이상 발생
        other_task_count = sum(
            1 for record in history
            if record["task_id"] != current["task_id"]
            and any(reason in f["reason"] for f in record.get("fail_reasons", []))
        )
        if other_task_count < 2:  # 현재 포함 총 3회
            return False

        # 2. 범용성: 특정 task_id 하나에만 국한되지 않음
        task_ids_with_reason = [
            record["task_id"] for record in history
            if any(reason in f["reason"] for f in record.get("fail_reasons", []))
        ]
        if len(set(task_ids_with_reason)) < 3:
            return False

        # 3. 수정 가능성
        if reason in self.NOT_FIXABLE_BY_SKILL:
            return False

        # 4. 중복 없음 (기존 스킬 파일명 기반 단순 검사)
        if self._already_exists(reason):
            return False

        return True

    def _already_exists(self, reason: str) -> bool:
        from pathlib import Path
        skill_dir = Path("/pyvis_memory/skill_library/verified")
        return any(reason.lower().replace(" ", "_") in f.stem
                   for f in skill_dir.glob("*.md"))
```

---

## 12. MCP 자율 툴 설치

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

    def register(self, tool_name: str, tool_meta: dict):
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
    Brain이 필요 시 자동 설치.
    승인 모드: requires_approval=True이면 설치 전 사람의 확인 필요.
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
            print(f"툴 설치 실패: {tool['name']} — {e}")
        return False

    def _request_approval(self, tool: dict):
        # 사람에게 알림 (텔레그램, 로그 등)
        print(f"[승인 필요] 툴 설치가 필요합니다: {tool['name']} — {tool.get('reason', '')}")
```

---

## 13. 장기 메모리 시스템

### 13.1 KG 서버 (`memory/kg_server.py`)

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
        self.dim = 384  # 임베딩 차원
        self.index = faiss.IndexFlatL2(self.dim)
        self.metadata = []
        self._load()

    def add(self, text: str, meta: dict):
        embedding = self._embed(text)
        self.index.add(np.array([embedding], dtype=np.float32))
        self.metadata.append(meta)

    def search(self, query: str, k: int = 5) -> list:
        embedding = self._embed(query)
        D, I = self.index.search(np.array([embedding], dtype=np.float32), k)
        return [self.metadata[i] for i in I[0] if i < len(self.metadata)]

    def _embed(self, text: str) -> list:
        # 경량 임베딩 모델 사용 (예: sentence-transformers/all-MiniLM-L6-v2)
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM-L6-v2')
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

### 13.2 스토리지 구조

```
/pyvis_memory/
├── models/                          # ~50GB: GGUF 모델 파일
│   ├── DeepSeek-R1-Distill-Qwen-32B-Q4_K_S.gguf
│   └── Qwen2.5-Coder-32B-Instruct-Q4_K_S.gguf
├── user_profile/
│   └── profile.json                 # 사용자 선호도, 성향, 스택
├── conversation_log/
│   └── YYYY-MM-DD.jsonl             # 세션별 대화 기록
├── project_history/
│   └── {task_id}/                   # 프로젝트별 의사결정 기록
├── knowledge_graph/
│   ├── index.faiss                  # FAISS 인덱스
│   └── metadata.pkl                 # 벡터 메타데이터
├── skill_library/
│   ├── verified/                    # 검증된 스킬 (자동 적용)
│   └── candidate/                   # 검토 대기 스킬 (미적용)
├── loop_records/
│   └── YYYY-MM-DD.jsonl             # 루프 비용 추적 로그
└── research_cache/
    └── {query_hash}.json            # 웹 검색 결과 캐시
```

---

## 14. 인터페이스 레이어 (4단계 예약)

> 일시 중단 — 구현 연기. 시스템 시그니처만 정의.

```python
# interface/audio.py (4단계 구현 예정)
class AudioModule:
    """Whisper 기반 STT/TTS"""
    wake_word: str = "hey pyvis"
    sample_rate: int = 16000

# interface/vision.py (4단계 구현 예정)
class VisionModule:
    """화면 캡처 및 분석"""
    port: int = 9999

# interface/telegram_bot.py (4단계 구현 예정)
class TelegramBot:
    """텔레그램 봇 인터페이스"""
    webhook_url: str = "http://localhost:8080/webhook"
```

---

## 15. 설정 파일

> **주의**: 아래에 포함된 YAML 설정은 **구버전**입니다 — 이전 2-GPU, 2포트 아키텍처 설계(GPU 0 포트 8001, GPU 1 포트 8002)와 구형 모델(DeepSeek-R1-Distill-Qwen-32B, Qwen2.5-Coder-32B)을 반영합니다. **현재** 프로덕션 설정은 포트 8001의 단일 서버 스왑 아키텍처와 업데이트된 모델(GLM-4.7-Flash, Qwen3-14B, Devstral-24B, DeepSeek-R1-Distill-Qwen-14B)을 사용합니다. 최신 권위 있는 설정은 `config/unified_node.yaml`을 참조하세요.

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
      name: "RTX 4070S"
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
    cot_strip: true            # <think> 블록 제거
  hands:
    system_prompt: "system/hands_prompt.txt"
    temperature: 0.2
    max_tokens: 8192
  judge:
    system_prompt: "system/judge_prompt.txt"
    temperature: 0.1
    max_tokens: 512
    kv_cache_reset: true       # 매번 KV 캐시 초기화 필요
    fresh_context: true        # 이전 대화 기록 없음

research_loop:
  max_loops: 5
  max_consecutive_fails: 3
  pass_threshold: 90           # 90 이상 = PASS
  revise_threshold: 70         # 70 이상 = REVISE, 미만 = ENRICH
  sandbox_timeout: 30
  min_repeat_count: 3          # 스킬 추가를 위한 최소 반복 실패 횟수
  min_task_diversity: 3        # 최소 서로 다른 태스크 수
  requires_human_review: true  # verified 승격 전 사람 검토 필요

mcp:
  requires_approval: true      # 툴 설치 전 사람 승인 필요

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

## 16. 구현 로드맵 및 단계별 작업

### 1단계: Rust 코어 (1~2주)

```
- [ ] Cargo.toml 워크스페이스 설정
- [ ] crossbeam 기반 락-프리 우선순위 큐 구현 및 테스트
- [ ] CPU 어피니티 스레드 풀 구현
- [ ] 모델 핫스왑 컨트롤러 구현 (ModelHotSwap)
- [ ] PyO3 Python 바인딩 빌드 및 Python import 검증
- [ ] 유닛 테스트 (cargo test)
```

### 2단계: AI 엔진 (3~4주)

```
- [ ] llama.cpp CUDA 빌드 (sm_86 + sm_89)
- [ ] Brain 서버 시작 및 검증 (GPU 0, 포트 8001)
- [ ] Hands/Judge 서버 시작 및 검증 (GPU 1, 포트 8002)
- [ ] VRAM 사용량 측정 및 최적 n_gpu_layers 값 결정
- [ ] Brain 클라이언트 구현 및 CoT 전처리 검증
- [ ] Hands 클라이언트 구현
- [ ] Judge 클라이언트 구현 (KV 캐시 초기화 검증 필수)
- [ ] 시스템 프롬프트 3개 작성 (brain/hands/judge_prompt.txt)
```

### 3단계: 오케스트레이션 (5~6주)

```
- [ ] Docker 샌드박스 이미지 빌드 및 테스트
  - macOS 지원: OrbStack을 Docker 대안으로 문서화, 실행 절차 포함
- [ ] CriticRunner 구현 및 오류 분류 검증
- [ ] LoopController 구현 (전체 루프 상태 머신)
- [ ] LoopTracker 구현 및 JSONL 영속성 검증
- [ ] SkillManager 구현 (verified/candidate 분리)
- [ ] SkillValidator 구현 (4가지 조건 검증)
- [ ] MCP ToolRegistry + ToolInstaller 구현
- [ ] FastAPI KG 서버 구현 (FAISS CPU)
- [ ] 세션 매니저 구현
- [ ] E2E 통합 테스트 (간단한 태스크로 전체 루프 검증)
```

### 4단계: 안정화 (7~8주)

```
- [ ] 메모리 누수 탐지 (Valgrind, heaptrack)
- [ ] 스트레스 테스트 (10회 연속 루프)
- [ ] 에스컬레이션 시나리오 테스트
- [ ] 루프 비용 추적 → 스킬 강화 파이프라인 검증
- [ ] 성능 프로파일링 (모델 전환 지연 시간 측정)
- [ ] 설정 파일 기반 동작 검증

5단계 이후 (예약):
- [ ] 인터페이스 레이어 (STT/TTS, 비전, 텔레그램)
- [ ] 웹 서비스 확장
```

---

## 17. 시스템 프롬프트 정의

### `system/brain_prompt.txt`

```
당신은 Pyvis의 Brain입니다.

역할:
- 태스크를 분석하고 실행 가능한 계획을 수립합니다
- TODO List, PASS 기준, 수정 범위를 명확하게 정의합니다
- 에스컬레이션 원인을 분석하고 계획을 수정합니다
- 최종 결과물을 검토합니다

절대 규칙:
- 코드를 직접 생성하지 않습니다
- 모든 구현은 Hands에게 위임합니다
- 요청된 형식(JSON)으로만 응답합니다
```

### `system/hands_prompt.txt`

```
당신은 Pyvis의 Hands입니다.

역할:
- Brain의 계획을 기반으로 코드를 구현합니다
- 수정 지시가 주어지면 허용된 범위 내에서만 수정합니다

절대 규칙:
- 계획에 명시되지 않은 설계 결정을 내리지 않습니다
- 수정 범위 외의 변경을 하지 않습니다
- 코드만 출력합니다. 설명은 최소화합니다.
```

### `system/judge_prompt.txt`

```
당신은 Pyvis의 Judge입니다.

역할:
- 계획의 PASS 기준과 실행 결과만으로 판정합니다
- 코드 구현 방법이나 과정에 대한 지식이 없습니다
- 결과물이 요구사항을 충족하는지만 평가합니다

절대 규칙:
- 칭찬하지 않습니다
- 정확히 하나의 판정을 내립니다: PASS / REVISE / ENRICH / ESCALATE
- JSON 형식으로만 응답합니다
- 이전 대화 기록이 없습니다. 지금 보이는 것만으로 판정합니다
```

---

## 18. 리스크 요소 및 대응 방안

| 리스크 | 영향도 | 대응 방안 |
|---|---|---|
| VRAM 부족 | 높음 | n_gpu_layers 조정; 실제 측정 후 최적값 결정 |
| Rust-Python 경계 버그 | 중간 | PyO3 엄격한 타입 체크, 유닛 테스트 |
| Judge 자기 합리화 | 중간 | KV 캐시 초기화 + 컨텍스트 격리 + 강제 fresh_context |
| 무한 루프 | 중간 | max_loops 하드 캡 5회, 타임아웃 설정 |
| 스킬 오염 | 중간 | candidate/verified 분리, 사람 검토 필수 |
| 샌드박스 보안 | 높음 | network=none, mem_limit, cpu_limit, 루트 권한 제거 |
| 모델 전환 지연 | 중간 | 두 모델 모두 RAM에 상주; 컨텍스트 스위치만 사용 |
| FAISS 인덱스 손상 | 낮음 | 세션 종료 시 save() 호출, 주기적 백업 |

---

## 성능 목표

| 지표 | 목표 |
|---|---|
| Brain 추론 속도 | 15-25 t/s |
| Hands/Judge 추론 속도 | 15-25 t/s |
| 모델 전환 지연 | < 100ms (컨텍스트 스위치) |
| Critic 실행 지연 | < 30s (타임아웃) |
| KG 검색 지연 | < 1ms |
| 단일 루프 소요 시간 | 2-5분 |
| 총 모델 전환 횟수 | 최소 2회 (에스컬레이션 없을 때) |

---

*— Pyvis v4.0 구현 설계 문서 끝 —*  
*구현: Claude Opus 4.6*
