from __future__ import annotations

import ast
import asyncio
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, Set

import importlib

from pyovis.execution.execution_plan import ExecutionPlan, ExecutionType

logger = logging.getLogger(__name__)


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
    # Dockerfile에 이미 설치된 패키지 (설치 불필요)
    _PREINSTALLED: Set[str] = {
        "requests", "pydantic", "fastapi", "httpx",
        "numpy", "pillow", "PIL", "matplotlib", "pandas",
        "scipy", "pygame", "pytest", "colorama", "click", "rich",
    }
    # Headless Linux 환경에서 실행 불가한 패키지 (디스플레이 필수)
    _HEADLESS_INCOMPATIBLE: Set[str] = {
        "vpython", "OpenGL", "PyOpenGL", "wx", "wxPython",
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "kivy", "pyglet",
    }
    # import명 → 실제 pip 패키지명 매핑
    _IMPORT_TO_PIP: Dict[str, str] = {
        "OpenGL": "PyOpenGL",
        "cv2": "opencv-python-headless",
        "PIL": "Pillow",
        "sklearn": "scikit-learn",
        "yaml": "PyYAML",
        "Crypto": "pycryptodome",
        "bs4": "beautifulsoup4",
        "serial": "pyserial",
        "dotenv": "python-dotenv",
        "googleapiclient": "google-api-python-client",
        "jwt": "PyJWT",
        "telegram": "python-telegram-bot",
        "dateutil": "python-dateutil",
        "magic": "python-magic",
        "psycopg2": "psycopg2-binary",
        "fitz": "PyMuPDF",
        "attr": "attrs",
    }
    # Python 표준 라이브러리 최상위 모듈 (설치 불필요)
    _STDLIB: Set[str] = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
        "os", "sys", "re", "io", "abc", "ast", "json", "math", "time",
        "random", "string", "struct", "socket", "threading", "subprocess",
        "pathlib", "functools", "itertools", "collections", "contextlib",
        "dataclasses", "enum", "copy", "typing", "types", "weakref",
        "hashlib", "hmac", "base64", "uuid", "datetime", "calendar",
        "logging", "warnings", "traceback", "inspect", "importlib",
        "unittest", "tempfile", "shutil", "glob", "fnmatch", "stat",
        "csv", "configparser", "argparse", "textwrap", "html", "http",
        "urllib", "email", "xml", "zipfile", "tarfile", "gzip",
        "queue", "heapq", "bisect", "array", "mmap", "signal",
        "ctypes", "platform", "gc", "dis", "code", "codeop",
        "builtins", "__future__",
    }

    def __init__(self) -> None:
        docker = importlib.import_module("docker")
        self.client = docker.from_env()
        os.makedirs(self.SANDBOX_PATH, exist_ok=True)

    def _extract_third_party_imports(self, code: str) -> list[str]:
        """AST로 코드에서 import된 외부 패키지 목록을 추출합니다."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        top_level: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level.add(node.module.split(".")[0])

        # 표준 라이브러리 + 이미 설치된 패키지 제외
        third_party = [
            pkg for pkg in sorted(top_level)
            if pkg not in self._STDLIB and pkg not in self._PREINSTALLED
            and not pkg.startswith("_")
        ]
        return third_party
    def _cmds_to_pkg_names(self, setup_commands: list[str]) -> list[str]:
        """setup_commands (\'pip install X\' 형식) 리스트에서 패키지명만 추출합니다."""
        result: list[str] = []
        for cmd in setup_commands:
            # \'pip install pkg1 pkg2\' 형식 또는 \'pkg1 pkg2\' 등 다양한 형태 지원
            stripped = cmd.strip()
            if stripped.startswith("pip install "):
                stripped = stripped[len("pip install "):].strip()
            parts = stripped.split()
            result.extend(p for p in parts if p and not p.startswith("-"))
        return result

    def _check_headless_incompatible(
        self, source: str, setup_commands: list[str] | None
    ) -> str | None:
        """headless 환경에서 실행 불가한 패키지가 사용되면 해당 패키지명을 반환합니다."""
        # setup_commands에서 감지
        if setup_commands:
            pkg_names = self._cmds_to_pkg_names(setup_commands)
            for pkg in pkg_names:
                if pkg in self._HEADLESS_INCOMPATIBLE:
                    return pkg
        # import 소스에서도 감지
        for pkg in self._HEADLESS_INCOMPATIBLE:
            if f"import {pkg}" in source or f"from {pkg}" in source:
                return pkg
        return None


    def _install_dependencies_sync(self, packages: list[str], timeout: int = 60) -> None:
        """sandbox 콘테이너 안에서 pip install을 실행합니다."""
        if not packages:
            return
        pkg_list = " ".join(packages)
        cmd = f"pip install --quiet {pkg_list}"
        logger.info(f"프리세인스톨 패키지 설치 시작: {packages}")
        container = None
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                cmd,
                volumes={self.SANDBOX_PATH: {"bind": "/workspace", "mode": "rw"}},
                network_mode="bridge",  # pip install은 네트워크 필요
                mem_limit="512m",
                detach=True,
                remove=False,
                stdout=True,
                stderr=True,
            )
            status = container.wait(timeout=timeout)
            exit_code = status.get("StatusCode", 0) if isinstance(status, dict) else 0
            if exit_code != 0:
                stderr_bytes = container.logs(stdout=False, stderr=True)
                stderr_text = stderr_bytes.decode() if isinstance(stderr_bytes, bytes) else str(stderr_bytes)
                logger.warning(f"패키지 설치 실패 (exit={exit_code}): {stderr_text[:300]}")
            else:
                logger.info(f"패키지 설치 완료: {packages}")
        except Exception as e:
            logger.warning(f"패키지 설치 중 예외: {e}")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _execute_sync(
        self,
        code: str | dict[str, str],
        timeout: int = 30,
        allow_network: bool = False,
        setup_commands: list[str] | None = None,
    ) -> ExecutionResult:
        """Synchronous Docker execution — called via run_in_executor.

        code가 dict인 경우 다중 파일 모드: {file_path: source_code}
        code가 str인 경우 다단일 파일 모드.
        """
        written_files: list[str] = []
        temp_file: str | None = None

        if isinstance(code, dict):
            # ── 다중 파일 모드 ──────────────────────────────────────
            files = code
            # 로컬 모듈 이름 = dict 키에서 .py 제거
            local_modules: set[str] = {
                os.path.splitext(os.path.basename(fp))[0] for fp in files
            }
            # 전체 소스에서 외부 패키지 추출 (로컈 모듈 제외)
            all_source = "\n".join(files.values())
            # Headless 불가 패키지 사전 감지
            headless_conflict = self._check_headless_incompatible(all_source, setup_commands)
            if headless_conflict:
                return ExecutionResult(
                    stdout="",
                    stderr=f"requires_display: {headless_conflict} 패키지는 headless 환경에서 실행 불가합니다. headless 호환 대안을 사용하세요 (예: pygame, matplotlib, pillow).",
                    exit_code=1,
                    execution_time=0.0,
                    error_type="missing_import",
                )

            # setup_commands 우선 사용, 없으면 AST fallback
            if setup_commands:
                pkg_names = self._cmds_to_pkg_names(setup_commands)
                if pkg_names:
                    self._install_dependencies_sync(pkg_names)
            else:
                local_modules: set[str] = {
                    os.path.splitext(os.path.basename(fp))[0] for fp in files
                }
                third_party = [
                    pkg for pkg in self._extract_third_party_imports(all_source)
                    if pkg not in local_modules
                ]
                mapped = [self._IMPORT_TO_PIP.get(p, p) for p in third_party]
                if mapped:
                    self._install_dependencies_sync(mapped)

            # 각 파일을 SANDBOX_PATH에 저장
            for fp, src in files.items():
                fname = os.path.basename(fp)
                dest = os.path.join(self.SANDBOX_PATH, fname)
                with open(dest, "w") as fh:
                    fh.write(src)
                os.chmod(dest, 0o644)
                written_files.append(dest)

            # 진입점 결정: main.py > 첫 번째 파일
            entry_name = next(
                (os.path.basename(fp) for fp in files if os.path.basename(fp) == "main.py"),
                os.path.basename(list(files.keys())[0]),
            )
            run_cmd = f"python /workspace/{entry_name}"
        else:
            # ── 다단일 파일 모드 ──────────────────────────────────────
            # Headless 불가 패키지 사전 감지
            headless_conflict = self._check_headless_incompatible(code, setup_commands)
            if headless_conflict:
                return ExecutionResult(
                    stdout="",
                    stderr=f"requires_display: {headless_conflict} 패키지는 headless 환경에서 실행 불가합니다. headless 호환 대안을 사용하세요 (예: pygame, matplotlib, pillow).",
                    exit_code=1,
                    execution_time=0.0,
                    error_type="missing_import",
                )

            # setup_commands 우선 사용, 없으면 AST fallback
            if setup_commands:
                pkg_names = self._cmds_to_pkg_names(setup_commands)
                if pkg_names:
                    self._install_dependencies_sync(pkg_names)
            else:
                third_party = self._extract_third_party_imports(code)
                mapped = [self._IMPORT_TO_PIP.get(p, p) for p in third_party]
                if mapped:
                    self._install_dependencies_sync(mapped)

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", dir=self.SANDBOX_PATH, delete=False
            ) as f:
                f.write(code)
                temp_file = f.name
            os.chmod(temp_file, 0o644)
            run_cmd = f"python /workspace/{os.path.basename(temp_file)}"

        container = None
        start_time = time.time()
        try:
            container = self.client.containers.run(
                "pyvis-sandbox:latest",
                run_cmd,
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
                return ExecutionResult(
                    stdout=stdout_text, stderr=stderr_text, exit_code=0, execution_time=elapsed
                )
            else:
                return ExecutionResult(
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=exit_code,
                    execution_time=elapsed,
                    error_type=self._classify_error(stderr_text),
                )

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
            # 다단일 파일 모드: 임시 파일 삭제
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)
            # 다중 파일 모드: sandbox에 복사한 파일들 삭제
            for wf in written_files:
                try:
                    if os.path.exists(wf):
                        os.unlink(wf)
                except Exception:
                    pass

    async def execute(
        self,
        code: str | dict[str, str],
        timeout: int = 30,
        allow_network: bool = False,
        setup_commands: list[str] | None = None,
    ) -> ExecutionResult:
        """Execute code in Docker sandbox without blocking the event loop."""
        import functools
        loop = asyncio.get_event_loop()
        fn = functools.partial(
            self._execute_sync, code, timeout, allow_network, setup_commands
        )
        return await loop.run_in_executor(None, fn)

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

