import json
import re
from dataclasses import dataclass

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager


@dataclass
class JudgeResult:
    verdict: str  # PASS / REVISE / ENRICH / ESCALATE
    score: int  # 0~100
    reason: str
    error_type: str | None  # Hands 자율 수정 가능 여부 판단용


class Judge:
    def __init__(self, swap_manager: ModelSwapManager) -> None:
        self.system_prompt = load_prompt("judge_prompt.txt")
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=300.0)

    async def evaluate(
        self, task: dict, pass_criteria: dict, critic_result: dict, loop_count: int
    ) -> JudgeResult:
        criteria = pass_criteria.get(str(task["id"]), [])

        user_message = f"""
Task: {task['title']}
PASS 기준:
{chr(10).join(f'- {c}' for c in criteria)}

실행 결과:
- 종료 코드: {critic_result.get('exit_code', -1)}
- 실행 시간: {critic_result.get('execution_time', 0):.2f}초
- 표준 출력: {critic_result.get('stdout', '없음')[:500]}
- 에러: {critic_result.get('stderr', '없음')[:500]}
- 현재 루프 횟수: {loop_count}

PASS 기준을 모두 충족하면 PASS.
일부 미충족이면 REVISE(70점 이상) 또는 ENRICH(70점 미만).
판단 불가 또는 반복 실패이면 ESCALATE.

반드시 다음 JSON 형식으로만 응답:
{{"verdict": "PASS|REVISE|ENRICH|ESCALATE", "score": 0-100,
  "reason": "판단 근거", "error_type": "에러 유형 (없으면 null)"}}
"""
        response = await self._call_fresh(user_message)
        return self._parse(response)

    async def _call_fresh(self, user_message: str) -> str:
        """매번 새로운 컨텍스트 — 이전 대화 기록 없음."""
        await self.swap.ensure_model("judge")

        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }
        resp = await self.client.post(self.swap.api_url, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _parse(self, response: str) -> JudgeResult:
        try:
            payload = response or ""
            payload = re.sub(r"```json|```", "", payload).strip()
            match = re.search(r"\{.*\}", payload, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found")
            data = json.loads(match.group(0))
            return JudgeResult(
                verdict=data["verdict"],
                score=int(data["score"]),
                reason=data["reason"],
                error_type=data.get("error_type"),
            )
        except Exception:
            return JudgeResult(
                verdict="ESCALATE",
                score=0,
                reason="Judge 응답 파싱 실패",
                error_type=None,
            )
