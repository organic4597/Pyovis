#!/usr/bin/env python3
"""
pyovis CLI entrypoint.

Usage:
    pyovis          # Start unified launcher (LLM server + Telegram bot)
    pyovis --help
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


def _setup_logging() -> None:
    """Configure root logger with timestamped console output."""
    fmt = "[%(asctime)s] %(levelname)-7s %(name)s  %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    for noisy in ("httpx", "httpcore", "telegram.ext", "apscheduler", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _load_env() -> None:
    """Load .env from project root (walks up from cwd and package dir)."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",  # /Pyvis/.env
    ]
    for env_path in candidates:
        if env_path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_path, override=False)
                print(f"[Config] Loaded {env_path}")
            except ImportError:
                # Manual parse fallback if python-dotenv not installed
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        os.environ.setdefault(key, val)
                print(f"[Config] Loaded {env_path} (manual parse)")
            return


def _kill_processes_by_pattern(pattern: str) -> None:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True
        )
        for pid_str in result.stdout.strip().split("\n"):
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
                if pid != os.getpid():
                    os.kill(pid, signal.SIGKILL)
                    print(f"[Cleanup] Killed {pattern} (PID: {pid})")
            except (ValueError, ProcessLookupError):
                pass
    except Exception:
        pass


def _kill_existing_processes() -> None:
    print("[Cleanup] Checking for existing processes...")
    for pattern in ("llama-server", "run_unified", "pyovis"):
        _kill_processes_by_pattern(pattern)
    print("[Cleanup] Done")


class UnifiedLauncher:
    def __init__(self) -> None:
        self.llm_process: subprocess.Popen | None = None
        self.bot = None
        self.session_manager = None
        self.session_task: asyncio.Task | None = None
        self.kg_web_task: asyncio.Task | None = None
        self.running = True

    # ------------------------------------------------------------------ LLM

    def _start_llm_server_sync(self, role: str = "brain") -> None:
        script_path = Path(__file__).parent.parent / "scripts" / "start_model.sh"
        if not script_path.exists():
            print(f"[LLM] Warning: {script_path} not found, skipping LLM server start")
            return

        print(f"[LLM] Starting {role} model...")
        self.llm_process = subprocess.Popen(
            [str(script_path), role],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )
        for line in self.llm_process.stdout:  # type: ignore[union-attr]
            if not self.running:
                break
            print(f"[LLM] {line.rstrip()}")

    async def _start_llm_server(self, role: str = "brain") -> None:
        t = threading.Thread(target=self._start_llm_server_sync, args=(role,))
        t.daemon = True
        t.start()
        print("[LLM] Started in background thread")

    async def _stop_llm_server(self) -> None:
        print("[LLM] Stopping server...")
        if self.llm_process:
            try:
                os.killpg(os.getpgid(self.llm_process.pid), signal.SIGTERM)
                self.llm_process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.llm_process.pid), signal.SIGKILL)
                except Exception:
                    pass
        _kill_processes_by_pattern("llama-server")
        print("[LLM] Server stopped")

    # ------------------------------------------------------------------ Main

    async def run(self) -> None:
        import pyovis_core
        from pyovis.ai import ModelSwapManager
        from pyovis.interface.telegram_bot import TelegramBot
        from pyovis.interface.kg_web import start_kg_web
        from pyovis.orchestration.session_manager import SessionManager
        from pyovis.tracking.loop_tracker import LoopTracker

        print("\n=== Pyovis v4.0 ===")
        _setup_logging()
        _kill_existing_processes()
        _load_env()

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            print("ERROR: TELEGRAM_BOT_TOKEN not set.")
            print("  Set it in .env or: export TELEGRAM_BOT_TOKEN=<token>")
            sys.exit(1)

        print("\n[1/5] Creating core components...")
        task_queue = pyovis_core.PyPriorityQueue()
        model_swap = ModelSwapManager()
        tracker = LoopTracker()

        print("[2/5] Starting SessionManager...")
        self.session_manager = SessionManager(task_queue, model_swap, tracker)

        print("[3/5] Starting Telegram Bot...")
        self.bot = TelegramBot(token=bot_token, session_manager=self.session_manager)
        self.session_manager.bot = self.bot
        await self.bot.start()
        await self.bot.notify_startup()
        print("[Telegram] Polling started")

        print("[4/5] Starting KG Web Viewer (port 8502)...")
        kg = self.session_manager.kg_builder
        self.kg_web_task = asyncio.create_task(start_kg_web(kg, port=8502))
        print("[KG Web] http://0.0.0.0:8502")

        print("[5/5] Starting LLM server (background)...")
        await self._start_llm_server("brain")

        print("\n=== Pyovis is running — send a message to your bot ===")
        print("  KG Viewer: http://localhost:8502")
        print("Press Ctrl+C to stop\n")

        self.session_task = asyncio.create_task(self.session_manager.run())

        while self.running:
            await asyncio.sleep(0.5)

    async def shutdown(self) -> None:
        print("\n[Shutdown] Stopping...")
        self.running = False

        if self.bot:
            try:
                await self.bot.notify_shutdown()
            except Exception:
                pass
        if self.bot:
            try:
                await self.bot.stop()
            except Exception:
                pass

        await self._stop_llm_server()

        if self.session_manager:
            tq = getattr(self.session_manager, "task_queue", None)
            if tq:
                try:
                    tq.enqueue(0, "stop", "")
                except Exception:
                    pass

        if self.session_task:
            self.session_task.cancel()

        if self.kg_web_task:
            self.kg_web_task.cancel()

        print("=== Pyovis shutdown complete ===")


# ---------------------------------------------------------------------- CLI

def main() -> None:
    """CLI entrypoint registered as `pyovis` command."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    launcher = UnifiedLauncher()

    async def _run() -> None:
        loop = asyncio.get_running_loop()

        def _on_signal(sig: int) -> None:
            print(f"\n[Signal] {signal.Signals(sig).name}")
            launcher.running = False
            asyncio.create_task(launcher.shutdown())

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: _on_signal(s))

        try:
            await launcher.run()
        except Exception as exc:
            print(f"[Error] {exc}")
            await launcher.shutdown()

    try:
        import uvloop
        uvloop.run(_run())
    except ImportError:
        asyncio.run(_run())


if __name__ == "__main__":
    main()
