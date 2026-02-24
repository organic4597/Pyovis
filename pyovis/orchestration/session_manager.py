from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

from pyovis.ai import Brain, Hands, Judge, ModelSwapManager, Planner
from pyovis.execution.critic_runner import CriticRunner
from pyovis.execution.file_writer import FileWriter, WorkspaceManager
from pyovis.memory.graph_builder import KnowledgeGraphBuilder
from pyovis.orchestration.loop_controller import LoopContext, ResearchLoopController
from pyovis.orchestration.request_analyzer import RequestAnalyzer, TaskComplexity, ToolStatus
from pyovis.skill.skill_manager import SkillManager
from pyovis.tracking.loop_tracker import LoopTracker
from pyovis.mcp.mcp_registry import MCPRegistryExplorer, MCPToolDiscovery
from pyovis.mcp.mcp_client import MCPClient, MCPServerConfig, MCPManager
from pyovis.mcp.tool_adapter import MCPToolAdapter
from pyovis.memory.conversation import ConversationMemory


logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        task_queue,
        model_swap,
        tracker: LoopTracker,
        result_callback: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.task_queue = task_queue
        self.model_swap = model_swap
        self.tracker = tracker
        self.result_callback = result_callback
        self.bot: Any = None
        self.skill_manager = SkillManager()
        self.critic = CriticRunner()
        
        # MCP integration
        self.mcp_manager = MCPManager()
        self.tool_adapter = MCPToolAdapter()
        self.kg_builder = KnowledgeGraphBuilder()
        
        # AI roles with tool adapter
        self.brain = Brain(self.model_swap)
        self.hands = Hands(self.model_swap, tool_adapter=self.tool_adapter)
        self.judge = Judge(self.model_swap)
        self.planner = Planner(self.model_swap)
        
        # Request analysis
        self.request_analyzer = RequestAnalyzer(self.model_swap)
        
        # MCP and Skill discovery
        self.mcp_explorer = MCPRegistryExplorer()
        self.skill_discovery = MCPToolDiscovery()
        self._mcp_initialized = False

        # Conversation memory (per-chat history)
        self.conversation_memory = ConversationMemory()

    _TOOL_FALLBACK: dict[str, list[str]] = {
        "weather_api": ["brave-search"],
        "date_finder": ["brave-search"],
        "news_api": ["brave-search"],
        "stock_api": ["brave-search"],
        "exchange_rate": ["brave-search"],
        "web_search": ["brave-search"],
        "browser": ["puppeteer", "fetch"],
        "file_system": ["filesystem"],
        "code_runner": ["filesystem"],
    }

    _KNOWN_MCP_TOOLS: list[str] = [
        "brave-search",
        "fetch",
        "filesystem",
        "git",
        "github",
        "slack",
        "google-maps",
        "puppeteer",
        "memory",
        "sequential-thinking",
    ]

    async def get_mcp_tools(self) -> list[str]:
        """Get list of available MCP tools from registry (known official tools)."""
        try:
            result = await self.mcp_explorer.search_servers(limit=50)
            registry_tools = [s.name for s in result.servers]
            if registry_tools:
                return registry_tools
        except Exception:
            logger.warning("session_manager: MCP registry search failed, using known tools list")
        return list(self._KNOWN_MCP_TOOLS)

    def suggest_alternative_tools(self, failed_tools: list[str]) -> dict[str, list[str]]:
        """Suggest alternative MCP tools for tools that failed to install."""
        suggestions: dict[str, list[str]] = {}
        for tool in failed_tools:
            tool_lower = tool.lower().replace("-", "_")
            if tool_lower in self._TOOL_FALLBACK:
                suggestions[tool] = self._TOOL_FALLBACK[tool_lower]
            else:
                # fallback: brave-search covers most realtime data needs
                suggestions[tool] = ["brave-search"]
        return suggestions

    async def _ensure_mcp_tools(self) -> None:
        if self._mcp_initialized:
            return

        workspace_path: str | None = None
        try:
            workspace_path = str(WorkspaceManager("mcp_default").project_root)
            config = MCPServerConfig(
                name="filesystem",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", workspace_path],
            )
            client = MCPClient(config)
            if await client.connect():
                self.mcp_manager.clients["filesystem"] = client
                self.tool_adapter.register_mcp_client("filesystem", client)
        except Exception:
            logger.warning(
                "session_manager: failed to connect filesystem MCP server (path=%s)",
                workspace_path,
                exc_info=True,
            )

        try:
            fetch_config = MCPServerConfig(
                name="fetch",
                command="mcp-server-fetch",
                args=["--ignore-robots-txt"],
            )
            fetch_client = MCPClient(fetch_config)
            if await fetch_client.connect():
                self.mcp_manager.clients["fetch"] = fetch_client
                self.tool_adapter.register_mcp_client("fetch", fetch_client)
                logger.info("session_manager: fetch MCP server connected")
        except Exception:
            logger.warning("session_manager: failed to connect fetch MCP server", exc_info=True)
            self._register_native_tools()

        self._mcp_initialized = True

    def _register_native_tools(self) -> None:
        import httpx as _httpx

        async def fetch(url: str, max_length: int = 8000) -> str:
            async with _httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "pyovis/4.0"})
                resp.raise_for_status()
                return resp.text[:max_length]

        self.tool_adapter.register_native_tool(
            name="fetch",
            description=(
                "HTTP GET으로 URL 내용을 가져온다. "
                "날씨(open-meteo.com), 환율, 뉴스 등 공개 API 호출에 사용. "
                "예: https://wttr.in/Seoul?format=3 또는 "
                "https://api.open-meteo.com/v1/forecast?latitude=37.5&longitude=127.0&current_weather=true"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "가져올 URL"},
                    "max_length": {"type": "integer", "description": "반환할 최대 문자 수 (기본 8000)"},
                },
                "required": ["url"],
            },
            handler=fetch,
        )

    def get_available_tools(self) -> list[str]:
        """Get list of currently available tools from connected MCP servers and skills."""
        tools = []
        
        # MCP tools from connected servers
        for server_name, client in self.mcp_manager.clients.items():
            for tool in client.tools:
                tools.append(f"{server_name}:{tool.name}")
        
        # Skills from verified directory
        from pyovis.skill.skill_manager import VERIFIED_DIR
        if VERIFIED_DIR.exists():
            for skill_file in VERIFIED_DIR.glob("*.md"):
                skill_name = skill_file.stem
                tools.append(f"skill:{skill_name}")
        
        return tools

    def get_tools_for_task(self, task_keywords: list[str]) -> dict:
        """
        Find relevant tools for a task from already available tools.
        Returns matched tools and whether external search is needed.
        """
        matched_mcp = []
        matched_skills = []
        
        task_lower = " ".join(task_keywords).lower()
        
        # Check MCP tools from connected clients
        for server_name, client in self.mcp_manager.clients.items():
            for tool in client.tools:
                tool_desc_lower = tool.description.lower()
                if any(kw in tool_desc_lower for kw in task_keywords):
                    matched_mcp.append({
                        "server": server_name,
                        "tool": tool.name,
                        "description": tool.description[:100],
                    })
        
        # Check skills from verified directory
        from pyovis.skill.skill_manager import VERIFIED_DIR
        if VERIFIED_DIR.exists():
            for skill_file in VERIFIED_DIR.glob("*.md"):
                content = skill_file.read_text(encoding="utf-8").lower()
                if any(kw in content for kw in task_keywords):
                    matched_skills.append({
                        "name": skill_file.stem,
                        "path": str(skill_file),
                    })
        
        needs_external = len(matched_mcp) == 0 and len(matched_skills) == 0
        
        return {
            "mcp_tools": matched_mcp,
            "skills": matched_skills,
            "needs_external_search": needs_external,
        }

    async def run(self) -> None:
        while True:
            task = self.task_queue.dequeue()
            if task is None:
                await asyncio.sleep(0.05)
                continue

            priority, task_type, task_data = task
            if task_type == "stop":
                break

            # Extract payload from task_data
            if isinstance(task_data, str):
                try:
                    task_data = json.loads(task_data)
                except json.JSONDecodeError:
                    task_data = {"text": task_data}
            
            if isinstance(task_data, dict):
                task_id = task_data.get("task_id", str(uuid.uuid4()))
                payload = task_data.get("text", str(task_data))
                chat_id = task_data.get("chat_id")
            else:
                task_id = str(uuid.uuid4())
                payload = str(task_data)
                chat_id = None
            
            await self._ensure_mcp_tools()

            result = await self._handle_task(task_id, payload, chat_id)
            
            # Call result callback if provided
            if self.result_callback and task_id:
                self.result_callback(task_id, result)
            
            if self.bot and task_id:
                self.bot.submit_result(task_id, result)

        await self.model_swap.shutdown()
        await self.mcp_manager.close_all()
        await self.mcp_explorer.close()
        await self.skill_discovery.close()

    async def _handle_task(self, task_id: str, payload: str, chat_id: str | int | None = None) -> dict:
        from pyovis.mcp.tool_adapter import ToolEnabledLLM
        from pyovis.ai.response_utils import parse_json_message
        from datetime import datetime, timezone, timedelta
        import time as _time

        logger.info("━━━ 새 요청 [%s] ━━━", task_id[:8])
        logger.info("📩 사용자: %s", payload[:120])
        t0 = _time.monotonic()

        KST = timezone(timedelta(hours=9))
        now_str = datetime.now(KST).strftime("%Y년 %m월 %d일 %A %H:%M KST")

        logger.info("🔍 Graph RAG 컨텍스트 조회 중...")
        graph_context = await self._enrich_with_graph_rag(payload)
        enriched_payload = payload
        if graph_context:
            enriched_payload = f"{payload}\n\n--- Knowledge Graph Context ---\n{graph_context}"
            logger.info("🔍 Graph RAG: %d자 컨텍스트 추가됨", len(graph_context))
        else:
            logger.info("🔍 Graph RAG: 컨텍스트 없음 (빈 그래프)")

        # 대화 히스토리 로드 (연관성 기반 필터링)
        conv_history = ""
        if chat_id:
            conv_history = self.conversation_memory.filter_relevant(chat_id, payload, last_n=10)
            if conv_history:
                logger.info("💬 대화 히스토리: %d자 로드됨 (연관성 필터 적용, chat_id=%s)", len(conv_history), chat_id)

        llm = ToolEnabledLLM(
            swap_manager=self.model_swap,
            role="brain",
            tool_adapter=self.tool_adapter,
            max_tool_iterations=5,
        )
        user_message = f"""현재 날짜/시간: {now_str}

{f"--- 이전 대화 기록 ---{chr(10)}{conv_history}{chr(10)}{chr(10)}" if conv_history else ""}사용자 요청: {enriched_payload}

요청을 분석하고 직접 처리하라. 실시간 정보(날씨, 환율 등)는 fetch 도구를 사용하라.
이전 대화 기록이 있다면, 사용자가 이전 대화를 참조하는 질문("아까", "어제", "전에 물어본", "그때" 등)을 할 때 해당 기록을 활용하여 답변하라.

다음 JSON 형식으로 응답하라:
{{
  "complexity": "simple 또는 complex",
  "status": "success",
  "result": "코드 작성 요청이면 실행 가능한 전체 코드만 (설명 없이), 일반 질문이면 답변"
  "file_path": "저장할 파일명 (예: game.py). 코드가 있으면 반드시 지정"
  "message": "사용자에게 전달할 메시지"
}}

 코드 작성: result에 실행 가능한 완전한 코드만 넣고 file_path 지정. 설명은 message에
- 날씨/실시간 정보: fetch 도구로 공개 API 호출
- 다중 파일 생성·아키텍처 설계 등 복잡한 작업: complexity를 "complex"로 설정
"""
        logger.info("🧠 LLM 호출 시작 (role=brain)...")
        t_llm = _time.monotonic()
        try:
            response = await llm.call_with_tools(self.request_analyzer.system_prompt, user_message)
        finally:
            await llm.aclose()
        elapsed_llm = _time.monotonic() - t_llm
        logger.info("🧠 LLM 응답 완료 (%.1f초, %d자)", elapsed_llm, len(response))

        result = parse_json_message(
            {"content": response},
            default={"complexity": "simple", "status": "success", "message": response},
        )
        logger.info("📋 판정: complexity=%s, status=%s", result.get("complexity"), result.get("status"))

        if result.get("complexity") == "complex":
            logger.info("🔄 복잡한 작업 → 전체 루프로 전환")
            if self.bot and chat_id:
                try:
                    asyncio.create_task(
                        self.bot.send_progress(int(chat_id), "⏳ 복잡한 작업을 시작합니다. 완료까지 좌시 기다려주세요..."))
                except Exception:
                    pass
            complex_result = await self._handle_complex_task(task_id, payload, chat_id=chat_id)
            complex_result["tool_status"] = "not_needed"
            complex_result["tools_used"] = []
            reasoning_text = "\n".join(complex_result.get("reasoning_log", []))
            asyncio.create_task(self._ingest_to_graph(payload, task_id, reasoning_text))
            elapsed_total = _time.monotonic() - t0
            logger.info("✅ 완료 [%s] (총 %.1f초, 경로=complex)", task_id[:8], elapsed_total)
            return complex_result

        if result.get("file_path") and result.get("result"):
            from pyovis.execution.file_writer import FileWriter, WorkspaceManager
            workspace = WorkspaceManager(task_id)
            FileWriter(workspace).save_code(result["file_path"], result["result"])
            result["workspace"] = str(workspace.project_root)
            logger.info("💾 파일 저장: %s", result["file_path"])
        elif result.get("result") and not result.get("file_path"):
            from pyovis.execution.file_writer import FileWriter, WorkspaceManager
            workspace = WorkspaceManager(task_id)
            default_path = "output.py"
            FileWriter(workspace).save_code(default_path, result["result"])
            result["file_path"] = default_path
            result["workspace"] = str(workspace.project_root)
            logger.info("💾 파일 저장 (기본경로): %s", default_path)
            if "message" in result:
                result["message"] = f"{result['message']}\n\n💾 파일 저장됨: {default_path}"
            else:
                result["message"] = f"코드가 {default_path}에 저장되었습니다."


        result["task_id"] = task_id
        result["path"] = "simple"
        result["tool_status"] = "not_needed"
        result["tools_used"] = []
        asyncio.create_task(self._ingest_to_graph(payload, task_id))
        elapsed_total = _time.monotonic() - t0
        logger.info("✅ 완료 [%s] (총 %.1f초, 경로=simple)", task_id[:8], elapsed_total)

        # 대화 히스토리에 현재 교환 저장
        response_msg = result.get("message", "")
        if chat_id and response_msg:
            self.conversation_memory.add_exchange(chat_id, payload, response_msg)
            logger.info("💬 대화 히스토리 저장 완료 (chat_id=%s)", chat_id)
        return result

    async def _handle_request(self, task_id: str, payload: str, analysis) -> dict:
        """Handle request based on Brain's analysis result."""
        
        if analysis.needs_clarification:
            return {
                "status": "clarification_needed",
                "task_id": task_id,
                "questions": analysis.clarification_questions,
                "message": "Additional information required",
            }
        
        if analysis.tool_status == ToolStatus.NEEDED_PENDING:
            tool_result = await self._handle_tool_requirements(analysis.required_tools)
            if tool_result.get("status") == "approval_required":
                return tool_result
        
        elif analysis.tool_status == ToolStatus.ALREADY_AVAILABLE:
            pass
        
        graph_context = await self._enrich_with_graph_rag(payload)
        enriched_payload = payload
        if graph_context:
            enriched_payload = (
                f"{payload}\n\n--- Knowledge Graph Context ---\n{graph_context}"
            )
        
        if analysis.complexity == TaskComplexity.CHAT:
            # Chat messages - just respond, no file generation
            result = await self._handle_chat(task_id, payload)
        elif analysis.complexity == TaskComplexity.SIMPLE:
            result = await self._handle_simple_task(task_id, enriched_payload)
        else:
            result = await self._handle_complex_task(task_id, enriched_payload)
        enriched_payload = payload
        if graph_context:
            enriched_payload = (
                f"{payload}\n\n--- Knowledge Graph Context ---\n{graph_context}"
            )
        
        if analysis.complexity == TaskComplexity.SIMPLE:
            result = await self._handle_simple_task(task_id, enriched_payload)
        else:
            result = await self._handle_complex_task(task_id, enriched_payload)
        
        result["tool_status"] = analysis.tool_status.value
        result["tools_used"] = analysis.available_tools_to_use
        
        await self._ingest_to_graph(payload, task_id)
        
        return result

    async def _enrich_with_graph_rag(self, payload: str) -> str:
        try:
            if self.kg_builder.get_stats()["total_nodes"] == 0:
                return ""
            rag_result = await self.kg_builder.query_graph_rag(
                payload, depth=2, use_llm_extraction=False,
            )
            return rag_result.get("context_text", "")
        except Exception:
            logger.debug("session_manager: graph RAG enrichment skipped", exc_info=True)
            return ""

    async def _ingest_to_graph(self, payload: str, task_id: str, reasoning: str = "") -> None:
        try:
            logger.info("📝 KG 백그라운드 저장 시작 [%s]", task_id[:8])
            await self.kg_builder.add_text(payload, source=f"task:{task_id}")
            if reasoning:
                from pyovis.ai.response_utils import summarize_thinking
                summarized = summarize_thinking(reasoning, max_chars=500)
                await self.kg_builder.add_text(summarized, source=f"reasoning:{task_id}")
            logger.info("📝 KG 백그라운드 저장 완료 [%s]", task_id[:8])
        except Exception:
            logger.debug("session_manager: graph ingestion skipped", exc_info=True)


    async def _handle_simple_task(self, task_id: str, payload: str) -> dict:
        from pyovis.mcp.tool_adapter import ToolEnabledLLM
        from pyovis.ai.response_utils import parse_json_message
        from datetime import datetime, timezone, timedelta

        KST = timezone(timedelta(hours=9))
        now_str = datetime.now(KST).strftime("%Y년 %m월 %d일 %A %H:%M KST")

        llm = ToolEnabledLLM(
            swap_manager=self.model_swap,
            role="brain",
            tool_adapter=self.tool_adapter,
            max_tool_iterations=5,
        )
        user_message = f"""현재 날짜/시간: {now_str}

사용자 요청: {payload}

이 요청을 직접 처리하라. 실시간 정보(날씨, 환율 등)가 필요하면 fetch 도구를 사용하라.

다음 JSON 형식으로 응답하라:
{{
  "status": "success",
  "result": "작업 결과 (코드, 답변 등)",
  "file_path": "저장할 파일 경로 (필요한 경우, 없으면 null)",
  "message": "사용자에게 전달할 메시지"
}}

작업 유형별 처리:
- 날짜/시간/요일 질문: 위 "현재 날짜/시간"을 기준으로 직접 답변 (fetch 불필요)
- 코드 작성: 완전하고 실행 가능한 코드를 작성
- 질문 답변: 명확하고 간결한 답변 제공
- 날씨/실시간 정보: fetch 도구로 open-meteo.com 등 공개 API 직접 호출
"""
        try:
            response = await llm.call_with_tools(self.request_analyzer.system_prompt, user_message)
        finally:
            await llm.aclose()

        result = parse_json_message(
            {"content": response},
            default={"status": "success", "message": response},
        )

        # Save to file if file_path specified
        if result.get("file_path") and result.get("result"):
            workspace = WorkspaceManager(task_id)
            file_writer = FileWriter(workspace)
            file_writer.save_code(result["file_path"], result["result"])
            result["workspace"] = str(workspace.project_root)

        result["task_id"] = task_id
        result["path"] = "simple"
        return result

    async def _handle_chat(self, task_id: str, payload: str) -> dict:
        """Handle chat/conversation - never generates files, just responds."""
        from pyovis.mcp.tool_adapter import ToolEnabledLLM
        from pyovis.ai.response_utils import parse_json_message
        from datetime import datetime, timezone, timedelta

        KST = timezone(timedelta(hours=9))
        now_str = datetime.now(KST).strftime("%Y 년 %m 월 %d 일 %A %H:%M KST")

        llm = ToolEnabledLLM(
            swap_manager=self.model_swap,
            role="brain",
            tool_adapter=self.tool_adapter,
            max_tool_iterations=5,
        )
        user_message = f"""현재 날짜/시간: {now_str}

사용자 메시지: {payload}

이것은 일반적인 대화 또는 인사말이다. **파일을 생성하지 마라**.

다음 JSON 형식으로 응답하라:
{{
    "status": "success",
    "result": "친근한 답변",
    "message": "사용자에게 전달할 메시지"
}}

**중요**: file_path 를 포함하지 마라. 이 요청은 파일 생성이 필요 없다.
"""
        try:
            response = await llm.call_with_tools(self.request_analyzer.system_prompt, user_message)
        finally:
            await llm.aclose()

        result = parse_json_message(
            {"content": response},
            default={"status": "success", "message": response},
        )

        # Ensure no file_path is set for chat
        result.pop("file_path", None)

        result["task_id"] = task_id
        result["path"] = "chat"
        return result
        result["path"] = "simple"
        return result

    async def _handle_complex_task(self, task_id: str, payload: str, *, chat_id: str | int | None = None) -> dict:
        """Handle complex task via full loop."""
        workspace = WorkspaceManager(task_id)
        file_writer = FileWriter(workspace)

        async def _progress(msg: str) -> None:
            if self.bot and chat_id:
                try:
                    await self.bot.send_progress(int(chat_id), msg)
                except Exception:
                    pass
        
        controller = ResearchLoopController(
            self.brain,
            self.hands,
            self.judge,
            self.critic,
            self.tracker,
            self.skill_manager,
            planner=self.planner,
            file_writer=file_writer,
            workspace=workspace,
        )
        
        ctx = LoopContext(
            task_id=task_id,
            task_description=payload,
            workspace=workspace,
            project_id=workspace.project_id,
            progress_callback=_progress,
        )
        
        result = await controller.run(ctx)
        result["path"] = "complex"
        return result

    async def _handle_tool_requirements(self, required_tools: list[str]) -> dict:
        """Handle required tools - discover and install."""
        installed = []
        pending = []
        
        for tool in required_tools:
            # Try to find in MCP registry
            search_result = await self.mcp_explorer.search_servers(query=tool, limit=1)
            
            if search_result.servers:
                server = search_result.servers[0]
                install_result = await self.mcp_explorer.install_server(
                    server.name,
                    requires_approval=True
                )
                
                if install_result.get("status") == "approval_required":
                    pending.append({
                        "tool": tool,
                        "server": server.name,
                        "install_command": server.install_command,
                    })
                else:
                    installed.append({
                        "tool": tool,
                        "server": server.name,
                        "status": "installed",
                    })
            else:
                alternatives = self.suggest_alternative_tools([tool]).get(tool, [])
                pending.append({
                    "tool": tool,
                    "server": None,
                    "message": f"Tool '{tool}' not found in MCP registry",
                    "alternatives": alternatives,
                })
        
        if pending:
            alt_hints = [
                f"{p['tool']} → {p['alternatives']}"
                for p in pending
                if p.get("alternatives")
            ]
            message = "Approval required to install tools"
            if alt_hints:
                message += f"\n대체 도구 제안: {', '.join(alt_hints)}"
            return {
                "status": "approval_required",
                "required_tools": required_tools,
                "pending_installations": pending,
                "installed": installed,
                "message": message,
            }
        
        return {
            "status": "tools_ready",
            "installed": installed,
        }
    
    async def install_tools(self, tools: list[str], original_request: str = "") -> dict:
        """Install tools after approval."""
        installed = []
        failed = []
        
        for tool in tools:
            try:
                search_result = await self.mcp_explorer.search_servers(query=tool, limit=1)
                
                if search_result.servers:
                    server = search_result.servers[0]
                    install_result = await self.mcp_explorer.install_server(
                        server.name,
                        requires_approval=False
                    )
                    
                    if install_result.get("status") == "installed":
                        installed.append({"tool": tool, "server": server.name})
                    else:
                        failed.append({"tool": tool, "reason": install_result.get("message", "Unknown error")})
                else:
                    failed.append({"tool": tool, "reason": "Not found in registry"})
            except Exception as e:
                failed.append({"tool": tool, "reason": str(e)})
        
        if installed and not failed:
            return {
                "status": "success",
                "message": f"도구 설치 완료: {', '.join(t['tool'] for t in installed)}\n원래 요청을 다시 보내주세요.",
                "installed": installed,
            }
        elif installed:
            return {
                "status": "partial",
                "message": f"일부 설치 완료: {', '.join(t['tool'] for t in installed)}\n실패: {', '.join(t['tool'] for t in failed)}",
                "installed": installed,
                "failed": failed,
            }
        else:
            failed_names = [t["tool"] for t in failed]
            alternatives = self.suggest_alternative_tools(failed_names)
            alt_msg = "\n".join(
                f"  {tool} → {alts}" for tool, alts in alternatives.items() if alts
            )
            base_msg = f"설치 실패: {', '.join(t['tool'] + ' - ' + t['reason'] for t in failed)}"
            message = f"{base_msg}\n대체 도구 제안:\n{alt_msg}" if alt_msg else base_msg
            return {
                "status": "error",
                "message": message,
                "failed": failed,
                "alternatives": alternatives,
            }

    async def discover_tools(self, query: str | None = None) -> dict:
        """Discover available MCP tools/servers."""
        servers = await self.mcp_explorer.search_servers(query=query)
        skills = await self.skill_discovery.discover_skills(query=query)
        
        return {
            "mcp_servers": [
                {
                    "name": s.name,
                    "description": s.description,
                    "official": s.official,
                    "install_command": s.install_command,
                }
                for s in servers.servers
            ],
            "skills": skills,
            "total_servers": servers.total,
        }
