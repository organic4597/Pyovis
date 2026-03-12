"""
Pyvis v5.1 — Workspace Snapshot & Rollback System

Git-based snapshot system for safe failure recovery.
Automatically saves workspace state before code generation and allows rollback on failure.

Usage:
    snapshot = WorkspaceSnapshot("/path/to/workspace")
    snapshot_id = snapshot.save("Before code generation")

    # ... code generation fails ...

    snapshot.restore(snapshot_id)  # Rollback to previous state
"""

from __future__ import annotations

import subprocess
import logging
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    """Snapshot metadata."""

    id: str  # Git commit hash
    timestamp: float
    message: str
    files_changed: int = 0


class WorkspaceSnapshot:
    """
    Git-based workspace snapshot manager.

    Features:
    - Automatic git repo initialization
    - Snapshot before each code generation
    - Rollback to any previous snapshot
    - Clean history with meaningful messages
    """

    def __init__(self, workspace_root: str | Path) -> None:
        """
        Initialize snapshot manager.

        Args:
            workspace_root: Path to workspace directory
        """
        self.workspace = Path(workspace_root)
        self.snapshots: List[Snapshot] = []
        self._git_initialized = False

    def init_git(self) -> bool:
        """
        Initialize git repository if not exists.

        Returns:
            True if successful, False otherwise
        """
        if self._git_initialized:
            return True

        git_dir = self.workspace / ".git"

        try:
            if not git_dir.exists():
                # Initialize git repo
                result = subprocess.run(
                    ["git", "init"],
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    logger.error(f"Git init failed: {result.stderr}")
                    return False

                # Configure git user
                subprocess.run(
                    ["git", "config", "user.name", "Pyvis Snapshot"],
                    cwd=self.workspace,
                    capture_output=True,
                    timeout=5,
                )
                subprocess.run(
                    ["git", "config", "user.email", "pyvis@snapshot.local"],
                    cwd=self.workspace,
                    capture_output=True,
                    timeout=5,
                )

                logger.info(f"Git repository initialized in {self.workspace}")

            self._git_initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize git: {e}")
            return False

    def save(self, message: str = "Auto-snapshot") -> Optional[Snapshot]:
        """
        Save current workspace state as snapshot.

        Args:
            message: Commit message for the snapshot

        Returns:
            Snapshot object or None if failed
        """
        if not self.init_git():
            return None

        try:
            # Add all changes
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Check if there are changes to commit
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if not status_result.stdout.strip():
                # No changes, create empty snapshot marker
                logger.debug("No changes to snapshot")
                return self._create_snapshot_entry(f"[No changes] {message}", 0)

            # Commit changes
            commit_result = subprocess.run(
                ["git", "commit", "-m", f"snapshot: {message}"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if commit_result.returncode != 0:
                logger.error(f"Git commit failed: {commit_result.stderr}")
                return None

            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if hash_result.returncode != 0:
                return None

            commit_hash = hash_result.stdout.strip()
            files_changed = len(status_result.stdout.strip().split("\n"))

            snapshot = self._create_snapshot_entry(message, files_changed, commit_hash)
            logger.info(f"Snapshot saved: {commit_hash[:8]} - {message}")

            return snapshot

        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}")
            return None

    def _create_snapshot_entry(
        self, message: str, files_changed: int, commit_hash: str = ""
    ) -> Snapshot:
        """Create snapshot entry and add to list."""
        snapshot = Snapshot(
            id=commit_hash or f"empty_{len(self.snapshots)}",
            timestamp=time.time(),
            message=message,
            files_changed=files_changed,
        )
        self.snapshots.append(snapshot)
        return snapshot

    def restore(self, snapshot_id: str) -> bool:
        """
        Restore workspace to specific snapshot.

        Args:
            snapshot_id: Git commit hash to restore to

        Returns:
            True if successful, False otherwise
        """
        if not self.init_git():
            return False

        try:
            # Checkout the snapshot
            result = subprocess.run(
                ["git", "checkout", snapshot_id],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.error(f"Git checkout failed: {result.stderr}")
                return False

            logger.info(f"Restored to snapshot: {snapshot_id[:8]}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore snapshot: {e}")
            return False

    def list_snapshots(self) -> List[Snapshot]:
        """Return list of all snapshots."""
        return self.snapshots.copy()

    def get_latest_snapshot(self) -> Optional[Snapshot]:
        """Return the most recent snapshot."""
        return self.snapshots[-1] if self.snapshots else None

    def rollback_to_previous(self) -> bool:
        """
        Rollback to the previous snapshot (one before latest).

        Returns:
            True if successful, False otherwise
        """
        if len(self.snapshots) < 2:
            logger.warning("Not enough snapshots to rollback")
            return False

        previous = self.snapshots[-2]
        return self.restore(previous.id)

    def cleanup_old_snapshots(self, keep_last_n: int = 10) -> int:
        """
        Remove old git commits beyond keep_last_n.

        Args:
            keep_last_n: Number of recent snapshots to keep

        Returns:
            Number of commits removed
        """
        if len(self.snapshots) <= keep_last_n:
            return 0

        removed_count = 0
        # Note: Git history cleanup is complex, this is a simplified version
        # In production, you might want to use git reflog or git filter-branch
        old_snapshots = self.snapshots[:-keep_last_n]
        removed_count = len(old_snapshots)
        self.snapshots = self.snapshots[-keep_last_n:]

        logger.info(f"Cleaned up {removed_count} old snapshots")
        return removed_count


# Convenience function for quick snapshotting
def snapshot_workspace(
    workspace_root: str, message: str = "Auto-snapshot"
) -> Optional[Snapshot]:
    """Quick snapshot with default settings."""
    snapshot_mgr = WorkspaceSnapshot(workspace_root)
    return snapshot_mgr.save(message)


# Global snapshot manager instance
_snapshot_manager: Optional[WorkspaceSnapshot] = None


def get_snapshot_manager(workspace_root: str) -> WorkspaceSnapshot:
    """Get or create global snapshot manager."""
    global _snapshot_manager
    if _snapshot_manager is None or _snapshot_manager.workspace != Path(workspace_root):
        _snapshot_manager = WorkspaceSnapshot(workspace_root)
    return _snapshot_manager
