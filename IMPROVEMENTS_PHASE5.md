# Pyvis v5.1 — Phase 5 Enhancement Roadmap

## Overview

This document outlines the comprehensive enhancement plan for Pyvis v5.1, transforming it from a "coding assistant" into a true "Jarvis-like" AI agent.

**Current State (Phase 1-4 Complete):**
- ✅ Chat Chain & Hard Limit
- ✅ Symbol Extractor (32K/58K dual context)
- ✅ Judge Thought Instruction (4-step checklist)
- ✅ Execution Plan auto-generation
- ✅ Experience DB (FAISS-based pattern storage)
- ✅ KG Thought Process storage
- ✅ 3-tier task classification (Chat/Simple/Complex)
- ✅ Zombie process prevention

---

## Three Core Perspectives

### 1. 🛠️ Coding Pipeline Perspective

**Missing Components:**

#### 1.1 Rollback Mechanism
- **Problem**: No way to revert when Hands fails mid-loop
- **Solution**: Git-based snapshot system
- **Impact**: Safe failure recovery, infinite loop escape

#### 1.2 Static Analysis
- **Problem**: Docker execution catches errors too late
- **Solution**: Pre-execution linting (ruff, mypy, pylint)
- **Impact**: 50% fewer Docker runs, 1-2 loop iterations saved

#### 1.3 Test Generation
- **Problem**: Code without tests is unverified
- **Solution**: Auto-generate pytest/unittest alongside code
- **Impact**: Clearer pass/fail criteria for Judge

#### 1.4 Parallel File Generation
- **Problem**: Sequential file creation is slow
- **Solution**: Dependency graph analysis + parallel execution
- **Impact**: 2-3x speedup for multi-file tasks

### 2. 🤖 Jarvis-like Intelligence Perspective

**Missing Components:**

#### 2.1 Active Monitoring
- **Problem**: System waits for user commands
- **Solution**: Background monitoring (battery, disk, cost alerts)
- **Impact**: Proactive problem detection

#### 2.2 User Profile Learning
- **Problem**: No memory of user preferences
- **Solution**: Accumulate patterns (FastAPI preference, Korean comments, test coverage importance)
- **Impact**: Personalized code generation

#### 2.3 Multi-modal Interface
- **Problem**: CLI-only feels robotic
- **Solution**: Telegram (voice STT, image analysis, real-time progress)
- **Impact**: Natural, conversational interaction

### 3. 🛡️ Stability Perspective

**Missing Components:**

#### 3.1 Auto-Recovery Watchdog
- **Problem**: llama-server dies overnight
- **Solution**: Watchdog process with auto-restart
- **Impact**: 24/7 unmanned operation

#### 3.2 Log Monitoring Dashboard
- **Problem**: Hard to diagnose issues
- **Solution**: Real-time UI for loop costs, model status
- **Impact**: Faster debugging, cost tracking

---

## Implementation Priority

### Phase 5.1: Immediate Wins (High Impact, Low Effort)
**Timeline: 1-2 hours total**

#### 5.1.1 Static Analysis (ruff/mypy)
- **File**: `pyovis/execution/static_analyzer.py`
- **Integration**: Before `CriticRunner.execute()`
- **Estimated effort**: 30 min
- **Code change**: ~50 lines

#### 5.1.2 Watchdog Auto-Recovery
- **File**: `pyovis/monitoring/watchdog.py`
- **Integration**: Background daemon
- **Estimated effort**: 30 min
- **Code change**: ~80 lines

#### 5.1.3 File Snapshot/Rollback
- **File**: `pyovis/execution/snapshot.py`
- **Integration**: `ResearchLoopController` rollback on failure
- **Estimated effort**: 1 hour
- **Code change**: ~120 lines

### Phase 5.2: Jarvis Experience (Medium Effort, High Perception)
**Timeline: 2-4 hours**

#### 5.2.1 Telegram Voice/Image
- **File**: `pyovis/interface/telegram_bot.py`
- **Features**: Whisper STT, LLaVA image analysis
- **Estimated effort**: 2 hours
- **Code change**: ~150 lines

#### 5.2.2 Active Monitoring + Alerts
- **File**: `pyovis/monitoring/health_monitor.py`
- **Features**: Disk, memory, cost alerts via Telegram
- **Estimated effort**: 1 hour
- **Code change**: ~100 lines

#### 5.2.3 User Profile Learning
- **File**: `pyovis/memory/user_profile.py`
- **Features**: Preference accumulation, auto-application
- **Estimated effort**: 2 hours
- **Code change**: ~180 lines

### Phase 5.3: Scaling (Optional, Advanced)
**Timeline: 4-8 hours**

#### 5.3.1 Parallel File Generation
- **File**: `pyovis/orchestration/parallel_executor.py`
- **Features**: Dependency graph, concurrent execution
- **Estimated effort**: 3 hours
- **Code change**: ~250 lines

#### 5.3.2 Auto Test Generation
- **File**: `pyovis/ai/hands.py` (modify prompt)
- **Features**: Generate pytest alongside code
- **Estimated effort**: 1 hour
- **Code change**: ~30 lines

#### 5.3.3 Log Monitoring Dashboard
- **File**: `web/dashboard.html` + `api/logs.py`
- **Features**: Real-time charts, cost tracking
- **Estimated effort**: 4 hours
- **Code change**: ~400 lines

---

## Detailed Specifications

### 5.1.1 Static Analysis (ruff/mypy)

