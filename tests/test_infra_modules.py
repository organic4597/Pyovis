from __future__ import annotations

import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from dataclasses import asdict

from pyovis.mcp.tool_registry import ToolRegistry, ToolRecord
from pyovis.mcp.tool_installer import ToolInstaller, ToolInstallResult


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        reg.register("grep", "Search files", requires_approval=False)
        tool = reg.get("grep")
        assert tool is not None
        assert tool.name == "grep"
        assert tool.description == "Search files"
        assert tool.requires_approval is False

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register("a", "desc_a")
        reg.register("b", "desc_b")
        tools = reg.list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"a", "b"}

    def test_remove(self):
        reg = ToolRegistry()
        reg.register("temp", "temporary tool")
        reg.remove("temp")
        assert reg.get("temp") is None

    def test_remove_nonexistent_no_error(self):
        reg = ToolRegistry()
        reg.remove("ghost")

    def test_overwrite_on_duplicate_register(self):
        reg = ToolRegistry()
        reg.register("tool", "v1")
        reg.register("tool", "v2")
        assert reg.get("tool").description == "v2"

    def test_default_requires_approval(self):
        reg = ToolRegistry()
        reg.register("x", "desc")
        assert reg.get("x").requires_approval is True


# ---------------------------------------------------------------------------
# ToolInstaller
# ---------------------------------------------------------------------------

class TestToolInstaller:
    def test_requires_approval_blocks(self):
        installer = ToolInstaller(requires_approval=True)
        result = installer.install("npm", "npm install something")
        assert result.success is False
        assert "Approval required" in result.message

    def test_no_approval_installs(self):
        installer = ToolInstaller(requires_approval=False)
        result = installer.install("npm", "npm install something")
        assert result.success is True
        assert result.name == "npm"

    def test_default_requires_approval(self):
        installer = ToolInstaller()
        assert installer.requires_approval is True


# ---------------------------------------------------------------------------
# SkillValidator
# ---------------------------------------------------------------------------

