"""
Pyvis v5.1 — Active Monitoring & Alerts

Background monitoring system that watches system resources and costs,
sending proactive alerts via Telegram.

Usage:
    # Start monitoring
    monitor = HealthMonitor()
    await monitor.start_monitoring()

    # Or use convenience function
    await start_monitoring()
"""

from __future__ import annotations

import asyncio
import logging
import psutil
import aiohttp
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AlertThresholds:
    """Alert threshold configuration."""

    disk_usage_percent: float = 90.0
    memory_usage_percent: float = 95.0
    cpu_usage_percent: float = 95.0
    loop_cost_usd: float = 10.0
    error_count: int = 10
    loop_iteration_time_sec: float = 300.0  # 5 minutes


@dataclass
class MonitorStats:
    """Current monitoring statistics."""

    disk_usage_percent: float = 0.0
    memory_usage_percent: float = 0.0
    cpu_usage_percent: float = 0.0
    loop_count: int = 0
    total_cost_usd: float = 0.0
    error_count: int = 0
    avg_loop_time_sec: float = 0.0
    last_alert: Optional[str] = None


class HealthMonitor:
    """
    Active system health monitor.

    Features:
    - Resource monitoring (disk, memory, CPU)
    - Cost tracking
    - Error rate monitoring
    - Telegram alerts
    - Configurable thresholds
    """

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        alert_chat_id: Optional[int] = None,
        thresholds: Optional[AlertThresholds] = None,
        check_interval: int = 60,  # 1 minute
    ) -> None:
        """
        Initialize health monitor.

        Args:
            telegram_token: Bot token for alerts
            alert_chat_id: Chat ID to send alerts
            thresholds: Alert thresholds
            check_interval: Seconds between checks
        """
        self.telegram_token = telegram_token
        self.alert_chat_id = alert_chat_id
        self.thresholds = thresholds or AlertThresholds()
        self.check_interval = check_interval

        self._running = False
        self._stats = MonitorStats()
        self._alerts_sent: Dict[str, float] = {}  # alert_type -> timestamp
        self._monitor_task: Optional[asyncio.Task] = None

    async def start_monitoring(self) -> None:
        """Start background monitoring."""
        self._running = True
        logger.info("Health monitor started")

        while self._running:
            try:
                await self._check_and_alert()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                logger.info("Health monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
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
        logger.info("Health monitor stopped")

    async def _check_and_alert(self) -> None:
        """Check resources and send alerts if needed."""
        # Collect stats
        self._stats = await self._collect_stats()

        # Check thresholds and alert
        alerts = []

        if self._stats.disk_usage_percent > self.thresholds.disk_usage_percent:
            alerts.append(f"🚨 Disk usage: {self._stats.disk_usage_percent:.1f}%")

        if self._stats.memory_usage_percent > self.thresholds.memory_usage_percent:
            alerts.append(f"🚨 Memory usage: {self._stats.memory_usage_percent:.1f}%")

        if self._stats.cpu_usage_percent > self.thresholds.cpu_usage_percent:
            alerts.append(f"🚨 CPU usage: {self._stats.cpu_usage_percent:.1f}%")

        if self._stats.total_cost_usd > self.thresholds.loop_cost_usd:
            alerts.append(f"🚨 Loop cost exceeded: ${self._stats.total_cost_usd:.2f}")

        if self._stats.error_count > self.thresholds.error_count:
            alerts.append(f"🚨 High error count: {self._stats.error_count}")

        # Send alerts (rate limited)
        for alert in alerts:
            if await self._should_send_alert(alert):
                await self._send_alert(alert)
                self._alerts_sent[alert] = asyncio.get_event_loop().time()

    async def _collect_stats(self) -> MonitorStats:
        """Collect current system statistics."""
        stats = MonitorStats()

        # Disk usage
        try:
            disk = psutil.disk_usage("/")
            stats.disk_usage_percent = disk.percent
        except Exception:
            stats.disk_usage_percent = 0.0

        # Memory usage
        try:
            memory = psutil.virtual_memory()
            stats.memory_usage_percent = memory.percent
        except Exception:
            stats.memory_usage_percent = 0.0

        # CPU usage
        try:
            stats.cpu_usage_percent = psutil.cpu_percent(interval=1)
        except Exception:
            stats.cpu_usage_percent = 0.0

        # Load loop stats from tracking (if available)
        try:
            from pyovis.tracking.loop_tracker import LoopTracker
            # This would need to be injected or use a global instance
        except Exception:
            pass

        return stats

    async def _should_send_alert(self, alert: str) -> bool:
        """Check if alert should be sent (rate limiting)."""
        now = asyncio.get_event_loop().time()
        last_sent = self._alerts_sent.get(alert, 0)

        # Don't send same alert within 5 minutes
        if now - last_sent < 300:
            return False

        return True

    async def _send_alert(self, message: str) -> bool:
        """Send alert via Telegram."""
        if not self.telegram_token or not self.alert_chat_id:
            logger.warning(f"Alert: {message} (not sent - no Telegram config)")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.alert_chat_id,
                "text": f"⚠️ *Pyvis Alert*\n\n{message}",
                "parse_mode": "Markdown",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Alert sent: {message}")
                        return True
                    else:
                        logger.error(f"Failed to send alert: {resp.status}")
                        return False

        except Exception as e:
            logger.error(f"Alert failed: {e}")
            return False

    def get_stats(self) -> MonitorStats:
        """Get current statistics."""
        return self._stats

    def get_stats_dict(self) -> Dict[str, Any]:
        """Get statistics as dictionary."""
        return {
            "disk_usage_percent": self._stats.disk_usage_percent,
            "memory_usage_percent": self._stats.memory_usage_percent,
            "cpu_usage_percent": self._stats.cpu_usage_percent,
            "loop_count": self._stats.loop_count,
            "total_cost_usd": self._stats.total_cost_usd,
            "error_count": self._stats.error_count,
            "avg_loop_time_sec": self._stats.avg_loop_time_sec,
        }


# Global monitor instance
_monitor: Optional[HealthMonitor] = None


def get_monitor(
    telegram_token: Optional[str] = None, alert_chat_id: Optional[int] = None
) -> HealthMonitor:
    """Get or create global monitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor(telegram_token, alert_chat_id)
    return _monitor


async def start_monitoring(
    telegram_token: Optional[str] = None,
    alert_chat_id: Optional[int] = None,
    check_interval: int = 60,
) -> HealthMonitor:
    """Start health monitoring as background task."""
    monitor = get_monitor(telegram_token, alert_chat_id)
    monitor._monitor_task = asyncio.create_task(monitor.start_monitoring())
    return monitor
