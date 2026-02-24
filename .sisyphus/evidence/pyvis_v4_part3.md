---

## 9. Critic Sandbox Execution Engine

### 9.1 CriticRunner (`execution/critic_runner.py`)

```python
import docker
import temp[CORRUPTED]import os
import time
from dataclasses import dataclass

@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_type: str = None  # For Judge's autonomous fix decision

class CriticRunner:
    SANDBOX_PATH = "/dev/shm/pyvis_sandbox"
    ERROR_PATTERNS = {
        "type_error":       "TypeError",
        "syntax_error":     "SyntaxError",
        "missing_import":   "ModuleNotFoundError",
        "name_error":       "NameError",
        "index_error":      "IndexError",
        "key_error":        "KeyError",
        "value_error":      "ValueError",
        "attribute_error":  "AttributeError",
    }

    def __init__(self):
        self.client = docker.from_env()
        os.makedirs(self.SANDBOX_PATH, exist_ok=True)

    async def execute(self, code: str,
                      timeout: int = 30,
                      allow_network: bool = False) -> ExecutionResult:
        """
        Execute code in Docker sandbox.
        Uses /dev/shm tmpfs to minimize disk I/O.
        """
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py',
            dir=self.SANDBOX_PATH, delete=False
        ) as f:
            f.write(code)
            temp_file = f.name

        start_time = time.time()
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                f"python /workspace/{os.path.basename(temp_file)}",
                volumes={self.SANDBOX_PATH: {'bind': '/workspace'}},
                network_mode="none" if not allow_network else "bridge",
                mem_limit="512m",
                cpu_quota=100000,   # 1 CPU core
                timeout=timeout,
                remove=True,
                stdout=True,
                stderr=True
            )
            elapsed = time.time() - start_time
            output = container.decode() if isinstance(container, bytes) else str(container)
            return ExecutionResult(
                stdout=output, stderr="",
            exit_code=0, execution_time=elapsed
            )

        except docker.errors.ContainerError as e:
            elapsed = time.time() - start_time
            stderr = e.stderr.decode() if e.stderr else str(e)
            return ExecutionResult(
                stdout="", stderr=stderr,
                exit_code=e.exit_status,
                execution_time=elapsed,
                error_type=self._classify_error(stderr)
            )

        except docker.errors.APIError as e:
            return ExecutionResult(
                stdout="", stderr=str(e),
                exit_code=-1, execution_time=0,
                error_type="docker_error"
            )
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def _classify_error(self, stderr: str) -> str:
        """Classify error type — used for determining whether Hands can autonomously fix"""
        for error_type, pattern in self.ERROR_PATTERNS.items():
            if pattern in stderr:
                re[CORRUPTED] error_type
        return "unknown_error"

    def format_report(self, result: ExecutionResult, task_title: str,
                      loop_count: int) -> str:
        return f"""## Execution Result Report
- Task: {task_title}
- Loop iteration: {loop_count}
- Exit code: {result.exit_code} ({'normal' if result.exit_code == 0 else 'abnormal'})
- Execution time: {result.execution_time:.2f}s
- Error type: {result.error_type or 'none'}
- Stdout: {result.stdout[:500] or 'none'}
- Stderr[CORRUPTED]: {result.stderr[:500] or 'none'}"""
```

### 9.2 Docker Sandbox Image (`docker/sandbox/Dockerfile`)

```dockerfile
FROM python:3.11-slim

# Security: remove root privileges
RUN useradd -m -u 1000 sandbox
WORKDIR /workspace
USER sandbox

# Install only essential packages
RUN pip install --no-cache-dir \
    requests \
    pydantic \
    fastapi \
    httpx

# Execution time limit
CMD ["python"]
```

---

## 10. Skill Library System

### 10.1 Skill File Format

```markdown
---
id: skill_001[CORRUPTED]FastAPI Type Safety
status: verified          # verified | candidate
category: web_backend
created_at: 2025-XX-XX
source_task_ids: [001, 003, 007]
fail_count: 4             # Number of times a task failed due to lacking this Skill
reviewed_by: human        # human | auto
---

## Application Conditions
Always apply when implementing FastAPI endpoints

## Rules
- Specify type hints on all function parameters
- Use explicit casting for inputs where int/str mixing is possible
- Use Pydantic BaseModel for request/response schemas
- Use Optional[T] = None format for optional parameters

## Application Example
\```python
from pydantic import BaseModel
from typing import Optional

class UserRequest(BaseModel):
    user_id: int
    name: str
    email: Optional[str] = None
\```

## Prohibited Patterns
- Function parameters without type hints
- Handling requests/responses with raw dict
```

### 10.2 SkillManager (`skill/skill_manager.py`)