class TestSkillValidator:
    def _make_record(self, task_id="task-1", fail_reasons=None):
        return {
            "task_id": task_id,
            "fail_reasons": [{"reason": r} for r in (fail_reasons or [])],
        }

    def _make_history(self, entries):
        """entries: list of (task_id, [reasons])"""
        return [self._make_record(tid, reasons) for tid, reasons in entries]

    def test_no_fail_reasons_returns_false(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(fail_reasons=[])
        assert v.should_add_skill(record, []) is False

    def test_not_fixable_error_returns_false(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(fail_reasons=["docker_error"])
        history = self._make_history([
            ("t2", ["docker_error"]),
            ("t3", ["docker_error"]),
            ("t4", ["docker_error"]),
        ])
        with patch.object(v, "_already_exists", return_value=False):
            assert v.should_add_skill(record, history) is False

    def test_insufficient_recurrence_returns_false(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(fail_reasons=["type_error"])
        history = self._make_history([
            ("t2", ["type_error"]),
        ])
        with patch.object(v, "_already_exists", return_value=False):
            assert v.should_add_skill(record, history) is False

    def test_all_conditions_met_returns_true(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(task_id="task-1", fail_reasons=["type_error"])
        history = self._make_history([
            ("task-2", ["type_error"]),
            ("task-3", ["type_error"]),
            ("task-4", ["type_error"]),
        ])
        with patch.object(v, "_already_exists", return_value=False):
            assert v.should_add_skill(record, history) is True

    def test_already_exists_returns_false(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(fail_reasons=["type_error"])
        history = self._make_history([
            ("t2", ["type_error"]),
            ("t3", ["type_error"]),
            ("t4", ["type_error"]),
        ])
        with patch.object(v, "_already_exists", return_value=True):
            assert v.should_add_skill(record, history) is False

    def test_generality_requires_3_unique_tasks(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(task_id="task-1", fail_reasons=["type_error"])
        history = self._make_history([
            ("task-2", ["type_error"]),
            ("task-3", ["type_error"]),
            ("task-4", ["type_error"]),
        ])
        with patch.object(v, "_already_exists", return_value=False):
            # task_ids_with_reason from history: {task-2, task-3, task-4} = 3 unique >= 3
            # other_task_count: 3 records with different task_id >= 2
            assert v.should_add_skill(record, history) is True

    def test_generality_fails_with_only_2_unique_tasks(self):
        from pyovis.skill.skill_validator import SkillValidator
        v = SkillValidator()
        record = self._make_record(task_id="task-1", fail_reasons=["type_error"])
        history = self._make_history([
            ("task-2", ["type_error"]),
            ("task-2", ["type_error"]),
            ("task-3", ["type_error"]),
        ])
        with patch.object(v, "_already_exists", return_value=False):
            # task_ids_with_reason from history: {task-2, task-3} = 2 unique < 3
            assert v.should_add_skill(record, history) is False


# ---------------------------------------------------------------------------
# LoopTracker
# ---------------------------------------------------------------------------

class TestLoopTracker:
    @patch("pyovis.tracking.loop_tracker.RECORDS_DIR", new_callable=lambda: MagicMock(spec=Path))
    def test_start_creates_record(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        from pyovis.tracking.loop_tracker import LoopTracker
        tracker = LoopTracker.__new__(LoopTracker)
        tracker._records = {}
        tracker._start_times = {}

        tracker.start("t1", "do something")

        assert "t1" in tracker._records
        assert tracker._records["t1"].task_description == "do something"
        assert "t1" in tracker._start_times

    @patch("pyovis.tracking.loop_tracker.RECORDS_DIR", new_callable=lambda: MagicMock(spec=Path))
    def test_record_switch_increments(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        from pyovis.tracking.loop_tracker import LoopTracker, LoopRecord
        tracker = LoopTracker.__new__(LoopTracker)
        tracker._records = {"t1": LoopRecord(task_id="t1", task_description="x")}
        tracker._start_times = {}

        tracker.record_switch("brain_to_hands", "t1")
        tracker.record_switch("hands_to_brain", "t1")

        assert tracker._records["t1"].switch_count == 2

    @patch("pyovis.tracking.loop_tracker.RECORDS_DIR", new_callable=lambda: MagicMock(spec=Path))
    def test_record_switch_no_task_id(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        from pyovis.tracking.loop_tracker import LoopTracker
        tracker = LoopTracker.__new__(LoopTracker)
        tracker._records = {}
        tracker._start_times = {}

        tracker.record_switch("brain_to_hands", None)
        tracker.record_switch("brain_to_hands")

    @patch("pyovis.tracking.loop_tracker.RECORDS_DIR", new_callable=lambda: MagicMock(spec=Path))
    def test_record_fail(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        from pyovis.tracking.loop_tracker import LoopTracker, LoopRecord
        tracker = LoopTracker.__new__(LoopTracker)
        tracker._records = {"t1": LoopRecord(task_id="t1", task_description="x")}
        tracker._start_times = {}

        tracker.record_fail("t1", "type_error")

        assert len(tracker._records["t1"].fail_reasons) == 1
        assert tracker._records["t1"].fail_reasons[0]["reason"] == "type_error"
        assert tracker._records["t1"].total_loops == 1

    @patch("pyovis.tracking.loop_tracker.RECORDS_DIR", new_callable=lambda: MagicMock(spec=Path))
    def test_get_record_returns_dict(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        from pyovis.tracking.loop_tracker import LoopTracker, LoopRecord
        tracker = LoopTracker.__new__(LoopTracker)
        tracker._records = {"t1": LoopRecord(task_id="t1", task_description="test")}
        tracker._start_times = {}

        result = tracker.get_record("t1")

        assert isinstance(result, dict)
        assert result["task_id"] == "t1"

    @patch("pyovis.tracking.loop_tracker.RECORDS_DIR", new_callable=lambda: MagicMock(spec=Path))
    def test_get_record_missing_returns_empty(self, mock_dir):
        mock_dir.mkdir = MagicMock()
        from pyovis.tracking.loop_tracker import LoopTracker
        tracker = LoopTracker.__new__(LoopTracker)
        tracker._records = {}
        tracker._start_times = {}

        assert tracker.get_record("nope") == {}


# ---------------------------------------------------------------------------
# CriticRunner._classify_error
# ---------------------------------------------------------------------------

class TestCriticRunnerClassifyError:
    def _get_classifier(self):
        from pyovis.execution.critic_runner import CriticRunner
        runner = CriticRunner.__new__(CriticRunner)
        return runner._classify_error

    def test_type_error(self):
        classify = self._get_classifier()
        assert classify("TypeError: unsupported operand") == "type_error"

    def test_syntax_error(self):
        classify = self._get_classifier()
        assert classify("SyntaxError: invalid syntax") == "syntax_error"

    def test_module_not_found(self):
        classify = self._get_classifier()
        assert classify("ModuleNotFoundError: No module named 'foo'") == "missing_import"

    def test_name_error(self):
        classify = self._get_classifier()
        assert classify("NameError: name 'x' is not defined") == "name_error"

    def test_index_error(self):
        classify = self._get_classifier()
        assert classify("IndexError: list index out of range") == "index_error"

    def test_key_error(self):
        classify = self._get_classifier()
        assert classify("KeyError: 'missing'") == "key_error"

    def test_value_error(self):
        classify = self._get_classifier()
        assert classify("ValueError: invalid literal") == "value_error"

    def test_attribute_error(self):
        classify = self._get_classifier()
        assert classify("AttributeError: 'NoneType'") == "attribute_error"

    def test_unknown_error(self):
        classify = self._get_classifier()
        assert classify("Something weird happened") == "unknown_error"

    def test_empty_stderr(self):
        classify = self._get_classifier()
        assert classify("") == "unknown_error"

    def test_first_match_wins(self):
        classify = self._get_classifier()
        result = classify("TypeError: foo\nSyntaxError: bar")
        assert result == "type_error"


# ---------------------------------------------------------------------------
# CriticRunner.format_report
# ---------------------------------------------------------------------------

class TestCriticRunnerFormatReport:
    def test_format_report_success(self):
        from pyovis.execution.critic_runner import CriticRunner, ExecutionResult
        runner = CriticRunner.__new__(CriticRunner)
        result = ExecutionResult(
            stdout="hello", stderr="", exit_code=0, execution_time=1.23
        )
        report = runner.format_report(result, "Task 1", 2)
        assert "Task 1" in report
        assert "2" in report
        assert "1.23" in report

    def test_format_report_failure(self):
        from pyovis.execution.critic_runner import CriticRunner, ExecutionResult
        runner = CriticRunner.__new__(CriticRunner)
        result = ExecutionResult(
            stdout="", stderr="TypeError", exit_code=1,
            execution_time=0.5, error_type="type_error"
        )
        report = runner.format_report(result, "Task X", 3)
        assert "type_error" in report


# ---------------------------------------------------------------------------
# SkillManager
# ---------------------------------------------------------------------------

class TestSkillManager:
    def test_load_verified_no_skills(self):
        from pyovis.skill.skill_manager import SkillManager

        with patch("pyovis.skill.skill_manager.VERIFIED_DIR") as mock_dir:
            mock_dir.exists.return_value = False
            mgr = SkillManager()
            result = mgr.load_verified("build a web app")
            assert result == "# 적용 가능한 Skill 없음"

    def test_extract_keywords(self):
        from pyovis.skill.skill_manager import SkillManager
        mgr = SkillManager()
        content = "category: type_error_handling\nname: fix types\nother: stuff"
        kws = mgr._extract_keywords(content)
        assert "type" in kws
        assert "error" in kws
        assert "handling" in kws
        assert "fix" in kws
        assert "types" in kws

    @pytest.mark.asyncio
    async def test_evaluate_and_patch_no_skill_needed(self):
        from pyovis.skill.skill_manager import SkillManager
        mgr = SkillManager()

        with patch("pyovis.skill.skill_manager.SkillValidator") as MockValidator:
            mock_v = MockValidator.return_value
            mock_v.should_add_skill.return_value = False

            ctx = MagicMock()
            ctx.task_id = "t1"

            await mgr.evaluate_and_patch(ctx, {"task_id": "t1", "fail_reasons": []})

    @pytest.mark.asyncio
    async def test_evaluate_and_patch_creates_candidate(self):
        from pyovis.skill.skill_manager import SkillManager
        mgr = SkillManager()

        with patch("pyovis.skill.skill_manager.SkillValidator") as MockValidator, \
             patch.object(mgr, "_get_history", return_value=[]), \
             patch.object(mgr, "_create_candidate", new_callable=AsyncMock) as mock_create:
            mock_v = MockValidator.return_value
            mock_v.should_add_skill.return_value = True

            ctx = MagicMock()
            loop_record = {"task_id": "t1", "fail_reasons": []}

            await mgr.evaluate_and_patch(ctx, loop_record)
            mock_create.assert_awaited_once_with(loop_record)
