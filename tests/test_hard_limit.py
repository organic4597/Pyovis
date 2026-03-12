"""
Tests for Pyvis v5.1 Hard Limit Checker

Tests cover:
- All 5 trigger types (diff, AST, clarification, max_turns, sycophancy)
- Trigger evaluation logic
- Escalation actions
- Statistics tracking
"""

import pytest
from pyovis.orchestration.hard_limit import (
    HardLimitChecker,
    HardLimitState,
    HardLimitResult,
    HardLimitTrigger,
    TriggerDefinition,
    EscalationAction,
    EscalationInfo,
    check_hard_limit,
    create_escalation_handler,
)


class TestTriggerDefinition:
    """Test TriggerDefinition"""

    def test_get_all_triggers(self):
        """Test retrieving all trigger definitions"""
        triggers = TriggerDefinition.get_all_triggers()

        assert len(triggers) == 5
        trigger_names = [t.name for t in triggers]

        assert HardLimitTrigger.DIFF_TOO_SMALL in trigger_names
        assert HardLimitTrigger.AST_ERROR_REPEAT in trigger_names
        assert HardLimitTrigger.CLARIFICATION_LOOP in trigger_names
        assert HardLimitTrigger.MAX_TURNS in trigger_names
        assert HardLimitTrigger.SYCOPHANCY in trigger_names

    def test_trigger_severity_ordering(self):
        """Test that high severity triggers are properly marked"""
        triggers = TriggerDefinition.get_all_triggers()

        # AST, clarification, and sycophancy should be high severity (3)
        high_severity = [t for t in triggers if t.severity == 3]
        assert len(high_severity) >= 3

        # Max turns should be low severity (1)
        max_turns = next(t for t in triggers if t.name == HardLimitTrigger.MAX_TURNS)
        assert max_turns.severity == 1


class TestHardLimitState:
    """Test HardLimitState"""

    def test_state_creation(self):
        """Test creating state object"""
        state = HardLimitState(
            turn=1, diff_lines=2, ast_error_count=0, clarification_count=1, max_turns=3
        )

        assert state.turn == 1
        assert state.diff_lines == 2
        assert state.ast_invalid is False
        assert state.immediate_consensus is False

    def test_state_from_chat_chain_state(self):
        """Test creating state from Chat Chain state"""
        valid_code = "```python\ndef test():\n    pass\n```"
        state = HardLimitState.from_chat_chain_state(
            turn=2,
            diff_lines=5,
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
            current_code=valid_code,
        )

        assert state.turn == 2
        assert state.diff_lines == 5
        assert state.ast_invalid is False

    def test_state_from_chat_chain_state_invalid_code(self):
        """Test state creation with invalid code"""
        invalid_code = "```python\ndef broken(\n```"
        state = HardLimitState.from_chat_chain_state(
            turn=1,
            diff_lines=0,
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
            current_code=invalid_code,
        )

        assert state.ast_invalid is True

    def test_state_from_chat_chain_state_no_code(self):
        """Test state creation without code"""
        state = HardLimitState.from_chat_chain_state(
            turn=0,
            diff_lines=0,
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
            current_code=None,
        )

        assert state.ast_invalid is False  # No code to validate


class TestHardLimitChecker:
    """Test HardLimitChecker"""

    def test_no_trigger_normal_state(self):
        """Test no trigger with normal state"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=1, diff_lines=5, ast_error_count=0, clarification_count=0, max_turns=3
        )

        result = checker.check(state)

        assert result.triggered is False
        assert result.trigger_name is None

    def test_trigger_diff_too_small(self):
        """Test diff too small trigger"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=2,  # turn > 0
            diff_lines=2,  # < 3
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
        )

        result = checker.check(state)

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.DIFF_TOO_SMALL

    def test_trigger_ast_error_repeat(self):
        """Test AST error repeat trigger"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=1,
            diff_lines=10,
            ast_error_count=2,  # >= 2
            clarification_count=0,
            max_turns=3,
        )

        result = checker.check(state)

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.AST_ERROR_REPEAT

    def test_trigger_clarification_loop(self):
        """Test clarification loop trigger"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=2,
            diff_lines=5,
            ast_error_count=0,
            clarification_count=3,  # >= 3
            max_turns=5,
        )

        result = checker.check(state)

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.CLARIFICATION_LOOP

    def test_trigger_max_turns(self):
        """Test max turns trigger"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=3,  # >= max_turns
            diff_lines=5,
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
        )

        result = checker.check(state)

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.MAX_TURNS

    def test_trigger_sycophancy(self):
        """Test sycophancy trigger"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=0,
            diff_lines=0,
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
            ast_invalid=True,
            immediate_consensus=True,
        )

        result = checker.check(state)

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.SYCOPHANCY


class TestHardLimitResult:
    """Test HardLimitResult"""

    def test_result_to_dict_triggered(self):
        """Test result dictionary conversion when triggered"""
        trigger_def = TriggerDefinition.get_all_triggers()[0]
        result = HardLimitResult(
            triggered=True,
            trigger_name=HardLimitTrigger.DIFF_TOO_SMALL,
            trigger_definition=trigger_def,
            message="Test message",
        )

        result_dict = result.to_dict()

        assert result_dict["triggered"] is True
        assert result_dict["trigger_name"] == "diff_too_small"
        assert result_dict["meaning"] is not None
        assert result_dict["action"] is not None
        assert result_dict["message"] == "Test message"

    def test_result_to_dict_not_triggered(self):
        """Test result dictionary conversion when not triggered"""
        result = HardLimitResult(triggered=False)
        result_dict = result.to_dict()

        assert result_dict["triggered"] is False
        assert result_dict["trigger_name"] is None
        assert result_dict["message"] is None


