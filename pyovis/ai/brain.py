import json
import logging
import re
from typing import Any, Dict

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager
from pyovis.ai.response_utils import extract_reasoning, parse_json_message, strip_cot

logger = logging.getLogger(__name__)


class Brain:
    def __init__(self, swap_manager: ModelSwapManager) -> None:
        self.system_prompt = load_prompt("brain_prompt.txt")
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=600.0)

    async def plan(self, ctx) -> tuple[Dict[str, Any], str]:
        user_message = f"""
과제: {ctx.task_description}

다음 형식으로 반드시 JSON으로만 응답하라:
{{
  "plan": "전체 아키텍처 및 구현 계획 (마크다운)",
  "todo_list": [
    {{"id": 1, "title": "Task 제목", "description": "상세 설명"}}
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
        response, reasoning = await self._call(user_message)
        clean = strip_cot(response)
        return parse_json_message({"content": clean}), reasoning

    async def handle_escalation(self, ctx) -> tuple[Dict[str, Any], str]:
        user_message = f"""
원래 계획서: {ctx.plan}
실패 원인 목록: {json.dumps(ctx.fail_reasons, ensure_ascii=False)}
루프 횟수: {ctx.loop_count}
마지막 에러: {ctx.critic_result.get('stderr', '')}

판단 기준:
- revise_plan 선택 조건 (코드/계획 수정으로 해결 가능):
  * plan_error: 잘못된 접근 방식, 아키텍처 오류, 파일 구조 문제
  * implementation_error: 로직 오류, 잘못된 알고리즘
  * environment_error 중 revise_plan 가능:
    - 외부 패키지 설치 실패 → 표준 라이브러리나 다른 방법으로 동일 기능 구현
    - 특정 라이브러리에 의존하는 구현 → 의존성 없이 재구현
- human_escalation 선택 조건 (코드 수정만으로 해결 불가):
  * 하드웨어/OS 제약 (GPU 없음, 파일시스템 권한 등)
  * 외부 API 키/인증 필요
  * 요구사항 자체가 불명확하거나 모순됨

원인을 분류하고 다음 형식으로 응답:
{{
  "cause_type": "plan_error | implementation_error | environment_error",
  "action": "revise_plan | human_escalation",
  "analysis": "분석 내용",
  "new_plan": "수정된 계획 (action이 revise_plan일 때)",
  "new_todo": [
    {{"id": 1, "title": "Task 제목", "description": "상세 설명"}}
  ],
  "new_criteria": {{
    "1": ["조건1", "조건2"]
  }}
}}
"""
        response, reasoning = await self._call(user_message)
        clean = strip_cot(response)
        return parse_json_message(
            {"content": clean},
            default={"action": "human_escalation", "cause_type": "parse_error"}
        ), reasoning

    async def final_review(self, ctx) -> tuple[Dict[str, Any], str]:
        # 생성된 파일 목록 정리
        file_list = ""
        if ctx.created_files:
            lines = []
            for f in ctx.created_files:
                fp = f.get("file_path", "?")
                sp = f.get("saved_path", "")
                size = f.get("size_bytes", 0)
                lines.append(f"  - {fp} ({size} bytes) → {sp}")
            file_list = "\n".join(lines)
        else:
            file_list = "  (생성된 파일 없음)"

        user_message = f"""다음 프로젝트의 최종 결과물을 검토하고 README.md를 작성하라.

## 과제
{ctx.task_description}

## 구현 계획
{ctx.plan or '(계획 없음)'}

## 생성된 파일 목록
{file_list}

## 요구사항
아래 내용을 포함하는 README.md를 마크다운 형식으로 작성하라:
1. 프로젝트 개요 (1-2문장)
2. 주요 기능
3. 파일 구조 설명
4. 설치 방법 (필요한 패키지, pip install 명령어)
5. 실행 방법 (명령어 예시)
6. 사용 방법 또는 조작법 (해당하는 경우)

코드 블록 없이 순수 마크다운 텍스트만 출력하라. 앞뒤 설명 없이 README 본문만 출력하라."""

        response, reasoning = await self._call(user_message)
        readme_content = strip_cot(response).strip()
        return {"status": "complete", "review": readme_content, "readme": readme_content}, reasoning

    async def _call(self, user_message: str) -> tuple[str, str]:
        logger.info("🧠 Brain._call() 시작")
        await self.swap.ensure_model("brain")
        logger.info("🧠 Brain 모델 로드 완료")

        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        logger.info("🧠 LLM 요청 전송...")
        resp = await self.client.post(self.swap.api_url, json=payload)
        resp.raise_for_status()
        logger.info("🧠 LLM 응답 수신")
        message = resp.json()["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        reasoning = extract_reasoning(message)
        logger.info(f"🧠 Brain._call() 완료 (content={len(content)}자, reasoning={len(reasoning)}자)")
        return content, reasoning
