# PYVIS v5.1 — Chat Chain 상세 설계서
> **v5.0 기술 검토 반영 개정판**  
> **구현 담당**: Claude Opus 4.6  
> **최종 수정**: 2026-02-22

---

## 변경 이력 (v5.0 → v5.1)

| 항목 | v5.0 | v5.1 | 근거 |
|---|---|---|---|
| Hands KV Cache | q4_0 고정 | **q8_0 + ctx 32K** | Symbol 추출 시 입력 10K 미만 → q8_0 가능 |
| Hands ctx | 58K 고정 | **32K (Symbol 기반)** | 코드 참조 정밀도 확보 |
| 모델 스왑 방식 | NVMe 직접 로드 | **RAM mmap 캐싱** | 스왑 지연 2~4초 → 1초 이내 |
| Chat Chain 종료 | [CONSENSUS] 태그만 | **태그 + Hard Limit** | Sycophancy / Deadlock 방지 |
| KG 검색 | ego_graph(radius=2) | **FAISS → PageRank** | 노드 증가 시 불필요 정보 제거 |
| Judge 판정 | 블랙박스 | **Thought Instruction 체크리스트** | 투명성 확보 |
| Hands 환각 방지 | 없음 | **Communicative Dehallucination** | 모호 지시 역질의 강제 |
| Skill 시스템 | 규칙 기반 차단 | **+ Experience DB (Phase 4)** | 성공 패턴 재사용 |

---

## 목차