```python
import os
import yaml
from pathlib import Path
from typing import Optional

SKILL_BASE = Path("/pyvis_memory/skill_library")
VE[CORRUPTED]RIFIED_DIR = SKILL_BASE / "verified"
CANDIDATE_DIR = SKILL_BASE / "candidate"

class SkillManager:

    def load_verified(self, task_description: str) -> str:
        """
        Load only verified Skills and insert into prompt.
        Candidate skills are not used.
        """
        relevant = self._find_relevant(task_description, status="verified")
        if not relevant:
            return "# No applicable Skill found"
        return "\n\n".join(skill["content"] for skill in relevant)

    [CORRUPTED]def _find_relevant(self, task_description: str, status: str) -> list:
        """Keyword-based relevant Skill search (can be upgraded to FAISS embedding search later)"""
        skill_dir = VERIFIED_DIR if status == "verified" else CANDIDATE_DIR
        results = []
        for skill_file in skill_dir.glob("*.md"):
            with open(skill_file) as f:
                content = f.read()
            # Simple keyword matching (initial implementation)
            if any(kw.lower() in task_description.lower()
               for kw in self._extract_keywords(content)):
                results.append({"file": skill_file.name, "content": content})
        return results

    def _extract_keywords(self, skill_content: str) -> list:
        """Extract keywords from Skill file's category and name"""
        keywords = []
        for line in skill_content.split('\n')[:20]:
            if 'category:' in line:
                keywords.extend(line.split(':')[1].strip().split('_'))
            if 'name:' in line:
                k[CORRUPTED]keywords.extend(line.split(':')[1].strip().split())
        return keywords

    async def evaluate_and_patch(self, ctx, loop_record: dict):
        """After loop completion, determine whether a Skill needs to be added"""
        from pyvis.skill.skill_validator import SkillValidator
        validator = SkillValidator()
        needs_skill = validator.should_add_skill(loop_record, self._get_history())
        if needs_skill:
            await self._create_candidate(loop_record)

    def _get_history(self) -> list:
        """Recent [CORRUPTED]       records_dir = Path("/pyvis_memory/loop_records")
        records = []
        for f in sorted(records_dir.glob("*.jsonl"))[-50:]:  # Last 50 records
            with open(f) as fh:
                import json
                for line in fh:
                    records.append(json.loads(line))
        return records

    async def _create_candidate(self, loop_record: dict):
        """Brain drafts Skill and saves to candidate"""
        # Request Skill draft from Brain
        skill_dra[CORRUPTED]ft = await self._request_skill_draft(loop_record)
        candidate_path = CANDIDATE_DIR / f"skill_{loop_record['task_id']}.md"
        with open(candidate_path, 'w') as f:
            f.write(skill_draft)
        # Notify human for review
        self._notify_review_needed(candidate_path)
```

---

## 11. Loop Cost Tracking + Selective Skill Reinforcement

### 11.1 LoopTracker (`tracking/loop_tracker.py`)

```python
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict[CORRUPTED], field
from datetime import datetime

RECORDS_DIR = Path("/pyvis_memory/loop_records")

@dataclass
class LoopRecord:
    task_id: str
    task_description: str
    task_category: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_loops: int = 0
    total_time_sec: float = 0.0
    switch_count: int = 0          # Model switch count
    escalated: bool = False
    fail_reasons: list = field(default_factory=list)
    final_quality: str = ""        # PASS | ESCALATED
    skill_patch_added: bool = False

class Loop[CORRUPTED]Tracker:
    def __init__(self):
        self._records: dict[str, LoopRecord] = {}
        self._start_times: dict[str, float] = {}
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    def start(self, task_id: str, task_description: str):
        self._records[task_id] = LoopRecord(
            task_id=task_id,
            task_description=task_description,
            started_at=datetime.now().isoformat()
        )
        self._start_times[task_id] = time.time()

    def record_switch(self, switch_type: str, task_id: str = None):
        if task_id and task_id in self._records:
            self._records[task_id].switch_count += 1

    def record_fail(self, task_id: str, reason: str):
        if task_id in self._records:
            self._records[task_id].fail_reasons.append({
                "reason": reason,
                "timestamp": datetime.now().isoformat()
            })
            self._records[task_id].total_loops += 1

    def finish(self, ctx, final_result: dict):
        record = self._records.get(ctx.task_id)
        if not record:
            return
        record.finished_at = datetime.now().isoformat()
        record.total_time_sec = time.time() - self._start_times[ctx.task_id]
        record.total_loops = ctx.loop_count
        record.escalated = final_result.get("status") == "escalated"
        record.final_quality = "ESCALATED" if record.escalated else "PASS"
        self._save(record)

    def get_record(self, task_id: str) -> dict:
        record = self._records.get(task_id)
        return asdict(record) if record else {}

    def _save(self, record: LoopRecord):
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = RECORDS_DIR / f"{date_str}.jsonl"
        with open(log_file, 'a') as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + '\n')
```

