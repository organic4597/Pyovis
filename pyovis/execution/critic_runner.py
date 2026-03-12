from __future__ import annotations

import ast
import asyncio
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set

from pyovis.execution.file_writer import WorkspaceManager
from pyovis.execution.execution_plan import ExecutionType


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    error_type: str | None = None


class CriticRunner:
    ERROR_PATTERNS: list[tuple[str, str]] = [
        ("type_error", "TypeError"),
        ("syntax_error", "SyntaxError"),
        ("missing_import", "ModuleNotFoundError"),
        ("name_error", "NameError"),
        ("index_error", "IndexError"),
        ("key_error", "KeyError"),
        ("value_error", "ValueError"),
        ("attribute_error", "AttributeError"),
        ("network_error", "ConnectionRefusedError"),
        ("network_error", "NetworkError"),
        ("network_error", "network is unreachable"),
        ("network_error", "Name or service not known"),
        ("network_error", "Connection refused"),
        ("install_error", "Could not find a version"),
        ("install_error", "No matching distribution"),
        ("install_error", "No module named"),
        ("env_error", "address already in use"),
        ("env_error", "No space left on device"),
        ("env_error", "Permission denied"),
    ]

    _PREINSTALLED: Set[str] = {
        "requests",
        "pydantic",
        "fastapi",
        "httpx",
        "numpy",
        "pillow",
        "PIL",
        "matplotlib",
        "pandas",
        "scipy",
        "pygame",
        "pytest",
        "colorama",
        "click",
        "rich",
        "OpenGL",
        "PyOpenGL",
    }

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
        "flask": "Flask",
        "lxml": "lxml",
        "gi": "PyGObject",
        "wx": "wxPython",
        "skimage": "scikit-image",
        "usb": "pyusb",
        "websocket": "websocket-client",
    }

    _STDLIB: Set[str] = (
        set(sys.stdlib_module_names)
        if hasattr(sys, "stdlib_module_names")
        else {
            "os",
            "sys",
            "re",
            "io",
            "abc",
            "ast",
            "json",
            "math",
            "time",
            "random",
            "string",
            "struct",
            "socket",
            "threading",
            "subprocess",
            "pathlib",
            "functools",
            "itertools",
            "collections",
            "contextlib",
            "dataclasses",
            "enum",
            "copy",
            "typing",
            "types",
            "weakref",
            "hashlib",
            "hmac",
            "base64",
            "uuid",
            "datetime",
            "calendar",
            "logging",
            "warnings",
            "traceback",
            "inspect",
            "importlib",
            "unittest",
            "tempfile",
            "shutil",
            "glob",
            "fnmatch",
            "stat",
            "csv",
            "configparser",
            "argparse",
            "textwrap",
            "html",
            "http",
            "urllib",
            "email",
            "xml",
            "zipfile",
            "tarfile",
            "gzip",
            "queue",
            "heapq",
            "bisect",
            "array",
            "mmap",
            "signal",
            "ctypes",
            "platform",
            "gc",
            "dis",
            "code",
            "codeop",
            "builtins",
            "__future__",
        }
    )

    def __init__(self, workspace: WorkspaceManager | None = None) -> None:
        self.workspace = workspace

    def _require_workspace(
        self, workspace: WorkspaceManager | None = None
    ) -> WorkspaceManager:
        effective_workspace = workspace or self.workspace
        if effective_workspace is None:
            raise RuntimeError("CriticRunner requires a WorkspaceManager")
        return effective_workspace

    def _extract_third_party_imports(self, code: str) -> list[str]:
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

        return [
            pkg
            for pkg in sorted(top_level)
            if pkg not in self._STDLIB
            and pkg not in self._PREINSTALLED
            and not pkg.startswith("_")
        ]

    def _cmds_to_pkg_names(self, setup_commands: list[str]) -> list[str]:
        result: list[str] = []
        for cmd in setup_commands:
            stripped = cmd.strip()
            if stripped.startswith("pip install "):
                stripped = stripped[len("pip install ") :].strip()
            parts = stripped.split()
            result.extend(p for p in parts if p and not p.startswith("-"))
        return result

    def _ensure_venv(
        self, workspace: WorkspaceManager | None = None
    ) -> tuple[WorkspaceManager, Path, Path]:
        workspace = self._require_workspace(workspace)
        workspace.create_project()
        workspace.mark_incomplete()
        workspace.create_venv()
        python_bin = workspace.get_venv_python()
        pip_bin = workspace.get_venv_pip()
        if not python_bin.exists() or not pip_bin.exists():
            raise RuntimeError(f"venv 생성 실패: {workspace.get_venv_path()}")
        return workspace, python_bin, pip_bin

    def _workspace_env(
        self, allow_network: bool, workspace: WorkspaceManager | None = None
    ) -> dict[str, str]:
        workspace = self._require_workspace(workspace)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYOVIS_ALLOW_NETWORK"] = "1" if allow_network else "0"
        env["VIRTUAL_ENV"] = str(workspace.get_venv_path())
        env["PATH"] = (
            f"{workspace.get_venv_pip().parent}{os.pathsep}{env.get('PATH', '')}"
        )
        return env

    def _install_dependencies_sync(
        self,
        packages: list[str],
        timeout: int = 120,
        workspace: WorkspaceManager | None = None,
    ) -> None:
        if not packages:
            return

        workspace = self._require_workspace(workspace)
        _, _, pip_bin = self._ensure_venv(workspace)
        cmd = [str(pip_bin), "install", "--quiet", *packages]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace.project_root,
            env=self._workspace_env(allow_network=True, workspace=workspace),
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(stderr or f"pip install failed: {' '.join(packages)}")

    def _prepare_workspace_files(
        self,
        code: str | dict[str, str],
        setup_commands: list[str] | None,
        workspace: WorkspaceManager | None = None,
    ) -> tuple[Path, list[Path]]:
        workspace = self._require_workspace(workspace)
        files = code if isinstance(code, dict) else {"main.py": code}
        all_source = "\n".join(files.values())

        if setup_commands:
            pkg_names = self._cmds_to_pkg_names(setup_commands)
            if pkg_names:
                self._install_dependencies_sync(pkg_names, workspace=workspace)
        else:
            local_modules = {Path(fp).stem for fp in files}
            third_party = [
                pkg
                for pkg in self._extract_third_party_imports(all_source)
                if pkg not in local_modules
            ]
            mapped = [self._IMPORT_TO_PIP.get(p, p) for p in third_party]
            if mapped:
                self._install_dependencies_sync(mapped, workspace=workspace)

        written_paths: list[Path] = []
        for fp, src in files.items():
            full_path = workspace.write_file(fp, src)
            written_paths.append(full_path)

        entry_name = None
        for fp in files:
            if Path(fp).name == "main.py":
                entry_name = fp
                break
        if entry_name is None:
            for fp in files:
                if Path(fp).name == "app.py":
                    entry_name = fp
                    break
        if entry_name is None:
            entry_name = next(iter(files.keys()))

        return workspace.get_file_path(entry_name), written_paths

    def _run_subprocess(
        self,
        cmd: list[str],
        timeout: int,
        allow_network: bool,
        cwd: Path | None = None,
        workspace: WorkspaceManager | None = None,
    ) -> ExecutionResult:
        workspace = self._require_workspace(workspace)
        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd or workspace.project_root,
                env=self._workspace_env(
                    allow_network=allow_network, workspace=workspace
                ),
            )
            elapsed = time.time() - start_time
            stderr = proc.stderr or ""
            return ExecutionResult(
                stdout=proc.stdout or "",
                stderr=stderr,
                exit_code=proc.returncode,
                execution_time=elapsed,
                error_type=None
                if proc.returncode == 0
                else self._classify_error(stderr),
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.time() - start_time
            stderr = exc.stderr or exc.stdout or ""
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            return ExecutionResult(
                stdout="",
                stderr=f"timeout_error: 코드 실행이 {timeout}초를 초과했습니다. {stderr}".strip(),
                exit_code=-1,
                execution_time=elapsed,
                error_type="timeout_error",
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            stderr = str(exc)
            return ExecutionResult(
                stdout="",
                stderr=stderr,
                exit_code=-1,
                execution_time=elapsed,
                error_type=self._classify_error(stderr),
            )

    def _execute_sync(
        self,
        code: str | dict[str, str],
        timeout: int = 30,
        allow_network: bool = False,
        setup_commands: list[str] | None = None,
        workspace: WorkspaceManager | None = None,
    ) -> ExecutionResult:
        workspace = self._require_workspace(workspace)
        _, python_bin, _ = self._ensure_venv(workspace)
        entry_path, _written = self._prepare_workspace_files(
            code, setup_commands, workspace
        )
        return self._run_subprocess(
            [str(python_bin), str(entry_path)],
            timeout=timeout,
            allow_network=allow_network,
            workspace=workspace,
        )

    async def execute(
        self,
        code: str | dict[str, str],
        timeout: int = 30,
        allow_network: bool = False,
        setup_commands: list[str] | None = None,
        workspace: WorkspaceManager | None = None,
    ) -> ExecutionResult:
        import functools

        loop = asyncio.get_event_loop()
        fn = functools.partial(
            self._execute_sync, code, timeout, allow_network, setup_commands, workspace
        )
        return await loop.run_in_executor(None, fn)

    async def execute_with_plan(
        self,
        code: str,
        execution_plan: Dict[str, Any],
        timeout: int = 30,
    ) -> ExecutionResult:
        setup_commands = execution_plan.get("setup_commands", [])
        exec_type = execution_plan.get("execution_type", "python_script")

        if exec_type == ExecutionType.PYTHON_TEST.value:
            return await self._run_pytest(code, execution_plan, timeout, setup_commands)
        if exec_type == ExecutionType.API_SERVER.value:
            return await self._run_api_test(
                code, execution_plan, timeout, setup_commands
            )
        if exec_type == ExecutionType.CLI_COMMAND.value:
            return await self._run_cli(code, execution_plan, timeout, setup_commands)

        allow_network = execution_plan.get("requires_network", False)
        return await self.execute(code, timeout, allow_network, setup_commands)

    async def _run_setup_command(self, command: str) -> None:
        packages = self._cmds_to_pkg_names([command])
        if packages:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._install_dependencies_sync, packages)

    async def _run_pytest(
        self,
        code: str,
        plan: Dict[str, Any],
        timeout: int,
        setup_commands: list[str] | None = None,
    ) -> ExecutionResult:
        workspace, python_bin, _ = self._ensure_venv()
        test_file = plan.get("entry_point", "test_generated.py")
        if not Path(test_file).name.startswith("test_"):
            path = Path(test_file)
            test_file = str(path.with_name(f"test_{path.name}"))
        workspace.write_file(test_file, code)
        if setup_commands:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._install_dependencies_sync,
                self._cmds_to_pkg_names(setup_commands),
                120,
                workspace,
            )
        return self._run_subprocess(
            [str(python_bin), "-m", "pytest", test_file, "-v"],
            timeout=timeout,
            allow_network=False,
            workspace=workspace,
        )

    async def _run_api_test(
        self,
        code: str,
        plan: Dict[str, Any],
        timeout: int,
        setup_commands: list[str] | None = None,
    ) -> ExecutionResult:
        allow_network = plan.get("requires_network", True)
        return await self.execute(code, timeout, allow_network, setup_commands)

    async def _run_cli(
        self,
        code: str,
        plan: Dict[str, Any],
        timeout: int,
        setup_commands: list[str] | None = None,
    ) -> ExecutionResult:
        workspace, python_bin, _ = self._ensure_venv()
        entry_point = plan.get("entry_point", "main.py")
        workspace.write_file(entry_point, code)
        if setup_commands:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._install_dependencies_sync,
                self._cmds_to_pkg_names(setup_commands),
                120,
                workspace,
            )
        args = [str(arg) for arg in plan.get("command_args", [])]
        return self._run_subprocess(
            [str(python_bin), str(workspace.get_file_path(entry_point)), *args],
            timeout=timeout,
            allow_network=False,
            workspace=workspace,
        )

    def _classify_error(self, stderr: str) -> str:
        for error_type, pattern in self.ERROR_PATTERNS:
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
