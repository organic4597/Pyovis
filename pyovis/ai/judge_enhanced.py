"""
Pyvis v5.1 — Enhanced Judge with Thought Instruction

Judge performs transparent evaluation using 4-step Thought Instruction checklist:
- CHECK 1: Exit code validation
- CHECK 2: PASS criteria verification
- CHECK 3: Missing symbols detection
- CHECK 4: Error classification

References: pyovis_v5_1.md section 6
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager


@dataclass
class CheckResult:
    """Result of individual checklist item"""

    check_name: str
    passed: bool
    details: str
    evidence: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "details": self.details,
            "evidence": self.evidence,
        }


@dataclass
class JudgeResult:
    """Enhanced Judge result with thought process"""

    verdict: str  # PASS / REVISE / ENRICH / ESCALATE
    score: int  # 0~100
    reason: str
    error_type: Optional[str]

    # v5.1 additions
    check_results: Dict[str, CheckResult] = field(default_factory=dict)
    thought_process: str = ""
    execution_plan_validated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "score": self.score,
            "reason": self.reason,
            "error_type": self.error_type,
            "check_results": {k: v.to_dict() for k, v in self.check_results.items()},
            "thought_process": self.thought_process,
            "execution_plan_validated": self.execution_plan_validated,
        }


class EnhancedJudge:
    """
    Enhanced Judge with Thought Instruction checklist.

    Usage:
        judge = EnhancedJudge(swap_manager)
        result = await judge.evaluate(
            task=task,
            pass_criteria=criteria,
            critic_result=critic_result,
            execution_plan=plan,  # NEW: from Hands
            loop_count=loop_count
        )
    """

    SYSTEM_PROMPT = """당신은 Pyvis 의 Judge 입니다. DeepSeek-R1-Distill-14B 모델입니다.

ROLE: 독립적인 실행 결과 평가 (PASS 기준 대비)

CORE PRINCIPLE:
- 이전 대화 기록 없음. 지금 보이는 것만 판단합니다.
- Hands 의 코드 작성 과정은 알지 못합니다.
- 칭찬하지 않습니다. 근거만 제시합니다.

MANDATORY THOUGHT PROCESS (반드시 4 단계를 순서대로 수행):

[CHECK 1] 종료 코드 (EXIT CODE)
→ exit_code == 0 인가?
→ 0 이 아니면: 어떤 유형의 에러인가? (SyntaxError / ImportError / RuntimeError / 기타)

[CHECK 2] PASS 기준 검증
→ pass_criteria 의 각 항목을 하나씩 확인하십시오.
→ 각 항목마다: ✅ SATISFIED / ❌ NOT_SATISFIED / ⚠️ PARTIAL 표시
→ 예시:
  - "HTTP 200 응답": ✅ stdout 에 200 표시됨
  - "None 입력 처리": ❌ None 입력 시 TypeError 발생

[CHECK 3] 누락 심볼
→ import 누락 / 정의되지 않은 함수 / 없는 파일이 있는가?
→ 모두 나열: ModuleNotFoundError, NameError, FileNotFoundError

[CHECK 4] 에러 분류
→ 이 에러는 Hands 자체 수정 가능한가?
→ 분류: type_error / syntax_error / missing_import / logic_error / architecture_error
→ architecture_error → ESCALATE 필수

SCORING:
  90~100: 모든 기준 충족 → PASS
  70~89: 경미한 문제, 자체 수정 가능 → REVISE
  0~69: 주요 문제 또는 다수 실패 → ENRICH
  -: 판단 불가 / 아키텍처 문제 → ESCALATE

OUTPUT FORMAT (체크리스트 수행 후 반드시 JSON 으로만 응답):
{
  "check_results": {
    "exit_code_ok": true/false,
    "criteria_results": [
      {"criterion": "...", "result": "SATISFIED/NOT_SATISFIED/PARTIAL"}
    ],
    "missing_symbols": [],
    "error_type": "..."
  },
  "verdict": "PASS|REVISE|ENRICH|ESCALATE",
  "score": 0-100,
  "reason": "한 줄 요약",
  "error_type": "..."
}

