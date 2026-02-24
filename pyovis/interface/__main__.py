"""
Pyovis v4.0 - AI Coding Agent

Usage:
    python -m pyvis.interface.telegram_bot

Environment Variables:
    TELEGRAM_BOT_TOKEN    - Telegram bot token
    SESSION_MANAGER_PORT  - Port for SessionManager (default: 8002)

Examples:
    # Start Telegram Bot
    export TELEGRAM_BOT_TOKEN="8214148788:AAH-c9t9OrhrS8t9CTrJ1GmwOm_-hzCVRWM"
    python -m pyvis.interface.telegram_bot

    # Or with custom session manager port
    export TELEGRAM_BOT_TOKEN="..."
    export SESSION_MANAGER_PORT="8002"
    python -m pyvis.interface.telegram_bot

Commands:
    /start      - Start bot help
    /help       - Help message
    /status     - System status
    /tools      - Available tools
    
Send any message to get AI assistance.
"""

__version__ = "4.0.0"
__all__ = ["TelegramBot", "run_telegram_bot"]