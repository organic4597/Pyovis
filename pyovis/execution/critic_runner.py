from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional

import importlib

from pyovis.execution.execution_plan import ExecutionPlan, ExecutionType


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_type: str | None = None


class CriticRunner:
    SANDBOX_PATH = "/dev/shm/pyvis_sandbox"
    ERROR_PATTERNS = {
        "type_error": "TypeError",
        "syntax_error": "SyntaxError",
        "missing_import": "ModuleNotFoundError",
        "name_error": "NameError",
        "index_error": "IndexError",
        "key_error": "KeyError",
        "value_error": "ValueError",
        "attribute_error": "AttributeError",
    }

    def __init__(self) -> None:
        docker = importlib.import_module("docker")
        self.client = docker.from_env()
        os.makedirs(self.SANDBOX_PATH, exist_ok=True)

    async def execute(
        self, code: str, timeout: int = 30, allow_network: bool = False
    ) -> ExecutionResult:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=self.SANDBOX_PATH, delete=False
        ) as f:
            f.write(code)
            temp_file = f.name
        os.chmod(temp_file, 0o644)

        container = None
        start_time = time.time()
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                f"python /workspace/{os.path.basename(temp_file)}",
                volumes={self.SANDBOX_PATH: {"bind": "/workspace", "mode": "rw"}},
                network_mode="none" if not allow_network else "bridge",
                mem_limit="512m",
                cpu_quota=100000,
                detach=True,
                environment={"PYTHONUNBUFFERED": "1"},
                remove=False,
                stdout=True,
                stderr=True,
            )
            status = container.wait(timeout=timeout)
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            elapsed = time.time() - start_time
            stdout_text = stdout_bytes.decode() if isinstance(stdout_bytes, bytes) else str(stdout_bytes)
            stderr_text = stderr_bytes.decode() if isinstance(stderr_bytes, bytes) else str(stderr_bytes)
            exit_code = status.get("StatusCode", 0) if isinstance(status, dict) else 0
            if exit_code == 0:
                result = ExecutionResult(
                    stdout=stdout_text, stderr=stderr_text, exit_code=0, execution_time=elapsed
                )
            else:
                result = ExecutionResult(
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=exit_code,
                    execution_time=elapsed,
                    error_type=self._classify_error(stderr_text),
                )
            return result

        except Exception as exc:
            elapsed = time.time() - start_time
            stderr = getattr(exc, "stderr", None)
            if stderr is None:
                stderr_text = str(exc)
            else:
                stderr_text = stderr.decode() if hasattr(stderr, "decode") else str(stderr)
            return ExecutionResult(
                stdout="",
                stderr=stderr_text,
                exit_code=getattr(exc, "exit_status", -1),
                execution_time=elapsed,
                error_type=self._classify_error(stderr_text),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    async def execute_with_plan(
        self,
        code: str,
        execution_plan: Dict[str, Any],
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute code according to an Execution Plan.
        
        Args:
            code: The code to execute
            execution_plan: Execution plan dict from Hands
            timeout: Maximum execution time in seconds
            
        Returns:
            ExecutionResult with execution details
        """
        # Run setup commands first
        setup_commands = execution_plan.get("setup_commands", [])
        for cmd in setup_commands:
            await self._run_setup_command(cmd)
        
        # Determine execution type
        exec_type = execution_plan.get("execution_type", "python_script")
        
        # Execute based on type
        if exec_type == "python_test":
            return await self._run_pytest(code, execution_plan, timeout)
        elif exec_type == "api_server":
            return await self._run_api_test(code, execution_plan, timeout)
        elif exec_type == "cli_command":
            return await self._run_cli(code, execution_plan, timeout)
        else:
            # Default: simple script execution
            allow_network = execution_plan.get("requires_network", False)
            return await self.execute(code, timeout, allow_network)

    async def _run_setup_command(self, command: str) -> None:
        """Run a setup command (e.g., pip install)."""
        # This would run in the sandbox - for now, log it
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Setup command: {command}")

    async def _run_pytest(
        self, code: str, plan: Dict[str, Any], timeout: int
    ) -> ExecutionResult:
        """Run pytest on the generated test code."""
        # Write test file
        test_file = plan.get("entry_point", "test_generated.py")
        if not test_file.startswith("test_"):
            test_file = f"test_{test_file}"
            
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=self.SANDBOX_PATH, delete=False
        ) as f:
            f.write(code)
            temp_file = f.name
        os.chmod(temp_file, 0o644)
        
        # Run pytest
        container = None
        start_time = time.time()
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                f"pytest /workspace/{os.path.basename(temp_file)} -v",
                volumes={self.SANDBOX_PATH: {"bind": "/workspace", "mode": "rw"}},
                network_mode="none",
                mem_limit="512m",
                cpu_quota=100000,
                detach=True,
                environment={"PYTHONUNBUFFERED": "1"},
                remove=False,
                stdout=True,
                stderr=True,
            )
            status = container.wait(timeout=timeout)
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            elapsed = time.time() - start_time
            stdout_text = stdout_bytes.decode() if isinstance(stdout_bytes, bytes) else str(stdout_bytes)
            stderr_text = stderr_bytes.decode() if isinstance(stderr_bytes, bytes) else str(stderr_bytes)
            exit_code = status.get("StatusCode", 0) if isinstance(status, dict) else 0
            
            return ExecutionResult(
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=exit_code,
                execution_time=elapsed,
                error_type=None if exit_code == 0 else "test_failure",
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            return ExecutionResult(
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                execution_time=elapsed,
                error_type="execution_error",
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    async def _run_api_test(
        self, code: str, plan: Dict[str, Any], timeout: int
    ) -> ExecutionResult:
        """Run API server test."""
        # For now, just run as regular script with network enabled
        allow_network = plan.get("requires_network", True)
        return await self.execute(code, timeout, allow_network)

    async def _run_cli(
        self, code: str, plan: Dict[str, Any], timeout: int
    ) -> ExecutionResult:
        """Run CLI command."""
        entry_point = plan.get("entry_point", "main.py")
        args = " ".join(plan.get("command_args", []))
        
        # Write the CLI script
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=self.SANDBOX_PATH, delete=False
        ) as f:
            f.write(code)
            temp_file = f.name
        os.chmod(temp_file, 0o755)
        
        container = None
        start_time = time.time()
        try:
            cmd = f"python /workspace/{os.path.basename(temp_file)} {args}"
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                cmd,
                volumes={self.SANDBOX_PATH: {"bind": "/workspace", "mode": "rw"}},
                network_mode="none",
                mem_limit="512m",
                cpu_quota=100000,
                detach=True,
                environment={"PYTHONUNBUFFERED": "1"},
                remove=False,
                stdout=True,
                stderr=True,
            )
            status = container.wait(timeout=timeout)
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            elapsed = time.time() - start_time
            stdout_text = stdout_bytes.decode() if isinstance(stdout_bytes, bytes) else str(stdout_bytes)
            stderr_text = stderr_bytes.decode() if isinstance(stderr_bytes, bytes) else str(stderr_bytes)
            exit_code = status.get("StatusCode", 0) if isinstance(status, dict) else 0
            
            return ExecutionResult(
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=exit_code,
                execution_time=elapsed,
                error_type=None if exit_code == 0 else "cli_error",
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            return ExecutionResult(
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                execution_time=elapsed,
                error_type="execution_error",
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def _classify_error(self, stderr: str) -> str:
        for error_type, pattern in self.ERROR_PATTERNS.items():
            if pattern in stderr:
                return error_type
        return "unknown_error"

    def format_report(
        self, result: ExecutionResult, task_title: str, loop_count: int
    ) -> str:
        status = "정상" if result.exit_code == 0 else "비정상"
        stdout = result.stdout[:500] or "없음"
        stderr = result.stderr[:500] or "없음"
        return (
            "## 실행 결과 리포트\n"
            f"- Task: {task_title}\n"
            f"- 루프 횟수: {loop_count}회차\n"
            f"- 종료 코드: {result.exit_code} ({status})\n"
            f"- 실행 시간: {result.execution_time:.2f}초\n"
            f"- 에러 유형: {result.error_type or '없음'}\n"
            f"- 표준 출력: {stdout}\n"
            f"- 에러 로그: {stderr}"
        )

import os
import tempfile
import time
from dataclasses import dataclass

import importlib


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_type: str | None = None


class CriticRunner:
    SANDBOX_PATH = "/dev/shm/pyvis_sandbox"
    ERROR_PATTERNS = {
        "type_error": "TypeError",
        "syntax_error": "SyntaxError",
        "missing_import": "ModuleNotFoundError",
        "name_error": "NameError",
        "index_error": "IndexError",
        "key_error": "KeyError",
        "value_error": "ValueError",
        "attribute_error": "AttributeError",
    }

    def __init__(self) -> None:
        docker = importlib.import_module("docker")
        self.client = docker.from_env()
        os.makedirs(self.SANDBOX_PATH, exist_ok=True)

    async def execute(
        self, code: str, timeout: int = 30, allow_network: bool = False
    ) -> ExecutionResult:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=self.SANDBOX_PATH, delete=False
        ) as f:
            f.write(code)
            temp_file = f.name
        os.chmod(temp_file, 0o644)

        container = None
        start_time = time.time()
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                f"python /workspace/{os.path.basename(temp_file)}",
                volumes={self.SANDBOX_PATH: {"bind": "/workspace", "mode": "rw"}},
                network_mode="none" if not allow_network else "bridge",
                mem_limit="512m",
                cpu_quota=100000,
                detach=True,
                environment={"PYTHONUNBUFFERED": "1"},
                remove=False,
                stdout=True,
                stderr=True,
            )
            status = container.wait(timeout=timeout)
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            elapsed = time.time() - start_time
            stdout_text = stdout_bytes.decode() if isinstance(stdout_bytes, bytes) else str(stdout_bytes)
            stderr_text = stderr_bytes.decode() if isinstance(stderr_bytes, bytes) else str(stderr_bytes)
            exit_code = status.get("StatusCode", 0) if isinstance(status, dict) else 0
            if exit_code == 0:
                result = ExecutionResult(
                    stdout=stdout_text, stderr=stderr_text, exit_code=0, execution_time=elapsed
                )
            else:
                result = ExecutionResult(
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=exit_code,
                    execution_time=elapsed,
                    error_type=self._classify_error(stderr_text),
                )
            return result

        except Exception as exc:
            elapsed = time.time() - start_time
            stderr = getattr(exc, "stderr", None)
            if stderr is None:
                stderr_text = str(exc)
            else:
                stderr_text = stderr.decode() if hasattr(stderr, "decode") else str(stderr)
            return ExecutionResult(
                stdout="",
                stderr=stderr_text,
                exit_code=getattr(exc, "exit_status", -1),
                execution_time=elapsed,
                error_type=self._classify_error(stderr_text),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def _classify_error(self, stderr: str) -> str:
        for error_type, pattern in self.ERROR_PATTERNS.items():
            if pattern in stderr:
                return error_type
        return "unknown_error"

    def format_report(
        self, result: ExecutionResult, task_title: str, loop_count: int
    ) -> str:
        status = "정상" if result.exit_code == 0 else "비정상"
        stdout = result.stdout[:500] or "없음"
        stderr = result.stderr[:500] or "없음"
        return (
            "## 실행 결과 리포트\n"
            f"- Task: {task_title}\n"
            f"- 루프 횟수: {loop_count}회차\n"
            f"- 종료 코드: {result.exit_code} ({status})\n"
            f"- 실행 시간: {result.execution_time:.2f}초\n"
            f"- 에러 유형: {result.error_type or '없음'}\n"
            f"- 표준 출력: {stdout}\n"
            f"- 에러 로그: {stderr}"
        )
