"""
Tests for Pyvis v5.1 Enhanced Judge

Tests cover:
- Thought Instruction 4-step checklist
- CheckResult parsing
- Execution plan validation
- Fallback parsing for malformed responses
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from pyovis.ai.judge_enhanced import (
    EnhancedJudge,
    JudgeResult,
    CheckResult,
)


class TestCheckResult:
    """Test CheckResult dataclass"""

    def test_check_result_creation(self):
        cr = CheckResult(
            check_name="exit_code",
            passed=True,
            details="Exit code is 0",
            evidence="stdout: OK",
        )

        assert cr.check_name == "exit_code"
        assert cr.passed is True
        assert cr.details == "Exit code is 0"
        assert cr.evidence == "stdout: OK"

    def test_check_result_to_dict(self):
        cr = CheckResult(check_name="criteria", passed=False, details="NOT_SATISFIED")

        result_dict = cr.to_dict()
        assert result_dict["check_name"] == "criteria"
        assert result_dict["passed"] is False
        assert result_dict["details"] == "NOT_SATISFIED"


class TestJudgeResult:
    """Test JudgeResult dataclass"""

    def test_judge_result_creation(self):
        result = JudgeResult(
            verdict="PASS", score=95, reason="All criteria satisfied", error_type=None
        )

        assert result.verdict == "PASS"
        assert result.score == 95
        assert result.reason == "All criteria satisfied"
        assert result.error_type is None

    def test_judge_result_with_check_results(self):
        check_results = {
            "exit_code": CheckResult("exit_code", True, "Exit code 0"),
            "criteria_0": CheckResult("criterion_0", True, "SATISFIED"),
        }

        result = JudgeResult(
            verdict="PASS",
            score=95,
            reason="All good",
            error_type=None,
            check_results=check_results,
            thought_process="Step 1: Check exit code...",
            execution_plan_validated=True,
        )

        assert "exit_code" in result.check_results
        assert result.check_results["exit_code"].passed is True
        assert result.thought_process == "Step 1: Check exit code..."
        assert result.execution_plan_validated is True

    def test_judge_result_to_dict(self):
        result = JudgeResult(
            verdict="PASS",
            score=90,
            reason="Success",
            error_type=None,
            check_results={"test": CheckResult("test", True, "ok")},
            thought_process="Analysis complete",
            execution_plan_validated=True,
        )

        result_dict = result.to_dict()
        assert result_dict["verdict"] == "PASS"
        assert result_dict["score"] == 90
        assert result_dict["thought_process"] == "Analysis complete"
        assert result_dict["execution_plan_validated"] is True
        assert "check_results" in result_dict


class TestEnhancedJudgeParsing:
    """Test EnhancedJudge parsing logic"""

    def test_parse_enhanced_with_thought_process(self):
        judge = EnhancedJudge(MagicMock())

        response = """[CHECK 1] Exit code is 0 ✓
[CHECK 2] Criteria all satisfied ✓
[CHECK 3] No missing symbols ✓
[CHECK 4] No errors ✓

{"verdict": "PASS", "score": 95, "reason": "All criteria met", "error_type": null,
"check_results": {"exit_code_ok": true, "criteria_results": [{"criterion": "test", "result": "SATISFIED"}], "missing_symbols": [], "error_type": null}}"""

        result = judge._parse_enhanced(response)

        assert result.verdict == "PASS"
        assert result.score == 95
        assert "[CHECK 1]" in result.thought_process

    def test_parse_enhanced_with_check_results(self):
        judge = EnhancedJudge(MagicMock())

        response = """{"verdict": "PASS", "score": 90, "reason": "Good", "error_type": null,
        "check_results": {"exit_code_ok": true, "criteria_results": [{"criterion": "HTTP 200", "result": "SATISFIED"}, {"criterion": "None handling", "result": "SATISFIED"}], "missing_symbols": [], "error_type": null}}"""

        result = judge._parse_enhanced(response)

        assert result.verdict == "PASS"
        assert result.score == 90
        assert "exit_code" in result.check_results
        assert result.check_results["exit_code"].passed is True

    def test_parse_enhanced_missing_symbols(self):
        judge = EnhancedJudge(MagicMock())

        response = """{"verdict": "REVISE", "score": 75, "reason": "Missing imports", "error_type": "missing_import",
        "check_results": {"exit_code_ok": false, "criteria_results": [], "missing_symbols": ["requests", "json"], "error_type": "missing_import"}}"""

        result = judge._parse_enhanced(response)

        assert result.verdict == "REVISE"
        assert result.error_type == "missing_import"
        assert "missing_symbols" in result.check_results
        assert result.check_results["missing_symbols"].passed is False

    def test_parse_fallback_malformed_response(self):
        judge = EnhancedJudge(MagicMock())

        response = 'verdict: PASS score: 90 reason: "Good"'

        result = judge._parse_fallback(response, "JSON parse error")

        assert result.verdict in ["PASS", "ESCALATE"]
        assert result.reason is not None
        assert result.score >= 0

    def test_parse_fallback_valid_json_in_messy_response(self):
        judge = EnhancedJudge(MagicMock())

        response = 'Some random text before {"verdict": "PASS", "score": 85, "reason": "OK", "error_type": null} and after'

        result = judge._parse_fallback(response, "error")

        assert result.verdict == "PASS"
        assert result.score == 85


class TestBuildUserMessage:
    """Test user message building"""

    def test_build_user_message_basic(self):
        judge = EnhancedJudge(MagicMock())

        task = {"id": 1, "title": "Test Task"}
        criteria = ["HTTP 200", "Handle None"]
        critic_result = {
            "exit_code": 0,
            "execution_time": 1.5,
            "stdout": "OK",
            "stderr": "",
        }

        message = judge._build_user_message(
            task=task,
            criteria=criteria,
            critic_result=critic_result,
            loop_count=1,
            execution_plan=None,
        )

        assert "Test Task" in message
        assert "HTTP 200" in message
        assert "종료 코드: 0" in message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
