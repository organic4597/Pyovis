"""
Telegram Bot Interface for Pyovis

Features:
- Send/receive messages via Telegram
- Command handling (/start, /help, /status, /tools)
- Forward user requests to SessionManager
- Async result waiting with callback
- Message splitting for 4096 char limit
- Stream responses back to user
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Callable, Awaitable

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from pyovis.orchestration.session_manager import SessionManager
from pyovis.ai.swap_manager import ModelSwapManager
from pyovis.tracking.loop_tracker import LoopTracker

logger = logging.getLogger(__name__)

# Telegram message limit
TELEGRAM_MAX_LENGTH = 4096

# Where to persist chat_id across restarts
_CHAT_ID_FILE = Path(__file__).parent.parent.parent / ".telegram_chat_id"


def _load_chat_id() -> int | None:
    try:
        if _CHAT_ID_FILE.exists():
            return int(_CHAT_ID_FILE.read_text().strip())
    except Exception:
        pass
    val = os.environ.get("TELEGRAM_CHAT_ID", "")
    if val.lstrip("-").isdigit():
        return int(val)
    return None


def _save_chat_id(chat_id: int) -> None:
    try:
        _CHAT_ID_FILE.write_text(str(chat_id))
    except Exception:
        pass


def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split a long message into chunks that fit within Telegram's limit.
    
    Tries to split on paragraph boundaries first, then on sentence boundaries,
    and finally on character boundaries if necessary.
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    remaining = text
    
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break
        
        # Try to find a good split point
        split_point = max_length
        
        # First try paragraph break
        para_break = remaining.rfind("\n\n", 0, max_length)
        if para_break > max_length // 2:
            split_point = para_break + 2
        else:
            # Try single newline
            line_break = remaining.rfind("\n", 0, max_length)
            if line_break > max_length // 2:
                split_point = line_break + 1
            else:
                # Try sentence end
                for end_char in ["。", ".", "!", "?", "；", ";"]:
                    sentence_end = remaining.rfind(end_char, 0, max_length)
                    if sentence_end > max_length // 2:
                        split_point = sentence_end + 1
                        break
        
        chunks.append(remaining[:split_point])
        remaining = remaining[split_point:]
    
    return chunks


class TelegramBot:
    def __init__(
        self,
        token: str | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")

        self.session_manager = session_manager
        self.application: Application | None = None
        self._running = False
        self._pending_results: dict[str, asyncio.Future[dict]] = {}
        self._result_callbacks: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._pending_approvals: dict[int, dict] = {}
        self._pending_escalations: dict[int, dict] = {}  # Track escalated tasks per chat_id
        self._owner_chat_id: int | None = _load_chat_id()

    async def start(self) -> None:
        self.application = Application.builder().token(self.token).build()  # type: ignore[arg-type]

        self.application.add_handler(CommandHandler("start", self._cmd_start))
        self.application.add_handler(CommandHandler("help", self._cmd_help))
        self.application.add_handler(CommandHandler("status", self._cmd_status))
        self.application.add_handler(CommandHandler("tools", self._cmd_tools))
        self.application.add_handler(CommandHandler("allow", self._cmd_allow))
        self.application.add_handler(CommandHandler("deny", self._cmd_deny))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()  # type: ignore[union-attr]

        self._running = True
        print("[TelegramBot] Started polling")

    async def stop(self) -> None:
        if self.application:
            await self.application.updater.stop()  # type: ignore[union-attr]
            await self.application.stop()
            await self.application.shutdown()
        self._running = False
        print("[TelegramBot] Stopped")

    async def notify_startup(self) -> None:
        if self._owner_chat_id:
            await self._safe_send(self._owner_chat_id, "✅ *Pyovis 서버 작동 중*")

    async def notify_shutdown(self) -> None:
        if self._owner_chat_id:
            await self._safe_send(self._owner_chat_id, "🔴 *Pyovis 서버 종료*")

    async def _safe_send(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
        if not self.application:
            return
        for chunk in split_message(text):
            try:
                await asyncio.wait_for(
                    self.application.bot.send_message(
                        chat_id=chat_id, text=chunk, parse_mode=parse_mode
                    ),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"send_message timeout for chat_id={chat_id}")
            except Exception:
                try:
                    await asyncio.wait_for(
                        self.application.bot.send_message(chat_id=chat_id, text=chunk),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"send_message fallback timeout for chat_id={chat_id}")
                except Exception as e:
                    logger.warning("send_message failed: %s", e)

    def _store_escalation(self, chat_id: int, result: dict) -> None:
        """Store escalation details for follow-up questions."""
        self._pending_escalations[chat_id] = {
            "fail_reasons": result.get("fail_reasons", []),
            "loop_count": result.get("loop_count", "?"),
            "created_files": result.get("created_files", []),
            "project_id": result.get("project_id"),
            "message": result.get("message", ""),
            "task_id": result.get("task_id"),
        }

    async def _send_escalation_details(self, chat_id: int, escalation: dict) -> None:
        """Send detailed escalation explanation to user."""
        text = "⚠️ *에스캼레이션 상세 정보*\n\n"
        
        if escalation.get("message"):
            text += f"{escalation['message']}\n\n"
        
        # 실패 원인
        fail_reasons = escalation.get("fail_reasons", [])
        if fail_reasons:
            text += "🔴 *실패 원인:*\n" + "\n".join(f"• {r}" for r in fail_reasons[:5]) + "\n\n"
        
        # 루프 정보
        loop_count = escalation.get("loop_count", "?")
        text += f"🔄 루프 횟수: {loop_count}\n\n"
        
        # 생성된 파일
        created_files = escalation.get("created_files", [])
        if created_files:
            files = [f.get("file_path", f.get("saved_path", "?")) for f in created_files[:3]]
            text += "📄 *생성된 파일:*\n" + "\n".join(f"• `{f}`" for f in files) + "\n\n"
        
        # 프로젝트 경로
        if escalation.get("project_id"):
            text += f"📁 Project: `{escalation['project_id']}`\n\n"
        
        text += "*해결 방법:*\n"
        text += "• 에러를 확인하고 요청을 더 구체적으로 수정하세요\n"
        text += "• 또는 생성된 파일을 수동으로 수정하세요"
        
        await self._safe_send(chat_id, text)
    
    # --- Command Handlers ---
    
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        welcome = """
*Pyovis v4.0* - AI Coding Agent

