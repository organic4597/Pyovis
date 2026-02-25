"""
Pyvis v5.1 — Telegram Bot Enhancements (Voice & Vision)

Multi-modal interface enhancements:
- Voice message transcription (Whisper STT)
- Image analysis (LLaVA)
- Code syntax highlighting
- Progress bar updates

Usage:
    # Voice message handler
    @bot.on_message(filters.voice)
    async def handle_voice(message):
        text = await transcribe_voice(message.voice.file_id)
        await handle_text(text)

    # Image handler
    @bot.on_message(filters.photo)
    async def handle_image(message):
        analysis = await analyze_image(message.photo[-1].file_id)
        await message.reply(analysis)
"""

from __future__ import annotations

import logging
import aiohttp
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class TelegramEnhancedBot:
    """
    Enhanced Telegram bot with voice and vision capabilities.

    Features:
    - Voice transcription via Whisper
    - Image analysis via LLaVA
    - Formatted code blocks
    - Progress tracking
    """

    def __init__(
        self,
        token: str,
        whisper_url: str = "http://localhost:8000/transcribe",
        llava_url: str = "http://localhost:8000/analyze",
    ) -> None:
        """
        Initialize enhanced bot.

        Args:
            token: Telegram bot token
            whisper_url: Whisper STT service URL
            llava_url: LLaVA vision service URL
        """
        self.token = token
        self.whisper_url = whisper_url
        self.llava_url = llava_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def transcribe_voice(self, file_id: str) -> Optional[str]:
        """
        Transcribe voice message to text.

        Args:
            file_id: Telegram file ID of voice message

        Returns:
            Transcribed text or None if failed
        """
        try:
            session = await self._get_session()

            # Download file from Telegram
            file_url = (
                f"https://api.telegram.org/bot{self.token}/getFile?file_id={file_id}"
            )
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to get file info: {resp.status}")
                    return None
                data = await resp.json()
                file_path = data["result"]["file_path"]

            # Download actual file
            file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to download file: {resp.status}")
                    return None
                audio_data = await resp.read()

            # Send to Whisper for transcription
            form = aiohttp.FormData()
            form.add_field(
                "file", audio_data, filename="voice.ogg", content_type="audio/ogg"
            )

            async with session.post(self.whisper_url, data=form) as resp:
                if resp.status != 200:
                    logger.error(f"Whisper error: {resp.status}")
                    return None
                result = await resp.json()
                return result.get("text")

        except Exception as e:
            logger.error(f"Voice transcription failed: {e}")
            return None

    async def analyze_image(
        self, file_id: str, prompt: str = "What's in this image?"
    ) -> Optional[str]:
        """
        Analyze image with LLaVA.

        Args:
            file_id: Telegram file ID of image
            prompt: Question about the image

        Returns:
            Analysis text or None if failed
        """
        try:
            session = await self._get_session()

            # Download image from Telegram
            file_url = (
                f"https://api.telegram.org/bot{self.token}/getFile?file_id={file_id}"
            )
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                file_path = data["result"]["file_path"]

            file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return None
                image_data = await resp.read()

            # Send to LLaVA for analysis
            form = aiohttp.FormData()
            form.add_field(
                "image", image_data, filename="image.jpg", content_type="image/jpeg"
            )
            form.add_field("prompt", prompt)

            async with session.post(
                self.llava_url, data=form, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"LLaVA error: {resp.status}")
                    return None
                result = await resp.json()
                return result.get("answer")

        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return None

    async def send_code(
        self, chat_id: int, code: str, language: str = "python", message: str = ""
    ) -> bool:
        """
        Send code with syntax highlighting.

        Args:
            chat_id: Target chat ID
            code: Code to send
            language: Programming language
            message: Optional caption

        Returns:
            True if sent successfully
        """
        try:
            session = await self._get_session()

            # Format with markdown
            formatted_code = f"```{language}\n{code}\n```"
            text = f"{message}\n\n{formatted_code}" if message else formatted_code

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

            async with session.post(url, json=payload) as resp:
                return resp.status == 200

        except Exception as e:
            logger.error(f"Failed to send code: {e}")
            return False

    async def send_progress(
        self, chat_id: int, step: int, total: int, message: str
    ) -> bool:
        """
        Send progress update.

        Args:
            chat_id: Target chat ID
            step: Current step
            total: Total steps
            message: Progress message

        Returns:
            True if sent successfully
        """
        try:
            session = await self._get_session()

            # Create progress bar
            progress = int(step / total * 10)
            bar = "█" * progress + "░" * (10 - progress)
            progress_text = f"[{bar}] {step}/{total} ({step * 100 // total}%) "

            text = f"{progress_text}{message}"

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

            async with session.post(url, json=payload) as resp:
                return resp.status == 200

        except Exception as e:
            logger.error(f"Failed to send progress: {e}")
            return False

    async def download_file(self, file_id: str) -> Optional[bytes]:
        """
        Download file from Telegram.

        Args:
            file_id: Telegram file ID

        Returns:
            File bytes or None if failed
        """
        try:
            session = await self._get_session()

            # Get file path
            file_url = (
                f"https://api.telegram.org/bot{self.token}/getFile?file_id={file_id}"
            )
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                file_path = data["result"]["file_path"]

            # Download file
            file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            async with session.get(file_url) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()

        except Exception as e:
            logger.error(f"File download failed: {e}")
            return None


# Convenience functions
_enhanced_bot: Optional[TelegramEnhancedBot] = None


def get_enhanced_bot(token: str) -> TelegramEnhancedBot:
    """Get or create enhanced bot instance."""
    global _enhanced_bot
    if _enhanced_bot is None:
        _enhanced_bot = TelegramEnhancedBot(token)
    return _enhanced_bot


async def transcribe_voice(token: str, file_id: str) -> Optional[str]:
    """Quick voice transcription."""
    bot = get_enhanced_bot(token)
    return await bot.transcribe_voice(file_id)


async def analyze_image(
    token: str, file_id: str, prompt: str = "What's in this image?"
) -> Optional[str]:
    """Quick image analysis."""
    bot = get_enhanced_bot(token)
    return await bot.analyze_image(file_id, prompt)
