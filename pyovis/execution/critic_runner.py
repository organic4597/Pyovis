from __future__ import annotations

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
