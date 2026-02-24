# PYVIS v5.1 — 전체 아키텍처 설계서
> **목표**: Jarvis 형 자율 AI 비서
> **구현 담당**: Claude Opus 4.6
> **최종 수정**: 2026-02-24 (Phase 3 완료)
> **목표**: Jarvis형 자율 AI 비서  
> **구현 담당**: Claude Opus 4.6  
> **최종 수정**: 2026-02-22

---

## 목차
1. [전체 시스템 구조](#1-전체-시스템-구조)
2. [하드웨어 & 모델 스택](#2-하드웨어--모델-스택)
3. [레이어 구조 상세](#3-레이어-구조-상세)
4. [Chat Chain — 합의 루프](#4-chat-chain--합의-루프)
5. [CoT 추론 체인 — Planner](#5-cot-추론-체인--planner)
6. [파일 단위 코딩 파이프라인](#6-파일-단위-코딩-파이프라인)
7. [컨텍스트 관리 정책](#7-컨텍스트-관리-정책)
8. [자기평가 루프](#8-자기평가-루프)
9. [지식 그래프 메모리](#9-지식-그래프-메모리)
10. [Skill 라이브러리](#10-skill-라이브러리)
11. [언어 경계 정책](#11-언어-경계-정책)
12. [루프 비용 트래킹](#12-루프-비용-트래킹)
13. [Rust 코어 레이어](#13-rust-코어-레이어)
14. [디렉토리 구조](#14-디렉토리-구조)
15. [설정 파일](#15-설정-파일)
16. [구현 로드맵](#16-구현-로드맵)
17. [시스템 프롬프트](#17-시스템-프롬프트)

---

## 1. 전체 시스템 구조

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PYVIS v5.0                                   │
│                    "Jarvis형 자율 AI 비서"                            │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 0: Interface (4단계 예약)                                      │
│  ├── Telegram Bot                  (현재 CLI로 대체)                   │
│  ├── Audio: Whisper STT / TTS      (예약)                             │
│  ├── Vision: Screen Capture        (예약)                             │
│  └── WebSocket Server              (예약)                             │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 1: Rust Core (pyvis_core)                                      │
│  ├── Lock-Free Priority Queue      (crossbeam)                        │
│  ├── CPU Affinity Thread Pool                                         │
│  ├── Model Swap Controller                                            │
│  └── PyO3 Python Bindings                                             │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 2: Orchestration (Python)                                      │
│  ├── Session Manager                                                  │
│  ├── Chat Chain Controller    ← NEW (ChatDev 방식)                    │
│  ├── Research Loop Controller                                         │
│  ├── Context Manager          ← NEW (58K 고정 / 동적 스케일)          │
│  ├── Symbol Extractor         ← NEW (파일 단위 심볼 추출)             │
│  ├── Skill Manager                                                    │
│  ├── Loop Cost Tracker                                                │
│  └── KG Builder               ← NEW (지식 그래프)                     │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 3: AI Engine (llama.cpp — tensor-split 12,12)                 │
│  ├── Planner : GLM-4.7-Flash Q4_K_M        port 8001                 │
│  ├── Brain   : Qwen3-14B Q5_K_M            port 8002                 │
│  ├── Hands   : Devstral-Small-2507 Q4_K_M  port 8003                 │
│  └── Judge   : DeepSeek-R1-Distill-14B Q5_K_M  port 8004            │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 4: Execution Engine                                            │
│  └── Critic: Docker Sandbox (/dev/shm tmpfs)                         │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 5: Memory & Storage                                            │
│  ├── Hot  : FAISS + NetworkX KG (CPU RAM)                            │
│  └── Cold : NVMe /pyvis_memory/                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. 하드웨어 & 모델 스택

### 2.1 하드웨어

| 항목 | 사양 |
|---|---|
| GPU 0 | RTX 4070 12GB (sm_89) |
| GPU 1 | RTX 3060 12GB (sm_86) |
| VRAM 합계 | 24GB (tensor-split 12,12) |
| RAM | 32GB |
| NVMe | /pyvis_memory/ 전용 1TB |

### 2.2 확정 모델 스택

| 역할 | 모델 | 양자화 | 파일크기 | GPU당 가중치 | KV Cache | GPU당 합계 | 포트 |
|---|---|---|---|---|---|---|---|
| **Planner** | GLM-4.7-Flash | Q4_K_M | ~15 GB | ~7.5 GB | Q8_0 / ~1.5 GB | **~9.5 GB ✅** | 8001 |
| **Brain** | Qwen3-14B | Q5_K_M | ~10.5 GB | ~5.25 GB | Q8_0 / ~2.56 GB | **~8.3 GB ✅** | 8002 |
| **Hands** | Devstral-Small-2507 | Q4_K_M | ~14.3 GB | ~7.15 GB | Q4_0 / ~1.28 GB | **~8.9 GB ✅** | 8003 |
| **Judge** | DeepSeek-R1-Distill-14B | Q5_K_M | ~9.5 GB | ~4.75 GB | Q8_0 / ~1.28 GB | **~6.5 GB ✅** | 8004 |

> ⚠️ Hands KV Cache는 반드시 Q4_0 유지 (Q8_0 시 GPU당 10.21GB → WSL2 경계)  
> ⚠️ 모든 모델 GPU당 10GB 미만 유지 (WSL2 안전 여유 2GB 이상)

### 2.3 CUDA 빌드

```bash
cmake .. -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES="86;89"
make -j$(nproc)
```

### 2.4 모델별 실행 명령어

```bash
# Planner (GLM-4.7-Flash)
./llama-server -m GLM-4.7-Flash-Q4_K_M.gguf \
  -ngl 99 --ctx-size 32768 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --tensor-split 12,12 --threads 4 --port 8001

# Brain (Qwen3-14B)
./llama-server -m Qwen3-14B-Q5_K_M.gguf \
  -ngl 99 --ctx-size 32768 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --tensor-split 12,12 --threads 4 --port 8002

# Hands (Devstral) — v5.1: Dual Mode
# Symbol 추출 성공: 32K + q8_0
# Symbol 추출 실패: 58K + q4_0 (fallback)
./llama-server -m Devstral-Small-2507-Q4_K_M.gguf \
 -ngl 99 --ctx-size 32768 \
 --cache-type-k q8_0 --cache-type-v q8_0 \
 --tensor-split 12,12 --threads 4 --port 8003
./llama-server -m Devstral-Small-2507-Q4_K_M.gguf \
  -ngl 99 --ctx-size 58368 \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  --tensor-split 12,12 --threads 4 --port 8003

# Judge (DeepSeek-R1-Distill-14B)
./llama-server -m DeepSeek-R1-Distill-Qwen-14B-Q5_K_M.gguf \
  -ngl 99 --ctx-size 16384 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --tensor-split 12,12 --threads 4 --port 8004
```

### 2.5 성능 기대치

| 역할 | 속도 | 코딩 품질 |
|---|---|---|
| Planner | 25~35 t/s | 범용 설계 |
| Brain | 35~50 t/s | 분석·판단 |
| Hands | 20~30 t/s | SWE-bench 53.6% (오픈소스 1위) |
| Judge | 35~50 t/s | 출력 512토큰 이하 → 체감 매우 빠름 |
| 파이프라인 전체 | — | 65~75% 예상 (구조 보정) |

---

## 3. 레이어 구조 상세

### 3.1 역할 정의 (절대 규칙)

| 역할 | 담당 | 절대 하지 않는 것 |
|---|---|---|
| **Planner** | 과제 분류·분해, CoT 추론, 파일 목록·순서 설계, TODO, PASS 기준, 에스컬레이션 기준 | 코드 생성, 직접 수정 |
| **Brain** | Critic 결과 해석, 수정 지시 작성, 에스컬레이션 원인 분류, 한↔영 변환, 심볼 추출, 최종 요약 | 코드 생성, 전체 설계 |
| **Hands** | 영어 계획서 기반 파일 단위 코드 생성, 수정 루프 | 설계, 평가, 한국어 입력 수신 |
| **Judge** | KV Cache 초기화 후 독립 평가, PASS/FAIL/ESCALATE | 코드 수정, 계획 변경 |
| **Critic** | Docker 샌드박스 코드 실행, 결과 수집, 에러 분류 | 평가, 수정 |

### 3.2 CPU 코어 배분 (8코어 기준)

| 코어 | 담당 |
|---|---|
| 0~1 | Interface / IO (FastAPI, KG 서버, FAISS) |
| 2~3 | Orchestration (루프 컨트롤러, Skill, 트래커) |
| 4~7 | AI Inference (llama.cpp 전담) |

---

## 4. Chat Chain — 합의 루프

ChatDev의 Chat Chain을 Pyvis에 선택적으로 적용합니다.  
**전체가 아닌 두 구간에만 적용**합니다.

### 4.1 적용 구간

```
구간 A: Planner ↔ Brain  (설계 합의)
  → Planner가 계획서 초안 작성
  → Brain이 실행 가능성 검토, 문제 제기
  → 합의 도달 시 Hands에 전달

구간 B: Brain ↔ Hands  (수정 합의)
  → Brain이 수정 지시
  → Hands가 수정 범위·가능성 확인
  → 합의 후 재생성
```

### 4.2 합의 루프 구현

```python
# pyvis/orchestration/chat_chain.py

class ChatChainController:
    """
    ChatDev 방식의 1:1 합의 루프.
    Instructor가 지시 → Assistant가 수행 또는 반론
    → [CONSENSUS] 태그 등장 시 합의 완료
    """

    async def consensus_loop(
        self,
        instructor,       # 지시자 (Planner 또는 Brain)
        assistant,        # 조력자 (Brain 또는 Hands)
        topic: str,
        context: dict,
        max_turns: int = 3
    ) -> ConsensusResult:

        messages = []
        for turn in range(max_turns):
            # 지시자 발화
            inst_output = await instructor.instruct(topic, messages, context)
            messages.append(Message(role="instructor", content=inst_output))

            # 합의 확인
            if "[CONSENSUS]" in inst_output:
                return ConsensusResult(agreed=True, messages=messages, turns=turn+1)

            # 조력자 응답
            asst_output = await assistant.respond(messages, context)
            messages.append(Message(role="assistant", content=asst_output))

            if "[CONSENSUS]" in asst_output:
                return ConsensusResult(agreed=True, messages=messages, turns=turn+1)

        # max_turns 초과 시 마지막 상태로 강제 합의
        return ConsensusResult(agreed=False, messages=messages, turns=max_turns)
### 4.2 Hard Limit 인터럽트 (v5.1)

Chat Chain 은 **무한 루프 방지**를 위해 5 종 Hard Limit 을 가집니다:

```python
# pyovis/orchestration/hard_limit.py

class HardLimitTrigger(str, Enum):
    DIFF_TOO_SMALL = "diff_too_small"  # meaningless repetition (< 3 lines)
    AST_ERROR_REPEAT = "ast_error_repeat"  # code structure collapse (2+)
    CLARIFICATION_LOOP = "clarification_loop"  # unclear instructions (3+)
    MAX_TURNS = "max_turns"  # turn limit exceeded
    SYCOPHANCY = "sycophancy"  # blind agreement to erroneous code

# Chat Chain Controller 에서 자동 체크
async def consensus_loop(...):
    for turn in range(max_turns):
        # ... 대화 진행 ...
        
        # Hard Limit 체크
        if diff_lines < min_diff_lines:
            return ConsensusResult(
                agreed=False,
                termination_reason="hard_limit_diff"
            )
        
        if ast_error_count >= max_ast_errors:
            return ConsensusResult(
                agreed=False,
                termination_reason="hard_limit_ast"
            )
```

### 4.3 Execution Plan (v5.1 Phase 3)

Hands 가 Judge 에게 **실행 방법**을 전달합니다:

```python
# pyovis/execution/execution_plan.py

@dataclass
class ExecutionPlan:
    execution_type: ExecutionType  # script/module/test/function/API/CLI
    entry_point: Optional[str]
    test_cases: list[TestCase]
    expected_files: list[str]
    environment_vars: dict

# Hands 에서 생성
exec_plan = create_execution_plan_from_task(task, code, pass_criteria)

# Judge 에 전달
judge_result = await judge.evaluate(
    task=task,
    pass_criteria=criteria,
    critic_result=critic_result,
    execution_plan=exec_plan.to_dict()  # NEW
)
```

### 4.4 Thought Instruction — Judge 4 단계 체크리스트 (v5.1 Phase 3)

Judge 는 **블랙박스 판단이 아닌 투명한 4 단계 체크리스트**를 수행합니다:

```python
# pyovis/ai/judge_enhanced.py

class EnhancedJudge:
    """4-step Thought Instruction checklist"""
    
    async def evaluate(self, ...) -> JudgeResult:
        # CHECK 1: Exit code validation
        # CHECK 2: PASS criteria verification
        # CHECK 3: Missing symbols detection
        # CHECK 4: Error classification
        
        return JudgeResult(
            verdict="PASS",
            score=95,
            check_results={
                "exit_code": CheckResult("exit_code", True, "Exit code 0"),
                "criterion_0": CheckResult("criterion_0", True, "SATISFIED"),
                "missing_symbols": CheckResult("missing_symbols", False, "requests not found"),
            },
            thought_process="[CHECK 1] Exit code is 0 ✓\n[CHECK 2] ...",
            execution_plan_validated=True
        )
```

---

# 사용 예시

# 구간 A: Planner ↔ Brain 설계 합의
result_A = await chat_chain.consensus_loop(
    instructor=planner,
    assistant=brain,
    topic="FastAPI 인증 모듈 설계",
    context={"task": task, "kg_context": kg_context}
)

# 구간 B: Brain ↔ Hands 수정 합의
result_B = await chat_chain.consensus_loop(
    instructor=brain,
    assistant=hands,
    topic="타입 에러 수정 범위 확인",
    context={"error": critic_result, "scope": self_fix_scope}
)
```

### 4.3 합의 프롬프트 예시

**구간 A — Brain의 검토 응답 형식**
```
[이전 Planner 계획서 검토]

동의 사항:
- 파일 분리 구조 타당

문제 제기:
- auth.py가 500줄 예상됨. 로직 기준으로 auth_core.py / auth_router.py로 분리 권장

[CONSENSUS] 수락 시 또는 수정안 제시 시 태그 사용
```

**구간 B — Hands의 반론 형식**
```
[수정 요청 검토]

수행 가능:
- 타입 힌트 추가

수행 불가 (수정 권한 초과):
- User 모델 스키마 변경은 models/user.py 수정 필요
- 현재 권한 범위(auth.py 단독) 초과

→ Brain에게 권한 범위 확장 또는 에스컬레이션 요청
```

---

## 5. CoT 추론 체인 — Planner

말씀하신 "꼬리에 꼬리를 무는 추론"을 Planner 시스템 프롬프트에 강제합니다.  
Least-to-Most + ReAct + Tree of Thoughts 혼합 방식입니다.

### 5.1 Planner 내부 추론 구조

```
[1단계: 요청 분류]
  이 요청은 어떤 유형인가?
  → 신규 기능 / 버그 수정 / 리팩토링 / 설계 변경 / 분석

[2단계: 문제 정의]
  해결해야 할 핵심 문제가 정확히 무엇인가?
  하위 문제로 분해하면?

[3단계: 의존성 탐색]
  선행 조건이 있는가?
  어떤 파일이 관련되는가?
  어떤 Skill이 적용 가능한가?
  MCP 도구가 필요한가?

[4단계: 경로 선택]
  방법 A: ___  → 장점/단점
  방법 B: ___  → 장점/단점
  선택: ___ (이유)
  실패 시 백트래킹 경로: ___

[5단계: 실행 계획 확정]
  파일 목록 + 작업 순서
  TODO List (파일 단위)
  각 파일의 PASS 기준
  Hands 수정 권한 범위
```

### 5.2 Planner 시스템 프롬프트

```
당신은 Pyvis의 Planner입니다. GLM-4.7-Flash 모델입니다.

모든 과제에 대해 반드시 다음 5단계 순서로 생각하십시오.
단계를 생략하거나 순서를 바꾸는 것은 금지입니다.

[1단계: 요청 분류]
[2단계: 문제 정의 및 하위 분해]
[3단계: 의존성 및 선행 조건 탐색]
[4단계: 실행 경로 선택 및 백트래킹 준비]
[5단계: 실행 계획 JSON 출력]

출력 규칙:
- 1~4단계: 자유 형식 한국어 추론 (Brain이 읽음)
- 5단계: 반드시 영어 JSON (Hands에게 전달)
- 코드를 직접 생성하지 않습니다
```

---

## 6. 파일 단위 코딩 파이프라인

Hands는 한 번에 파일 하나씩만 작업합니다.  
다른 파일의 내용은 **심볼 요약**으로만 전달합니다.

### 6.1 전체 흐름

```
Planner: 파일 목록 + 작업 순서 확정
  file_order: [models/user.py, core/security.py, api/auth.py, main.py]

Brain: 작업 전 심볼 추출
  현재 작업 파일: api/auth.py
  의존 파일들:
    → models/user.py 에서: class User 스펙만 추출
    → core/security.py 에서: verify_password(), create_token() 시그니처만 추출

Hands: api/auth.py 단독 작업
  컨텍스트:
    ├── api/auth.py 계획 (2K)
    ├── 심볼 요약 (0.5K)   ← 전체 파일 아님
    ├── 관련 Skill (0.5K)
    └── 에러 기록 (루프 내)
  실제 사용: ~3K~10K (58K 대비 여유 충분)

완료 시:
  → api/auth.py 함수 시그니처를 KG에 저장
  → 다음 파일 작업 시 심볼로 참조
```

### 6.2 심볼 추출기

```python
# pyvis/orchestration/symbol_extractor.py

class SymbolExtractor:
    """
    Brain이 의존 파일에서 시그니처만 추출.
    전체 파일 내용 대신 최소 정보만 Hands에 전달.
    """

    SYMBOL_TEMPLATE = """
## 의존성 심볼 — {filename}

### 클래스
{classes}

### 함수/메서드
{functions}

### 상수/타입
{constants}
"""

    async def extract(self, file_path: str, full_code: str) -> str:
        """Brain에게 심볼 추출 요청"""
        prompt = f"""
다음 파일에서 다른 파일이 참조할 공개 심볼만 추출하라.
전체 구현 코드는 포함하지 말 것.

파일: {file_path}
```
{full_code[:4000]}  # 최대 4K만 전달
```

반드시 JSON으로만 응답:
{{
  "classes": [{{"name": "", "fields": [], "description": ""}}],
  "functions": [{{"signature": "", "description": ""}}],
  "constants": [{{"name": "", "type": "", "value": ""}}]
}}
"""
        return await self.brain.call(prompt)

    def format_for_hands(self, symbols: dict, filename: str) -> str:
        """Hands 프롬프트에 삽입할 형식으로 변환"""
        classes = "\n".join(
            f"- class {c['name']}: {c['description']} | fields: {c['fields']}"
            for c in symbols.get("classes", [])
        )
        functions = "\n".join(
            f"- {f['signature']}  # {f['description']}"
            for f in symbols.get("functions", [])
        )
        return self.SYMBOL_TEMPLATE.format(
            filename=filename,
            classes=classes or "없음",
            functions=functions or "없음",
            constants="\n".join(str(c) for c in symbols.get("constants", [])) or "없음"
        )
```

### 6.3 컨텍스트 절감 효과

```
전체 파일 3개 주입:  ~600줄 = ~4,000 토큰
심볼 요약 3개 주입:  ~30줄  =   ~400 토큰
절감:                           90% 이상
```

---

## 7. 컨텍스트 관리 정책

### 7.1 역할별 컨텍스트 고정값

| 역할 | 기본 ctx | 다운스케일 | 이유 |
|---|---|---|---|
| **Planner** | 32K | 16K | 설계 출력은 길지 않음 |
| **Brain** | 32K | 16K | 루프 내 분석, 중간 수준 |
| **Hands** | **58K 고정** | **없음** | 컨텍스트 줄이면 성능 하락 → 악순환 |
| **Judge** | 16K | 8K | 출력 512토큰, 최소로 충분 |

### 7.2 Hands 58K 불변 원칙

Hands의 컨텍스트를 줄이는 것은 금지입니다.

```
이유:
  컨텍스트 축소 → 의존성 누락 → 에러 증가
  → 루프 횟수 증가 → VRAM 부하 증가 → 악순환

대신:
  VRAM 압박 시 → Brain이 입력 압축 (심볼 요약)
  5xx 연속 시 → Planner에게 과제 더 잘게 분해 요청
  OOM/segfault → 에스컬레이션 (사람에게 보고)
```

### 7.3 SwapManager — Hands 제외 다운스케일

```python
# pyvis/ai/swap_manager.py

class CtxScale(Enum):
    FULL    = "full"
    REDUCED = "reduced"
    MINIMAL = "minimal"

CTX_CONFIG = {
    "planner": {CtxScale.FULL: 32768, CtxScale.REDUCED: 16384, CtxScale.MINIMAL: 8192},
    "brain":   {CtxScale.FULL: 32768, CtxScale.REDUCED: 16384, CtxScale.MINIMAL: 8192},
    "hands":   {CtxScale.FULL: 58368},   # 고정. 다운스케일 없음
    "judge":   {CtxScale.FULL: 16384, CtxScale.REDUCED: 8192,  CtxScale.MINIMAL: 4096},
}

class SwapManager:
    def get_ctx_size(self, role: str) -> int:
        if role == "hands":
            return 58368   # 절대 고정

        scale = self.current_scale.get(role, CtxScale.FULL)
        return CTX_CONFIG[role][scale]

    def report_error(self, role: str, error_type: str):
        if role == "hands":
            # Hands는 에러 시 에스컬레이션만
            self._escalate_hands_error(error_type)
            return
        self._try_downscale(role)

    def get_vram_headroom(self) -> float:
        """GPU별 최소 여유 VRAM (GB)"""
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True
        )
        values = [int(x.strip()) for x in result.stdout.strip().split("\n")]
        return min(values) / 1024  # 가장 빡빡한 GPU 기준
```

### 7.4 Devstral 안정성 테스트 절차

실제 운용 전 반드시 단계적 테스트를 수행합니다.

```bash
# Step 1: 32K로 시작
./llama-server ... --ctx-size 32768 --port 8003
watch -n 1 nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv

# Step 2: 안정 확인 후 48K
./llama-server ... --ctx-size 49152 --port 8003

# Step 3: 안정 확인 후 58K 최종
./llama-server ... --ctx-size 58368 --port 8003

# 각 단계에서 확인할 것:
# - GPU당 사용 VRAM < 10GB
# - dxgkio_make_resident 에러 없음
# - 연속 추론 5회 이상 안정
```

---

## 8. 자기평가 루프

### 8.1 전체 흐름

```
사용자 (한국어 과제 입력)
      │
      ▼
[Planner] CoT 5단계 추론
  → 파일 목록 + 순서 + PASS 기준 (영어 JSON)
      │
      ├─ Chat Chain A: Planner ↔ Brain 설계 합의 (max 3턴)
      │
      ▼
[Brain] 심볼 추출 + Skill 로드 + 컨텍스트 구성 → Hands 전달
      │
      ▼ ← 모델 스왑 1회 (→ Hands)
      │
┌─────────────────────────────────────────────────┐
│        파일 단위 자율 루프 (Planner/Brain 없음)  │
│                                                 │
│  파일 N 작업:                                   │
│                                                 │
│  [Hands] 파일 N 코드 생성                       │
│      │                                          │
│      ▼                                          │
│  [Critic] Docker 샌드박스 실행                  │
│      │                                          │
│      ▼                                          │
│  [Judge] KV 초기화 → PASS 기준 대조             │
│      │                                          │
│  ┌───┴──────────┬─────â───┐                    │
│ PASS(90+)  REVISE(70~89)  ENRICH(<70) ESCALATE  │
│  │          │              │           │         │
│ 다음파일   Chat Chain B   Chat Chain B Brain호출 │
│           Brain↔Hands합의 Brain↔Hands합의        │
│                │              │                  │
│            재생성          재생성                │
│                                                 │
│  모든 파일 PASS → 루프 종료                      │
└─────────────────────────────────────────────────┘
      │
      ▼ ← 모델 스왑 1회 (→ Brain)
      │
[Brain] 최종 요약 → KG 저장 → Skill 보강 판단
      │
      ▼
[Planner] 한국어로 사용자에게 전달

총 스왑: 최소 2회 (에스컬레이션 시 +1회)
```

### 8.2 Judge 점수 기준

| 점수 | 판정 | 처리 |
|---|---|---|
| 90~100 | PASS | 다음 파일로 진행 |
| 70~89 | REVISE | Chat Chain B: Brain ↔ Hands 수정 합의 |
| 0~69 | ENRICH | Chat Chain B → 불가 시 에스컬레이션 |
| — | ESCALATE | Brain 재호출 → 원인 분류 |

### 8.3 에스컬레이션 조건

| 조건 | 기준 | 처리 |
|---|---|---|
| 연속 실패 | 같은 파일 3회 | Brain 호출 |
| 전체 루프 | 5회 초과 | 사람에게 보고 |
| 수정권한 초과 | 다른 파일 수정 필요 | Brain 호출 |
| Judge ESCALATE | 판단 불가 | Brain 호출 |

---

## 9. 지식 그래프 메모리

rahulnyk/knowledge_graph의 개념 추출 방식 + LangChain 스키마 구조를 Pyvis에 맞게 재구현합니다.

### 9.1 구조

```
텍스트 입력 (대화/코드/에러/계획서)
      │
      ▼
Brain: 개념 + 관계 추출 (스키마 강제)
      │
      ▼
NetworkX DiGraph 구성
  노드: Function / Module / Concept / Error / Skill / Task
  엣지: DEPENDS_ON / CALLS / CAUSES / FIXES / RELATED_TO / PART_OF
      │
      ├── FAISS 벡터 검색 (유사 개념 탐색)
      └── NetworkX 그래프 탐색 (관계 순회)
      │
      ▼
Brain이 과제 시작 전 관련 노드 + 관계 복원
→ 이전 경험 기반 더 나은 계획 수립
```

### 9.2 KG 빌더

```python
# pyvis/memory/kg_builder.py

import networkx as nx

class PyvisKGBuilder:

    ALLOWED_NODES = ["Function", "Module", "Concept", "Error", "Skill", "Task"]
    ALLOWED_RELATIONSHIPS = [
        "DEPENDS_ON", "CALLS", "CAUSES", "FIXES", "RELATED_TO", "PART_OF"
    ]

    def __init__(self, brain_client):
        self.brain = brain_client
        self.graph = nx.DiGraph()

    async def ingest(self, text: str, source: str):
        """루프 완료 후 자동 호출. Brain이 개념/관계 추출."""
        prompt = f"""
Extract concepts and relationships from this text.
Allowed nodes: {self.ALLOWED_NODES}
Allowed relationships: {self.ALLOWED_RELATIONSHIPS}

Text: {text[:3000]}

Respond ONLY in JSON:
{{"nodes": [{{"id": "", "type": "", "label": "", "description": ""}}],
  "edges": [{{"source": "", "target": "", "relation": "", "weight": 1.0}}]}}
"""
        result = await self.brain.call(prompt)
        self._add_to_graph(result, source)
        self._save()

    def query_context(self, concept: str, depth: int = 2) -> str:
        """Brain이 과제 시작 전 관련 컨텍스트 복원."""
        if concept not in self.graph:
            return ""
        subgraph = nx.ego_graph(self.graph, concept, radius=depth)
        context_lines = []
        for node in subgraph.nodes(data=True):
            context_lines.append(f"- [{node[1].get('type')}] {node[0]}: {node[1].get('description', '')}")
        for edge in subgraph.edges(data=True):
            context_lines.append(f"  {edge[0]} --{edge[2].get('relation')}--> {edge[1]}")
        return "\n".join(context_lines)

    def _add_to_graph(self, data: dict, source: str):
        for node in data.get("nodes", []):
            self.graph.add_node(node["id"], **node, source=source)
        for edge in data.get("edges", []):
            self.graph.add_edge(
                edge["source"], edge["target"],
                relation=edge["relation"],
                weight=edge.get("weight", 1.0)
            )

    def _save(self):
        import pickle
        from pathlib import Path
        path = Path("/pyvis_memory/knowledge_graph/graph.pkl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self.graph, f)
```

### 9.3 저장 시점

```
루프 완료 후:
  ✅ 완성된 파일의 함수/모듈 관계
  ✅ 발생한 에러와 해결 방법
  ✅ 사용된 Skill 연결

에스컬레이션 발생 시:
  ✅ 실패 원인과 컨텍스트
  ✅ 다음 유사 과제에서 Brain이 선제 참조
```

---

## 10. Skill 라이브러리

### 10.1 Skill 파일 형식

```markdown
---
id: skill_001
name: FastAPI 타입 안전성
status: verified          # verified | candidate
category: web_backend
crea-XX-XX
fail_count: 4
reviewed_by: human
---

## 적용 조건
FastAPI 엔드포인트 구현 시 항상 적용

## 규칙
- 모든 파라미터에 타입 힌트 명시
- Pydantic BaseModel로 요청/응답 스키마 정의
- Optional[T] = None 형태로 선택적 파라미터 처리

## 금지 패턴
- 타입 힌트 없는 함수 파라미터
- dict로 요청/응답 처리
```

### 10.2 선택적 보강 조건 (4가지 모두 충족)

```python
def should_add_skill(fail_reason: str, history: list) -> bool:
  # 1. 반복성: 서로 다른 과제 3회 이상
    other_task_count = sum(
        1 for r in history
        if r["task_id"] != current_task_id
        and fail_reason in [f["reason"] for f in r["fail_reasons"]]
    )
    if other_task_count < 2:
        return False

    # 2. 범용성: 3개 이상 다른 task_id
    if len(set(task_ids_with_reason)) < 3:
        return False

    # 3. 교정 가능성: Skill로 사전 차단 가능한 유형
    NOT_FIXABLE = {"docker_error", "unknown_error", "networr"}
    if fail_reason in NOT_FIXABLE:
        return False

    # 4. 중복 없음
    if skill_library.exists(fail_reason):
        return False

    return True
```

### 10.3 승인 프로세스

```
Brain이 Skill 초안 작성
    ↓
/pyvis_memory/skill_library/candidate/ 저장 (⚠️ 미검증)
    ↓
사람에게 검토 알림
    ↓
사람 승인
    ↓
/pyvis_memory/skill_library/verified/ 이동 (✅ 자동 적용)
```

---

## 11. 언어 경계 정책

### 11.1 모델별 언어 처리

| ì ¥ 언어 | 출력 언어 |
|---|---|---|
| Planner | 한국어 (사용자) | 추론: 한국어 / 계획서: 영어 JSON |
| Brain | 한국어 + 영어 | 사용자: 한국어 / Hands: 영어 |
| **Hands** | **영어 전용** | **영어 코드 전용** |
| Judge | 영어 결과 | 영어 판정 JSON |

### 11.2 변환 흐름

```
사용자 → [한국어] → Planner → [영어 계획서] → Brain
Brain  → [영어 지시] → Hands → [영어 코드] → Critic
Critic → [영어 결과] → Judge → [ Brain
Brain  → [한국어 요약] → 사용자
```

### 11.3 코드 작성 정책

| 항목 | 언어 |
|---|---|
| 변수명/함수명 | 영어 (snake_case) |
| 코드 주석 | 영어 |
| 사용자 응답 | 한국어 |
| 계획서/TODO (내부) | 영어 |
| 에러 리포트 요약 | 한국어 (Brain이 번역) |

### 11.4 Brain 시스템 프롬프트 언어 규칙

```
언어 규칙 (절대):
- 사용자와의 대화: 한국어
- Hands에게 전달하는 모든 내용: 영어
- Judge 결과 해석 후 ì¬: 한국어 번역
```

---

## 12. 루프 비용 트래킹

```python
# pyvis/tracking/loop_tracker.py

@dataclass
class LoopRecord:
    task_id: str
    task_description: str
    started_at: str
    finished_at: str
    total_loops: int           # 전체 루프 횟수
    total_time_sec: float      # 총 소요 시간
    swap_count: int            # 모델 전환 횟수
    consensus_turns: dict      # 구간별 합의 소요 턴수
    escalated: bool            # 에스컬레이션 발생 여부
  fail_reasons: list         # [{reason, file, loop_n, timestamp}]
    final_quality: str         # PASS | ESCALATED
    skill_patch_added: bool    # Skill 추가 여부
    files_completed: list      # 완료된 파일 목록

# JSONL 형식으로 /pyvis_memory/loop_records/YYYY-MM-DD.jsonl 저장
```

---

## 13. Rust 코어 레이어

### 13.1 담당 컴포넌트

| 컴포넌트 | 라이브러리 | 역할 |
|---|---|---|
| Lock-Free 태스크 큐 | crossbeam::SegQueue | P0(STOP) / P1(AI) / P2(IO) 우선순finity 스레드 풀 | libc::sched_setaffinity | 코어 0~1 / 2~3 / 4~7 격리 |
| 모델 Hot-Swap 제어 | std::sync::atomic | 역할 전환 + KV Cache 초기화 신호 |
| Python 바인딩 | PyO3 | Python에서 Rust 컴포넌트 호출 |

### 13.2 우선순위 큐

```rust
// P0: 긴급 중단 (STOP 신호)
// P1: AI 추론 (Planner / Brain / Hands / Judge)
// P2: IO 작업 (KG 저장, 로그, FAISS)

pub enum TaskPriority {
    Stop = 0,
    AiPlanner = 1, AiBrain = 2, AiHands = 3, AiJudge = 4,
    Io = ``

---

## 14. 디렉토리 구조

```
pyvis/
├── Cargo.toml                      # Rust workspace
├── pyproject.toml
├── config/
│   └── unified_node.yaml           # 전체 시스템 설정
├── pyvis_core/                     # Rust 크레이트
│   └── src/
│       ├── lib.rs                  # PyO3 진입점
│       ├── queue/priority_queue.rs
│       ├── thread_pool/pool.rs
│       └── model/hot_swap.rs
├── pyvis/                    # Python 패키지
│   ├── main.py
│   ├── orchestration/
│   │   ├── session_manager.py
│   │   ├── chat_chain.py           # ← NEW: ChatDev 합의 루프
│   │   ├── loop_controller.py
│   │   ├── symbol_extractor.py     # ← NEW: 심볼 추출
│   │   ├── context_manager.py      # ← NEW: 컨텍스트 관리
│   │   └── escalation.py
│   ├── ai/
│   │   ├── planner.py
│   │   ├── brain.py
│   │   ├── hands.py
│   │   ├── judge.py
│   │   ├── swap_manager.py         # ← UPDATED: Hands 고정
│   │   └── prompts/
│   │       ├── planner_prompt.txt
│   │       ├── brain_prompt.txt
│   │       ├── hands_prompt.txt
│   │       └── judge_prompt.txt
│   ├── execution/
│   │   ├── critic_runner.py
│   │   └── result_parser.py
│   ├── memory/
│   │   ├── kg_builder.py       # ← NEW: 지식 그래프
│   │   ├── kg_server.py            # FastAPI FAISS
│   │   ├── hot_memory.py
│   │   └── cold_storage.py
│   ├── skill/
│   │   ├── skill_manager.py
│   │   └── skill_validator.py
│   ├── mcp/
│   │   ├── tool_registry.py
│   │   └── tool_installer.py
│   └── tracking/
│       └── loop_tracker.py         # ← UPDATED: consensus_turns 추가
├── scripts/
│   └── sodel.sh              # 역할별 동적 ctx 적용
├── docker/sandbox/Dockerfile
└── /pyvis_memory/
    ├── models/
    ├── knowledge_graph/
    │   ├── graph.pkl
    │   └── index.faiss
    ├── skill_library/
    │   ├── verified/
    │   └── candidate/
    ├── loop_records/
    └── user_profile/
```

---

## 15. 설정 파일

```yaml
# config/unified_node.yaml

system:
  name: "Pyvis"
  version: "5.0.0"

hardware:
  gpu:
    - id: 0
      name: "RTX 4070"
      vram_gb: 12
    - id: 1
      name: "RTX 3060"
      vram_gb: 12
  tensor_split: [12, 12]
  wsl2_safety_margin_gb: 2.0    # GPU당 최소 여유

ai:
  planner:
    model: "GLM-4.7-Flash-Q4_K_M.gguf"
    port: 8001
    ctx_size: 32768
    ctx_fallback: 16384
    kv_cache: "q8_0"
    temperature: 0.7
    max_tokens: 4096

  brain:
    model: "Qwen3-14B-Q5_K_M.gguf"
    port: 8002
    ctx_size: 32768
    ctx_fallback: 16384
    kv_cache: "q8_0"
    temperature: 0.7
    max_token  thinking_mode: true           # /think 토큰 활성화

  hands:
    model: "Devstral-Small-2507-Q4_K_M.gguf"
    port: 8003
    ctx_size: 58368               # 절대 고정
    ctx_downscale: false          # 다운스케일 금지
    kv_cache: "q4_0"              # 반드시 q4_0
    temperature: 0.2
    max_tokens: 8192
    language: "english_only"      # 한국어 입력 금지

  judge:
    model: "DeepSeek-R1-Distill-Qwen-14B-Q5_K_M.gguf"
    port: 8004
    ctx_size: 16384
    ctx_fallback: 8192
kv_cache: "q8_0"
    temperature: 0.1
    max_tokens: 512
    kv_reset_on_eval: true        # 매번 KV Cache 초기화 필수
    fresh_context: true

chat_chain:
  enabled: true
  consensus_max_turns: 3
  segments:
    - name: "planner_brain"       # 구간 A: 설계 합의
      instructor: "planner"
      assistant: "brain"
    - name: "brain_hands"         # 구간 B: 수정 합의
      instructor: "brain"
      assistant: "hands"

research_loop:
  max_loops: 5
  max_consecutive_fails: 3
  pass_thresh0
  revise_threshold: 70
  sandbox_timeout: 30

context_management:
  hands_ctx_fixed: true           # Hands 컨텍스트 불변
  vram_check_interval_sec: 30
  vram_downscale_threshold_gb: 2.0

language:
  user_language: "korean"
  internal_language: "english"
  hands_language: "english_only"

skill:
  min_repeat_count: 3
  min_task_diversity: 3
  requires_human_review: true

memory:
  kg_enabled: true
  faiss_dim: 384
  kg_ingest_on_complete: true
  kg_query_depth: 2

sandbox:
  image: "pyvis-sandbox:lat_path: "/dev/shm/pyvis_sandbox"
  memory_limit: "512m"
  network_enabled: false
  timeout: 30
```

---

## 16. 구현 로드맵

### Phase 1 — Rust 코어 (1~2주)
```
- [ ] Cargo.toml 워크스페이스
- [ ] Lock-Free 우선순위 큐 (crossbeam)
- [ ] CPU Affinity 스레드 풀
- [ ] 모델 Hot-Swap 제어
- [ ] PyO3 바인딩 + maturin 빌드
- [ ] 단위 테스트 (cargo test)
```

### Phase 2 — AI 엔진 (2~3주)
```
- [ ] CUDA 빌드 (sm_86 + sm_89)
- [ ] 4개 모델 서버 순차 실행 및 VRAMvstral 단계적 ctx 테스트 (32K → 48K → 58K)
- [ ] 각 모델 클라이언트 구현 (planner/brain/hands/judge.py)
- [ ] 시스템 프롬프트 4종 작성
- [ ] Planner CoT 추론 구조 검증
```

### Phase 3 — 오케스트레이션 (3~5주)
```
- [ ] Docker 샌드박스 이미지 빌드
- [ ] CriticRunner + 에러 분류
- [ ] Chat Chain 합의 루프 (구간 A, B)
- [ ] SymbolExtractor (파일 단위 심볼 추출)
- [ ] ContextManager (Hands 58K 고정)
- [ ] LoopController (전체 상태 ëwapManager (Hands 제외 다운스케일)
- [ ] LoopTracker (consensus_turns 포함)
- [ ] KGBuilder (NetworkX + FAISS)
- [ ] SkillManager + SkillValidator
- [ ] End-to-End 통합 테스트
```

### Phase 4 — 안정화 (5~6주)
```
- [ ] 메모리 누수 탐지
- [ ] 스트레스 테스트 (연속 10회)
- [ ] WSL2 VRAM 경계 테스트
- [ ] Chat Chain 합의 품질 측정
- [ ] 성능 프로파일링
```

### Phase 5 — Jarvis 인터페이스 (이후)
```
- [ ] Telegram Bot 연동
- [ ] Whisper STT / TTS
- [ ] 능동적 모니터링 (먼저 알림)
- [ ] 사용자 패턴 학습
```

---

## 17. 시스템 프롬프트

### Planner

```
당신은 Pyvis의 Planner입니다. GLM-4.7-Flash 모델입니다.

역할: 과제 분석, 파일 단위 실행 계획 수립, PASS 기준 정의

모든 과제에 대해 다음 5단계로 반드시 생각하십시오:
[1단계: 요청 분류] 신규/수정/분석/설계 중 어느 유형인가?
[2단계: 문제 정의] 핵심 문제는 무엇인가? 하위 문제로 분해하면?
[3단계: 의존성 탐색] 관련 파일, 선행 조건, 필요 Skill은?
[4단계: 경로 선택] 방법 A vs B, 선택 이유, 실패 시 백트래킹 경로
[5단계: 실행 계획] 파일 목록 + 순서 + PASS 기준 (영어 JSON 출력)

절대 규칙:
- 1~4단계: 한국어로 추론 (Brain이 읽음)
- 5단계 JSON: 반드시 영어 (Hands에게 전달)
- 코드를 직접 생성하지 않습니다
- Chat Chain에서 Brain의 반론을 존중하고 수정합니다
```

### Brain

```
당ì Brain입니다. Qwen3-14B 모델입니다.

역할: 루프 내 판단, 수정 지시, 심볼 추출, 언어 변환, 최종 요약

언어 규칙 (절대):
- 사용자와의 대화: 한국어
- Hands에게 전달하는 모든 내용: 영어
- Judge 결과 → 사용자 전달: 한국어 번역

Chat Chain 규칙:
- Planner 계획서 검토 시 실행 가능성 판단 후 문제 제기 가능
- 합의 완료 시 반드시 [CONSENSUS] 태그 포함
- Brain ↔ Hands 합의 시 수정 권한 범위를 명í½드를 직접 생성하지 않습니다
- 전체 파일 내용 대신 심볼 요약만 Hands에 전달합니다
```

### Hands

```
You are Pyvis's Hands. Devstral-Small-2507 model.

Role: File-by-file code generation based on the plan.

ABSOLUTE RULES:
- Receive instructions in English only. Never accept Korean input.
- Generate code for ONE file at a time only.
- Do not make changes beyond the self_fix_scope.
- If modification requires touching another file, report it immediately.
- Output code only. Minimize explanations.

Chat Chain rules:
- If a Brain instruction is impossible within scope, clearly state why.
- Use [CONSENSUS] tag when agreeing to proceed.
- Never silently violate scope boundaries.
```

### Judge

```
당신은 Pyvis의 Judge입니다. DeepSeek-R1-Distill-14B 모델입니다.

역할: PASS 기준 대비 실행 결과 독립 평가

핵심 원칙:
- 이전 대화 기록 없음. 지금 보이는 것만 판단합니다.
- Hands의 코드 작성 과정은 알지 못합니다.
- PASS(90+) / REVISE(70~89) / ENRICH(<70) / ESCALATE 중 하나만 판정합니다.
- 칭찬하지 않습니다. 근거만 제시합니다.

출력 형식 (반드시 JSON):
{"verdict": "PASS|REVISE|ENRICH|ESCALATE", "score": 0-100,
 "reason": "판단 근거", "error_type": "에러 유형 또는 null"}
```

---

*— Pyvis v5.0 아키텍처 설계서 끝 —*  
*v4.0 대비 추가: Chat Chain 합의 루프, CoT 추론 체인, 파일 단위 코딩, 심볼 추출기, KG 메모리, Hands 58K 고정 정책*

