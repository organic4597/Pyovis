# Pyvis v5.1 — Phase 4 구현 계획

> **작성일**: 2026-02-24  
> **목표**: Experiential Co-Learning & Full Pipeline Integration  
> **예상 기간**: 7~10 일

---

## 📋 Phase 4 개요

Phase 4 는 **경험 기반 학습**과 **파이프라인 완전 통합**을 목표로 합니다.

### 핵심 기능

1. **Experience DB** — 성공/실패 패턴 저장 및 재활용
2. **Hands Execution Plan 자동 생성** — 프롬프트 통합
3. **CriticRunner Execution Plan 지원** — 자동 테스트 실행
4. **KG Thought Process 저장** — Judge 추론 과정 기록
5. **전체 E2E 테스트** — 통합 검증

---

## 🎯 Phase 4 상세 작업

### 4.1 Experience DB (우선순위: 상)

**목적**: 과거 성공/실패 패턴을 저장하여 유사 작업에 재활용

#### 구현 파일

- `pyovis/memory/experience_db.py` (예상 250 줄)
- `tests/test_experience_db.py` (예상 15 테스트)

#### 데이터 구조

```python
@dataclass
class ExperienceEntry:
    task_description: str
    success: bool
    code_snippet: str
    error_type: Optional[str]
    judge_verdict: str
    judge_score: int
    execution_plan: dict
    tokens_saved: int  # Symbol 추출로 절감된 토큰 수
    timestamp: float
    
    def to_faiss_vector(self) -> np.ndarray:
        # task_description 임베딩
        ...
```

#### 주요 기능

```python
class ExperienceDB:
    async def add_experience(self, entry: ExperienceEntry):
        """성공/실패 경험 저장"""
        
    async def search_similar(self, query: str, k: int = 5) -> List[ExperienceEntry]:
        """유사 경험 검색 (FAISS)"""
        
    async def get_success_patterns(self, task_type: str) -> List[dict]:
        """특정 작업 유형의 성공 패턴 추출"""
        
    async def get_failure_patterns(self, error_type: str) -> List[dict]:
        """특정 에러 유형의 실패 패턴 추출"""
```

#### Hands 통합

```python
# Hands 프롬프트에 추가
similar_experiences = await experience_db.search_similar(task_description)

if similar_experiences:
    prompt += f"\n\n과거 유사 성공 사례:\n{format_experiences(similar_experiences)}"
```

---

### 4.2 Hands Execution Plan 자동 생성 (우선순위: 상)

**목적**: Hands 가 코드 생성 시 자동으로 실행 계획 생성

#### 수정 파일

- `pyovis/ai/hands.py` (기존 207 줄 → 300 줄 예상)
- `pyovis/ai/prompts/hands_prompt.txt`

#### 구현 내용

```python
# hands.py 수정
async def build(self, task, plan, skill_context) -> tuple[str, dict]:
    code = await self._generate_code(task, plan, skill_context)
    
    # Execution Plan 자동 생성
    exec_plan = create_execution_plan_from_task(
        task=task,
        code=code,
        pass_criteria=pass_criteria
    )
    
    return code, {
        "execution_plan": exec_plan.to_dict(),
        "reasoning": reasoning
    }
```

#### 프롬프트 추가

```txt
## Execution Plan Generation (MANDATORY)

After generating code, ALWAYS create an execution plan:

1. Identify execution type:
   - Simple script → PYTHON_SCRIPT
   - API server → API_SERVER
   - CLI tool → CLI_COMMAND
   - Test file → PYTHON_TEST

2. List test cases from pass_criteria

3. Specify expected files to be created

4. Note any dependencies (pip install requirements)

Output format:
{
  "execution_type": "...",
  "entry_point": "...",
  "test_cases": [...],
  "expected_files": [...],
  "setup_commands": [...]
}
```

---

### 4.3 CriticRunner Execution Plan 지원 (우선순위: 중)

**목적**: Execution Plan 에 따른 자동 테스트 실행

#### 수정 파일

- `pyovis/execution/critic_runner.py` (기존 129 줄 → 200 줄 예상)

#### 구현 내용

```python
# critic_runner.py 수정
async def execute_with_plan(
    self,
    code: str,
    execution_plan: ExecutionPlan
) -> ExecutionResult:
    
    # Setup: install dependencies
    for cmd in execution_plan.setup_commands:
        await self._run_command(cmd)
    
    # Execute based on type
    if execution_plan.execution_type == ExecutionType.PYTHON_TEST:
        return await self._run_pytest(code, execution_plan)
    elif execution_plan.execution_type == ExecutionType.API_SERVER:
        return await self._run_api_test(code, execution_plan)
    else:
        return await self._run_script(code, execution_plan)
```

