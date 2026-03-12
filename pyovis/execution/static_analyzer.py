"""
Pyvis v5.1 — Static Code Analyzer

Pre-execution linting and type checking to catch errors before Docker execution.
Reduces loop iterations by catching syntax/type errors early.

Usage:
    analyzer = StaticAnalyzer()
    result = await analyzer.lint(code, "output.py")
    if not result.success:
        # Fix errors before Docker execution
"""

from __future__ import annotations

import subprocess
import logging
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)


@dataclass
class LintError:
    """Single lint error."""

    line: int
    column: int
    message: str
    code: str  # Error code (e.g., "E402")
    severity: str  # "error" or "warning"


@dataclass
class LintResult:
    """Result of static analysis."""

    success: bool
    errors: List[LintError]
    warnings: List[LintError]
    fixed_code: Optional[str] = None

    def to_error_message(self) -> str:
        """Format errors as a message."""
        if not self.errors:
            return ""

        lines = ["Static analysis failed:"]
        for err in self.errors[:10]:  # Limit to first 10
            lines.append(f"  Line {err.line}:{err.column} [{err.code}]: {err.message}")

        if len(self.errors) > 10:
            lines.append(f"  ... and {len(self.errors) - 10} more errors")

        return "\n".join(lines)


class StaticAnalyzer:
    """
    Static code analyzer using ruff and mypy.

    Features:
    - Syntax error detection (ruff)
    - Type checking (mypy)
    - Auto-fix support (ruff --fix)
    - Fast execution (< 5s typical)
    """

    def __init__(self, tools: Optional[List[str]] = None) -> None:
        """
        Initialize analyzer.

        Args:
            tools: List of tools to use. Default: ["ruff", "mypy"]
        """
        self.tools = tools or ["ruff", "mypy"]
        self._ruff_available: Optional[bool] = None
        self._mypy_available: Optional[bool] = None

    def _check_tool_available(self, tool: str) -> bool:
        """Check if a tool is installed."""
        try:
            result = subprocess.run([tool, "--version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    async def lint(
        self, code: str, file_path: str = "output.py", auto_fix: bool = False
    ) -> LintResult:
        """
        Run static analysis on code.

        Args:
            code: Python code to analyze
            file_path: Path for error reporting
            auto_fix: Whether to attempt auto-fix (ruff --fix)

        Returns:
            LintResult with errors and warnings
        """
        errors = []
        warnings = []
        fixed_code = None

        # Create temporary file for analysis
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            # Run ruff
            if "ruff" in self.tools:
                ruff_errors, ruff_warnings, fixed_code = await self._run_ruff(
                    temp_path, auto_fix
                )
                errors.extend(ruff_errors)
                warnings.extend(ruff_warnings)

            # Run mypy
            if "mypy" in self.tools:
                mypy_errors, mypy_warnings = await self._run_mypy(temp_path)
                errors.extend(mypy_errors)
                warnings.extend(mypy_warnings)

            return LintResult(
                success=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                fixed_code=fixed_code,
            )

        finally:
            # Clean up temp file
            Path(temp_path).unlink(missing_ok=True)

    async def _run_ruff(
        self, file_path: str, auto_fix: bool
    ) -> tuple[List[LintError], List[LintError], Optional[str]]:
        """Run ruff linter."""
        errors = []
        warnings = []

        # Check if ruff is available
        if self._ruff_available is None:
            self._ruff_available = self._check_tool_available("ruff")

        if not self._ruff_available:
            logger.debug("Ruff not available, skipping")
            return errors, warnings, None

        try:
            # Run ruff check
            cmd = ["ruff", "check", "--output-format=json", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.stdout:
                import json

                try:
                    ruff_results = json.loads(result.stdout)
                    for violation in ruff_results:
                        lint_error = LintError(
                            line=violation.get("location", {}).get("row", 0),
                            column=violation.get("location", {}).get("column", 0),
                            message=violation.get("message", ""),
                            code=violation.get("code", ""),
                            severity="error",
                        )
                        errors.append(lint_error)
                except json.JSONDecodeError:
                    # Fallback to plain text parsing
                    for line in result.stdout.split("\n"):
                        if ":" in line:
                            parts = line.split(":", 3)
                            if len(parts) >= 4:
                                try:
                                    lint_error = LintError(
                                        line=int(parts[1].strip()),
                                        column=int(parts[2].strip()),
                                        message=parts[3].strip(),
                                        code=parts[0].strip(),
                                        severity="error",
                                    )
                                    errors.append(lint_error)
                                except (ValueError, IndexError):
                                    pass

            # Auto-fix if requested
            fixed_code = None
            if auto_fix and errors:
                fix_result = subprocess.run(
                    ["ruff", "check", "--fix", file_path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if fix_result.returncode == 0:
                    with open(file_path, "r") as f:
                        fixed_code = f.read()

        except subprocess.TimeoutExpired:
            logger.warning("Ruff timed out")
        except Exception as e:
            logger.debug(f"Ruff error: {e}")

        return errors, warnings, fixed_code

    async def _run_mypy(
        self, file_path: str
    ) -> tuple[List[LintError], List[LintError]]:
        """Run mypy type checker."""
        errors = []
        warnings = []

        # Check if mypy is available
        if self._mypy_available is None:
            self._mypy_available = self._check_tool_available("mypy")

        if not self._mypy_available:
            logger.debug("Mypy not available, skipping")
            return errors, warnings

        try:
            # Run mypy
            cmd = ["mypy", "--ignore-missing-imports", "--no-error-summary", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.stdout:
                # Parse mypy output (format: file:line:col: type: message)
                for line in result.stdout.split("\n"):
                    if ":" in line and " error:" in line:
                        parts = line.split(":", 4)
                        if len(parts) >= 4:
                            try:
                                message = (
                                    parts[4].strip() if len(parts) > 4 else "Type error"
                                )
                                lint_error = LintError(
                                    line=int(parts[1].strip())
                                    if parts[1].strip().isdigit()
                                    else 0,
                                    column=0,  # mypy doesn't always provide column
                                    message=message,
                                    code="type",
                                    severity="error",
                                )
                                errors.append(lint_error)
                            except (ValueError, IndexError):
                                pass

        except subprocess.TimeoutExpired:
            logger.warning("Mypy timed out")
        except Exception as e:
            logger.debug(f"Mypy error: {e}")

        return errors, warnings


# Convenience function for quick linting
async def lint_code(code: str, file_path: str = "output.py") -> LintResult:
    """Quick linting with default settings."""
    analyzer = StaticAnalyzer()
    return await analyzer.lint(code, file_path)


# Global analyzer instance
_analyzer: Optional[StaticAnalyzer] = None


def get_analyzer() -> StaticAnalyzer:
    """Get global analyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = StaticAnalyzer()
    return _analyzer
