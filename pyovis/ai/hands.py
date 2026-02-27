import logging
import re
from typing import TYPE_CHECKING, Optional, Dict, Any

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager
from pyovis.ai.response_utils import extract_reasoning
from pyovis.execution.search_replace import (
    apply_search_replace,
    format_metrics,
    ApplyResult,
)
from pyovis.memory.experience_db import (
    get_experience_db,
    ExperienceEntry,
    TaskType,
)
from pyovis.execution.execution_plan import (
    create_execution_plan_from_task,
    ExecutionPlan,
)

if TYPE_CHECKING:
    from pyovis.mcp.tool_adapter import MCPToolAdapter

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"^```[\w]*\n?", re.MULTILINE)
_CODE_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")


class Hands:
    def __init__(
        self,
        swap_manager: ModelSwapManager,
        tool_adapter: "MCPToolAdapter | None" = None,
        experience_db=None,
    ) -> None:
        self.system_prompt = load_prompt("hands_prompt.txt")
        self.revise_prompt = load_prompt("hands_revise_prompt.txt")
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=600.0)
        self.tool_adapter = tool_adapter
        self.max_tool_iterations = 3
        self.experience_db = experience_db

    async def _get_experience_context(
        self, task_description: str, task_type: str = TaskType.PYTHON_SCRIPT.value
    ) -> str:
        """Get relevant experience patterns for the current task."""
        if self.experience_db is False:  # Explicitly disabled
            return ""

        db = self.experience_db or get_experience_db()

        try:
            # Search for similar successful experiences
            similar = await db.search_similar(
                query=task_description,
                k=3,
                task_type_filter=task_type,
                success_only=True,
            )

            if not similar:
                return ""

            # Format experiences for prompt
            lines = ["\n## Similar Success Cases (for reference):"]
            for i, exp in enumerate(similar[:2], 1):
                lines.append(f"\n### Case {i}")
                lines.append(f"- Task: {exp.task_description[:100]}")
                if exp.judge_feedback:
                    lines.append(f"- Judge Feedback: {exp.judge_feedback[:200]}")
                if exp.techniques_used:
                    lines.append(f"- Techniques Used: {', '.join(exp.techniques_used)}")

            return "\n".join(lines)

        except Exception:
            return ""

    async def _detect_task_type(self, task: dict | str) -> str:
        """Detect task type from task description."""
        if isinstance(task, str):
            desc = task
        else:
            desc = task.get("description", "") + " " + task.get("title", "")

        desc_lower = desc.lower()

        if "test" in desc_lower or "unittest" in desc_lower or "pytest" in desc_lower:
            return TaskType.TEST_FILE.value
        elif "api" in desc_lower or "server" in desc_lower or "fastapi" in desc_lower:
            return TaskType.API_SERVER.value
        elif "cli" in desc_lower or "command" in desc_lower or "argparse" in desc_lower:
            return TaskType.CLI_TOOL.value
        elif "refactor" in desc_lower or "리팩터" in desc_lower:
            return TaskType.REFACTOR.value
        elif "debug" in desc_lower or "수정" in desc_lower or "에러" in desc_lower:
            return TaskType.DEBUG.value
        else:
            return TaskType.PYTHON_SCRIPT.value

    async def build(
        self,
        task: dict | str,
        plan: str,
        skill_context: str,
        pass_criteria: dict | None = None,
    ) -> tuple[str, str, Dict[str, Any]]:
        """Build code with execution plan.

        Returns:
            Tuple of (code, reasoning, execution_plan_dict)
        """
        # Extract task info
        if isinstance(task, str):
            file_path = "output.py"
            title = task[:50]
            description = task
        else:
            file_path = task.get("file_path", "output.py")
            title = task.get("title", "구현")
            description = task.get("description", "")

        # Get experience context
        task_type = await self._detect_task_type(task)
        full_desc = f"{title}: {description}"
        experience_context = await self._get_experience_context(full_desc, task_type)

        user_message = f"""
## Overall Plan:
{plan}

## Current File to Implement: {file_path}
### Task: {title}
### Details: {description}
{experience_context}

## Skill Rules to Apply:
{skill_context}

### Instructions:
1. Only implement code for the file specified above
2. Do not write code for other files
3. Write complete, executable code
4. Include all necessary import statements
"""
        code, reasoning = await self._call_with_tools(user_message)

        # Create execution plan from generated code
        task_dict = {
            "file_path": file_path,
            "title": title,
            "description": description,
        }
        execution_plan = create_execution_plan_from_task(
            task=task_dict,
            code=code,
            pass_criteria=pass_criteria or {},
        )

        return code, reasoning, execution_plan.to_dict()

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
        """Revise code using Search/Replace blocks with whole-file fallback.

        Returns:
            Tuple of (revised_code, reasoning)
        """
        criteria_list = []
        if pass_criteria:
            task_id = str(task.get("id", 1))
            criteria_list = pass_criteria.get(task_id, [])

        judge_feedback = ""
        if judge_result:
            judge_feedback = f"""
### Judge Evaluation Result:
- Verdict: {judge_result.get("verdict", "UNKNOWN")}
- Score: {judge_result.get("score", 0)}/100
- Reason: {judge_result.get("reason", "none")}
- Error Type: {judge_result.get("error_type", "none")}
"""

        criteria_text = (
            "\n".join(f"- {c}" for c in criteria_list) if criteria_list else "none"
        )

        skill_section = (
            f"""
## Skill Rules to Apply:
{skill_context}
"""
            if skill_context
            else ""
        )

        # Get failure patterns from experience DB
        failure_context = ""
        if judge_result and judge_result.get("error_type"):
            if self.experience_db is not False:
                db = self.experience_db or get_experience_db()
                try:
                    failures = await db.get_failure_patterns(
                        judge_result.get("error_type")
                    )
                    if failures:
                        lines = ["\n## Similar Past Failures (avoid these mistakes):"]
                        for i, fail in enumerate(failures[:2], 1):
                            lines.append(f"\n### Failure {i}")
                            lines.append(
                                f"- Error: {fail.get('error_type', 'unknown')}"
                            )
                            feedback = fail.get("judge_feedback")
                            if feedback:
                                lines.append(f"- Feedback: {feedback[:150]}")
                        failure_context = "\n".join(lines)
                except Exception:
                    pass

        user_message = f"""
### Task: {task.get("title", "Code Revision")}
### File: {task.get("file_path", "output.py")}
{skill_section}

## Previous Code:
```
{prev_code}
```

## PASS Criteria:
{criteria_text}
{judge_feedback}

## Execution Result:
- Exit Code: {critic_result.get("exit_code", 0)}
- Execution Time: {critic_result.get("execution_time", 0):.2f}sec
- Stdout: {critic_result.get("stdout", "none")[:1000]}
- Stderr: {critic_result.get("stderr", "none")[:1000]}
{failure_context}

## Allowed Modification Scope: {self_fix_scope.get("allowed", [])}

### Instructions:
1. Fix the above errors
2. Improve code to meet PASS criteria
3. Changes outside allowed scope are prohibited
4. Use SEARCH/REPLACE blocks to show only the changed parts
5. Each SEARCH block must exactly match the original code
"""
        # Use revise-specific system prompt for S/R format
        llm_response, reasoning = await self._call_with_tools(
            user_message, system_prompt=self.revise_prompt
        )

        # Apply S/R blocks to previous code
        new_code = self._apply_search_replace_with_fallback(
            prev_code, llm_response
        )

        return new_code, reasoning

    def _apply_search_replace_with_fallback(
        self, prev_code: str, llm_response: str
    ) -> str:
        """Apply S/R blocks to code. Falls back to whole-file on failure.

        Stores metrics in self._last_sr_metrics for logging by loop_controller.
        """
        sr_result = apply_search_replace(prev_code, llm_response)
        self._last_sr_metrics = format_metrics(sr_result)

        if sr_result.success:
            logger.info(
                f"S/R 블록 적용 성공: {sr_result.blocks_applied}개 블록, "
                f"매칭 방식: {sr_result.match_types}"
            )
            return sr_result.new_code

        # Fallback: treat LLM response as whole-file rewrite
        logger.warning(
            f"S/R 블록 실패 ({sr_result.fail_reason}), "
            f"전체 파일 재작성으로 fallback"
        )
        self._last_sr_metrics["sr_fallback_triggered"] = True

        # The LLM response IS the full code (fallback behavior)
        return llm_response

    async def _call_with_tools(self, user_message: str, system_prompt: str | None = None) -> tuple[str, str]:
        """LLM call with tool calling."""
        await self.swap.ensure_model("hands")

        messages = [
            {"role": "system", "content": system_prompt or self.system_prompt},
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

            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                final_content = _CODE_FENCE_RE.sub("", final_content)
                final_content = _CODE_FENCE_CLOSE_RE.sub("", final_content)
                return final_content.strip(), final_reasoning

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            )

            if self.tool_adapter:
                results = await self.tool_adapter.execute_tool_calls(tool_calls)

                for i, result in enumerate(results):
                    tool_call_id = tool_calls[i].get("id", f"call_{i}")

                    if result.success:
                        import json

                        tool_result = (
                            json.dumps(result.result, ensure_ascii=False)
                            if isinstance(result.result, dict)
                            else str(result.result)
                        )
                    else:
                        tool_result = f"Error: {result.error}"

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": tool_result[:2000],
                        }
                    )

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