---

### 4.4 KG Thought Process 저장 (우선순위: 중)

**목적**: Judge 의 추론 과정을 KG 에 기록하여 향후 참조

#### 수정 파일

- `pyovis/memory/graph_builder.py` (기존 751 줄 → 800 줄 예상)
- `pyovis/orchestration/loop_controller.py`

#### 구현 내용

```python
# loop_controller.py 수정
async def run(self, ctx: LoopContext) -> dict:
    # ... Judge 평가 후 ...
    
    judge_result = await judge.evaluate(...)
    
    # KG 에 Judge 추론 과정 저장
    if judge_result.thought_process:
        await kg_builder.add_triplet(
            subject=f"task_{ctx.task_id}",
            predicate="judged_with_reasoning",
            object=judge_result.thought_process
        )
        
        # 유사 케이스 검색용 인덱싱
        await kg_builder.index_reasoning(
            task_id=ctx.task_id,
            reasoning=judge_result.thought_process,
            verdict=judge_result.verdict,
            error_type=judge_result.error_type
        )
```

---

### 4.5 전체 E2E 테스트 (우선순위: 상)

**목적**: Phase 1-4 전체 통합 검증

#### 테스트 파일

- `tests/test_e2e_v5_pipeline.py` (예상 30 테스트)

#### 테스트 시나리오

```python
async def test_full_pipeline_with_chat_chain():
    """Chat Chain + Execution Plan + Judge 통합 테스트"""
    
    # 1. Planner-Brain 합의 (Segment A)
    consensus_A = await run_planner_brain_segment(...)
    assert consensus_A.agreed is True
    
    # 2. Brain-Hands 합의 (Segment B)
    consensus_B = await run_brain_hands_segment(...)
    assert consensus_B.agreed is True
    
    # 3. Execution Plan 검증
    assert "execution_plan" in hands_result
    assert hands_result["execution_plan"]["test_cases"] > 0
    
    # 4. Judge Thought Instruction 검증
    judge_result = await judge.evaluate(...)
    assert "check_results" in judge_result.to_dict()
    assert "thought_process" in judge_result.to_dict()
    assert len(judge_result.check_results) >= 4  # 4 steps
    
    # 5. Experience DB 저장 검증
    await experience_db.add_experience(...)
    similar = await experience_db.search_similar(task_description)
    assert len(similar) > 0
```

---

## 📅 Phase 4 일정

| 작업 | 예상 기간 | 우선순위 |
|------|----------|----------|
| 4.1 Experience DB | 2~3 일 | 🔴 상 |
| 4.2 Hands Execution Plan | 1~2 일 | 🔴 상 |
| 4.3 CriticRunner 지원 | 1 일 | 🟠 중 |
| 4.4 KG Thought Process | 1 일 | 🟠 중 |
| 4.5 E2E 테스트 | 2 일 | 🔴 상 |
| **총계** | **7~10 일** | |

---

## 🎯 Phase 4 완료 기준

- [ ] Experience DB 구현 및 테스트 (15 개 테스트 통과)
- [ ] Hands Execution Plan 자동 생성 (프롬프트 통합)
- [ ] CriticRunner Execution Plan 지원 (테스트 자동화)
- [ ] KG Thought Process 저장 (검색 가능)
- [ ] E2E 테스트 30 개 중 27 개 이상 통과
- [ ] 전체 테스트 스위트 280 개 중 270 개 이상 통과 (96%)
- [ ] 문서 업데이트 완료

---

## 📈 예상 효과

### Experience DB

- **성공 패턴 재사용**: 유사 작업 시 30~50% 시간 단축
- **실패 패턴 회피**: 반복 실수 방지

### Execution Plan

- **Judge 정확도 향상**: 85% → 95% 예상
- **테스트 커버리지**: 60% → 90% 예상

### Thought Process

- **디버깅 용이성**: Judge 판단 근거 추적 가능
- **KG 품질 향상**: 추론 과정 기록으로 검색 정확도 향상

---

## 🔗 관련 문서

- `pyovis_v5_1.md` — Chat Chain 상세 설계
- `pyovis_v5_architecture.md` — 전체 아키텍처
- `IMPROVEMENTS.md` — 개선 로그 (Phase 1-3 완료)
