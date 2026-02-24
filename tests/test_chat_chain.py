"""
Tests for Pyvis v5.1 Chat Chain Controller

Tests cover:
- Consensus loop normal flow
- Hard Limit triggers (diff, AST, clarification, sycophancy, max_turns)
- Message tracking and turn counting
- Content extraction and diff counting
"""

import pytest
import ast
from unittest.mock import AsyncMock, MagicMock
from pyovis.orchestration.chat_chain import (
    ChatChainController,
    ConsensusResult,
    HardLimitConfig,
    TerminationReason,
    run_planner_brain_segment,
    run_brain_hands_segment,
)


class TestConsensusResult:
    """Test ConsensusResult dataclass"""

    def test_consensus_result_creation(self):
        result = ConsensusResult(
            agreed=True,
            final_content="test content",
            messages=[{"role": "user", "content": "test"}],
            turns=2,
            termination_reason=TerminationReason.CONSENSUS,
        )

        assert result.agreed is True
        assert result.final_content == "test content"
        assert result.turns == 2
        assert result.termination_reason == TerminationReason.CONSENSUS

    def test_consensus_result_to_dict(self):
        result = ConsensusResult(
            agreed=False,
            final_content="content",
            messages=[],
            turns=3,
            termination_reason=TerminationReason.HARD_LIMIT_DIFF,
            hard_limit_triggered="diff_too_small",
        )

        result_dict = result.to_dict()
        assert result_dict["agreed"] is False
        assert result_dict["turns"] == 3
        assert result_dict["termination_reason"] == "hard_limit_diff"
        assert result_dict["hard_limit_triggered"] == "diff_too_small"


class TestHardLimitConfig:
    """Test HardLimitConfig"""

    def test_default_config(self):
        config = HardLimitConfig()
        assert config.min_diff_lines == 3
        assert config.max_ast_errors == 2
        assert config.max_clarification == 3
        assert config.max_turns == 3

    def test_config_from_dict(self):
        config_dict = {
            "min_diff_lines": 5,
            "max_ast_errors": 3,
            "max_clarification": 2,
            "max_turns": 5,
        }
        config = HardLimitConfig.from_dict(config_dict)

        assert config.min_diff_lines == 5
        assert config.max_ast_errors == 3
        assert config.max_clarification == 2
        assert config.max_turns == 5

    def test_config_from_dict_partial(self):
        config_dict = {"min_diff_lines": 10}
        config = HardLimitConfig.from_dict(config_dict)

        assert config.min_diff_lines == 10
        assert config.max_ast_errors == 2  # default
        assert config.max_turns == 3  # default


class TestChatChainControllerConsensus:
    """Test Chat Chain consensus flow"""

    @pytest.mark.asyncio
    async def test_immediate_consensus_instructor(self):
        """Test consensus reached on first instructor turn"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "[CONSENSUS] Plan approved"

        assistant = AsyncMock()

        controller = ChatChainController()
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        assert result.agreed is True
        assert result.termination_reason == TerminationReason.CONSENSUS
        assert result.turns == 1
        assert instructor.instruct.called
        assert not assistant.respond.called  # Consensus before assistant response

    @pytest.mark.asyncio
    async def test_consensus_after_one_turn(self):
        """Test consensus reached after one full turn"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "Review this plan"

        assistant = AsyncMock()
        assistant.respond.return_value = "Looks good [CONSENSUS]"

        controller = ChatChainController()
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        assert result.agreed is True
        assert result.termination_reason == TerminationReason.CONSENSUS
        assert result.turns == 1
        assert instructor.instruct.called
        assert assistant.respond.called

    @pytest.mark.asyncio
    async def test_max_turns_reached(self):
        """Test max turns reached without consensus"""
        instructor = AsyncMock()
        instructor.instruct.side_effect = ["Revise", "More"]  # 2 turns
        
        assistant = AsyncMock()
        # Text responses (no code) that don't trigger diff limit
        assistant.respond.side_effect = [
            "Response 1",  # Turn 0
            "Response 2",  # Turn 1 - no code, no diff check
        ]
        
        config = HardLimitConfig(max_turns=2, min_diff_lines=3)
        controller = ChatChainController(config)
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )
        
        assert result.agreed is False
        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert result.turns == 2
    async def test_max_turns_reached(self):
        """Test max turns reached without consensus"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "Revise this"

        assistant = AsyncMock()
        assistant.respond.return_value = "Here's revision"  # No consensus

        config = HardLimitConfig(max_turns=2)
        controller = ChatChainController(config)
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        assert result.agreed is False
        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert result.turns == 2


class TestChatChainHardLimitDiff:
    """Test Hard Limit - diff too small"""

    @pytest.mark.asyncio
    async def test_hard_limit_diff_too_small(self):
        """Test Hard Limit trigger when diff lines < minimum"""
        instructor = AsyncMock()
        instructor.instruct.side_effect = ["Revise this", "Still needs work"]

        assistant = AsyncMock()
        assistant.respond.side_effect = [
            "```python\nx = 1\n```",  # Turn 0
            "```python\nx = 2\n```",  # Turn 1 - only 1 line changed
        ]

        config = HardLimitConfig(min_diff_lines=3, max_turns=3)
        controller = ChatChainController(config)
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="```python\nx = 0\n```",
            context={},
        )

        assert result.agreed is False
        assert result.termination_reason == TerminationReason.HARD_LIMIT_DIFF
        assert result.hard_limit_triggered == "diff_too_small"


class TestChatChainHardLimitAST:
    """Test Hard Limit - AST errors"""

    @pytest.mark.asyncio
    async def test_hard_limit_ast_error_repeat(self):
        """Test Hard Limit trigger on consecutive AST errors"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "Fix syntax"

        assistant = AsyncMock()
        assistant.respond.side_effect = [
            "```python\ndef broken(\n```",  # Syntax error 1
            "```python\nclass Invalid:\n```",  # Syntax error 2
        ]

        config = HardLimitConfig(max_ast_errors=2, max_turns=3)
        controller = ChatChainController(config)
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        assert result.agreed is False
        assert result.termination_reason == TerminationReason.HARD_LIMIT_AST
        assert result.hard_limit_triggered == "ast_error_repeat"

    @pytest.mark.asyncio
    async def test_ast_valid_code_passes(self):
        """Test that valid code doesn't trigger AST error"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "Write code"

        assistant = AsyncMock()
        assistant.respond.return_value = "```python\ndef valid():\n    return True\n```"

        controller = ChatChainController()
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        # Should not trigger AST error
        assert result.termination_reason != TerminationReason.HARD_LIMIT_AST


class TestChatChainClarification:
    """Test Clarification handling"""

    @pytest.mark.asyncio
    async def test_clarification_loop_hard_limit(self):
        """Test Hard Limit on excessive clarification requests"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "Implement this"

        assistant = AsyncMock()
        assistant.respond.side_effect = [
            "[CLARIFICATION_NEEDED] Question: What type?",
            "[CLARIFICATION_NEEDED] Question: Still unclear",
            "[CLARIFICATION_NEEDED] Question: Need more info",
        ]

        config = HardLimitConfig(max_clarification=3, max_turns=5)
        controller = ChatChainController(config)
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        assert result.agreed is False
        assert result.termination_reason == TerminationReason.HARD_LIMIT_CLARIFICATION
        assert result.hard_limit_triggered == "clarification_loop"

    @pytest.mark.asyncio
    async def test_clarification_then_consensus(self):
        """Test normal flow: clarification then consensus"""
        instructor = AsyncMock()
        instructor.instruct.side_effect = ["Implement auth", "Use option B"]

        assistant = AsyncMock()
        assistant.respond.side_effect = [
            "[CLARIFICATION_NEEDED] Question: Which auth method?",
            "Done [CONSENSUS]",
        ]

        controller = ChatChainController()
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content="plan",
            context={},
        )

        assert result.agreed is True
        assert result.termination_reason == TerminationReason.CONSENSUS