class TestHardLimitCheckerHistory:
    """Test HardLimitChecker history tracking"""

    def test_trigger_history_tracking(self):
        """Test that triggers are tracked in history"""
        checker = HardLimitChecker()

        # Trigger diff too small
        state1 = HardLimitState(
            turn=2, diff_lines=1, ast_error_count=0, clarification_count=0, max_turns=3
        )
        checker.check(state1)

        # Trigger AST error
        state2 = HardLimitState(
            turn=1, diff_lines=10, ast_error_count=2, clarification_count=0, max_turns=3
        )
        checker.check(state2)

        history = checker.get_trigger_history()

        assert len(history) == 2
        assert HardLimitTrigger.DIFF_TOO_SMALL in history
        assert HardLimitTrigger.AST_ERROR_REPEAT in history

    def test_clear_history(self):
        """Test clearing trigger history"""
        checker = HardLimitChecker()
        state = HardLimitState(
            turn=2, diff_lines=1, ast_error_count=0, clarification_count=0, max_turns=3
        )
        checker.check(state)

        checker.clear_history()

        assert len(checker.get_trigger_history()) == 0

    def test_get_statistics(self):
        """Test getting trigger statistics"""
        checker = HardLimitChecker()

        # Trigger same type multiple times
        for _ in range(3):
            state = HardLimitState(
                turn=2,
                diff_lines=1,
                ast_error_count=0,
                clarification_count=0,
                max_turns=3,
            )
            checker.check(state)

        stats = checker.get_statistics()

        assert stats["total_triggers"] == 3
        assert "diff_too_small" in stats["by_type"]
        assert stats["by_type"]["diff_too_small"] == 3


class TestCheckHardLimitFunction:
    """Test convenience function"""

    def test_check_hard_limit_no_trigger(self):
        """Test check_hard_limit with no trigger"""
        result = check_hard_limit(
            turn=1, diff_lines=5, ast_error_count=0, clarification_count=0, max_turns=3
        )

        assert result.triggered is False

    def test_check_hard_limit_with_trigger(self):
        """Test check_hard_limit with trigger"""
        result = check_hard_limit(
            turn=2, diff_lines=1, ast_error_count=0, clarification_count=0, max_turns=3
        )

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.DIFF_TOO_SMALL

    def test_check_hard_limit_with_invalid_code(self):
        """Test check_hard_limit with invalid code"""
        invalid_code = "```python\ndef broken(\n```"
        result = check_hard_limit(
            turn=1,
            diff_lines=0,
            ast_error_count=0,
            clarification_count=0,
            max_turns=3,
            current_code=invalid_code,
            immediate_consensus=True,
        )

        assert result.triggered is True
        assert result.trigger_name == HardLimitTrigger.SYCOPHANCY


class TestEscalationInfo:
    """Test EscalationInfo"""

    def test_escalation_info_to_summary(self):
        """Test escalation summary generation"""
        info = EscalationInfo(
            trigger=HardLimitTrigger.AST_ERROR_REPEAT,
            action=EscalationAction.ESCALATE_TO_BRAIN,
            state=HardLimitState(
                turn=2,
                diff_lines=0,
                ast_error_count=2,
                clarification_count=0,
                max_turns=3,
            ),
            messages=[],
            context={},
        )

        summary = info.to_summary()

        assert "Hard Limit Escalation" in summary
        assert "ast_error_repeat" in summary
        assert "Turn: 2/3" in summary
        assert "AST Errors: 2" in summary


class TestEscalationHandler:
    """Test escalation handler"""

    @pytest.mark.asyncio
    async def test_escalate_to_brain(self):
        """Test escalation to Brain"""
        info = EscalationInfo(
            trigger=HardLimitTrigger.AST_ERROR_REPEAT,
            action=EscalationAction.ESCALATE_TO_BRAIN,
            state=HardLimitState(
                turn=1,
                diff_lines=0,
                ast_error_count=2,
                clarification_count=0,
                max_turns=3,
            ),
            messages=[],
            context={},
        )

        handler = create_escalation_handler()
        result = await handler(info)

        assert result["action"] == "escalate_to_brain"
        assert result["requires_brain_analysis"] is True

    @pytest.mark.asyncio
    async def test_escalate_to_planner(self):
        """Test escalation to Planner"""
        info = EscalationInfo(
            trigger=HardLimitTrigger.CLARIFICATION_LOOP,
            action=EscalationAction.ESCALATE_TO_PLANNER,
            state=HardLimitState(
                turn=2,
                diff_lines=5,
                ast_error_count=0,
                clarification_count=3,
                max_turns=5,
            ),
            messages=[],
            context={},
        )

        handler = create_escalation_handler()
        result = await handler(info)

        assert result["action"] == "escalate_to_planner"
        assert result["requires_plan_revision"] is True

    @pytest.mark.asyncio
    async def test_force_last_state(self):
        """Test forcing last state"""
        info = EscalationInfo(
            trigger=HardLimitTrigger.MAX_TURNS,
            action=EscalationAction.FORCE_LAST_STATE,
            state=HardLimitState(
                turn=3,
                diff_lines=5,
                ast_error_count=0,
                clarification_count=0,
                max_turns=3,
            ),
            messages=[],
            context={},
        )

        handler = create_escalation_handler()
        result = await handler(info)

        assert result["action"] == "force_last_state"
        assert result["proceed_to_judge"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
