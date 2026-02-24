충분한 데이터가 모였습니다. ChatDev 원본 논문까지 분석했습니다.

---

## 현재 Pyvis에 적용된 것 vs 아직 안 된 것

```
✅ 이미 적용
  Chat Chain (구간 A: Planner↔Brain, 구간 B: Brain↔Hands)
  [CONSENSUS] 태그
  max_turns 상한

❌ 아직 미적용 — 아래가 핵심
```

---

## 채용할 만한 아이디어 3가지

---

### 1. Communicative Dehallucination (가장 중요)

에이전트가 직접 응답하기 전에 더 구체적인 정보를 먼저 요청하고, 그 정보를 바탕으로 다음 대화를 이어가는 방식입니다.

현재 Pyvis의 문제:
```
Brain → Hands: "타입 에러 수정해"
Hands → (모호한 지시를 억지로 해석) → 잘못된 코드
```

Communicative Dehallucination 적용 후:
```
Brain → Hands: "타입 에러 수정해"
Hands → Brain: "어떤 타입으로 캐스팅? Optional[str] vs str?"
           ← 이게 Role Reversal (조력자가 지시자처럼 질문)
Brain → Hands: "Optional[str] = None으로 처리"
Hands → 정확한 코드 생성
```

코딩 환각(Coding Hallucination)은 어시스턴트가 모호한 지시를 억지로 따르려 할 때 발생합니다. Communicative Dehallucination은 어시스턴트가 응답 전에 구체적인 정보를 능동적으로 요청하게 만들어 이를 방지합니다.

**Pyvis 적용 방식:**

```python
# Hands 시스템 프롬프트에 추가

HANDS_DEHALLUCINATION_RULE = """
Clarification Rule (MANDATORY):
If any instruction is ambiguous, DO NOT guess.
Instead, ask ONE specific question before generating code.

Format:
[CLARIFICATION_NEEDED]
Question: <정확히 무엇이 불명확한지>
Options: <가능한 선택지 A / B / C>

Only proceed after Brain provides the answer.
"""
```

---

### 2. Thought Instruction (Judge에 적용)

에이전트가 코드 리뷰나 디버깅 중에 "어떤 함수가 아직 구현되지 않았는지 파악하기" 같은 CoT 단계와 체크리스트를 명시적으로 표현하는 방식입니다.

현재 Judge:
```
실행 결과 → PASS/FAIL/ESCALATE (블랙박스 판정)
```

Thought Instruction 적용 후:
```
Judge 내부 추론 (강제):
  [체크 1] 종료 코드가 0인가?
  [체크 2] 예상 출력과 일치하는가?
  [체크 3] 누락된 함수/모듈이 있는가?
  [체크 4] 각 PASS 기준을 순서대로 대조
  → 위 체크리스트 기반으로 점수 산정
  → 판정 근거 명시
```

**Judge 시스템 프롬프트 수정:**

```
판정 전 반드시 다음 체크리스트를 순서대로 수행하라:
[CHECK 1] Exit code: 0인가?
[CHECK 2] 각 PASS 기준 항목을 하나씩 대조
[CHECK 3] 누락된 import/함수/파일이 있는가?
[CHECK 4] 에러 메시지가 Hands 자율 수정 가능 범위인가?

체크리스트 완료 후 JSON 판정 출력.
체크리스트 생략 금지.
```

---

### 3. Experiential Co-Learning (장기적으로 가장 강력)

ChatDev의 파생 프레임워크인 Experiential Co-Learning은 Instructor와 Assistant가 과거 궤적에서 지름길 경험을 수집하고, 이를 미래 과제 수행에 활용합니다.

Co-Saving 방식은 과거의 성공적인 전환 경험(shortcut)을 활용해 토큰 사용량 50.85% 감소, 코드 품질 10.06% 향상을 달성했습니다.

Pyvis에서 이미 Skill 라이브러리가 비슷한 역할을 하지만, 더 정밀하게 만들 수 있습니다.

```
현재 Skill: "이런 실수를 하지 마라" (규칙 기반)

Experiential Co-Learning 방식:
  "지난번 FastAPI 타입 에러 → 이렇게 해결했음" (경험 기반)
  "모듈 B와 모듈 C를 연결할 때 이 패턴이 성공했음"

차이:
  규칙 기반 → 사전 방지
  경험 기반 → 성공 패턴 재사용 + 실패 패턴 회피
```

---

## 우선순위 정리

| 아이디어 | 효과 | 구현 난이도 | 적용 순위 |
|---|---|---|---|
| **Communicative Dehallucination** | 환각 감소, 코드 품질 향상 | 낮음 (프롬프트 추가) | **1순위 즉시** |
| **Thought Instruction** | Judge 판정 투명성, 정확도 향상 | 낮음 (프롬프트 추가) | **1순위 즉시** |
| **Experiential Co-Learning** | 토큰 50% 절감, 품질 향상 | 높음 (KG + 경험 DB 연동) | 3순위 (Phase 4) |

1순위 두 개는 **프롬프트 수정만으로 즉시 적용 가능**합니다. 아키텍처 변경 없이 내일 당장 반영할 수 있습니다. 설계서에 추가할까요?
