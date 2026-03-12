"""
Pyvis v5.1 Chat Chain Controller

Implements consensus-based agreement loops between AI roles with Hard Limit interruption.
Two segments:
- Segment A: Planner ↔ Brain (design agreement)
- Segment B: Brain ↔ Hands (revision agreement)

References: pyovis_v5_1.md sections 2-4
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import ast
import difflib
import re


class TerminationReason(str, Enum):
    """Reasons for Chat Chain termination"""

    CONSENSUS = "consensus"
    HARD_LIMIT_DIFF = "hard_limit_diff"  # Meaningless repetition
    HARD_LIMIT_AST = "hard_limit_ast"  # Code structure collapse
    HARD_LIMIT_CLARIFICATION = "hard_limit_clarification"  # Unclear instructions
    HARD_LIMIT_SYCOPHANCY = "hard_limit_sycophancy"  # Blind agreement to errors
    MAX_TURNS = "max_turns"  # Turn limit reached


@dataclass
class ConsensusResult:
    """Result of a Chat Chain consensus loop"""

    agreed: bool
    final_content: str
    messages: List[Dict[str, Any]]
    turns: int
    termination_reason: TerminationReason
    hard_limit_triggered: Optional[str] = None  # Which hard limit triggered (if any)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agreed": self.agreed,
            "final_content": self.final_content,
            "turns": self.turns,
            "termination_reason": self.termination_reason.value,
            "hard_limit_triggered": self.hard_limit_triggered,
        }


@dataclass
class HardLimitConfig:
    """Configuration for Hard Limit triggers"""

    min_diff_lines: int = 3  # Minimum meaningful code changes
    max_ast_errors: int = 2  # Consecutive AST parse errors
    max_clarification: int = 3  # Maximum clarification rounds
    max_turns: int = 3  # Maximum conversation turns

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "HardLimitConfig":
        return cls(
            min_diff_lines=config.get("min_diff_lines", 3),
            max_ast_errors=config.get("max_ast_errors", 2),
            max_clarification=config.get("max_clarification", 3),
            max_turns=config.get("max_turns", 3),
        )


class ChatChainController:
    """
    Manages consensus-based agreement loops with Hard Limit interruption.

    Usage:
        controller = ChatChainController(config)
        result = await controller.consensus_loop(
            instructor=planner,
            assistant=brain,
            topic="FastAPI auth module design",
            initial_content=plan_json,
            context=context_dict
        )
    """

    def __init__(self, config: Optional[HardLimitConfig] = None):
        self.config = config or HardLimitConfig()

    async def consensus_loop(
        self,
        instructor: Any,  # Instructor role (Planner or Brain)
        assistant: Any,  # Assistant role (Brain or Hands)
        topic: str,
        initial_content: str,
        context: Dict[str, Any],
    ) -> ConsensusResult:
        """
        Run consensus loop between instructor and assistant.

        Args:
            instructor: Role that gives instructions (Planner or Brain)
            assistant: Role that responds and executes (Brain or Hands)
            topic: Topic of the consensus discussion
            initial_content: Initial content from instructor
            context: Context dictionary for both roles

        Returns:
            ConsensusResult with agreed content and termination reason
        """
        messages: List[Dict[str, Any]] = []
        prev_content = initial_content
        ast_error_count = 0
        clarification_count = 0
        prev_had_ast_error = False
        immediate_consensus = False

        for turn in range(self.config.max_turns):
            # ── Instructor utterance ──────────────────────────────
            inst_output = await self._instructor_instruct(
                instructor, topic, prev_content, messages, context
            )
            messages.append(
                {"role": "instructor", "content": inst_output, "turn": turn}
            )

            # Check for consensus
            if "[CONSENSUS]" in inst_output:
                if (
                    turn == 0
                    and self._has_code(prev_content)
                    and not self._ast_valid(prev_content)
                ):
                    # Sycophancy check: immediate consensus on invalid code
                    return ConsensusResult(
                        agreed=False,
                        final_content=inst_output,
                        messages=messages,
                        turns=turn + 1,
                        termination_reason=TerminationReason.HARD_LIMIT_SYCOPHANCY,
                        hard_limit_triggered="sycophancy",
                    )
                return ConsensusResult(
                    agreed=True,
                    final_content=inst_output,
                    messages=messages,
                    turns=turn + 1,
                    termination_reason=TerminationReason.CONSENSUS,
                )

            # ── Assistant response ───────────────────────────────
            asst_output = await self._assistant_respond(assistant, messages, context)
            messages.append({"role": "assistant", "content": asst_output, "turn": turn})

            # ── CLARIFICATION_NEEDED count ───────────────────────
            if "[CLARIFICATION_NEEDED]" in asst_output:
                clarification_count += 1
                if clarification_count >= self.config.max_clarification:
                    return ConsensusResult(
                        agreed=False,
                        final_content=asst_output,
                        messages=messages,
                        turns=turn + 1,
                        termination_reason=TerminationReason.HARD_LIMIT_CLARIFICATION,
                        hard_limit_triggered="clarification_loop",
                    )
                prev_content = (
                    initial_content  # Keep content same, waiting for clarification
                )
                continue

            # Check for consensus
            if "[CONSENSUS]" in asst_output:
                # Sycophancy check
                current_content = self._extract_content(asst_output)
                if self._has_code(asst_output) and not self._ast_valid(
                    current_content
                ):
                    if immediate_consensus or turn == 0:
                        return ConsensusResult(
                            agreed=False,
                            final_content=asst_output,
                            messages=messages,
                            turns=turn + 1,
                            termination_reason=TerminationReason.HARD_LIMIT_SYCOPHANCY,
                            hard_limit_triggered="sycophancy",
                        )

                return ConsensusResult(
                    agreed=True,
                    final_content=asst_output,
                    messages=messages,
                    turns=turn + 1,
                    termination_reason=TerminationReason.CONSENSUS,
                )
            # ── Hard Limit 2: AST parse error monitoring ─────────
            current_content = self._extract_content(asst_output)
            if self._has_code(asst_output):  # Check raw text for markers
                ast_valid = self._ast_valid(current_content)
                if not ast_valid:
                    ast_error_count += 1
                    if ast_error_count >= self.config.max_ast_errors:
                        return ConsensusResult(
                            agreed=False,
                            final_content=asst_output,
                            messages=messages,
                            turns=turn + 1,
                            termination_reason=TerminationReason.HARD_LIMIT_AST,
                            hard_limit_triggered="ast_error_repeat",
                        )
                    prev_had_ast_error = True
                else:
                    prev_had_ast_error = False

            # ── Hard Limit 1: Diff change monitoring ─────────────
            diff_lines = self._count_diff_lines(prev_content, current_content)
            if turn > 0 and diff_lines < self.config.min_diff_lines:
                return ConsensusResult(
                    agreed=False,
                    final_content=asst_output,
                    messages=messages,
                    turns=turn + 1,
                    termination_reason=TerminationReason.HARD_LIMIT_DIFF,
                    hard_limit_triggered="diff_too_small",
                )

            # Track for sycophancy detection
            immediate_consensus = False

            prev_content = current_content

        # Max turns reached
        return ConsensusResult(
            agreed=False,
            final_content=prev_content,
            messages=messages,
            turns=self.config.max_turns,
            termination_reason=TerminationReason.MAX_TURNS,
            hard_limit_triggered="max_turns",
        )

    async def _instructor_instruct(
        self,
        instructor: Any,
        topic: str,
        prev_content: str,
        messages: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> str:
        """Call instructor's instruct method"""
        return await instructor.instruct(topic, prev_content, messages, context)

    async def _assistant_respond(
        self, assistant: Any, messages: List[Dict[str, Any]], context: Dict[str, Any]
    ) -> str:
        """Call assistant's respond method"""
        return await assistant.respond(messages, context)

    def _count_diff_lines(self, prev: str, curr: str) -> int:
        """Count meaningful diff lines between two content versions"""
        diff = list(
            difflib.unified_diff(prev.splitlines(), curr.splitlines(), lineterm="")
        )
        return sum(
            1
            for line in diff
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )

    def _ast_valid(self, code: str) -> bool:
        """Check if code is valid Python AST"""
        code_block = self._extract_code_block(code)
        if not code_block:
            # If no code block found, check if entire text is valid Python
            code_to_check = code.strip()
        else:
            code_to_check = code_block
        
        if not code_to_check:
            return True  # No code to validate
        try:
            ast.parse(code_to_check)
            return True
        except SyntaxError:
            return False

    def _extract_code_block(self, text: str) -> Optional[str]:
        """Extract Python code block from text"""
        match = re.search(r"```python\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _has_code(self, text: str) -> bool:
        """Check if text contains Python code"""
        return "```python" in text

    def _extract_content(self, text: str) -> str:
        """Extract content (code block or text) from response"""
        code = self._extract_code_block(text)
        return code if code else text


class SegmentType(str, Enum):
    """Chat Chain segment types"""

    PLANNER_BRAIN = "planner_brain"  # Design agreement
    BRAIN_HANDS = "brain_hands"  # Revision agreement


@dataclass
class ChatChainSegment:
    """Configuration for a Chat Chain segment"""

    name: SegmentType
    instructor_role: str
    assistant_role: str

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "ChatChainSegment":
        return cls(
            name=SegmentType(config["name"]),
            instructor_role=config["instructor"],
            assistant_role=config["assistant"],
        )


# Convenience functions for common segments


async def run_planner_brain_segment(
    planner: Any,
    brain: Any,
    topic: str,
    initial_plan: str,
    context: Dict[str, Any],
    config: Optional[HardLimitConfig] = None,
) -> ConsensusResult:
    """
    Run Segment A: Planner ↔ Brain design agreement.

    Planner proposes plan → Brain reviews feasibility → Reach consensus
    """
    controller = ChatChainController(config)
    return await controller.consensus_loop(
        instructor=planner,
        assistant=brain,
        topic=topic,
        initial_content=initial_plan,
        context=context,
    )


async def run_brain_hands_segment(
    brain: Any,
    hands: Any,
    topic: str,
    revision_instruction: str,
    context: Dict[str, Any],
    config: Optional[HardLimitConfig] = None,
) -> ConsensusResult:
    """
    Run Segment B: Brain ↔ Hands revision agreement.

    Brain gives revision instruction → Hands confirms feasibility → Reach consensus
    """
    controller = ChatChainController(config)
    return await controller.consensus_loop(
        instructor=brain,
        assistant=hands,
        topic=topic,
        initial_content=revision_instruction,
        context=context,
    )