CHECKLIST 생략 금지. 반드시 4 단계를 모두 수행하십시오.
"""

    def __init__(self, swap_manager: ModelSwapManager) -> None:
        self.system_prompt = self.SYSTEM_PROMPT
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=300.0)

    async def evaluate(
        self,
        task: dict,
        pass_criteria: dict,
        critic_result: dict,
        loop_count: int,
        execution_plan: Optional[dict] = None,  # NEW parameter
    ) -> JudgeResult:
        """
        Evaluate code execution with Thought Instruction.

        Args:
            task: Task dictionary
            pass_criteria: PASS criteria from Planner
            critic_result: Execution result from Critic
            loop_count: Current loop count
            execution_plan: Execution plan from Hands (optional)

        Returns:
            JudgeResult with check_results and thought_process
        """
        criteria = pass_criteria.get(str(task["id"]), [])

        # Build user message
        user_message = self._build_user_message(
            task=task,
            criteria=criteria,
            critic_result=critic_result,
            loop_count=loop_count,
            execution_plan=execution_plan,
        )

        # Call Judge model
        response = await self._call_fresh(user_message)

        # Parse response with thought process extraction
        return self._parse_enhanced(response)

    def _build_user_message(
        self,
        task: dict,
        criteria: list,
        critic_result: dict,
        loop_count: int,
        execution_plan: Optional[dict] = None,
    ) -> str:
        """Build user message for Judge"""
        parts = [
            f"Task: {task['title']}",
            "",
            "PASS 기준:",
        ]

        for i, c in enumerate(criteria, 1):
            if isinstance(c, str):
                parts.append(f"  {i}. {c}")
            elif isinstance(c, dict):
                name = c.get("name", f"Criterion {i}")
                desc = c.get("description", str(c))
                parts.append(f"  {i}. {name}: {desc}")

        parts.extend(
            [
                "",
                "실행 결과:",
                f"- 종료 코드: {critic_result.get('exit_code', -1)}",
                f"- 실행 시간: {critic_result.get('execution_time', 0):.2f}초",
                f"- 표준 출력: {critic_result.get('stdout', '없음')[:500]}",
                f"- 에러: {critic_result.get('stderr', '없음')[:500]}",
                f"- 현재 루프 횟수: {loop_count}",
            ]
        )

        # Add execution plan if available
        if execution_plan:
            parts.extend(
                [
                    "",
                    "실행 계획 (Hands 가 제공):",
                    f"- 실행 유형: {execution_plan.get('execution_type', 'unknown')}",
                    f"- 진입점: {execution_plan.get('entry_point', 'N/A')}",
                ]
            )

            test_cases = execution_plan.get("test_cases", [])
            if test_cases:
                parts.append(f"- 테스트 케이스: {len(test_cases)}개")
                for tc in test_cases[:3]:  # Show first 3
                    parts.append(
                        f"  • {tc.get('name', 'Test')}: {tc.get('description', '')[:50]}"
                    )

            expected_files = execution_plan.get("expected_files", [])
            if expected_files:
                parts.append(f"- 예상 파일: {', '.join(expected_files[:5])}")

        parts.extend(
            [
                "",
                "지시사항:",
                "1. Thought Instruction 4 단계 체크리스트를 반드시 순서대로 수행하십시오.",
                "2. 실행 계획이 제공되면, 테스트 케이스와 실제 결과를 비교하십시오.",
                "3. PASS 기준을 모두 충족하면 PASS.",
                "4. 일부 미충족이면 REVISE(70 점 이상) 또는 ENRICH(70 점 미만).",
                "5. 판단 불가 또는 반복 실패이면 ESCALATE.",
                "",
                "반드시 다음 JSON 형식으로만 응답하십시오:",
                "{",
                '  "check_results": {',
                '    "exit_code_ok": true/false,',
                '    "criteria_results": [{"criterion": "...", "result": "SATISFIED/NOT_SATISFIED/PARTIAL"}],',
                '    "missing_symbols": [],',
                '    "error_type": "..."',
                "  },",
                '  "verdict": "PASS|REVISE|ENRICH|ESCALATE",',
                '  "score": 0-100,',
                '  "reason": "판단 근거",',
                '  "error_type": "..."',
                "}",
            ]
        )

        return "\n".join(parts)

    async def _call_fresh(self, user_message: str) -> str:
        """Call Judge model with fresh context (no conversation history)"""
        await self.swap.ensure_model("judge")

        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,  # Increased for thought process
        }

        resp = await self.client.post(self.swap.api_url, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _parse_enhanced(self, response: str) -> JudgeResult:
        """Parse enhanced Judge response with check_results"""
        try:
            # Extract JSON
            payload = re.sub(r"```json|```", "", response or "").strip()
            match = re.search(r"\{.*\}", payload, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found")

            data = json.loads(match.group(0))

            # Parse check_results
            check_results = {}
            if "check_results" in data:
                cr = data["check_results"]

                # Exit code check
                if "exit_code_ok" in cr:
                    check_results["exit_code"] = CheckResult(
                        check_name="exit_code",
                        passed=cr["exit_code_ok"],
                        details="종료 코드 정상"
                        if cr["exit_code_ok"]
                        else "종료 코드 오류",
                    )

                # Criteria results
                if "criteria_results" in cr:
                    for i, item in enumerate(cr["criteria_results"]):
                        criterion_name = item.get("criterion", f"criterion_{i}")
                        result = item.get("result", "NOT_SATISFIED")
                        passed = result == "SATISFIED"
                        check_results[f"criterion_{i}"] = CheckResult(
                            check_name=f"criterion_{i}",
                            passed=passed,
                            details=result,
                            evidence=item.get("evidence"),
                        )

                # Missing symbols
                if "missing_symbols" in cr and cr["missing_symbols"]:
                    check_results["missing_symbols"] = CheckResult(
                        check_name="missing_symbols",
                        passed=False,
                        details=f"누락 심볼 발견: {', '.join(cr['missing_symbols'])}",
                    )

            # Extract thought process (if present in response before JSON)
            thought_process = ""
            json_start = response.find("{")
            if json_start > 0:
                thought_process = response[:json_start].strip()

            return JudgeResult(
                verdict=data["verdict"],
                score=int(data["score"]),
                reason=data["reason"],
                error_type=data.get("error_type"),
                check_results=check_results,
                thought_process=thought_process,
                execution_plan_validated="test_cases"
                in str(data.get("check_results", {})),
            )

        except Exception as e:
            # Fallback to basic parsing
            return self._parse_fallback(response, str(e))

    def _parse_fallback(self, response: str, error: str) -> JudgeResult:
        """Fallback parsing for malformed responses"""
        try:
            # Try to extract basic fields
            verdict_match = re.search(
                r'"verdict"\s*:\s*"(PASS|REVISE|ENRICH|ESCALATE)"', response
            )
            score_match = re.search(r'"score"\s*:\s*(\d+)', response)
            reason_match = re.search(r'"reason"\s*:\s*"([^"]+)"', response)

            verdict = verdict_match.group(1) if verdict_match else "ESCALATE"
            score = int(score_match.group(1)) if score_match else 0
            reason = reason_match.group(1) if reason_match else f"파싱 오류: {error}"

            return JudgeResult(
                verdict=verdict,
                score=score,
                reason=reason,
                error_type=None,
                check_results={},
                thought_process=response[:500],
                execution_plan_validated=False,
            )
        except Exception:
            # Ultimate fallback
            return JudgeResult(
                verdict="ESCALATE",
                score=0,
                reason=f"Judge 응답 파싱 실패: {error}",
                error_type=None,
                check_results={},
                thought_process="",
                execution_plan_validated=False,
            )


# Backward compatibility alias
Judge = EnhancedJudge
