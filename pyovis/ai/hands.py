import re
from typing import TYPE_CHECKING

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager
from pyovis.ai.response_utils import extract_reasoning

if TYPE_CHECKING:
    from pyovis.mcp.tool_adapter import MCPToolAdapter

_CODE_FENCE_RE = re.compile(r"^```[\w]*\n?", re.MULTILINE)
_CODE_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")


class Hands:
    def __init__(
        self, 
        swap_manager: ModelSwapManager,
        tool_adapter: "MCPToolAdapter | None" = None,
    ) -> None:
        self.system_prompt = load_prompt("hands_prompt.txt")
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=600.0)
        self.tool_adapter = tool_adapter
        self.max_tool_iterations = 3

    async def build(self, task: dict | str, plan: str, skill_context: str) -> tuple[str, str]:
        if isinstance(task, str):
            file_path = 'output.py'
            title = task[:50]
            description = task
        else:
            file_path = task.get('file_path', 'output.py')
            title = task.get('title', '구현')
            description = task.get('description', '')
        
        user_message = f"""
전체 계획서:
{plan}

현재 구현할 파일: {file_path}
Task: {title}
상세 내용: {description}

적용할 Skill 규칙:
{skill_context}

지시사항:
1. 위 파일 경로에 해당하는 코드만 구현하라
2. 다른 파일의 코드는 작성하지 마라
3. 완전하고 실행 가능한 코드를 작성하라
4. 필요한 import 문을 포함하라
"""
        return await self._call_with_tools(user_message)

    async def revise(
        self, 
        task: dict, 
        prev_code: str, 
        critic_result: dict, 
        self_fix_scope: dict,
        judge_result: dict | None = None,
        pass_criteria: dict | None = None,
        skill_context: str = "",
    ) -> tuple[str, str]:
        criteria_list = []
        if pass_criteria:
            task_id = str(task.get("id", 1))
            criteria_list = pass_criteria.get(task_id, [])
        
        judge_feedback = ""
        if judge_result:
            judge_feedback = f"""
Judge 평가 결과:
- 판정: {judge_result.get('verdict', 'UNKNOWN')}
- 점수: {judge_result.get('score', 0)}/100
- 사유: {judge_result.get('reason', '없음')}
- 에러 유형: {judge_result.get('error_type', '없음')}
"""
        
        criteria_text = "\n".join(f"- {c}" for c in criteria_list) if criteria_list else "없음"
        
        skill_section = f"""
적용할 Skill 규칙:
{skill_context}
""" if skill_context else ""
        
        user_message = f"""
Task: {task.get('title', '코드 수정')}
파일: {task.get('file_path', 'output.py')}
{skill_section}
이전 코드:
```
{prev_code}
```

PASS 기준:
{criteria_text}
{judge_feedback}
실행 결과:
- 종료 코드: {critic_result.get('exit_code', 0)}
- 실행 시간: {critic_result.get('execution_time', 0):.2f}초
- 표준 출력: {critic_result.get('stdout', '없음')[:1000]}
- 에러 출력: {critic_result.get('stderr', '없음')[:1000]}

자율 수정 가능 범위: {self_fix_scope.get('allowed', [])}

지시사항:
1. 위 에러를 수정하라
2. PASS 기준을 충족하도록 코드를 개선하라
3. 허용 범위 외 변경은 금지
4. 전체 코드를 다시 작성하라 (수정된 부분만 보내지 마라)
"""
        return await self._call_with_tools(user_message)

    async def _call_with_tools(self, user_message: str) -> tuple[str, str]:
        """Tool calling이 포함된 LLM 호출."""
        await self.swap.ensure_model("hands")
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        
        final_content = ""
        final_reasoning = ""
        
        for iteration in range(self.max_tool_iterations):
            payload = {
                "model": "local",
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 8192,
            }
            
            if self.tool_adapter:
                tools = self.tool_adapter.get_tools_schema()
                if tools:
                    payload["tools"] = tools
            
            resp = await self.client.post(self.swap.api_url, json=payload)
            resp.raise_for_status()
            response = resp.json()
            
            message = response["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content") or ""
            final_content = content
            final_reasoning = extract_reasoning(message)
            final_reasoning = extract_reasoning(message)
            
            tool_calls = message.get("tool_calls", [])
            
            if not tool_calls:
                final_content = _CODE_FENCE_RE.sub("", final_content)
                final_content = _CODE_FENCE_CLOSE_RE.sub("", final_content)
                return final_content.strip(), final_reasoning
            
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            
            if self.tool_adapter:
                results = await self.tool_adapter.execute_tool_calls(tool_calls)
                
                for i, result in enumerate(results):
                    tool_call_id = tool_calls[i].get("id", f"call_{i}")
                    
                    if result.success:
                        import json
                        tool_result = json.dumps(result.result, ensure_ascii=False) if isinstance(result.result, dict) else str(result.result)
                    else:
                        tool_result = f"Error: {result.error}"
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_result[:2000],
                    })
        
        final_content = _CODE_FENCE_RE.sub("", final_content)
        final_content = _CODE_FENCE_CLOSE_RE.sub("", final_content)
        return final_content.strip(), final_reasoning

    async def _call(self, user_message: str) -> tuple[str, str]:
        await self.swap.ensure_model("hands")

        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.2,
            "max_tokens": 8192,
        }
        resp = await self.client.post(self.swap.api_url, json=payload)
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        reasoning = extract_reasoning(message)
        content = _CODE_FENCE_RE.sub("", content)
        content = _CODE_FENCE_CLOSE_RE.sub("", content)
        return content.strip(), reasoning
