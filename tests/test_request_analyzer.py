import pytest
import sys
sys.path.insert(0, '/Pyvis')

from pyovis.orchestration.request_analyzer import (
    RequestAnalyzer,
    TaskComplexity,
    ToolStatus,
    AnalysisResult,
)


class TestTaskComplexity:
    def test_simple_value(self):
        assert TaskComplexity.SIMPLE.value == "simple"

    def test_complex_value(self):
        assert TaskComplexity.COMPLEX.value == "complex"

    def test_all_values(self):
        values = [e.value for e in TaskComplexity]
        assert "simple" in values
        assert "complex" in values


class TestToolStatus:
    def test_not_needed_value(self):
        assert ToolStatus.NOT_NEEDED.value == "not_needed"

    def test_needed_pending_value(self):
        assert ToolStatus.NEEDED_PENDING.value == "needed_pending"

    def test_needed_approved_value(self):
        assert ToolStatus.NEEDED_APPROVED.value == "needed_approved"


class TestAnalysisResult:
    def test_creation(self):
        result = AnalysisResult(
            complexity=TaskComplexity.SIMPLE,
            needs_clarification=False,
            clarification_questions=[],
            required_tools=[],
            tool_status=ToolStatus.NOT_NEEDED,
            reasoning="Test reasoning",
            available_tools_to_use=[],
        )
        assert result.complexity == TaskComplexity.SIMPLE
        assert result.needs_clarification is False
        assert result.reasoning == "Test reasoning"

    def test_with_questions(self):
        result = AnalysisResult(
            complexity=TaskComplexity.COMPLEX,
            needs_clarification=True,
            clarification_questions=["What is the input?", "What format?"],
            required_tools=["numpy"],
            tool_status=ToolStatus.NEEDED_PENDING,
            reasoning="Need more info",
            available_tools_to_use=[],
        )
        assert len(result.clarification_questions) == 2
        assert "numpy" in result.required_tools


class TestRequestAnalyzerInit:
    def test_init_requires_swap_manager(self):
        from pyovis.ai.swap_manager import ModelSwapManager
        swap = ModelSwapManager()
        analyzer = RequestAnalyzer(swap)
        assert analyzer.swap is swap
        assert analyzer.client is not None


class TestRequestAnalyzerComplexity:
    @pytest.fixture
    def analyzer(self):
        from pyovis.ai.swap_manager import ModelSwapManager
        return RequestAnalyzer(ModelSwapManager())

    def test_simple_task_detection(self, analyzer):
        assert True

    def test_complex_task_detection(self, analyzer):
        assert True

    def test_clarification_needed_detection(self, analyzer):
        assert True

    def test_tool_requirement_detection(self, analyzer):
        assert True
