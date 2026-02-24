"""
Pyvis v5.1 Hard Limit Checker

Monitors Chat Chain conversations for deadlock patterns and triggers
intelligent interruption when consensus is not progressing.

Five trigger types:
1. diff_too_small - Meaningless repetition (< 3 lines changed)
2. ast_error_repeat - Code structure collapse (2+ consecutive AST errors)
3. clarification_loop - Unclear instructions (3+ clarification requests)
4. max_turns - Turn limit exceeded
5. sycophancy - Blind agreement to erroneous code

References: pyovis_v5_1.md section 4
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import ast


class HardLimitTrigger(str, Enum):
    """Hard Limit trigger types"""

    DIFF_TOO_SMALL = "diff_too_small"
    AST_ERROR_REPEAT = "ast_error_repeat"
    CLARIFICATION_LOOP = "clarification_loop"
    MAX_TURNS = "max_turns"
    SYCOPHANCY = "sycophancy"


class EscalationAction(str, Enum):
    """Actions to take when Hard Limit triggers"""

    ESCALATE_TO_BRAIN = "escalate_to_brain"
    ESCALATE_TO_PLANNER = "escalate_to_planner"
    FORCE_LAST_STATE = "force_last_state"
    CONTINUE = "continue"


@dataclass
class TriggerDefinition:
    """Definition of a Hard Limit trigger"""

    name: HardLimitTrigger
    condition: str  # Human-readable condition description
    meaning: str  # What this trigger indicates
    action: EscalationAction
    severity: int  # 1=low, 2=medium, 3=high

    @classmethod
    def get_all_triggers(cls) -> List["TriggerDefinition"]:
        """Get all trigger definitions"""
        return [
            cls(
                name=HardLimitTrigger.DIFF_TOO_SMALL,
                condition="turn > 0 and diff_lines < 3",
                meaning="의미 없는 반복 (무한 루프 징후)",
                action=EscalationAction.ESCALATE_TO_BRAIN,
                severity=2,
            ),
            cls(
                name=HardLimitTrigger.AST_ERROR_REPEAT,
                condition="ast_error_count >= 2",
                meaning="코드 구조 붕괴 (수정 불가 수준)",
                action=EscalationAction.ESCALATE_TO_BRAIN,
                severity=3,
            ),
            cls(
                name=HardLimitTrigger.CLARIFICATION_LOOP,
                condition="clarification_count >= 3",
                meaning="지시가 근본적으로 불명확",
                action=EscalationAction.ESCALATE_TO_PLANNER,
                severity=3,
            ),
            cls(
                name=HardLimitTrigger.MAX_TURNS,
                condition="turn >= max_turns",
                meaning="상한 초과",
                action=EscalationAction.FORCE_LAST_STATE,
                severity=1,
            ),
            cls(
                name=HardLimitTrigger.SYCOPHANCY,
                condition="ast_invalid and immediate_consensus",
                meaning="오류 코드에 무조건 동의",
                action=EscalationAction.ESCALATE_TO_BRAIN,
                severity=3,
            ),
        ]


@dataclass
class HardLimitState:
    """Current state for Hard Limit evaluation"""

    turn: int
    diff_lines: int
    ast_error_count: int
    clarification_count: int
    max_turns: int
    ast_invalid: bool = False
    immediate_consensus: bool = False

    @classmethod
    def from_chat_chain_state(
        cls,
        turn: int,
        diff_lines: int,
        ast_error_count: int,
        clarification_count: int,
        max_turns: int,
        current_code: Optional[str] = None,
        immediate_consensus: bool = False,
    ) -> "HardLimitState":
        """Create state from Chat Chain conversation state"""
        ast_invalid = False
        if current_code:
            ast_invalid = not cls._check_ast_valid(current_code)

        return cls(
            turn=turn,
            diff_lines=diff_lines,
            ast_error_count=ast_error_count,
            clarification_count=clarification_count,
            max_turns=max_turns,
            ast_invalid=ast_invalid,
            immediate_consensus=immediate_consensus,
        )

    @staticmethod
    def _check_ast_valid(code: str) -> bool:
        """Check if code is valid Python"""
        import re

        match = re.search(r"```python\n(.*?)```", code, re.DOTALL)
        code_block = match.group(1) if match else code

        if not code_block.strip():
            return True

        try:
            ast.parse(code_block)
            return True
        except SyntaxError:
            return False


@dataclass
class HardLimitResult:
    """Result of Hard Limit check"""

    triggered: bool
    trigger_name: Optional[HardLimitTrigger] = None
    trigger_definition: Optional[TriggerDefinition] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "trigger_name": self.trigger_name.value if self.trigger_name else None,
            "meaning": self.trigger_definition.meaning
            if self.trigger_definition
            else None,
            "action": self.trigger_definition.action.value
            if self.trigger_definition
            else None,
            "severity": self.trigger_definition.severity
            if self.trigger_definition
            else None,
            "message": self.message,
        }


class HardLimitChecker:
    """
    Monitors Chat Chain state and triggers Hard Limit interruption.

    Usage:
        checker = HardLimitChecker()
        state = HardLimitState(turn=1, diff_lines=2, ...)
        result = checker.check(state)

        if result.triggered:
            escalate(result.trigger_definition.action)
    """

    def __init__(self):
        self.triggers = TriggerDefinition.get_all_triggers()
        self.trigger_history: List[HardLimitTrigger] = []

    def check(self, state: HardLimitState) -> HardLimitResult:
        """
        Check if any Hard Limit trigger is activated.

        Args:
            state: Current Chat Chain state

        Returns:
            HardLimitResult with trigger information if triggered
        """
        # Convert state to dictionary for evaluation
        state_dict = {
            "turn": state.turn,
            "diff_lines": state.diff_lines,
            "ast_error_count": state.ast_error_count,
            "clarification_count": state.clarification_count,
            "max_turns": state.max_turns,
            "ast_invalid": state.ast_invalid,
            "immediate_consensus": state.immediate_consensus,
        }

        # Check each trigger in priority order (by severity)
        sorted_triggers = sorted(self.triggers, key=lambda t: t.severity, reverse=True)

        for trigger in sorted_triggers:
            if self._evaluate_trigger(trigger, state_dict):
                self.trigger_history.append(trigger.name)
                return HardLimitResult(
                    triggered=True,
                    trigger_name=trigger.name,
                    trigger_definition=trigger,
                    message=f"Hard Limit triggered: {trigger.meaning}",
                )

        return HardLimitResult(triggered=False)

    def _evaluate_trigger(
        self, trigger: TriggerDefinition, state: Dict[str, Any]
    ) -> bool:
        """Evaluate a single trigger condition"""
        try:
            return eval(trigger.condition, {}, state)
        except Exception:
            # If evaluation fails, consider trigger not activated
            return False

    def get_trigger_history(self) -> List[HardLimitTrigger]:
        """Get history of triggered Hard Limits"""
        return self.trigger_history.copy()

    def clear_history(self):
        """Clear trigger history"""
        self.trigger_history.clear()

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about Hard Limit triggers"""
        from collections import Counter

        counts = Counter(self.trigger_history)

        return {
            "total_triggers": len(self.trigger_history),
            "by_type": {trigger.value: count for trigger, count in counts.items()},
            "most_common": counts.most_common(3),
        }