1. [Chat Chain 전체 구조](#1-chat-chain-전체-구조)
2. [구간 A — Planner ↔ Brain 설계 합의](#2-구간-a--planner--brain-설계-합의)
3. [구간 B — Brain ↔ Hands 수정 합의](#3-구간-b--brain--hands-수정-합의)
4. [Hard Limit 인터럽트](#4-hard-limit-인터럽트)
5. [Communicative Dehallucination](#5-communicative-dehallucination)
6. [Thought Instruction — Judge](#6-thought-instruction--judge)
7. [Hands 컨텍스트 정책 수정](#7-hands-컨텍스트-정책-수정)
8. [RAM mmap 스왑 최적화](#8-ram-mmap-스왑-최적화)
9. [KG 검색 고도화 — FAISS + PageRank](#9-kg-검색-고도화--faiss--pagerank)
10. [Experiential Co-Learning (Phase 4)](#10-experiential-co-learning-phase-4)
11. [전체 루프 흐름 (수정 반영)](#11-전체-루프-흐름-수정-반영)
12. [시스템 프롬프트 (수정본)](#12-시스템-프롬프트-수정본)
13. [설정 파일 (수정본)](#13-설정-파일-수정본)

---

## 1. Chat Chain 전체 구조

### 1.1 적용 구간

```
전체 파이프라인 중 Chat Chain이 작동하는 구간:

사용자 입력
    │
    ▼
[Planner] CoT 5단계 추론 → 계획서 초안
    │
    ▼
┌─────────────────────────────────────────┐
│  구간 A: Planner ↔ Brain (설계 합의)    │  ← Chat Chain
│  max 3턴 / Hard Limit 감시              │
└─────────────────────────────────────────┘
    │ 합의된 계획서 (영어 JSON)
    ▼
[Brain] 심볼 추출 + KG 검색 + Skill 로드
    │
    ▼ (파일 단위 루프)
[Hands] 코드 생성
    │
    ▼
[Critic] 실행
    │
    ▼
[Judge] Thought Instruction 체크리스트 → 판정
    │
    ├── PASS → 다음 파일
    │
    └── REVISE / ENRICH
            │
            ▼
┌─────────────────────────────────────────┐
│  구간 B: Brain ↔ Hands (수정 합의)      │  ← Chat Chain
│  max 3턴 / Hard Limit 감시              │
│  Communicative Dehallucination 적용     │
└─────────────────────────────────────────┘
    │
    └── ESCALATE → Brain 직접 호출
```

### 1.2 Chat Chain이 적용되지 않는 구간

```
❌ Judge 판정 과정        (독립성 유지를 위해 단방향)
❌ Critic 실행             (코드 실행, 합의 불필요)
❌ Brain 에스컬레이션 처리 (긴급, 합의 시간 없음)
```

### 1.3 합의 루프 공통 종료 조건

```
✅ 정상 종료:
  [CONSENSUS] 태그 등장

⛔ Hard Limit 강제 종료 (중 하나라도 충족 시):
  1. 2턴 내 Diff 변경 라인 < 3줄  (무의미한 반복)
  2. 연속 AST 파싱 에러 2회       (코드 구조 붕괴)
  3. [CLARIFICATION_NEEDED] 3회 연속 (Hands가 계속 모호함 호소)
  4. max_turns(3) 초과             (상한 도달)

Hard Limit 트리거 시 → 즉시 Brain에게 제어권 이전
```

---

## 2. 구간 A — Planner ↔ Brain 설계 합의

### 2.1 목적

Planner가 작성한 계획서를 Brain이 **실행 가능성** 관점에서 검토합니다.  
Brain은 파일 분리, VRAM 한계, 수정 권한 범위 등 실무적 문제를 제기합니다.

### 2.2 역할

| 역할 | 모델 | 담당 |
|---|---|---|
| Instructor | Planner (GLM-4.7-Flash) | 계획서 제시, 수정안 수락/거부 |
| Assistant | Brain (Qwen3-14B) | 실행 가능성 검토, 문제 제기, 반론 |

### 2.3 대화 흐름

```
Turn 1:
  Planner → Brain:
    "다음 계획서를 검토하라.
     [계획서 JSON: 파일 3개, 순서, PASS 기준]"

  Brain → Planner:
    [검토 결과]
    동의: 파일 분리 구조 타당
    문제 제기:
    - auth.py 예상 500줄 → auth_core.py / auth_router.py 분리 권장
    - PASS 기준 #2 "응답 시간 < 100ms"는 Docker 샌드박스에서 측정 불가

Turn 2:
  Planner → Brain:
    "auth.py 분리안 수락. 응답시간 기준은 '에러 없이 실행 완료'로 수정.
     [수정된 계획서 JSON]
     [CONSENSUS]"

  → 합의 완료. Hands에게 전달.
```

### 2.4 구현 코드

```python
# pyvis/orchestration/chat_chain.py

from dataclasses import dataclass
from typing import Optional
import ast, difflib

@dataclass
class ConsensusResult:
    agreed: bool
    final_content: str
    messages: list
    turns: int
    termination_reason: str  # "consensus" | "hard_limit_diff" | "hard_limit_ast" | "max_turns"


class ChatChainController:

    def __init__(self, hard_limit_config: dict):
        self.min_diff_lines = hard_limit_config.get("min_diff_lines", 3)
        self.max_ast_errors = hard_limit_config.get("max_ast_errors", 2)
        self.max_clarification = hard_limit_config.get("max_clarification", 3)
        self.max_turns = hard_limit_config.get("max_turns", 3)

    async def consensus_loop(
        self,
        instructor,
        assistant,
        topic: str,
        initial_content: str,
        context: dict
    ) -> ConsensusResult:

        messages = []
        prev_content = initial_content
        ast_error_count = 0
        clarification_count = 0

        for turn in range(self.max_turns):

            # ── Instructor 발화 ──────────────────────────────
            inst_output = await instructor.instruct(
                topic, prev_content, messages, context
            )
            messages.append({"role": "instructor", "content": inst_output, "turn": turn})

            if "[CONSENSUS]" in inst_output:
                return ConsensusResult(
                    agreed=True, final_content=inst_output,
                    messages=messages, turns=turn+1,
                    termination_reason="consensus"
                )

            # ── Assistant 응답 ───────────────────────────────
            asst_output = await assistant.respond(messages, context)
            messages.append({"role": "assistant", "content": asst_output, "turn": turn})

            # ── CLARIFICATION_NEEDED 카운트 ──────────────────
            if "[CLARIFICATION_NEEDED]" in asst_output:
                clarification_count += 1
                if clarification_count >= self.max_clarification:
                    return ConsensusResult(
                        agreed=False, final_content=asst_output,
                        messages=messages, turns=turn+1,
                        termination_reason="hard_limit_clarification"
                    )
                continue

            if "[CONSENSUS]" in asst_output:
                return ConsensusResult(
                    agreed=True, final_content=asst_output,
                    messages=messages, turns=turn+1,
                    termination_reason="consensus"
                )

            # ── Hard Limit 1: Diff 변경량 감시 ───────────────
            current_content = self._extract_content(asst_output)
            diff_lines = self._count_diff_lines(prev_content, current_content)
            if turn > 0 and diff_lines < self.min_diff_lines:
                return ConsensusResult(
                    agreed=False, final_content=asst_output,
                    messages=messages, turns=turn+1,
                    termination_reason="hard_limit_diff"
                )

            # ── Hard Limit 2: AST 파싱 에러 감시 ────────────
            if self._has_code(current_content):
                if not self._ast_valid(current_content):
                    ast_error_count += 1
                    if ast_error_count >= self.max_ast_errors:
                        return ConsensusResult(
                            agreed=False, final_content=asst_output,
                            messages=messages, turns=turn+1,
                            termination_reason="hard_limit_ast"
                        )

            prev_content = current_content

        return ConsensusResult(
            agreed=False, final_content=prev_content,
            messages=messages, turns=self.max_turns,
            termination_reason="max_turns"
        )

    def _count_diff_lines(self, prev: str, curr: str) -> int:
        diff = list(difflib.unified_diff(
            prev.splitlines(), curr.splitlines()
        ))
        return sum(1 for l in diff if l.startswith(('+', '-')) and not l.startswith(('+++', '---')))

    def _ast_valid(self, code: str) -> bool:
        code_block = self._extract_code_block(code)
        if not code_block:
            return True
        try:
            ast.parse(code_block)
            return True
        except SyntaxError:
            return False

    def _extract_code_block(self, text: str) -> Optional[str]:
        import re
        match = re.search(r'```python\n(.*?)```', text, re.DOTALL)
        return match.group(1) if match else None

    def _has_code(self, text: str) -> bool:
        return "```python" in text

    def _extract_content(self, text: str) -> str:
        code = self._extract_code_block(text)
        return code if code else text
```

---

## 3. 구간 B — Brain ↔ Hands 수정 합의

### 3.1 목적

Judge의 REVISE/ENRICH 판정 후, Brain이 수정 지시를 내리기 전  
**Hands가 수정 범위·가능성을 먼저 확인**합니다.  
이 과정에서 Communicative Dehallucination이 작동합니다.

### 3.2 역할

| 역할 | 모델 | 담당 |
|---|---|---|
| Instructor | Brain (Qwen3-14B) | 수정 지시, 권한 범위 확장 결정 |
| Assistant | Hands (Devstral) | 수행 가능성 확인, 모호함 역질의 |

### 3.3 대화 흐름 — 정상 케이스

```
Turn 1:
  Brain → Hands:
    "다음 에러를 수정하라.
     TypeError: argument of type 'NoneType' is not iterable
     에러 위치: api/auth.py line 42
     수정 권한 범위: auth.py 단독"

  Hands → Brain:
    [CLARIFICATION_NEEDED]
    Question: user 객체가 None일 때 처리 방식이 불명확합니다.
    Options:
      A. None 체크 후 빈 리스트 반환
      B. None 체크 후 HTTPException(404) raise
      C. Optional 타입으로 래핑하여 상위에서 처리

Turn 2:
  Brain → Hands:
    "Option B 선택. 404 raise.
     수정 권한 범위 유지.
     [CONSENSUS]"

  → 합의 완료. Hands가 코드 재생성.
```

### 3.4 대화 흐름 — 권한 초과 케이스

```
Turn 1:
  Brain → Hands: "타입 에러 수정하라"

  Hands → Brain:
    수행 불가 (수정 권한 초과):
    - User 모델 스키마 변경 필요 → models/user.py 수정 필요
    - 현재 권한: auth.py 단독
    → 권한 범위 확장 또는 에스컬레이션 요청

Turn 2:
  Brain → Hands (또는 → Planner로 에스컬레이션):
    "수정 권한 확장: auth.py + models/user.py
     User.email 필드에 Optional[str] 적용
     [CONSENSUS]"
```

### 3.5 Hard Limit — Sycophancy 방지

```
Sycophancy 감지 조건:
  - Brain이 명백한 오류 코드를 제시했는데
    Hands가 [CONSENSUS] 없이 바로 수락

감지 방법:
  - AST 파싱 에러가 있는 코드에 즉시 동의한 경우
  - diff_lines < 3 인데 [CONSENSUS] 태그

트리거 시:
  → Hard Limit 인터럽트 → Brain에 제어권 이전
```

---

## 4. Hard Limit 인터럽트

### 4.1 트리거 조건 전체

```python
# pyvis/orchestration/hard_limit.py

class HardLimitChecker:

    TRIGGERS = {
        "diff_too_small": {
            "condition": "turn > 0 and diff_lines < 3",
            "meaning": "의미 없는 반복 (무한 루프 징후)",
            "action": "escalate_to_brain"
        },
        "ast_error_repeat": {
            "condition": "ast_error_count >= 2",
            "meaning": "코드 구조 붕괴 (수정 불가 수준)",
            "action": "escalate_to_brain"
        },
        "clarification_loop": {
            "condition": "clarification_count >= 3",
            "meaning": "지시가 근본적으로 불명확",
            "action": "escalate_to_planner"
        },
        "max_turns": {
            "condition": "turn >= max_turns",
            "meaning": "상한 초과",
            "action": "force_last_state"
        },
        "sycophancy": {
            "condition": "ast_invalid and immediate_consensus",
            "meaning": "오류 코드에 무조건 동의",
            "action": "escalate_to_brain"
        }
    }

    def check(self, state: dict) -> Optional[str]:
        for name, trigger in self.TRIGGERS.items():
            if self._evaluate(trigger["condition"], state):
                return trigger["action"]
        return None

    def _evaluate(self, condition: str, state: dict) -> bool:
        return eval(condition, {}, state)
```

### 4.2 에스컬레이션 경로

```
hard_limit_diff / hard_limit_ast / sycophancy
    → Brain: 원인 분류 후 재지시 또는 Planner 에스컬레이션

hard_limit_clarification
    → Planner: 계획서 재작성 (지시 자체가 불명확)

max_turns
    → 마지막 상태를 Judge에게 전달 (판정 위임)
```

---

## 5. Communicative Dehallucination

### 5.1 개념

Hands가 모호한 지시를 억지로 해석하여 억측 코드를 생성하는  
"코딩 환각(Coding Hallucination)"을 방지합니다.

조력자(Hands)가 응답하기 전에 불확실한 부분을  
**구체적인 선택지**와 함께 역질의(Role Reversal)합니다.

### 5.2 작동 조건

```
Communicative Dehallucination 발동 조건:
  ✅ 타입이 모호한 경우     (str vs Optional[str])
  ✅ 에러 처리 방식 미명시  (raise vs return None)
  ✅ 파일 경로 불명확       (상대 경로 vs 절대 경로)
  ✅ 의존 버전 불명확       (라이브러리 버전)
  ✅ 인터페이스 변경 범위 불명확

  ❌ 단순 구현 방법론 선택  (발동 안 함, 스스로 판단)
  ❌ 이미 계획서에 명시된 내용
```

### 5.3 Hands 시스템 프롬프트 추가 규칙

```
## Clarification Rule (MANDATORY)

Before generating any code, check if the instruction is ambiguous.

TRIGGER CONDITIONS (any one → must clarify):
- Type is unspecified (str? Optional[str]? int?)
- Error handling approach is missing (raise? return None? log?)
- File path scope is unclear
- Dependency version is unspecified
- Interface change boundary is unclear

IF TRIGGERED:
  DO NOT guess. DO NOT generate partial code.
  Output EXACTLY this format:

  [CLARIFICATION_NEEDED]
  Question: <하나의 구체적인 질문>
  Options:
    A. <선택지 A>
    B. <선택지 B>
    C. <선택지 C (있는 경우)>

RULES:
  - Ask ONE question per turn. Not multiple.
  - Provide concrete options. Not open-ended.
  - After Brain answers, proceed immediately.
  - Maximum 3 clarification rounds. After that, choose best option and state assumption.
```

### 5.4 Brain의 역질의 응답 처리

```python
# pyvis/ai/brain.py

async def handle_clarification(self, clarification: dict, context: dict) -> str:
    """Hands의 [CLARIFICATION_NEEDED] 응답 처리"""
    question = clarification["question"]
    options = clarification["options"]

    prompt = f"""
Hands가 명확한 지시를 요청합니다.

질문: {question}
선택지:
{chr(10).join(f"  {k}. {v}" for k, v in options.items())}

현재 컨텍스트:
- 파일: {context['current_file']}
- 수정 권한: {context['self_fix_scope']}
- PASS 기준: {context['pass_criteria']}

가장 적합한 선택지를 고르고 이유를 한 줄로 설명하라.
반드시 영어로 응답하라 (Hands에게 전달).
"""
    response = await self._call(prompt)
    return response
```

---

## 6. Thought Instruction — Judge

### 6.1 개념

Judge의 판정 과정을 블랙박스에서 **투명한 체크리스트 기반**으로 전환합니다.  
판정 전 반드시 4개 체크를 순서대로 수행해야 합니다.

### 6.2 Judge 시스템 프롬프트 (수정본)

```
You are Pyvis's Judge. DeepSeek-R1-Distill-14B model.

ROLE: Independent evaluation of execution results against PASS criteria.

CORE PRINCIPLE:
- No previous conversation history. Judge only what you see NOW.
- You do not know how Hands wrote the code.
- Never praise. State only evidence-based reasoning.

## MANDATORY THOUGHT PROCESS (do NOT skip any step)

Before outputting the final verdict, execute this checklist IN ORDER:

[CHECK 1] EXIT CODE
  → Is exit_code == 0?
  → If not 0: what type of error? (SyntaxError / ImportError / RuntimeError / other)

[CHECK 2] PASS CRITERIA VERIFICATION
  → Go through each criterion in pass_criteria one by one.
  → Mark each: ✅ SATISFIED / ❌ NOT SATISFIED / ⚠️ PARTIAL
  → Example:
      - "Returns HTTP 200 on success": ✅ stdout shows 200
      - "Handles None input": ❌ TypeError on None input

[CHECK 3] MISSING SYMBOLS
  → Are there any missing imports / undefined functions / missing files?
  → List all: ModuleNotFoundError, NameError, FileNotFoundError

[CHECK 4] ERROR CLASSIFICATION
  → Is the error type within Hands self-fix scope?
  → Classify: type_error / syntax_error / missing_import / logic_error / architecture_error
  → architecture_error → must ESCALATE

## SCORING

Based on CHECK 1-4:
  90~100: All criteria satisfied → PASS
  70~89:  Minor issues, self-fixable → REVISE
  0~69:   Major issues or many failures → ENRICH
  -:      Cannot determine / architecture issue → ESCALATE

## OUTPUT FORMAT (JSON only, after checklist)

{
  "check_results": {
    "exit_code_ok": true/false,
    "criteria_results": [{"criterion": "...", "result": "SATISFIED/NOT_SATISFIED/PARTIAL"}],
    "missing_symbols": [],
    "error_type": "..."
  },
  "verdict": "PASS|REVISE|ENRICH|ESCALATE",
  "score": 0-100,
  "reason": "one sentence summary",
  "error_type": "..."
}

CHECKLIST OMISSION IS PROHIBITED.
```

### 6.3 Judge 결과 파싱 (수정)

```python
# pyvis/ai/judge.py

@dataclass
class JudgeResult:
    verdict: str
    score: int
    reason: str
    error_type: Optional[str]
    check_results: dict        # ← NEW: 체크리스트 결과 포함
    thought_process: str       # ← NEW: 추론 과정 보존 (KG 저장용)

async def evaluate(self, ...) -> JudgeResult:
    response = await self._call_fresh(user_message)

    # 체크리스트 추출
    thought = self._extract_thought(response)

    # JSON 파싱
    data = json.loads(self._extract_json(response))

    return JudgeResult(
        verdict=data["verdict"],
        score=int(data["score"]),
        reason=data["reason"],
        error_type=data.get("error_type"),
        check_results=data.get("check_results", {}),
        thought_process=thought
    )
```

---

## 7. Hands 컨텍스트 정책 수정

### 7.1 변경 근거

v5.0의 "58K 고정 + KV q4_0" 조합은 두 가지 문제가 있었습니다.

```
문제 1: q4_0 KV Cache
  장문 컨텍스트(32K+)에서 변수명·함수 시그니처 참조 정밀도 하락
  → Hands 환각 증가

문제 2: 58K 상한이 필요한 전제
  Symbol Extractor 정상 작동 시 실제 입력은 10K 미만
  → 58K는 과도한 여유. q4_0 희생이 불필요
```

### 7.2 수정된 정책

```
Symbol Extractor 작동 시 (정상):
  실제 Hands 입력 = 계획 2K + 심볼 요약 0.5K + 에러 기록 2K + 코드 5K
  → 약 10K 사용

수정 정책:
  ctx_size: 32K (이전 58K → 축소)
  kv_cache: q8_0 (이전 q4_0 → 상향)
  GPU당 KV 공간: 80KB × 32,768 / 2 = 1.28GB

VRAM 재계산:
  가중치:       GPU당 7.15GB
  KV q8_0 32K: GPU당 1.28GB
  오버헤드:     GPU당 0.5GB
  ────────────────────â GPU당 합계:   8.93GB ✅ (여유 3.07GB)
```

### 7.3 Symbol Extractor 실패 시 폴백

```python
# pyvis/ai/swap_manager.py

def get_ctx_and_kv(self, role: str, symbol_extraction_ok: bool) -> tuple:
    if role != "hands":
        return self._get_standard(role)

    if symbol_extraction_ok:
        # 정상: 입력 10K 이하 → 32K + q8_0
        return (32768, "q8_0")
    else:
        # Symbol 추출 실패: 전체 파일 주입 가능성 → 58K + q4_0
        return (58368, "q4_0")
```

---

## 8. RAM mmap 스왑 최적화

### 8.1 문제

```
4개 모델 총 가중치: ~50GB
24GB VRAM: 동시 적재 불가 → 스왑 필수

기존 방식:
  NVMe → VRAM 직접 로드
  10~15GB 파일 / 7,000 MB/s → 최소 2초
  오프로드 포함 시 → 4초+

목표: 1초 이내
```

### 8.2 해결책 — Linux RAM mmap + Page Cache 활용

```bash
# /etc/sysctl.conf 또는 실행 시 적용

# Page Cache를 최대한 보존 (기본값 100 → 낮출수록 캐시 보존)
vm.vfs_cache_pressure = 50

# Swap 사용 최소화 (RAM 여유 있으면 절대 Swap 안 씀)
vm.swappiness = 10

# 적용
sudo sysctl -p
```

```bash
# llama-server mmap 활성화 (기본값이지만 명시 권장)
# mmap = 파일을 RAM Page Cache에 매핑
# 두 번째 로드 시 NVMe I/O 없이 RAM에서 즉시 로드

./llama-server \
  -m GLM-4.7-Flash-Q4_K_M.gguf \
  --mmap true \           # RAM Page Cache 매핑
  --no-mlock false \      # 메모리 고정 비활성 (Page Cache 활용)
  ...
```

### 8.3 모델별 RAM 상주 전략

```
32GB RAM 배분:
  OS + 프로세스:         ~5GB
  Planner (GLM):         ~15GB  ← Page Cache 상주 (가장 자주 호출)
  Brain (Qwen3-14B):     ~10.5GB ← Page Cache 상주 (루프 내 상시 호출)
  Hands (Devstral):      적재 시점에 로드 (Page Cache 활용)
  Judge (DeepSeek-R1):   적재 시점에 로드
  여유:                  ~1.5GB
  ──────────────────────────────
  합계:                  ~32GB

효과:
  Planner/Brain: 스왑 시 RAM → VRAM (NVMe I/O 없음) → 0.3~0.5초
  Hands/Judge:   첫 로드 후 Page Cache → 두 번째부터 0.5~1초
```

### 8.4 스크립트

```bash
# scripts/preload_models.sh
# 시스템 시작 시 모델을 RAM Page Cache에 미리 적재

echo "모델 Page Cache 사전 적재 중..."

# cat으로 파일 읽기 → 자동으로 Page Cache에 적재
cat /pyvis_memory/models/GLM-4.7-Flash-Q4_K_M.gguf > /dev/null &
cat /pyvis_memory/models/Qwen3-14B-Q5_K_M.gguf > /dev/null &
wait

echo "Planner + Brain 사전 적재 완료"
echo "스왑 예상 시간: 0.3~0.5초"

# Page Cache 상태 확인
vmtouch /pyvis_memory/models/GLM-4.7-Flash-Q4_K_M.gguf
vmtouch /pyvis_memory/models/Qwen3-14B-Q5_K_M.gguf
```

---

## 9. KG 검색 고도화 — FAISS + PageRank

### 9.1 문제

```
v5.0 방식: ego_graph(radius=2)
  노드 100개 → depth 2 순회 → 최대 수백 노드 반환
  대부분 무관한 노드 포함
  → Brain 컨텍스트 오염
```

### 9.2 개선된 2단계 검색

```
Stage 1: FAISS 유사도 검색
  쿼리 개념 → 임베딩 → 상위 N개 유사 노드 후보 추출
  (관련 없는 노드 사전 제거)
      ↓
Stage 2: PageRank 가중치 탐색
  Stage 1 결과 노드들만의 서브그래프 구성
  엣지 weight 기반 PageRank 계산
  → 연관성 높은 상위 K개만 반환
```

### 9.3 구현

```python
# pyvis/memory/kg_retriever.py

import faiss
import numpy as np
import networkx as nx
from sentence_transformers import SentenceTransformer

class KGRetriever:

    def __init__(self, graph: nx.DiGraph, faiss_index, metadata: list):
        self.graph = graph
        self.index = faiss_index
        self.metadata = metadata
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")

    def retrieve(self, query: str, top_n: int = 10, top_k: int = 5) -> str:
        """
        2단계 검색:
        1. FAISS로 상위 N개 후보 노드 추출
        2. 서브그래프 PageRank로 상위 K개 선별
        """

        # ── Stage 1: FAISS 유사도 검색 ───────────────────────
        embedding = self.embedder.encode([query])
        D, I = self.index.search(
            np.array(embedding, dtype=np.float32), top_n
        )
        candidate_ids = [
            self.metadata[i]["id"]
            for i in I[0]
            if i < len(self.metadata)
        ]

        if not candidate_ids:
            return ""

        # ── Stage 2: 서브그래프 PageRank ─────────────────────
        # 후보 노드들만의 서브그래프 구성
        subgraph = self.graph.subgraph(
            [n for n in candidate_ids if n in self.graph]
        ).copy()

        if len(subgraph.nodes) == 0:
            return self._format_nodes(candidate_ids[:top_k])

        # 엣지 weight 기반 PageRank
        try:
            pagerank_scores = nx.pagerank(
                subgraph,
       ght="weight",
                max_iter=100
            )
            # 상위 K개 선별
            top_nodes = sorted(
                pagerank_scores.items(),
                key=lambda x: x[1],
                reverse=True
            )[:top_k]
            selected_ids = [node_id for node_id, _ in top_nodes]
        except nx.PowerIterationFailedConvergence:
            # PageRank 수렴 실패 시 FAISS 결과 그대로 사용
            selected_ids = candidate_ids[:top_k]

        return self._form_context(selected_ids, subgraph)

    def _format_context(self, node_ids: list, subgraph: nx.DiGraph) -> str:
        """Brain 프롬프트 삽입용 컨텍스트 포맷"""
        lines = ["## 관련 지식 그래프 컨텍스트\n"]
        for node_id in node_ids:
            if node_id not in self.graph:
                continue
            data = self.graph.nodes[node_id]
            lines.append(
                f"- [{data.get('type', '?')}] {node_id}: {data.get('description', '')}"
            )
            # 이 노드의 엣지 관계도 포함
            for src, dst, edata in subgraph.edges(node_id, data=True):
                lines.append(
                    f"  └─ {src} --{edata.get('relation', '?')}--> {dst} "
                    f"(weight: {edata.get('weight', 1.0):.1f})"
                )
        return "\n".join(lines)
```

---

## 10. Experiential Co-Learning (Phase 4)

### 10.1 현재 Skill vs Experience DB 차이

```
현재 Skill 라이브러리 (규칙 기반):
  "FastAPI 엔드포리지 마라"
  → 사전 차단 / 수동적

Experience DB (경험 기반) — Phase 4 추가:
  "Task #043: auth.py의 JWT 토큰 검증 로직
   → 처음 시도한 방법: jwt.decode() 단독 사용 → 실패
   → 성공한 방법: try/except JWTError + HTTPException(401)
   → 재사용 조건: JWT 검증 로직이 포함된 모든 파일"
  → 성공 패턴 재사용 / 능동적
```

### 10.2 설계 (Phase 4 구현 예정)

```python
# pyvis/memory/experience_db.py (Phase 4)

@dataclass
classerienceEntry:
    task_id: str
    domain: str                    # "web_backend", "auth", "database" ...
    problem_description: str
    failed_approaches: list        # 실패한 방법들
    successful_approach: str       # 성공한 패턴
    code_pattern: str              # 성공 코드 패턴 (익명화)
    applicable_conditions: list    # 재사용 조건
    token_saved: int               # 이 경험으로 절감된 토큰

# 활용 방식:
# Planner/Brain이 과제 시작 시 FAISS로 유사 경험 검색
# → "지난번에 이런 문제에서 이 패턴이 성공했음"을 컨텍스트에 주입
# → 실패 패턴 반복 방지 + 성공 패턴 재사용
```

### 10.3 기대 효과

기존 연구 기준으로 Co-Saving 방식 적용 시:
- 토큰 사용량 약 50% 감소
- 코드 품질 약 10% 향상

---

## 11. 전체 루프 흐름 (수정 반영)

```
사용자 (한국어)
    │
    ▼
[Planner] CoT 5단계 추론 → 파일 목록 + PASS 기준 (영어 JSON)
    │
    ▼
━━━â━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  구간 A Chat Chain: Planner ↔ Brain
  Hard Limit: diff / AST / max_turns
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    │ 합의된 계획서
    ▼
[Brain] KG 검색 (FAISS → PageRank) + 심볼 추출 + Skill 로드
    │
    │  ← 모델 스왑 (RAM mmap → 1초 이내)
    ▼
┌──────────â───────────────────────────────┐
│             파일 단위 자율 루프                      │
│                                                     │
│  [Hands] 파일 N 코드 생성                           │
│    ↑ 입력: 계획 2K + 심볼 0.5K + Skill 0.5K        │
│    ↑ ctx: 32K / KV: q8_0 (Symbol 정상 시)           │
│      │                                              │
│      ▼                                            │
│  [Critic] Docker 실행 → 에러 분류                   │
│      │                                              │
│      ▼                                              │
│  [Judge] Thought Instruction 체크리스트             │
│    CHECK 1: exit_code                               │
│    CHECK 2: PASS 기준 대조                          │
│    CHECK 3: 누락 심볼                               │
│    CHECK 4: 에러 분류                           │
│      │                                              │
│   ┌──┴──────────┬──────────┬──────────┐            │
│ PASS(90+)  REVISE(70~89) ENRICH(<70)  ESCALATE     │
│   │          │             │           │            │
│  다음      ━━━━━━━━━━━━━━  │           Brain 호출   │
│  파일     구간 B Chat Chain │                       │
ain ↔ Hands   │                       │
│           Dehallucination ┘                        │
│           Hard Limit 감시                          │
│               │                                    │
│           재생성 → Critic → Judge                  │
│                                                     │
│  모든 파일 PASS → 루프 종료                         │
└─────────────────────────────────────────┘
    │
    │  ← 모델 스왑 (RAM mmap → 1초 이내)
    ▼
[Brain] 최종 요약 → KG 저장 (FAISS+PageRank 인덱스 업데이트)
    → Skill 보강 판단 → 한국어로 사용자 전달
    → Experience DB 업데이트 (Phase 4)
```

---

## 12. 시스템 프롬프트 (수정본)

### Planner (변경 없음)

```
당신은 Pyvis의 Planner입니다. GLM-4.7-Flash 모델입니다.

모든 과제에 대해 다음 5단계로 반드시 생각하십시오:
[1단계: 요청 분류]
[2단계: 문제 정의 및 하위 분해]
[3단계: 의존성 및 선행 조건 탐색]
[4단계: 실행 경로 선택 및 백트래킹 준비]
[5단계: 실행 계획 JSON 출력 (영어)]

Chat Chain 규칙:
- Brain의 실행 가능성 문제 제기를 존중합니다
- 합의 완료 시 반드시 [CONSENSUS] 포함
- 코드를 직접 생성하지 않습니다
```

### Brain (수정)

```
당신은 Pyvis의 Brain입니다. Qwen3-14B 모델입니다.

역할:
- 구간 Ar 계획서 실행 가능성 검토 및 반론
- 루프 내: Critic 결과 해석, 수정 지시, KG 검색, 심볼 추출
- 구간 B: Hands 수정 합의 주도
- 최종: 한국어 요약 작성

언어 규칙 (절대):
- 사용자와 대화: 한국어
- Hands에게 전달: 영어 전용

Chat Chain 규칙:
- 구간 A: Planner 계획서에 실행 불가 요소 발견 시 반드시 문제 제기
- 구간 B: Hands의 [CLARIFICATION_NEEDED] 응답 시 구체적 선택지로 답변
- 합의 완료 시 반드ìS] 포함
- Hard Limit 트리거 시 즉시 에스컬레이션 선언

절대 규칙:
- 코드를 직접 생성하지 않습니다
- 전체 파일 대신 심볼 요약만 Hands에 전달합니다
```

### Hands (수정 — Communicative Dehallucination 추가)

```
You are Pyvis's Hands. Devstral-Small-2507 model.

ROLE: File-by-file code generation based on the plan.

ABSOLUTE RULES:
- Accept English instructions ONLY. Reject Korean input.
- Work on ONE file at a time.
- Do not modify files outside self_ftput code only. Minimize explanations.

## Clarification Rule (MANDATORY)

Before generating code, check if any instruction is ambiguous.

TRIGGER CONDITIONS (any one → must clarify):
- Type unspecified (str? Optional[str]? int?)
- Error handling approach missing (raise? return None?)
- File path scope unclear
- Dependency version unspecified
- Interface change boundary unclear

IF TRIGGERED — output EXACTLY:
  [CLARIFICATION_NEEDED]
  Question: <one specific question>
  Options:
    A. <option A>
    Bption B>
    C. <option C if applicable>

RULES:
- ONE question per turn only
- Concrete options required (not open-ended)
- After Brain answers, proceed immediately
- After 3 rounds of clarification, choose best option and state assumption clearly

Chat Chain Rules:
- Use [CONSENSUS] only when genuinely agreeing to proceed
- If scope is exceeded, report immediately — do NOT silently violate
- If Brain instruction contains syntax errors, report them — do NOT blindly accept
```

### Judge (수정 — Thouction 추가)

위 6.2절 참조 (전체 프롬프트 명시됨)

---

## 13. 설정 파일 (수정본)

```yaml
# config/unified_node.yaml (v5.1)

system:
  name: "Pyvis"
  version: "5.1.0"

hardware:
  tensor_split: [12, 12]
  wsl2_safety_margin_gb: 2.0
  ram_gb: 32

ai:
  planner:
    model: "GLM-4.7-Flash-Q4_K_M.gguf"
    port: 8001
    ctx_size: 32768
    ctx_fallback: 16384
    kv_cache: "q8_0"
    temperature: 0.7
    max_tokens: 4096
    mmap: true

  brain:
    model: "Qwen3-14B-Q5_K_M.gguf"
    p  ctx_size: 32768
    ctx_fallback: 16384
    kv_cache: "q8_0"
    temperature: 0.7
    max_tokens: 4096
    thinking_mode: true
    mmap: true

  hands:
    model: "Devstral-Small-2507-Q4_K_M.gguf"
    port: 8003
    # Symbol Extractor 정상 시: 32K + q8_0
    ctx_size_normal: 32768
    kv_cache_normal: "q8_0"
    # Symbol Extractor 실패 시 폴백: 58K + q4_0
    ctx_size_fallback: 58368
    kv_cache_fallback: "q4_0"
    ctx_downscale: false          # 절대 다운스케일 없음
    temperature: 0.    max_tokens: 8192
    language: "english_only"
    mmap: true

  judge:
    model: "DeepSeek-R1-Distill-Qwen-14B-Q5_K_M.gguf"
    port: 8004
    ctx_size: 16384
    ctx_fallback: 8192
    kv_cache: "q8_0"
    temperature: 0.1
    max_tokens: 1024              # 체크리스트 포함으로 증가
    kv_reset_on_eval: true
    fresh_context: true
    mmap: true

chat_chain:
  enabled: true
  segments:
    - name: "planner_brain"
      instructor: "planner"
      assistant: "brain"
    - name: "brain_hand   instructor: "brain"
      assistant: "hands"
  hard_limit:
    max_turns: 3
    min_diff_lines: 3             # 변경 라인 최소치
    max_ast_errors: 2             # 연속 AST 에러 상한
    max_clarification: 3          # Hands 역질의 상한

communicative_dehallucination:
  enabled: true
  max_clarification_rounds: 3

thought_instruction:
  enabled: true
  checklist_steps: 4
  include_check_results_in_output: true

context_management:
  hands_ctx_fixed: true
  symbol_extractor_fallback: tr # 실패 시 58K+q4_0 자동 전환
  vram_check_interval_sec: 30
  vram_downscale_threshold_gb: 2.0

ram_optimization:
  vfs_cache_pressure: 50
  swappiness: 10
  preload_models: ["planner", "brain"]  # 시작 시 RAM Page Cache 사전 적재

research_loop:
  max_loops: 5
  max_consecutive_fails: 3
  pass_threshold: 90
  revise_threshold: 70
  sandbox_timeout: 30

knowledge_graph:
  enabled: true
  faiss_top_n: 10               # 1차 FAISS 후보 수
  pagerank_top_k: 5             # 2차 PageRank 최ìgerank_max_iter: 100
  ingest_on_complete: true

experience_db:
  enabled: false                # Phase 4에서 활성화
  similarity_threshold: 0.85

skill:
  min_repeat_count: 3
  min_task_diversity: 3
  requires_human_review: true

language:
  user: "korean"
  internal: "english"
  hands: "english_only"

sandbox:
  image: "pyvis-sandbox:latest"
  tmpfs_path: "/dev/shm/pyvis_sandbox"
  memory_limit: "512m"
  network_enabled: false
  timeout: 30
```

---

## 정리 — v5.0 vs v5.1 핵심 차이

| 항ë.1 |
|---|---|---|
| Hands ctx | 58K 고정 | **32K (Symbol 정상) / 58K (폴백)** |
| Hands KV | q4_0 | **q8_0 (Symbol 정상) / q4_0 (폴백)** |
| 모델 스왑 속도 | 2~4초 | **0.3~1초 (RAM mmap)** |
| Chat Chain 종료 | [CONSENSUS]만 | **태그 + Hard Limit 5종** |
| Judge 판정 | 블랙박스 | **Thought Instruction 4체크** |
| Hands 환각 | 억측 생성 | **Communicative Dehallucination** |
| KG 검색 | ego_graph(r=2) | **FAISS → PageRank** |
| 성공 패턴 재사용 | 없음 | **Eience DB (Phase 4)** |

*— Pyvis v5.1 Chat Chain 상세 설계서 끝 —*

