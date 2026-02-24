"""
MCP Tool Adapter - MCP tools를 LLM function calling으로 변환

LLM (llama-server)은 OpenAI 호환 API를 사용하므로
MCP tools를 OpenAI function schema로 변환하여 전달.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

import httpx

from pyovis.mcp.mcp_client import MCPClient, MCPTool

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    tool_name: str
    success: bool
    result: Any
    error: str | None = None


class MCPToolAdapter:
    """
    MCP tools를 OpenAI function calling 형식으로 변환.
    
    사용 예:
    ```python
    adapter = MCPToolAdapter()
    adapter.register_mcp_client("filesystem", filesystem_client)
    
    # OpenAI tools 스키마 생성
    tools_schema = adapter.get_tools_schema()
    
    # LLM 응답에서 tool_calls 실행
    results = await adapter.execute_tool_calls(tool_calls)
    ```
    """
    
    def __init__(self):
        self.mcp_clients: dict[str, MCPClient] = {}
        self._tool_to_client: dict[str, str] = {}
        self._native_tools: dict[str, dict] = {}
        self._native_handlers: dict[str, Any] = {}

    def register_native_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: "Callable[..., Awaitable[Any]]",
    ) -> None:
        self._native_tools[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        self._native_handlers[name] = handler
    
    def register_mcp_client(self, server_name: str, client: MCPClient):
        """MCP 클라이언트 등록."""
        self.mcp_clients[server_name] = client
        for tool in client.tools:
            self._tool_to_client[tool.name] = server_name
    
    def get_tools_schema(self) -> list[dict]:
        tools = list(self._native_tools.values())
        for server_name, client in self.mcp_clients.items():
            for tool in client.tools:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    }
                })
        return tools
    
    async def execute_tool_call(
        self,
        tool_name: str,
        arguments: dict
    ) -> ToolCallResult:
        if tool_name in self._native_handlers:
            try:
                result = await self._native_handlers[tool_name](**arguments)
                return ToolCallResult(tool_name=tool_name, success=True, result=result)
            except Exception as e:
                return ToolCallResult(tool_name=tool_name, success=False, result=None, error=str(e))

        if tool_name not in self._tool_to_client:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                result=None,
                error=f"Tool '{tool_name}' not found"
            )

        server_name = self._tool_to_client[tool_name]
        client = self.mcp_clients.get(server_name)

        if not client or not client.is_connected:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                result=None,
                error=f"MCP server '{server_name}' not connected"
            )

        try:
            result = await client.call_tool(tool_name, arguments)
            return ToolCallResult(
                tool_name=tool_name,
                success=True,
                result=result
            )
        except Exception as e:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                result=None,
                error=str(e)
            )
    
    async def execute_tool_calls(self, tool_calls: list[dict]) -> list[ToolCallResult]:
        """여러 tool 호출 실행."""
        results = []
        for call in tool_calls:
            tool_name = call.get("function", {}).get("name", "")
            arguments_str = call.get("function", {}).get("arguments", "{}")
            
            try:
                arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
            except json.JSONDecodeError:
                arguments = {}
            
            result = await self.execute_tool_call(tool_name, arguments)
            results.append(result)
        
        return results


class ToolEnabledLLM:
    """
    Tool calling이 가능한 LLM 래퍼.
    
    LLM이 tool_calls를 반환하면 자동으로 실행하고
    결과를 다시 LLM에 전달하는 루프 처리.
    """
    
    def __init__(
        self,
        swap_manager,
        role: str,
        tool_adapter: MCPToolAdapter | None = None,
        max_tool_iterations: int = 5,
    ):
        self.swap = swap_manager
        self.role = role
        self.tool_adapter = tool_adapter
        self.max_tool_iterations = max_tool_iterations
        self._http_client = httpx.AsyncClient(timeout=600.0)
    
    async def call_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        await self.swap.ensure_model(self.role)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        
        tools_schema = []
        if self.tool_adapter:
            tools_schema = self.tool_adapter.get_tools_schema()
        tool_names = [t["function"]["name"] for t in tools_schema] if tools_schema else []
        logger.info("🔧 사용 가능한 도구: %s", tool_names or "(없음)")

        content = ""
        
        for iteration in range(self.max_tool_iterations):
            payload = {
                "model": "local",
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            
            if tools_schema:
                payload["tools"] = tools_schema
            
            logger.info("🧠 LLM 요청 전송 (iteration %d/%d, messages=%d)...",
                        iteration + 1, self.max_tool_iterations, len(messages))
            t0 = _time.monotonic()
            resp = await self._http_client.post(self.swap.api_url, json=payload)
            resp.raise_for_status()
            response = resp.json()
            elapsed = _time.monotonic() - t0
            
            message = response["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content") or ""
            
            usage = response.get("usage", {})
            logger.info("🧠 LLM 응답 수신 (%.1f초, prompt=%d, completion=%d tokens)",
                        elapsed,
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0))
            
            tool_calls = message.get("tool_calls", [])
            
            if not tool_calls:
                logger.info("🧠 도구 호출 없음 → 최종 응답 반환")
                return content
            
            logger.info("🔧 도구 호출 %d건 감지: %s",
                        len(tool_calls),
                        [tc.get("function", {}).get("name", "?") for tc in tool_calls])

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            
            if self.tool_adapter:
                results = await self.tool_adapter.execute_tool_calls(tool_calls)
                
                for i, result in enumerate(results):
                    tool_call_id = tool_calls[i].get("id", f"call_{i}")
                    tool_name = tool_calls[i].get("function", {}).get("name", "?")
                    
                    if result.success:
                        tool_result = json.dumps(result.result) if isinstance(result.result, dict) else str(result.result)
                        logger.info("  ✅ %s → %d자 결과", tool_name, len(tool_result))
                    else:
                        tool_result = f"Error: {result.error}"
                        logger.warning("  ❌ %s → %s", tool_name, result.error)
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_result,
                    })
        
        logger.warning("🧠 최대 iteration(%d) 도달, 마지막 응답 반환", self.max_tool_iterations)
        return content
    
    async def aclose(self) -> None:
        await self._http_client.aclose()


def format_tools_for_prompt(tools: list[MCPTool]) -> str:
    """Tools 목록을 프롬프트용 텍스트로 변환."""
    if not tools:
        return "사용 가능한 도구 없음"
    
    lines = ["## 사용 가능한 도구 (MCP Tools)"]
    lines.append("")
    
    for tool in tools:
        lines.append(f"### {tool.name}")
        lines.append(f"{tool.description}")
        
        if tool.input_schema.get("properties"):
            lines.append("**매개변수:**")
            for prop, schema in tool.input_schema.get("properties", {}).items():
                prop_type = schema.get("type", "any")
                prop_desc = schema.get("description", "")
                required = prop in tool.input_schema.get("required", [])
                req_str = " (필수)" if required else ""
                lines.append(f"- `{prop}` ({prop_type}){req_str}: {prop_desc}")
        
        lines.append("")
    
    return "\n".join(lines)