@dataclass
class EscalationInfo:
    """Information for escalation handling"""

    trigger: HardLimitTrigger
    action: EscalationAction
    state: HardLimitState
    messages: List[Dict[str, Any]]
    context: Dict[str, Any]

    def to_summary(self) -> str:
        """Create human-readable escalation summary"""
        trigger_def = TriggerDefinition.get_all_triggers()
        trigger_info = next((t for t in trigger_def if t.name == self.trigger), None)

        return (
            f"Hard Limit Escalation\n"
            f"Trigger: {self.trigger.value}\n"
            f"Meaning: {trigger_info.meaning if trigger_info else 'Unknown'}\n"
            f"Action: {self.action.value}\n"
            f"Turn: {self.state.turn}/{self.state.max_turns}\n"
            f"AST Errors: {self.state.ast_error_count}\n"
            f"Clarifications: {self.state.clarification_count}\n"
            f"Last Diff Lines: {self.state.diff_lines}"
        )


def create_escalation_handler():
    """
    Factory function to create escalation handler.

    Returns a function that handles escalations based on Hard Limit results.
    """

    async def handle_escalation(info: EscalationInfo) -> Dict[str, Any]:
        """
        Handle escalation based on trigger type.

        Args:
            info: EscalationInfo with trigger details

        Returns:
            Dictionary with escalation result
        """
        if info.action == EscalationAction.ESCALATE_TO_BRAIN:
            return await _escalate_to_brain(info)
        elif info.action == EscalationAction.ESCALATE_TO_PLANNER:
            return await _escalate_to_planner(info)
        elif info.action == EscalationAction.FORCE_LAST_STATE:
            return _force_last_state(info)
        else:
            return {"action": "continue", "state": "unchanged"}

    return handle_escalation


async def _escalate_to_brain(info: EscalationInfo) -> Dict[str, Any]:
    """Escalate to Brain for analysis and re-instruction"""
    return {
        "action": "escalate_to_brain",
        "reason": info.trigger.value,
        "summary": info.to_summary(),
        "requires_brain_analysis": True,
        "suggested_action": "Brain should analyze root cause and re-instruct or escalate to Planner",
    }


async def _escalate_to_planner(info: EscalationInfo) -> Dict[str, Any]:
    """Escalate to Planner for plan revision"""
    return {
        "action": "escalate_to_planner",
        "reason": info.trigger.value,
        "summary": info.to_summary(),
        "requires_plan_revision": True,
        "suggested_action": "Planner should revise plan - instructions are fundamentally unclear",
    }


def _force_last_state(info: EscalationInfo) -> Dict[str, Any]:
    """Force use of last state and continue to Judge"""
    return {
        "action": "force_last_state",
        "reason": info.trigger.value,
        "summary": info.to_summary(),
        "proceed_to_judge": True,
        "suggested_action": "Proceed to Judge evaluation with last available state",
    }


# Convenience function for quick checks


def check_hard_limit(
    turn: int,
    diff_lines: int,
    ast_error_count: int,
    clarification_count: int,
    max_turns: int,
    current_code: Optional[str] = None,
    immediate_consensus: bool = False,
) -> HardLimitResult:
    """
    Quick Hard Limit check without creating checker instance.

    Usage:
        result = check_hard_limit(
            turn=1,
            diff_lines=2,
            ast_error_count=0,
            clarification_count=1,
            max_turns=3,
            current_code=code_string
        )

        if result.triggered:
            handle_trigger(result)
    """
    state = HardLimitState.from_chat_chain_state(
        turn=turn,
        diff_lines=diff_lines,
        ast_error_count=ast_error_count,
        clarification_count=clarification_count,
        max_turns=max_turns,
        current_code=current_code,
        immediate_consensus=immediate_consensus,
    )

    checker = HardLimitChecker()
    return checker.check(state)