사용 가능한 명령어:
/start - 이 도움말
/help - 도움말
/status - 시스템 상태
/tools - 사용 가능한 도구

그 외 메시지는 작업 요청으로 처리됩니다.
"""
        await update.message.reply_text(welcome, parse_mode="Markdown")
    
    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_text = """
*Pyovis 사용법*

1. 작업 요청
   그냥 메시지를 보내세요. 예:
   "Python으로 간단한 계산기 만들어줘"
   
2. 파일 저장
   생성된 코드는 자동으로 저장됩니다.
   
3. 피드백 루프
   코드가 실패하면 자동으로 수정을 시도합니다.

*난이도 분기*
- Simple: Brain이 직접 처리 (빠름)
- Complex: Planner → Hands → Judge 루프
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")
    
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        status_lines = ["*Pyovis Status*"]
        
        # GPU status
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,memory.used,memory.free", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                status_lines.append("\n*GPU:*")
                for line in result.stdout.strip().split("\n"):
                    idx, used, free = line.split(", ")
                    status_lines.append(f"  GPU {idx}: {used.strip()} used, {free.strip()} free")
        except Exception:
            status_lines.append("\nGPU: N/A")
        
        # Model status
        try:
            import httpx
            resp = httpx.get("http://localhost:8001/health", timeout=5.0)
            if resp.status_code == 200:
                status_lines.append("\n*LLM:* Online ✅")
            else:
                status_lines.append("\n*LLM:* Offline ❌")
        except Exception:
            status_lines.append("\n*LLM:* Offline ❌")
        
        await update.message.reply_text("\n".join(status_lines), parse_mode="Markdown")
    
    async def _cmd_tools(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /tools command."""
        tools_text = """
*사용 가능한 MCP Tools*

*Filesystem:*
- read_file: 파일 읽기
- write_file: 파일 쓰기
- list_directory: 디렉토리 목록
- search_files: 파일 검색

*Skills:*
- code_generator: 코드 생성
- test_writer: 테스트 작성
- code_reviewer: 코드 리뷰

*설치 가능한 MCP 서버:*
- git, github, fetch, brave-search, slack, google-maps, memory, puppeteer
"""
        await update.message.reply_text(tools_text, parse_mode="Markdown")
    
    async def _cmd_allow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /allow command - approve tool installation."""
        chat_id = update.effective_chat.id
        
        if chat_id not in self._pending_approvals:
            await update.message.reply_text("승인 대기 중인 요청이 없습니다.")
            return
        
        approval = self._pending_approvals.pop(chat_id)
        tools = approval.get("tools", [])
        original_request = approval.get("original_request", "")
        
        await update.message.reply_text(f"도구 설치 승인됨: {', '.join(tools)}\n설치 진행 중...")
        
        if self.session_manager:
            result = await self.session_manager.install_tools(tools, original_request)
            await self._send_response(chat_id, result)
        else:
            await update.message.reply_text("SessionManager가 연결되지 않았습니다.")
    
    async def _cmd_deny(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /deny command - reject tool installation."""
        chat_id = update.effective_chat.id
        
        if chat_id not in self._pending_approvals:
            await update.message.reply_text("승인 대기 중인 요청이 없습니다.")
            return
        
        approval = self._pending_approvals.pop(chat_id)
        tools = approval.get("tools", [])
        
        await update.message.reply_text(f"도구 설치 거부됨: {', '.join(tools)}\n요청을 취소합니다.")
    
    def set_pending_approval(self, chat_id: int, tools: list[str], original_request: str) -> None:
        """Set pending approval for tool installation."""
        self._pending_approvals[chat_id] = {
            "tools": tools,
            "original_request": original_request,
        }
    
    # --- Message Handler ---
    
    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        user_text = update.message.text.strip()
        chat_id = update.effective_chat.id

        if self._owner_chat_id is None:
            self._owner_chat_id = chat_id
            _save_chat_id(chat_id)
            print(f"[TelegramBot] Owner chat_id saved: {chat_id}")

        if user_text.lower() in ["allow", "승인", "yes", "y", "許可"]:
            await self._safe_send(chat_id, "승인 기능은 /allow 명령어를 사용하세요.")
            return

        if user_text.lower() in ["restart", "재시작"]:
            await self._safe_send(chat_id, "재시작 기능은 현재 개발 중입니다.")
            return

        logger.info("📨 텔레그램 메시지 수신 (chat=%d): %s", chat_id, user_text[:80])

        # Check if there's a pending escalation for this chat
        if chat_id in self._pending_escalations:
            # User is following up on an escalated task
            if len(user_text) < 50 and any(word in user_text.lower() for word in ["뭐", "왜", "문제", "이유", "what", "why", "problem", "reason", "어떻게", "해결"]):
                # Short follow-up question about the escalation
                escalation = self._pending_escalations[chat_id]
                await self._send_escalation_details(chat_id, escalation)
                return
            else:
                # New substantial request - clear escalation state
                self._pending_escalations.pop(chat_id, None)

        async def keep_typing():
            while True:
                try:
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    break
                await asyncio.sleep(2)

        typing_task = asyncio.create_task(keep_typing())

        try:
            if self.session_manager:
                result = await self._process_via_session_manager(user_text, chat_id)
            else:
                result = await self._process_direct(user_text, chat_id)

            typing_task.cancel()
            logger.info("📤 응답 전송 (chat=%d, status=%s)", chat_id, result.get("status", "?"))
            await self._send_response(chat_id, result)

        except Exception as e:
            typing_task.cancel()
            await self._safe_send(chat_id, f"❌ Error: {str(e)[:500]}")
    
    async def _process_direct(self, user_text: str, chat_id: int) -> dict:
        """Process request directly without session manager."""
        from pyovis.orchestration.request_analyzer import RequestAnalyzer, TaskComplexity
        from pyovis.ai.swap_manager import ModelSwapManager
        
        swap = ModelSwapManager()
        analyzer = RequestAnalyzer(swap)
        
        # Get available tools
        available_tools = ["filesystem:read_file", "filesystem:write_file", "skill:code_generator"]
        
        # Analyze request
        analysis = await analyzer.analyze(user_text, available_tools)

        if analysis.needs_clarification:
            return {
                "status": "clarification",
                "questions": analysis.clarification_questions
            }

        if analysis.complexity == TaskComplexity.CHAT:
            # Chat - respond without file generation
            result = await analyzer.handle_simple_task(user_text)
            # Remove any file_path or workspace that might have been set
            result.pop("file_path", None)
            result.pop("workspace", None)
            await swap.shutdown()
            return result
        elif analysis.complexity == TaskComplexity.SIMPLE:
            # Simple task - Brain handles directly
            result = await analyzer.handle_simple_task(user_text)
            await swap.shutdown()
            return result
        else:
            # Complex task - need full loop
            await swap.shutdown()
            return {
                "status": "complex",
                "message": "Complex task detected. Full implementation coming soon."
            }
    
    async def _process_via_session_manager(self, user_text: str, chat_id: int) -> dict:
        """Process via session manager. Waits up to 1 hour for complex tasks."""
        task_id = str(uuid.uuid4())

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending_results[task_id] = future

        self.session_manager.task_queue.enqueue(
            priority=1,
            task_type="ai",
            payload=json.dumps({"text": user_text, "task_id": task_id, "chat_id": chat_id}),
        )

        try:
            result = await asyncio.wait_for(future, timeout=3600.0)
            return result
        except asyncio.TimeoutError:
            self._pending_results.pop(task_id, None)
            return {"status": "timeout", "message": "작업 시간 초과 (1시간). 서버 상태를 확인하세요."}
        except Exception as e:
            self._pending_results.pop(task_id, None)
            return {"status": "error", "message": str(e)}

    def submit_result(self, task_id: str, result: dict) -> None:
        """Submit result for a pending task. Called by SessionManager."""
        future = self._pending_results.pop(task_id, None)
        if future is None or future.done():
            return
        # set_result must be called from the loop that owns the future
        try:
            future.get_loop().call_soon_threadsafe(future.set_result, result)
        except RuntimeError:
            # fallback: already in the correct loop
            if not future.done():
                future.set_result(result)

    async def send_progress(self, chat_id: int, text: str) -> None:
        """Send a progress notification to a chat. Called by SessionManager."""
        try:
            await asyncio.wait_for(self._safe_send(chat_id, text), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"send_progress timeout for chat_id={chat_id}")
        except Exception as e:
            logger.warning(f"send_progress failed: {e}")
    
    async def _send_response(self, chat_id: int, result: dict) -> None:
        status = result.get("status", "unknown")
        
        logger.info(f"[DEBUG] _send_response: status={status}, result keys={list(result.keys())}")
        if status == "clarification":
            questions = result.get("questions", [])
            text = "📋 *추가 정보가 필요합니다:*\n\n" + "\n".join(f"• {q}" for q in questions)
        
        elif status in ("success", "need_info"):
            text = f"✅ *완료*\n\n{result.get('message', 'Task completed')}"
            # Only show workspace for non-chat tasks
            if result.get("workspace") and result.get("path") != "chat":
                text += f"\n\n📁 Workspace: `{result['workspace']}`"
            if result.get("files"):
                text += f"\n\n📄 Files:\n" + "\n".join(f" • `{f}`" for f in result["files"][:10])

        elif status == "complete":
            text = f"✅ *완료*\n\n{result.get('review', result.get('message', '작업이 완료되었습니다.'))}"
            if result.get("workspace_root"):
                text += f"\n\n📁 저장 위치: `{result['workspace_root']}`"
            created_files = result.get("created_files", [])
            if created_files:
                paths = [f.get("saved_path") or f.get("file_path", "?") for f in created_files[:10]]
                text += "\n\n📄 *생성된 파일:*\n" + "\n".join(f" • `{p}`" for p in paths)
        
        elif status == "complex":
            text = f"🔄 *복잡한 작업*\n\n{result.get('message', 'Processing...')}"
        
        elif status == "escalated":
            # Store escalation for follow-up questions
            self._store_escalation(chat_id, result)
            
            text = f"⚠️ *에스캼레이션*\n\n{result.get('message', 'Human intervention needed')}"
            
            # 실패 원인
            if result.get("fail_reasons"):
                text += "\n\n🔴 *실패 원인:*\n" + "\n".join(f"• {r}" for r in result["fail_reasons"][:5])
            
            # 루프 정보
            loop_count = result.get("loop_count", "?")
            text += f"\n\n🔄 루프 횟수: {loop_count}"
            
            # 생성된 파일 (있으면)
            if result.get("created_files"):
                files = [f.get("saved_path") or f.get("file_path", "?") for f in result["created_files"][:3]]
                text += "\n\n📄 *생성된 파일:*\n" + "\n".join(f"• `{f}`" for f in files)
            
            # 프로젝트 경로
            if result.get("project_id"):
                text += f"\n\n📁 Project: `{result['project_id']}`"
        
        elif status == "queued":
            text = f"⏳ *대기 중*\n\n{result.get('message', 'Task queued')}"
        
        elif status == "approval_required":
            tools = [p['tool'] for p in result.get("pending_installations", [])]
            original_request = result.get("original_request", "")
            
            if tools:
                self.set_pending_approval(chat_id, tools, original_request)
            
            text = f"🔑 *승인 필요*\n\n{result.get('message', '')}"
            if result.get("pending_installations"):
                text += "\n\n설치 요청:\n" + "\n".join(
                    f"• {p['tool']}: `{p.get('install_command', 'N/A')}`"
                    for p in result["pending_installations"][:5]
                )
            text += "\n\n✅ 승인: /allow\n❌ 거부: /deny"
        
        else:
            text = f"📋 *상태: {status}*\n\n{result.get('message', '')}"
        
        await self._safe_send(chat_id, text)


async def run_telegram_bot(token: str | None = None) -> None:
    """Run the Telegram bot."""
    bot = TelegramBot(token=token)
    
    try:
        await bot.start()
        print("Telegram bot started. Press Ctrl+C to stop.")
        while bot._running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await bot.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_telegram_bot())
