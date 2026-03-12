"""
MCP Protocol Client - 실제 MCP 서버와 통신하는 클라이언트

MCP 서버는 stdio 또는 SSE로 통신합니다.
- stdio: 프로세스 실행 후 stdin/stdout으로 JSON-RPC
- SSE: HTTP Server-Sent Events로 JSON-RPC

프로토콜: JSON-RPC 2.0
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Callable
from pathlib import Path


class MCPTransport(Enum):
    STDIO = "stdio"
    SSE = "sse"


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict


@dataclass
class MCPResource:
    uri: str
    name: str
    description: str
    mime_type: str | None = None


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    transport: MCPTransport = MCPTransport.STDIO
    cwd: str | None = None


class MCPClient:
    """
    MCP 서버와 통신하는 클라이언트.
    
    사용 예:
    ```python
    client = MCPClient(MCPServerConfig(
        name="filesystem",
        command="npx",
        args=["@modelcontextprotocol/server-filesystem", "/path"],
    ))
    await client.connect()
    tools = await client.list_tools()
    result = await client.call_tool("read_file", {"path": "test.txt"})
    await client.close()
    ```
    """
    
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self.request_id = 0
        self.tools: list[MCPTool] = []
        self.resources: list[MCPResource] = []
        self._connected = False
    
    async def connect(self) -> bool:
        """MCP 서버에 연결."""
        if self.config.transport == MCPTransport.STDIO:
            return await self._connect_stdio()
        else:
            raise NotImplementedError(f"Transport {self.config.transport} not implemented")
    
    async def _connect_stdio(self) -> bool:
        """stdio 방식으로 연결."""
        try:
            self.process = subprocess.Popen(
                [self.config.command] + self.config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **self.config.env},
                cwd=self.config.cwd,
            )
            
            # Initialize handshake
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "pyovis",
                    "version": "4.0.0"
                }
            })
            
            if init_result and "result" in init_result:
                # Send initialized notification
                await self._send_notification("notifications/initialized", {})
                self._connected = True
                
                # Cache tools and resources
                await self._load_capabilities()
                return True
            
            return False
        except Exception as e:
            print(f"Failed to connect to MCP server: {e}")
            return False
    
    async def _load_capabilities(self):
        """서버의 tools와 resources 로드."""
        try:
            tools_result = await self._send_request("tools/list", {})
            if tools_result and "result" in tools_result:
                for tool in tools_result["result"].get("tools", []):
                    self.tools.append(MCPTool(
                        name=tool.get("name", ""),
                        description=tool.get("description", ""),
                        input_schema=tool.get("inputSchema", {}),
                    ))
        except Exception:
            pass
        
        try:
            resources_result = await self._send_request("resources/list", {})
            if resources_result and "result" in resources_result:
                for resource in resources_result["result"].get("resources", []):
                    self.resources.append(MCPResource(
                        uri=resource.get("uri", ""),
                        name=resource.get("name", ""),
                        description=resource.get("description", ""),
                        mime_type=resource.get("mimeType"),
                    ))
        except Exception:
            pass
    
    async def list_tools(self) -> list[MCPTool]:
        """사용 가능한 tools 목록."""
        return self.tools
    
    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Tool 호출."""
        if not self._connected:
            raise RuntimeError("Not connected to MCP server")
        
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments
        })
        
        return result.get("result", {})
    
    async def read_resource(self, uri: str) -> dict:
        """Resource 읽기."""
        if not self._connected:
            raise RuntimeError("Not connected to MCP server")
        
        result = await self._send_request("resources/read", {"uri": uri})
        return result.get("result", {})
    
    async def _send_request(self, method: str, params: dict) -> dict:
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise RuntimeError("Process not running")
        
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params
        }
        
        request_str = json.dumps(request) + "\n"
        self.process.stdin.write(request_str.encode())
        self.process.stdin.flush()
        
        loop = asyncio.get_running_loop()
        stdout = self.process.stdout
        try:
            response_line = await asyncio.wait_for(
                loop.run_in_executor(None, stdout.readline),
                timeout=30.0
            )
            if response_line:
                return json.loads(response_line.decode())
        except asyncio.TimeoutError:
            pass
        
        return {}
    
    async def _send_notification(self, method: str, params: dict):
        """JSON-RPC notification 전송 (응답 없음)."""
        if not self.process or not self.process.stdin:
            return
        
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        
        notification_str = json.dumps(notification) + "\n"
        self.process.stdin.write(notification_str.encode())
        self.process.stdin.flush()
    
    async def close(self):
        if self.process:
            self.process.terminate()
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, self.process.wait),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                self.process.kill()
            self.process = None
        self._connected = False
    
    @property
    def is_connected(self) -> bool:
        return self._connected


class MCPManager:
    """
    여러 MCP 서버를 관리하는 매니저.
    """
    
    def __init__(self):
        self.clients: dict[str, MCPClient] = {}
    
    async def add_server(self, config: MCPServerConfig) -> bool:
        """서버 추가 및 연결."""
        if config.name in self.clients:
            return True
        
        client = MCPClient(config)
        if await client.connect():
            self.clients[config.name] = client
            return True
        return False
    
    async def remove_server(self, name: str):
        """서버 제거."""
        if name in self.clients:
            await self.clients[name].close()
            del self.clients[name]
    
    def get_all_tools(self) -> dict[str, list[MCPTool]]:
        """모든 서버의 tools 반환."""
        return {
            name: client.tools
            for name, client in self.clients.items()
            if client.is_connected
        }
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        """특정 서버의 tool 호출."""
        if server_name not in self.clients:
            raise ValueError(f"Server {server_name} not found")
        
        return await self.clients[server_name].call_tool(tool_name, arguments)
    
    async def close_all(self):
        """모든 서버 종료."""
        for client in self.clients.values():
            await client.close()
        self.clients.clear()


# 미리 정의된 서버 설정들
BUILTIN_SERVER_CONFIGS = {
    "filesystem": MCPServerConfig(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
    ),
    "git": MCPServerConfig(
        name="git",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-git"],
    ),
    "fetch": MCPServerConfig(
        name="fetch",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-fetch"],
    ),
    "memory": MCPServerConfig(
        name="memory",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
    ),
    "sequential-thinking": MCPServerConfig(
        name="sequential-thinking",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
    ),
}
