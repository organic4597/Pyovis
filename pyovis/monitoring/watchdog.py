"""
Pyvis v5.1 — Watchdog Auto-Recovery for llama-server

Background monitoring process that automatically restarts llama-server
when it dies or becomes unresponsive.

Usage:
    # In main entry point
    watchdog = Watchdog()
    asyncio.create_task(watchdog.start_monitoring())
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import aiohttp
from typing import Optional, List

logger = logging.getLogger(__name__)


class Watchdog:
    """
    Watchdog monitor for llama-server processes.

    Features:
    - Health check every N seconds
    - Auto-restart on failure
    - Restart count tracking
    - Graceful shutdown
    """

    def __init__(
        self,
        health_url: str = "http://localhost:8001/health",
        check_interval: int = 10,
        restart_delay: int = 2,
        max_restarts: int = 5,
        restart_window: int = 300,  # 5 minutes
    ) -> None:
        """
        Initialize watchdog.

        Args:
            health_url: URL to check server health
            check_interval: Seconds between health checks
            restart_delay: Seconds to wait before restart
            max_restarts: Max restarts in restart_window
            restart_window: Time window for max_restarts (seconds)
        """
        self.health_url = health_url
        self.check_interval = check_interval
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts
        self.restart_window = restart_window

        self._running = False
        self._restart_times: List[float] = []
        self._restart_count = 0
        self._monitor_task: Optional[asyncio.Task] = None

    async def start_monitoring(self) -> None:
        """Start background monitoring loop."""
        self._running = True
        logger.info("Watchdog started monitoring")

        while self._running:
            try:
                if not await self._check_health():
                    await self._handle_unhealthy()

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                logger.info("Watchdog monitoring cancelled")
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                await asyncio.sleep(self.check_interval)

    async def stop_monitoring(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Watchdog stopped")

    async def _check_health(self) -> bool:
        """
        Check if llama-server is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                resp = await session.get(self.health_url)
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("status") == "ok"
                return False
        except Exception:
            return False

    async def _handle_unhealthy(self) -> None:
        """Handle unhealthy server - attempt restart."""
        logger.warning("Watchdog: Server unhealthy, attempting restart")

        # Check restart rate limit
        current_time = asyncio.get_event_loop().time()
        self._cleanup_old_restarts(current_time)

        if len(self._restart_times) >= self.max_restarts:
            logger.error(
                f"Watchdog: Max restarts ({self.max_restarts}) reached "
                f"in last {self.restart_window}s. Stopping watchdog."
            )
            self._running = False
            return

        # Restart server
        await self._restart_server()

        # Track restart
        self._restart_times.append(current_time)
        self._restart_count += 1
        logger.info(
            f"Watchdog: Server restarted (total restarts: {self._restart_count})"
        )

    def _cleanup_old_restarts(self, current_time: float) -> None:
        """Remove restart times outside the window."""
        cutoff = current_time - self.restart_window
        self._restart_times = [t for t in self._restart_times if t > cutoff]

    async def _restart_server(self) -> None:
        """Restart llama-server process."""
        logger.info("Watchdog: Restarting llama-server")

        # Kill existing process
        try:
            subprocess.run(["pkill", "-f", "llama-server"], timeout=10)
            logger.info("Watchdog: Killed existing llama-server")
        except Exception as e:
            logger.warning(f"Watchdog: Failed to kill llama-server: {e}")

        # Wait for GPU memory to be freed
        await asyncio.sleep(self.restart_delay)

        # Restart command - this depends on deployment
        # Options: systemd, docker-compose, or custom script
        restart_methods = [
            # Try systemd first
            ["systemctl", "restart", "llama-server"],
            # Try docker-compose
            ["docker-compose", "restart", "llama-server"],
            # Try custom restart script
            ["/usr/local/bin/restart-llama.sh"],
        ]

        for cmd in restart_methods:
            try:
                result = subprocess.run(cmd, timeout=30, capture_output=True)
                if result.returncode == 0:
                    logger.info(f"Watchdog: Restarted with {' '.join(cmd)}")
                    return
            except Exception:
                continue

        logger.error("Watchdog: All restart methods failed")

    def get_stats(self) -> dict:
        """Get watchdog statistics."""
        return {
            "running": self._running,
            "restart_count": self._restart_count,
            "recent_restarts": len(self._restart_times),
            "health_url": self.health_url,
        }


# Global watchdog instance
_watchdog: Optional[Watchdog] = None


def get_watchdog(health_url: str = "http://localhost:8001/health") -> Watchdog:
    """Get or create global watchdog instance."""
    global _watchdog
    if _watchdog is None:
        _watchdog = Watchdog(health_url=health_url)
    return _watchdog


async def start_watchdog(
    health_url: str = "http://localhost:8001/health", check_interval: int = 10
) -> Watchdog:
    """Start watchdog as background task."""
    watchdog = get_watchdog(health_url)
    watchdog._monitor_task = asyncio.create_task(watchdog.start_monitoring())
    return watchdog