### 11.2 SkillValidator — Selective Reinforcement Decision (`skill/skill_validator.py`)

```python
from collections import Counter

class SkillValidator:
    """
    Skill addition conditions (all 4 must be met[CORRUPTED]):
    1. Recurrence: Same type of mistake occurs across 3+ different tasks
    2. Generality: Not a one-off exception specific to a particular task
    3. Correctability: The type of error can actually be prevented by a Skill
    4. No duplicates: Content not already covered by an existing Skill
    """

    NOT_FIXABLE_BY_SKILL = {
        "docker_error", "unknown_error", "environment_error", "network_error"
    }

    def should_add_skill(self, current_record: dict, history: list) -> bool:
        [CORRUPTED]fail_reasons = [f["reason"] for f in current_record.get("fail_reasons", [])]
        if not fail_reasons:
            return False

        for reason in set(fail_reasons):
            if self._check_all_conditions(reason, current_record, history):
                return True
        return False

    def _check_all_conditions(self, reason: str, current: dict,
                               history: list) -> bool:
        # 1. Recurrence: 3+ occurrences across different tasks
        other_task_count = sum(
            [CORRUPTED]1 for record in history
            if record["task_id"] != current["task_id"]
            and any(reason in f["reason"] for f in record.get("fail_reasons", []))
        )
        if other_task_count < 2:  # Including current = 3 total
            return False

        # 2. Generality: not tied to a single specific task_id
        task_ids_with_reason = [
            record["task_id"] for record in history
            if any(reason in f["reason"] for f in record.get("fail_reasons", []))
        ]
        if le[CORRUPTED]n(set(task_ids_with_reason)) < 3:
            return False

        # 3. Correctability
        if reason in self.NOT_FIXABLE_BY_SKILL:
            return False

        # 4. No duplicates (simple check based on existing Skill file names)
        if self._already_exists(reason):
            return False

        return True

    def _already_exists(self, reason: str) -> bool:
        from pathlib import Path
        skill_dir = Path("/pyvis_memory/skill_library/verified")
        return any(reason.lower().replace(" ", "_") in [CORRUPTED] f.stem
                   for f in skill_dir.glob("*.md"))
```

---

## 12. MCP Autonomous Tool Installation

### 12.1 ToolRegistry (`mcp/tool_registry.py`)

```python
import json
from pathlib import Path

REGISTRY_FILE = Path("/pyvis_memory/mcp_registry.json")

class ToolRegistry:
    def __init__(self):
        self._tools = self._load()

    def is_installed(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def get_all(self) -> dict:
        return self._tools.copy()

    def register(sel[CORRUPTED]f, tool_name: str, tool_meta: dict):
        self._tools[tool_name] = tool_meta
        self._save()

    def _load(self) -> dict:
        if REGISTRY_FILE.exists():
            return json.loads(REGISTRY_FILE.read_text())
        return {}

    def _save(self):
        REGISTRY_FILE.write_text(json.dumps(self._tools, indent=2))
```

### 12.2 ToolInstaller (`mcp/tool_installer.py`)

```python
import subprocess
from pyvis.mcp.tool_registry import ToolRegistry

class ToolInstaller:
    """
    Brain auto-installs when needed.
    Approval mode: if requires_approval=True, installation requires human confirmation.
    """

    def __init__(self, requires_approval: bool = True):
        self.registry = ToolRegistry()
        self.requires_approval = requires_approval

    async def prepare_tools(self, required_tools: list) -> dict:
        results = {}
        for tool in required_tools:
            if self.registry.is_installed(tool["name"]):
                results[tool["name"]] = "already_installed"
            elif self.requires_approval:
                results[tool["name"]] = "pending_approval"
                self._request_approval(tool)
            else:
                success = self._install(tool)
                results[tool["name"]] = "installed" if success else "failed"
        return results

    def _install(self, tool: dict) -> bool:
        try:
            cmd = tool.get("install_cmd", f"pip install {tool['name']}")
            result = subprocess.run(cmd.split(), capture_output=True, timeout=60)
            if result.returncode == 0:
                self.registry.register(tool["name"], tool)
                return True
        except Exception as e:
            print(f"Tool installation failed: {tool['name']} — {e}")
        return False

    def _request_approval(self, tool: dict):
        # Notify human (Telegram, logs, etc.)
        print(f"[Approval Required] Tool installation needed: {tool['name']} — {tool.get('reason', '')}")
```

---

## 13. Long-Term Memory System

### 13.1 KG Server (`memory/kg_server.py`)
