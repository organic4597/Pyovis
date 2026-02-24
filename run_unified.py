#!/usr/bin/env python3
import asyncio
import os
import signal
import sys
import subprocess
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pyovis_core
from pyovis.ai import ModelSwapManager
from pyovis.tracking.loop_tracker import LoopTracker
from pyovis.orchestration.session_manager import SessionManager
from pyovis.interface.telegram_bot import TelegramBot


def kill_existing_processes():
    print("[Cleanup] Checking for existing processes...")
    
    for name, pattern in [("llama-server", "llama-server"), ("run_unified", "run_unified")]:
        try:
            result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
            if result.stdout.strip():
                for pid in result.stdout.strip().split("\n"):
                    try:
                        if int(pid) != os.getpid():
                            os.kill(int(pid), signal.SIGKILL)
                            print(f"[Cleanup] Killed {name} (PID: {pid})")
                    except:
                        pass
        except:
            pass
    
    print("[Cleanup] Done")


class UnifiedLauncher:
    def __init__(self):
        self.llm_process = None
        self.bot = None
        self.session_manager = None
        self.session_task = None
        self.loop = None
        self.running = True
    
    def start_llm_server_sync(self, role: str = "brain"):
        script_path = Path(__file__).parent / "scripts" / "start_model.sh"
        print(f"[LLM] Starting {role} model...")
        
        self.llm_process = subprocess.Popen(
            [str(script_path), role],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid
        )
        
        for line in self.llm_process.stdout:
            if not self.running:
                break
            print(f"[LLM] {line.rstrip()}")
    
    async def start_llm_server(self, role: str = "brain"):
        thread = threading.Thread(target=self.start_llm_server_sync, args=(role,))
        thread.daemon = True
        thread.start()
        print(f"[LLM] Started in background thread")
    
    async def stop_llm_server(self):
        if self.llm_process:
            print("[LLM] Stopping server...")
            try:
                os.killpg(os.getpgid(self.llm_process.pid), signal.SIGTERM)
                self.llm_process.wait(timeout=5)
            except:
                try:
                    os.killpg(os.getpgid(self.llm_process.pid), signal.SIGKILL)
                except:
                    pass
            print("[LLM] Server stopped")
    
    async def main(self):
        self.loop = asyncio.get_running_loop()
        print("\n=== Pyovis v4.0 Complete Unified Launcher ===")
        
        kill_existing_processes()
        
        try:
            from dotenv import load_dotenv
            load_dotenv()
            print("[Config] Loaded .env")
        except:
            print("[Config] dotenv not found")
        
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            print("ERROR: TELEGRAM_BOT_TOKEN not found")
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
        print("[Telegram Bot] Started polling...")
        
        print("[4/5] Starting LLM server (background)...")
        await self.start_llm_server("brain")
        
        print("\n=== Pyovis is running! ===")
        print("Send a message to your Telegram bot")
        print("Press Ctrl+C to stop\n")
        
        self.session_task = asyncio.create_task(self.session_manager.run())
        
        while self.running:
            await asyncio.sleep(0.5)
    
    async def shutdown(self):
        print("\n[Shutdown] Stopping all components...")
        self.running = False
        
        if self.bot:
            try:
                await self.bot.stop()
                print("[Telegram Bot] Stopped")
            except:
                pass
        
        await self.stop_llm_server()
        
        if self.session_manager:
            task_queue = getattr(self.session_manager, 'task_queue', None)
            if task_queue:
                try:
                    task_queue.enqueue(0, "stop", "")
                except:
                    pass
        
        if self.session_task:
            try:
                self.session_task.cancel()
            except:
                pass
        
        kill_existing_processes()
        print("\n=== Pyovis shutdown complete ===")
        sys.exit(0)


async def main():
    launcher = UnifiedLauncher()
    loop = asyncio.get_running_loop()
    
    def signal_handler(sig):
        print("\n[Signal] Received")
        launcher.running = False
        asyncio.create_task(launcher.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
    
    try:
        await launcher.main()
    except Exception as e:
        print(f"Error: {e}")
        await launcher.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
