import json
import logging
import re
from typing import Any, Dict

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager
from pyovis.ai.response_utils import extract_reasoning, parse_json_message, strip_cot

logger = logging.getLogger(__name__)

class Planner:
    def __init__(self, swap_manager: ModelSwapManager) -> None:
        self.system_prompt = load_prompt("planner_prompt.txt")
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=600.0)

    async def plan(self, ctx) -> tuple[Dict[str, Any], str]:
        user_message = f"""
과제: {ctx.task_description}

다음 형식으로 반드시 JSON으로만 응답하라:
{{
  "plan": "전체 아키텍처 및 구현 계획 (마크다운)",
  "file_structure": [
    "path/to/file1.py - 파일 설명",
    "path/to/file2.py - 파일 설명"
  ],
  "todo_list": [
    {{
      "id": 1,
      "file_path": "path/to/file.py",
      "title": "구현할 기능 제목",
      "description": "이 파일에 구현할 구체적인 내용. 어떤 클래스/함수를 만들지, 어떤 로직을 구현할지 상세히",
      "pass_type": "output_check"
    }}
  ],
  "pass_criteria": {{
    "1": ["파일이 생성됨", "함수가 정상 작동함"],
    "2": ["조건1"]
  }},
  "self_fix_scope": {{
    "allowed": ["type_error", "syntax_error", "missing_import", "logic_error"],
    "escalate": ["architecture_change", "new_file_needed"]
  }}
}}

중요:
1. file_structure를 먼저 작성하여 전체 파일 구조를 보여라
2. todo_list는 의존성 순서대로 작성하라 (의존받는 파일이 먼저)
3. 각 todo는 정확히 하나의 파일에 대한 작업이어야 한다
4. description은 Hands가 바로 구현할 수 있도록 구체적으로 작성하라
"""
        response, reasoning = await self._call(user_message)
        clean = strip_cot(response)
        result = parse_json_message({"content": clean})
        
        # Normalize todo_list: ensure all required fields exist
        todo_list = result.get("todo_list", [])
        normalized = []
        for i, item in enumerate(todo_list):
            if isinstance(item, str):
                normalized.append({
                    "id": i + 1,
                    "file_path": f"file_{i+1}.py",
                    "title": item[:50],
                    "description": item
                })
            elif isinstance(item, dict):
                if "id" not in item:
                    item["id"] = i + 1
                if "file_path" not in item:
                    item["file_path"] = f"file_{item.get('id', i+1)}.py"
                else:
                    # LLM이 "config.py - 설명" 형태로 반환하는 경우 설명 부분 제거
                    fp = item["file_path"]
                    if " - " in fp:
                        item["file_path"] = fp.split(" - ")[0].strip()
                normalized.append(item)
        result["todo_list"] = normalized

        # P2-4: Planner 스키마 검증 — 필수 필드 보증
        if not result.get("todo_list"):
            logger.warning("Planner가 빈 todo_list 반환, 기본값 생성")
            result["todo_list"] = [{"id": 1, "file_path": "main.py", "title": "main", "description": ctx.task_description}]
        if not result.get("pass_criteria"):
            logger.warning("Planner가 pass_criteria 누락, 기본값 생성")
            result["pass_criteria"] = {
                str(t["id"]): f"{t.get('title', 'task')} 완료"
                for t in result["todo_list"]
            }

        # P2-2: pass_criteria 키 str 정규화 (Brain이 int 키 반환 가능)
        raw_criteria = result.get("pass_criteria", {})
        result["pass_criteria"] = {str(k): v for k, v in raw_criteria.items()}

        # file_structure도 동일하게 정리 ("app.py - 설명" → "app.py")
        file_structure = result.get("file_structure", [])
        cleaned_structure = []
        for entry in file_structure:
            if isinstance(entry, str) and " - " in entry:
                cleaned_structure.append(entry.split(" - ")[0].strip())
            else:
                cleaned_structure.append(entry)
        result["file_structure"] = cleaned_structure
        
        return result, reasoning

    async def _call(self, user_message: str) -> tuple[str, str]:
        await self.swap.ensure_model("planner")

        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.4,
            "max_tokens": 4096,
        }
        resp = await self.client.post(self.swap.api_url, json=payload)
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        reasoning = extract_reasoning(message)
        return content, reasoning