```python
# pyovis/execution/static_analyzer.py
from dataclasses import dataclass
from typing import List, Optional
import subprocess

@dataclass
class LintResult:
    success: bool
    errors: List[str]
    warnings: List[str]
    fixed_code: Optional[str]

class StaticAnalyzer:
    def __init__(self, tools: List[str] = ["ruff", "mypy"]):
        self.tools = tools
    
    async def lint(self, code: str, file_path: str = "output.py") -> LintResult:
        errors = []
        warnings = []
        
        # Run ruff
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", file_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                errors.append(f"ruff: {result.stdout}")
        except Exception as e:
            warnings.append(f"ruff skipped: {e}")
        
        # Run mypy
        try:
            result = subprocess.run(
                ["mypy", "--ignore-missing-imports", file_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                errors.append(f"mypy: {result.stdout}")
        except Exception as e:
            warnings.append(f"mypy skipped: {e}")
        
        return LintResult(
            success=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            fixed_code=None
        )
```

**Integration Point:**
```python
# In CriticRunner.execute()
async def execute(self, code: str, ...) -> ExecutionResult:
    # NEW: Static analysis before Docker
    lint_result = await self.static_analyzer.lint(code)
    if not lint_result.success:
        return ExecutionResult(
            stdout="",
            stderr="\n".join(lint_result.errors),
            exit_code=-1,
            execution_time=0,
            error_type="lint_error"
        )
    
    # Existing Docker execution...
```

### 5.1.2 File Snapshot/Rollback

```python
# pyovis/execution/snapshot.py
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass
class Snapshot:
    id: str
    timestamp: float
    message: str

class WorkspaceSnapshot:
    def __init__(self, workspace_root: str):
        self.workspace = Path(workspace_root)
        self.snapshots: list[Snapshot] = []
    
    def init_git(self) -> None:
        """Initialize git repo if not exists."""
        if not (self.workspace / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.workspace, check=True)
            # Create initial commit
            self._commit("Initial commit")
    
    def save(self, message: str = "Auto-snapshot") -> Snapshot:
        """Save current state as snapshot."""
        self.init_git()
        snapshot_id = self._commit(message)
        snapshot = Snapshot(
            id=snapshot_id,
            timestamp=time.time(),
            message=message
        )
        self.snapshots.append(snapshot)
        return snapshot
    
    def restore(self, snapshot_id: str) -> bool:
        """Restore to specific snapshot."""
        try:
            subprocess.run(
                ["git", "checkout", snapshot_id],
                cwd=self.workspace,
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError:
            return False
    
    def _commit(self, message: str) -> str:
        """Create git commit and return hash."""
        subprocess.run(["git", "add", "-A"], cwd=self.workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.workspace,
            check=True,
            capture_output=True
        )
        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
```

**Integration Point:**
```python
# In ResearchLoopController.run()
async def run(self, ctx: LoopContext) -> dict:
    # Initialize snapshot system
    snapshot = WorkspaceSnapshot(str(self.workspace.project_root))
    snapshot.save("Before loop start")
    
    while ctx.current_step != LoopStep.COMPLETE:
        try:
            # ... existing loop logic ...
        except Exception as e:
            # Rollback on failure
            if ctx.consecutive_fails >= 2:
                snapshot.restore(snapshot.snapshots[-2].id)  # Restore previous
                return {"status": "rolled_back", "reason": str(e)}
```

### 5.1.3 Watchdog Auto-Recovery

```python
# pyovis/monitoring/watchdog.py
import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

class Watchdog:
    def __init__(self, health_url: str = "http://localhost:8001/health"):
        self.health_url = health_url
        self.restart_count = 0
    
    async def start_monitoring(self, interval: int = 5):
        """Start background monitoring."""
        while True:
            if not await self._check_health():
                await self._restart_server()
            await asyncio.sleep(interval)
    
    async def _check_health(self) -> bool:
        """Check if llama-server is healthy."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                resp = await session.get(self.health_url)
                return resp.status == 200
        except Exception:
            return False
    
    async def _restart_server(self):
        """Restart llama-server process."""
        self.restart_count += 1
        logger.warning(f"Watchdog: Restarting llama-server (attempt #{self.restart_count})")
        
        # Kill existing process
        subprocess.run(["pkill", "-f", "llama-server"])
        await asyncio.sleep(2)
        
        # Restart (implementation depends on deployment)
        # This could be systemd, docker-compose, or custom script
        subprocess.run(["systemctl", "restart", "llama-server"])
        
        logger.info("Watchdog: llama-server restarted")
```

**Integration:**
```python
# In main entry point
async def main():
    # Start watchdog as background task
    watchdog = Watchdog()
    asyncio.create_task(watchdog.start_monitoring())
    
    # Start bot
    await run_telegram_bot()
```

---

## Success Metrics

| Feature | Metric | Target |
|---------|--------|--------|
| Static Analysis | Loop iterations saved | -30% |
| Rollback | Recovery time | < 5s |
| Watchdog | Uptime | 99.9% |
| Telegram Voice | Voice command accuracy | > 95% |
| User Profile | Preference match rate | > 80% |
| Parallel Execution | Multi-file task speed | 2-3x faster |

---

## Next Steps

1. **Review this roadmap** and prioritize
2. **Select first feature** to implement (recommend: 5.1.1 Static Analysis)
3. **Create detailed implementation plan** for selected feature
4. **Execute and test**
5. **Iterate**

---

**Document Status**: Draft  
**Last Updated**: 2026-02-25  
**Author**: Sisyphus (via user feedback)
