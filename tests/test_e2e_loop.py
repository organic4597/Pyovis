"""
Pyovis v4.0 — E2E Loop Integration Tests

Tests the full ResearchLoopController state machine:
  PLAN → BUILD → CRITIQUE → EVALUATE → (PASS|REVISE|ESCALATE) → COMPLETE
All external dependencies (LLM, Docker, filesystem) are mocked.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from pyovis.orchestration.loop_controller import (
    LoopContext,
    LoopStep,
    JudgeVerdict,
    ResearchLoopController,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(**overrides) -> LoopContext:
    defaults = dict(task_id="test-001", task_description="Build a hello world script")
    defaults.update(overrides)
    return LoopContext(**defaults)


def _plan_output():
    return {
        "plan": "# Plan\nStep 1: write code",
        "todo_list": [
            {"id": 1, "title": "Write script", "description": "hello world"},
        ],
        "pass_criteria": {"1": ["exit_code == 0", "stdout contains hello"]},
        "self_fix_scope": {
            "allowed": ["type_error", "syntax_error"],
            "escalate": ["architecture_change"],
        },
    }


def _plan_output_multi():
    """Plan with 2 tasks."""
    return {
        "plan": "# Plan\nTwo tasks",
        "todo_list": [
            {"id": 1, "title": "Task A", "description": "first"},
            {"id": 2, "title": "Task B", "description": "second"},
        ],
        "pass_criteria": {"1": ["ok"], "2": ["ok"]},
        "self_fix_scope": {"allowed": ["type_error"], "escalate": []},
    }


@dataclass
class FakeExecResult:
    stdout: str = "hello"
    stderr: str = ""
    exit_code: int = 0
    execution_time: float = 0.5
    error_type: str | None = None


@dataclass
class FakeJudgeResult:
    verdict: str = "PASS"
    score: int = 90
    reason: str = "Good"
    error_type: str | None = None


def _build_controller(
    plan_output=None,
    build_code="print('hello')",
    exec_result=None,
    judge_result=None,
    escalation_result=None,
    final_review=None,
    use_planner=False,
):
    """Build a ResearchLoopController with fully mocked dependencies."""
    brain = AsyncMock()
    brain.plan = AsyncMock(return_value=(plan_output or _plan_output(), ""))
    brain.handle_escalation = AsyncMock(
        return_value=(escalation_result or {"action": "human_escalation"}, "")
    )
    brain.final_review = AsyncMock(
        return_value=(final_review or {"status": "complete", "review": "LGTM"}, "")
    )

    hands = AsyncMock()
    hands.build = AsyncMock(return_value=(build_code, ""))
    hands.revise = AsyncMock(return_value=("print('fixed')", ""))

    judge = AsyncMock()
    judge.evaluate = AsyncMock(
        return_value=judge_result or FakeJudgeResult()
    )

    critic = AsyncMock()
    critic.execute = AsyncMock(
        return_value=exec_result or FakeExecResult()
    )

    tracker = MagicMock()
    tracker.start = MagicMock()
    tracker.record_switch = MagicMock()
    tracker.finish = MagicMock()
    tracker.get_record = MagicMock(return_value={"task_id": "test-001", "fail_reasons": []})

    skill_manager = MagicMock()
    skill_manager.load_verified = MagicMock(return_value="# No skill")
    skill_manager.evaluate_and_patch = AsyncMock()

    planner = None
    if use_planner:
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=(plan_output or _plan_output(), ""))

    controller = ResearchLoopController(
        brain=brain,
        hands=hands,
        judge=judge,
        critic=critic,
        tracker=tracker,
        skill_manager=skill_manager,
        planner=planner,
    )
    return controller, brain, hands, judge, critic, tracker, skill_manager, planner


# ---------------------------------------------------------------------------
# Tests — Happy Path
# ---------------------------------------------------------------------------

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_task_pass(self):
        """PLAN → BUILD → CRITIQUE → EVALUATE(PASS) → COMPLETE."""
        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller()
        ctx = _make_ctx()

        result = await ctrl.run(ctx)

        assert result["status"] == "complete"
        brain.plan.assert_awaited_once()
        hands.build.assert_awaited_once()
        critic.execute.assert_awaited_once()
        judge.evaluate.assert_awaited_once()
        brain.final_review.assert_awaited_once()
        tracker.start.assert_called_once_with("test-001", ctx.task_description)
        tracker.finish.assert_called_once()
        sm.evaluate_and_patch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multi_task_all_pass(self):
        """Two tasks, both PASS on first try."""
        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller(
            plan_output=_plan_output_multi()
        )
        ctx = _make_ctx()

        result = await ctrl.run(ctx)

        assert result["status"] == "complete"
        assert hands.build.await_count == 2
        assert critic.execute.await_count == 2
        assert judge.evaluate.await_count == 2

    @pytest.mark.asyncio
    async def test_uses_planner_when_provided(self):
        """When planner is provided, use planner.plan() instead of brain.plan()."""
        ctrl, brain, hands, judge, critic, tracker, sm, planner = _build_controller(
            use_planner=True
        )
        ctx = _make_ctx()

        result = await ctrl.run(ctx)

        assert result["status"] == "complete"
        planner.plan.assert_awaited_once()
        brain.plan.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests — REVISE Path
# ---------------------------------------------------------------------------

class TestRevisePath:
    @pytest.mark.asyncio
    async def test_revise_then_pass(self):
        """EVALUATE returns REVISE once, then PASS."""
        revise_result = FakeJudgeResult(verdict="REVISE", score=75, reason="minor issue")
        pass_result = FakeJudgeResult(verdict="PASS", score=95, reason="fixed")

        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller(
            exec_result=FakeExecResult(
                stderr="TypeError: ...", exit_code=1, error_type="type_error"
            ),
        )
        judge.evaluate = AsyncMock(side_effect=[revise_result, pass_result])
        ctx = _make_ctx()

        result = await ctrl.run(ctx)

        assert result["status"] == "complete"
        hands.revise.assert_awaited_once()
        assert judge.evaluate.await_count == 2

    @pytest.mark.asyncio
    async def test_revise_cant_self_fix_escalates(self):
        """REVISE with error_type not in self_fix_scope → ESCALATE."""
        revise_result = FakeJudgeResult(verdict="REVISE", score=60, reason="bad")

        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller(
            exec_result=FakeExecResult(
                stderr="SomeWeirdError", exit_code=1, error_type="architecture_change"
            ),
        )
        judge.evaluate = AsyncMock(return_value=revise_result)
        ctx = _make_ctx(max_loops=10)

        result = await ctrl.run(ctx)

        # Cannot self-fix → escalate → brain.handle_escalation returns human_escalation
        assert result["status"] == "escalated"
        hands.revise.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests — ESCALATION Path
# ---------------------------------------------------------------------------

class TestEscalation:
    @pytest.mark.asyncio
    async def test_direct_escalate_verdict(self):
        """Judge returns ESCALATE directly."""
        escalate_result = FakeJudgeResult(verdict="ESCALATE", score=0, reason="impossible")

        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller()
        judge.evaluate = AsyncMock(return_value=escalate_result)
        ctx = _make_ctx()

        result = await ctrl.run(ctx)

        assert result["status"] == "escalated"
        brain.handle_escalation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consecutive_fails_escalate(self):
        """3 consecutive REVISE → escalation."""
        revise_result = FakeJudgeResult(verdict="REVISE", score=50, reason="still broken")

        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller(
            exec_result=FakeExecResult(
                stderr="TypeError: x", exit_code=1, error_type="type_error"
            ),
        )
        judge.evaluate = AsyncMock(return_value=revise_result)
        ctx = _make_ctx(max_consecutive_fails=3, max_loops=10)

        result = await ctrl.run(ctx)

        assert result["status"] == "escalated"
        # 3 consecutive fails needed to escalate
        assert ctx.consecutive_fails >= 3

    @pytest.mark.asyncio
    async def test_max_loops_human_escalation(self):
        """When loop_count >= max_loops at ESCALATE step → human_escalation."""
        escalate_result = FakeJudgeResult(verdict="ESCALATE", score=0, reason="stuck")

        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller()
        judge.evaluate = AsyncMock(return_value=escalate_result)
        ctx = _make_ctx(max_loops=1)

        result = await ctrl.run(ctx)

        # loop_count becomes 1 after first evaluate, then ESCALATE step checks >= max_loops(1)
        assert result["status"] == "escalated"
        assert "사람의 판단이 필요합니다" in result["message"]

    @pytest.mark.asyncio
    async def test_brain_revises_plan_on_escalation(self):
        """Brain returns revise_plan action → resets and continues."""
        escalate_result = FakeJudgeResult(verdict="ESCALATE", score=0, reason="stuck")
        pass_result = FakeJudgeResult(verdict="PASS", score=90, reason="ok now")

        new_plan = {
            "action": "revise_plan",
            "new_plan": "# Revised plan",
            "new_todo": [{"id": 1, "title": "Revised task", "description": "try again"}],
            "new_criteria": {"1": ["new condition"]},
        }

        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller(
            escalation_result=new_plan,
        )
        # First call: ESCALATE, second call (after revised plan): PASS
        judge.evaluate = AsyncMock(side_effect=[escalate_result, pass_result])
        ctx = _make_ctx(max_loops=10)

        result = await ctrl.run(ctx)

        assert result["status"] == "complete"
        brain.handle_escalation.assert_awaited_once()
        assert ctx.plan == "# Revised plan"


# ---------------------------------------------------------------------------
# Tests — Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_code_in_critique_raises(self):
        """CRITIQUE step with current_code=None raises RuntimeError."""
        ctrl, *_ = _build_controller()
        ctx = _make_ctx()
        ctx.current_step = LoopStep.CRITIQUE
        ctx.current_code = None

        with pytest.raises(RuntimeError, match="No code to execute"):
            await ctrl.run(ctx)

    @pytest.mark.asyncio
    async def test_tracker_records_switches(self):
        """Tracker receives brain_to_hands and hands_to_brain calls."""
        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller()
        ctx = _make_ctx()

        await ctrl.run(ctx)

        tracker.record_switch.assert_any_call("brain_to_hands", "test-001")
        tracker.record_switch.assert_any_call("hands_to_brain", "test-001")

    @pytest.mark.asyncio
    async def test_skill_manager_load_verified_called(self):
        """skill_manager.load_verified is called with task_description during BUILD."""
        ctrl, brain, hands, judge, critic, tracker, sm, _ = _build_controller()
        ctx = _make_ctx()

        await ctrl.run(ctx)

        sm.load_verified.assert_called_with(ctx.task_description)


# ---------------------------------------------------------------------------
# Tests — _check_escalation / _can_self_fix unit tests
# ---------------------------------------------------------------------------

class TestHelperMethods:
    def test_check_escalation_consecutive_fails(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx(consecutive_fails=3, max_consecutive_fails=3, max_loops=10, loop_count=1)
        assert ctrl._check_escalation(ctx) == LoopStep.ESCALATE

    def test_check_escalation_max_loops(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx(consecutive_fails=1, max_consecutive_fails=3, loop_count=5, max_loops=5)
        assert ctrl._check_escalation(ctx) == LoopStep.ESCALATE

    def test_check_escalation_continue(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx(consecutive_fails=1, max_consecutive_fails=3, loop_count=2, max_loops=5)
        assert ctrl._check_escalation(ctx) == LoopStep.REVISE

    def test_can_self_fix_allowed(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx()
        ctx.self_fix_scope = {"allowed": ["type_error", "syntax_error"]}
        ctx.critic_result = {"error_type": "type_error"}
        assert ctrl._can_self_fix(ctx) is True

    def test_can_self_fix_denied(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx()
        ctx.self_fix_scope = {"allowed": ["type_error"]}
        ctx.critic_result = {"error_type": "architecture_change"}
        assert ctrl._can_self_fix(ctx) is False

    def test_can_self_fix_empty_scope(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx()
        ctx.self_fix_scope = {}
        ctx.critic_result = {"error_type": "type_error"}
        assert ctrl._can_self_fix(ctx) is False

    def test_human_escalation_format(self):
        ctrl, *_ = _build_controller()
        ctx = _make_ctx(loop_count=5, fail_reasons=["err1", "err2"])
        result = ctrl._human_escalation(ctx)
        assert result["status"] == "escalated"
        assert result["task_id"] == "test-001"
        assert result["loop_count"] == 5
        assert len(result["fail_reasons"]) == 2
        assert "사람의 판단" in result["message"]
