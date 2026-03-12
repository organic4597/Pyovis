"""
MCP Registry Explorer - Discover and install MCP servers from official registry.

Official MCP Registry: https://registry.modelcontextprotocol.io/
GitHub: https://github.com/modelcontextprotocol/servers
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path

import httpx


class MCPServerCategory(Enum):
    FILESYSTEM = "filesystem"
    DATABASE = "database"
    WEB = "web"
    DEVELOPMENT = "development"
    COMMUNICATION = "communication"
    AI = "ai"
    CLOUD = "cloud"
    OTHER = "other"


@dataclass
class MCPServerInfo:
    name: str
    description: str
    version: str
    repository: str
    author: str
    categories: list[str]
    install_command: str
    stars: int = 0
    official: bool = False


@dataclass
class MCPRegistryResult:
    servers: list[MCPServerInfo]
    total: int
    error: Optional[str] = None


class MCPRegistryExplorer:
    """
    Explore and install MCP servers from the official registry.
    
    Registry endpoints:
    - https://registry.modelcontextprotocol.io/ - Official registry
    - https://github.com/modelcontextprotocol/servers - Reference implementations
    """
    
    REGISTRY_URL = "https://registry.modelcontextprotocol.io"
    GITHUB_API = "https://api.github.com"
    
    # Official MCP servers from modelcontextprotocol org
    OFFICIAL_SERVERS = [
        {"name": "filesystem", "repo": "modelcontextprotocol/servers", "path": "src/filesystem"},
        {"name": "git", "repo": "modelcontextprotocol/servers", "path": "src/git"},
        {"name": "github", "repo": "modelcontextprotocol/servers", "path": "src/github"},
        {"name": "fetch", "repo": "modelcontextprotocol/servers", "path": "src/fetch"},
        {"name": "brave-search", "repo": "modelcontextprotocol/servers", "path": "src/brave-search"},
        {"name": "slack", "repo": "modelcontextprotocol/servers", "path": "src/slack"},
        {"name": "google-maps", "repo": "modelcontextprotocol/servers", "path": "src/google-maps"},
        {"name": "memory", "repo": "modelcontextprotocol/servers", "path": "src/memory"},
        {"name": "sequential-thinking", "repo": "modelcontextprotocol/servers", "path": "src/sequentialthinking"},
        {"name": "puppeteer", "repo": "modelcontextprotocol/servers", "path": "src/puppeteer"},
    ]
    
    def __init__(self, install_dir: str = "/pyovis_memory/mcp_servers"):
        self.install_dir = Path(install_dir)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def search_servers(
        self, 
        query: str | None = None,
        category: MCPServerCategory | None = None,
        limit: int = 20
    ) -> MCPRegistryResult:
        """
        Search MCP servers from registry.
        
        Args:
            query: Search query (optional)
            category: Filter by category (optional)
            limit: Max results
        
        Returns:
            MCPRegistryResult with list of servers
        """
        servers = []
        
        # Try official registry first
        try:
            registry_servers = await self._fetch_registry_servers()
            servers.extend(registry_servers)
        except Exception as e:
            pass
        
        # Add known official servers
        for server_info in self.OFFICIAL_SERVERS:
            try:
                server = await self._fetch_github_server_info(server_info)
                if server and server not in [s.name for s in servers]:
                    servers.append(server)
            except Exception:
                # Add with basic info if fetch fails
                servers.append(MCPServerInfo(
                    name=server_info["name"],
                    description=f"Official MCP server: {server_info['name']}",
                    version="latest",
                    repository=f"https://github.com/{server_info['repo']}",
                    author="modelcontextprotocol",
                    categories=[server_info["name"]],
                    install_command=f"npx @modelcontextprotocol/server-{server_info['name']}",
                    official=True,
                ))
        
        # Filter by query
        if query:
            query_lower = query.lower()
            servers = [
                s for s in servers 
                if query_lower in s.name.lower() or query_lower in s.description.lower()
            ]
        
        # Filter by category
        if category:
            servers = [s for s in servers if category.value in s.categories]
        
        # Sort: official first, then by stars
        servers.sort(key=lambda s: (not s.official, -s.stars))
        
        return MCPRegistryResult(
            servers=servers[:limit],
            total=len(servers),
        )
    
    async def _fetch_registry_servers(self) -> list[MCPServerInfo]:
        """Fetch servers from official MCP registry."""
        servers = []
        
        try:
            resp = await self.client.get(f"{self.REGISTRY_URL}/servers")
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("servers", [])[:50]:
                    servers.append(MCPServerInfo(
                        name=item.get("name", "unknown"),
                        description=item.get("description", ""),
                        version=item.get("version", "latest"),
                        repository=item.get("repository", {}).get("url", ""),
                        author=item.get("publisher", {}).get("name", "unknown"),
                        categories=item.get("categories", []),
                        install_command=item.get("install", {}).get("command", ""),
                    ))
        except Exception:
            pass
        
        return servers
    
    async def _fetch_github_server_info(self, server_info: dict) -> MCPServerInfo | None:
        """Fetch server info from GitHub API."""
        try:
            repo = server_info["repo"]
            resp = await self.client.get(f"{self.GITHUB_API}/repos/{repo}")
            if resp.status_code == 200:
                data = resp.json()
                return MCPServerInfo(
                    name=server_info["name"],
                    description=data.get("description", ""),
                    version="latest",
                    repository=data.get("html_url", ""),
                    author=data.get("owner", {}).get("login", ""),
                    categories=[server_info["name"]],
                    install_command=f"npx @modelcontextprotocol/server-{server_info['name']}",
                    stars=data.get("stargazers_count", 0),
                    official=True,
                )
        except Exception:
            pass
        return None
    
    async def install_server(
        self, 
        server_name: str, 
        method: str = "npm",
        requires_approval: bool = True
    ) -> dict:
        """
        Install an MCP server.
        
        Args:
            server_name: Name of the server to install
            method: Installation method (npm, pip, git)
            requires_approval: Whether to require user approval
        
        Returns:
            Installation result
        """
        if requires_approval:
            return {
                "status": "approval_required",
                "server": server_name,
                "message": f"Approval required to install MCP server: {server_name}",
            }
        
        server_dir = self.install_dir / server_name
        
        if method == "npm":
            cmd = ["npm", "install", "-g", f"@modelcontextprotocol/server-{server_name}"]
        elif method == "pip":
            cmd = ["pip", "install", f"mcp-server-{server_name}"]
        elif method == "git":
            cmd = ["git", "clone", 
                   f"https://github.com/modelcontextprotocol/servers.git",
                   str(server_dir)]
        else:
            return {"status": "error", "message": f"Unknown install method: {method}"}
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                return {
                    "status": "installed",
                    "server": server_name,
                    "path": str(server_dir),
                    "output": result.stdout,
                }
            else:
                return {
                    "status": "error",
                    "server": server_name,
                    "message": result.stderr,
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "server": server_name, "message": "Installation timeout"}
        except Exception as e:
            return {"status": "error", "server": server_name, "message": str(e)}
    
    async def get_server_config(self, server_name: str) -> dict:
        """
        Get configuration template for an MCP server.
        
        Returns a config dict that can be added to MCP client config.
        """
        return {
            "mcpServers": {
                server_name: {
                    "command": "npx",
                    "args": [f"@modelcontextprotocol/server-{server_name}"],
                    "env": {},
                }
            }
        }
    
    async def close(self):
        await self.client.aclose()


class MCPToolDiscovery:
    """
    Discover and manage MCP tools/skills from various sources.
    """
    
    SKILL_SOURCES = {
        "langchain": {
            "url": "https://github.com/langchain-ai/langchain",
            "type": "framework",
            "description": "LangChain tools and agents",
        },
        "autogpt": {
            "url": "https://github.com/Significant-Gravitas/AutoGPT",
            "type": "framework",
            "description": "AutoGPT plugins and skills",
        },
        "crewai": {
            "url": "https://github.com/joaomdmoura/crewAI",
            "type": "framework", 
            "description": "CrewAI agents and tools",
        },
        "semantic_kernel": {
            "url": "https://github.com/microsoft/semantic-kernel",
            "type": "framework",
            "description": "Microsoft Semantic Kernel skills",
        },
    }
    
    def __init__(self, cache_dir: str = "/pyovis_memory/skill_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def discover_skills(self, query: str | None = None) -> list[dict]:
        """
        Discover available skills from registered sources.
        
        Returns list of skill definitions that can be installed.
        """
        skills = []
        
        # Default skills bundled with Pyovis
        default_skills = [
            {
                "name": "code_generator",
                "description": "Generate code from specifications",
                "source": "builtin",
                "install": None,
            },
            {
                "name": "code_reviewer",
                "description": "Review code for issues and improvements",
                "source": "builtin",
                "install": None,
            },
            {
                "name": "test_writer",
                "description": "Generate unit tests for code",
                "source": "builtin",
                "install": None,
            },
            {
                "name": "doc_writer",
                "description": "Generate documentation for code",
                "source": "builtin",
                "install": None,
            },
            {
                "name": "file_manager",
                "description": "File system operations",
                "source": "builtin",
                "install": None,
            },
        ]
        
        skills.extend(default_skills)
        
        # Filter by query
        if query:
            query_lower = query.lower()
            skills = [
                s for s in skills
                if query_lower in s["name"].lower() or query_lower in s["description"].lower()
            ]
        
        return skills
    
    async def install_skill(self, skill_name: str, source: str = "builtin") -> dict:
        """Install a skill from a source."""
        if source == "builtin":
            return {
                "status": "ready",
                "skill": skill_name,
                "message": f"Built-in skill '{skill_name}' is already available",
            }
        
        return {
            "status": "approval_required",
            "skill": skill_name,
            "source": source,
            "message": f"Approval required to install skill from {source}",
        }
    
    async def close(self):
        await self.client.aclose()