class TestChatChainSycophancy:
    """Test Sycophancy detection"""

    @pytest.mark.asyncio
    async def test_sycophancy_detection(self):
        """Test detection of blind agreement to erroneous code"""
        instructor = AsyncMock()
        instructor.instruct.return_value = "This is correct [CONSENSUS]"

        assistant = AsyncMock()

        # Invalid code in initial content
        invalid_code = "```python\ndef broken(\n```"

        controller = ChatChainController()
        result = await controller.consensus_loop(
            instructor=instructor,
            assistant=assistant,
            topic="test",
            initial_content=invalid_code,
            context={},
        )

        # Should detect sycophancy: consensus on invalid code
        assert result.agreed is False
        assert result.termination_reason == TerminationReason.HARD_LIMIT_SYCOPHANCY
        assert result.hard_limit_triggered == "sycophancy"


class TestUtilityFunctions:
    """Test utility functions"""

    def test_count_diff_lines_identical(self):
        """Test diff counting with identical content"""
        controller = ChatChainController()
        diff = controller._count_diff_lines("same content", "same content")
        assert diff == 0

    def test_count_diff_lines_different(self):
        """Test diff counting with different content"""
        controller = ChatChainController()
        prev = "line1\nline2\nline3"
        curr = "line1\nmodified\nline3"
        diff = controller._count_diff_lines(prev, curr)
        assert diff == 2  # One removed, one added

    def test_ast_valid_function(self):
        """Test AST validation with valid code"""
        controller = ChatChainController()
        code = "def valid():\n    return True"
        assert controller._ast_valid(code) is True

    def test_ast_invalid_function(self):
        """Test AST validation with invalid code"""
        controller = ChatChainController()
        code = "def broken(\n"
        assert controller._ast_valid(code) is False

    def test_extract_code_block(self):
        """Test code block extraction"""
        controller = ChatChainController()
        text = "Here's the code:\n```python\ndef test():\n    pass\n```"
        code = controller._extract_code_block(text)
        assert code == "def test():\n    pass"

    def test_extract_code_block_none(self):
        """Test code block extraction when no code"""
        controller = ChatChainController()
        text = "No code here"
        code = controller._extract_code_block(text)
        assert code is None

    def test_has_code_true(self):
        """Test code detection"""
        controller = ChatChainController()
        assert controller._has_code("```python\ncode") is True

    def test_has_code_false(self):
        """Test code detection when no code"""
        controller = ChatChainController()
        assert controller._has_code("just text") is False


class TestSegmentFunctions:
    """Test convenience functions for segments"""

    @pytest.mark.asyncio
    async def test_run_planner_brain_segment(self):
        """Test Planner-Brain segment function"""
        planner = AsyncMock()
        planner.instruct.return_value = "[CONSENSUS] Plan approved"

        brain = AsyncMock()

        result = await run_planner_brain_segment(
            planner=planner,
            brain=brain,
            topic="design",
            initial_plan="plan",
            context={},
        )

        assert result.agreed is True
        assert planner.instruct.called

    @pytest.mark.asyncio
    async def test_run_brain_hands_segment(self):
        """Test Brain-Hands segment function"""
        brain = AsyncMock()
        brain.instruct.return_value = "[CONSENSUS] Revision approved"

        hands = AsyncMock()

        result = await run_brain_hands_segment(
            brain=brain,
            hands=hands,
            topic="revision",
            revision_instruction="fix error",
            context={},
        )

        assert result.agreed is True
        assert brain.instruct.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
