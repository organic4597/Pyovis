#!/usr/bin/env python3
"""
Pyovis v4.0 - Telegram Bot Launcher

Starts Telegram Bot with SessionManager and LLM server.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

async def main():
    """Main entry point."""
    print("\n=== Pyovis v4.0 Telegram Bot ===")
    print("Starting Telegram Bot with SessionManager...")
    
    # Set up environment
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "8214148788:AAH-c9t9OrhrS8t9CTrJ1GmwOm_-hzCVRWM")
    
    # Import after path setup
    from pyovis.interface.telegram_bot import run_telegram_bot
    
    try:
        await run_telegram_bot()
    except KeyboardInterrupt:
        print("\nStopping Telegram Bot...")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1

if __name__ == "__main__":
    asyncio.run(main())