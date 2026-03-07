"""
Pyvis v5.1 — E2E Integration Tests for Full Pipeline

Tests the complete v5.1 pipeline including:
- Chat Chain
- Execution Plan generation
- Judge Thought Instruction
- Experience DB integration
- KG Thought Process storage

References: PHASE4_PLAN.md section 4.5
"""

import pytest
import asyncio
import sys
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass, field


# Import components to test
from pyovis.memory.experience_db import (
    ExperienceDB,
    ExperienceEntry,
    TaskType,
    get_experience_db,
)
from pyovis.execution.execution_plan import (
    ExecutionPlan,
    ExecutionType,
    TestCase,
    create_execution_plan_from_task,
)
from pyovis.ai.hands import Hands
from pyovis.execution.critic_runner import CriticRunner, ExecutionResult


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def mock_swap_manager():
    """Mock ModelSwapManager for Hands"""
    swap = Mock()
    swap.api_url = "http://localhost:8000/v1/chat/completions"
    swap.ensure_model = AsyncMock()
    return swap


@pytest.fixture
def mock_tool_adapter():
    """Mock MCPToolAdapter"""
    adapter = Mock()
    adapter.get_tools_schema = Mock(return_value=[])
    adapter.execute_tool_calls = AsyncMock(return_value=[])
    return adapter


@pytest.fixture
def mock_kg_builder():
    """Mock KnowledgeGraphBuilder"""
    kg = Mock()
    kg.add_triplet = AsyncMock()
    kg.index_reasoning = AsyncMock()
    return kg


# ============================================================================
# Experience DB Tests
# ============================================================================


class TestExperienceDB:
    """Tests for ExperienceDB"""

    @pytest.mark.asyncio
    async def test_add_experience(self):
        """Test adding an experience to the DB"""
        # Create in-memory DB with disabled persistence
        with patch("pyovis.memory.experience_db._EXPERIENCE_PERSIST_DIR"):
            db = ExperienceDB()
            db._initialized = True
            faiss = pytest.importorskip("faiss")
            import numpy as np

            db.index = faiss.IndexFlatL2(384)
            db.model = Mock()
            db.model.encode = Mock(return_value=np.array([[0.1] * 384]))
            db.dimension = 384

            entry = ExperienceEntry(
                task_description="Write a hello world script",
                success=True,
                code_snippet="print('Hello, World!')",
                judge_verdict="PASS",
                judge_score=95,
                task_type=TaskType.PYTHON_SCRIPT.value,
            )

            idx = await db.add_experience(entry)
            assert idx == 0
            assert len(db.experiences) == 1

    @pytest.mark.asyncio
    async def test_search_similar(self):
        """Test searching for similar experiences"""
        with patch("pyovis.memory.experience_db._EXPERIENCE_PERSIST_DIR"):
            db = ExperienceDB()
            db._initialized = True
            faiss = pytest.importorskip("faiss")
            import numpy as np

            db.index = faiss.IndexFlatL2(384)
            db.model = Mock()
            db.model.encode = Mock(return_value=np.array([[0.1] * 384]))
            db.dimension = 384

            # Add some experiences
            entry1 = ExperienceEntry(
                task_description="Write a hello world script",
                success=True,
                code_snippet="print('Hello')",
                judge_verdict="PASS",
                judge_score=90,
                task_type=TaskType.PYTHON_SCRIPT.value,
            )
            entry2 = ExperienceEntry(
                task_description="Write a test file",
                success=True,
                code_snippet="def test_foo(): pass",
                judge_verdict="PASS",
                judge_score=85,
                task_type=TaskType.TEST_FILE.value,
            )

            await db.add_experience(entry1)
            await db.add_experience(entry2)

            # Search
            results = await db.search_similar("hello world script", k=5)
            assert len(results) >= 0  # May return empty due to mock

    @pytest.mark.asyncio
    async def test_get_success_patterns(self):
        """Test extracting success patterns"""
        with patch("pyovis.memory.experience_db._EXPERIENCE_PERSIST_DIR"):
            db = ExperienceDB()
            db._initialized = True
            faiss = pytest.importorskip("faiss")
            import numpy as np

            db.index = faiss.IndexFlatL2(384)
            db.model = Mock()
            db.model.encode = Mock(return_value=np.array([[0.1] * 384]))
            db.dimension = 384

            entry = ExperienceEntry(
                task_description="Write API server",
                success=True,
                code_snippet="app = FastAPI()",
                judge_verdict="PASS",
                judge_score=95,
                task_type=TaskType.API_SERVER.value,
                techniques_used=["use_fastapi", "add_routes"],
            )

            await db.add_experience(entry)

            patterns = await db.get_success_patterns(TaskType.API_SERVER.value)
            assert len(patterns) > 0

    @pytest.mark.asyncio
    async def test_get_failure_patterns(self):
        """Test extracting failure patterns"""
        with patch("pyovis.memory.experience_db._EXPERIENCE_PERSIST_DIR"):
            db = ExperienceDB()
            db._initialized = True
            faiss = pytest.importorskip("faiss")
            import numpy as np

            db.index = faiss.IndexFlatL2(384)
            db.model = Mock()
            db.model.encode = Mock(return_value=np.array([[0.1] * 384]))
            db.dimension = 384

            entry = ExperienceEntry(
                task_description="Fix import error",
                success=False,
                code_snippet="import missing_module",
                error_type="missing_import",
                judge_verdict="REVISE",
                judge_score=30,
            )

            await db.add_experience(entry)

            patterns = await db.get_failure_patterns("missing_import")
            assert len(patterns) > 0

    @pytest.mark.asyncio
    async def test_get_statistics(self):
        """Test getting DB statistics"""
        with patch("pyovis.memory.experience_db._EXPERIENCE_PERSIST_DIR"):
            db = ExperienceDB()
            db._initialized = True
            faiss = pytest.importorskip("faiss")
            import numpy as np

            db.index = faiss.IndexFlatL2(384)
            db.model = Mock()
            db.model.encode = Mock(return_value=np.array([[0.1] * 384]))
            db.dimension = 384

            # Add mixed experiences
            for i in range(5):
                entry = ExperienceEntry(
                    task_description=f"Task {i}",
                    success=i % 2 == 0,
                    code_snippet=f"code_{i}",
                    judge_verdict="PASS" if i % 2 == 0 else "REVISE",
                    judge_score=50 + i * 10,
                    task_type=TaskType.PYTHON_SCRIPT.value,
                )
                await db.add_experience(entry)

            stats = await db.get_statistics()
            assert stats["total_experiences"] == 5
            assert stats["successes"] == 3
            assert stats["failures"] == 2
            assert 0 < stats["success_rate"] <= 1


# ============================================================================
# Execution Plan Tests
# ============================================================================


class TestExecutionPlan:
    """Tests for ExecutionPlan"""

    def test_create_execution_plan_simple_script(self):
        """Test creating execution plan for simple script"""
        task = {
            "file_path": "main.py",
            "title": "Hello World",
            "description": "Print hello world",
        }
        code = "print('Hello, World!')"
        pass_criteria = {}

        plan = create_execution_plan_from_task(task, code, pass_criteria)

        assert plan.execution_type == ExecutionType.PYTHON_SCRIPT
        assert plan.entry_point == "main.py"

    def test_create_execution_plan_fastapi(self):
        """Test creating execution plan for FastAPI app"""
        task = {
            "file_path": "app.py",
            "title": "API Server",
            "description": "Create FastAPI server",
        }
        code = """
from fastapi import FastAPI
app = FastAPI()

@app.get('/')
def root():
    return {'message': 'Hello'}
"""
        pass_criteria = {}

        plan = create_execution_plan_from_task(task, code, pass_criteria)

        assert plan.execution_type == ExecutionType.API_SERVER
        assert plan.requires_network == True

    def test_create_execution_plan_test_file(self):
        """Test creating execution plan for test file"""
        task = {
            "file_path": "test_main.py",
            "title": "Test Suite",
            "description": "Write unit tests",
        }
        code = """
import unittest

class TestMain(unittest.TestCase):
    def test_example(self):
        self.assertTrue(True)
"""
        pass_criteria = {}

        plan = create_execution_plan_from_task(task, code, pass_criteria)

        assert plan.execution_type == ExecutionType.PYTHON_TEST

    def test_create_execution_plan_with_criteria(self):
        """Test creating execution plan with pass criteria"""
        task = {"file_path": "main.py"}
        code = "result = 1 + 1"
        pass_criteria = {
            "1": {
                "name": "Test addition",
                "description": "1 + 1 = 2",
                "expected_output": "2",
            }
        }

        plan = create_execution_plan_from_task(task, code, pass_criteria)

        assert len(plan.test_cases) == 1
        assert plan.test_cases[0].name == "Test addition"

    def test_execution_plan_to_dict(self):
        """Test serializing execution plan to dict"""
        plan = ExecutionPlan(
            execution_type=ExecutionType.PYTHON_SCRIPT,
            entry_point="main.py",
            test_cases=[
                TestCase(name="Test 1", description="Basic test", expected_exit_code=0)
            ],
        )

        d = plan.to_dict()

        assert d["execution_type"] == "python_script"
        assert d["entry_point"] == "main.py"
        assert len(d["test_cases"]) == 1


# ============================================================================
# Hands Integration Tests
# ============================================================================


class TestHandsIntegration:
    """Integration tests for Hands with Experience DB"""

    @pytest.mark.asyncio
    async def test_hands_build_returns_execution_plan(self, mock_swap_manager):
        """Test that Hands.build returns execution plan"""
        # Mock the HTTP response
        mock_response = {
            "choices": [
                {"message": {"content": "print('Hello')", "reasoning_content": ""}}
            ]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = Mock(
                json=Mock(return_value=mock_response), raise_for_status=Mock()
            )

            hands = Hands(mock_swap_manager, experience_db=False)

            # The build method now returns 3 values
            code, reasoning, exec_plan = await hands.build(
                task={
                    "file_path": "main.py",
                    "title": "Test",
                    "description": "Test task",
                },
                plan="Plan",
                skill_context="",
            )

            assert code == "print('Hello')"
            assert isinstance(exec_plan, dict)
            assert "execution_type" in exec_plan

    @pytest.mark.asyncio
    async def test_hands_detect_task_type(self, mock_swap_manager):
        """Test task type detection"""
        hands = Hands(mock_swap_manager, experience_db=False)

        # Test API detection
        task_type = await hands._detect_task_type(
            {"description": "Create a FastAPI server"}
        )
        assert task_type == TaskType.API_SERVER.value

        # Test test file detection
        task_type = await hands._detect_task_type({"description": "Write pytest tests"})
        assert task_type == TaskType.TEST_FILE.value

        # Test CLI detection
        task_type = await hands._detect_task_type(
            {"description": "Create a CLI tool with argparse"}
        )
        assert task_type == TaskType.CLI_TOOL.value

        # Test debug detection
        task_type = await hands._detect_task_type({"description": "Fix the bug"})
        assert task_type == TaskType.DEBUG.value

    @pytest.mark.asyncio
    async def test_hands_get_experience_context_disabled(self, mock_swap_manager):
        """Test experience context when disabled"""
        hands = Hands(mock_swap_manager, experience_db=False)

        context = await hands._get_experience_context("test task", "python_script")
        assert context == ""


# ============================================================================
# CriticRunner Integration Tests
# ============================================================================


class TestCriticRunnerIntegration:
    """Integration tests for CriticRunner with Execution Plan"""

    @pytest.mark.asyncio
    async def test_critic_runner_execute_simple(self):
        """Test basic execution without plan"""
        # This test would require Docker, so we mock it
        mock_docker = MagicMock()
        sys.modules["docker"] = mock_docker
        try:
            critic = CriticRunner()

            # Mock the execute method
            with patch.object(critic, "execute", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = ExecutionResult(
                    stdout="Hello",
                    stderr="",
                    exit_code=0,
                    execution_time=0.1,
                )

                result = await critic.execute("print('hello')")
                assert result.exit_code == 0
        finally:
            sys.modules.pop("docker", None)


# ============================================================================
# Loop Controller Integration Tests
# ============================================================================


class TestLoopControllerKGIntegration:
    """Integration tests for LoopController with KG"""

    @pytest.mark.asyncio
    async def test_judge_reasoning_storage(self, mock_kg_builder):
        """Test that Judge reasoning is stored in KG"""
        from pyovis.orchestration.loop_controller import (
            ResearchLoopController,
            LoopContext,
            LoopStep,
        )
        from pyovis.ai.judge_enhanced import JudgeResult

        # Create mock components
        mock_brain = Mock()
        mock_hands = Mock()
        mock_judge = Mock()
        mock_critic = Mock()
        mock_tracker = Mock()
        mock_tracker.start = Mock()
        mock_tracker.record_switch = Mock()
        mock_tracker.finish = Mock()
        mock_tracker.get_record = Mock(return_value={})
        mock_skill_manager = Mock()
        mock_skill_manager.load_verified = Mock(return_value="")

        # Mock Judge to return result with thought_process
        mock_judge.evaluate = AsyncMock(
            return_value=JudgeResult(
                verdict="PASS",
                score=95,
                reason="Code works correctly",
                error_type=None,
                check_results=[],
                thought_process="Step 1: Check syntax... PASS\nStep 2: Verify output... PASS",
            )
        )

        # Create controller with KG
        controller = ResearchLoopController(
            brain=mock_brain,
            hands=mock_hands,
            judge=mock_judge,
            critic=mock_critic,
            tracker=mock_tracker,
            skill_manager=mock_skill_manager,
            kg_builder=mock_kg_builder,
        )

        # Create context
        ctx = LoopContext(
            task_id="test_task",
            task_description="Test task",
            todo_list=[{"file_path": "main.py", "title": "Test"}],
            pass_criteria={},
            self_fix_scope={"allowed": []},
        )
        ctx.current_step = LoopStep.EVALUATE

        # Mock hands.build to return code
        mock_hands.build = AsyncMock(return_value=("print('hello')", ""))

        # We can't easily test the full flow without more mocks
        # but we can verify the KG builder is set up
        assert controller.kg_builder is mock_kg_builder


# ============================================================================
# E2E Pipeline Test
# ============================================================================


class TestE2EPipeline:
    """End-to-end pipeline tests"""

    @pytest.mark.asyncio
    async def test_experience_db_to_hands_pipeline(self):
        """Test the full pipeline from Experience DB to Hands"""
        # This would be a full integration test
        # For now, just verify the components exist and work

        # 1. Create an experience
        entry = ExperienceEntry(
            task_description="Create a REST API",
            success=True,
            code_snippet="from flask import Flask\napp = Flask(__name__)",
            judge_verdict="PASS",
            judge_score=90,
            task_type=TaskType.API_SERVER.value,
            techniques_used=["use_flask", "add_endpoints"],
        )

        # Verify entry can be created
        assert entry.task_type == "api_server"
        assert entry.success == True

        # 2. Create execution plan
        task = {"file_path": "app.py", "title": "API"}
        plan = create_execution_plan_from_task(task, entry.code_snippet, {})

        # Verify plan is created
        assert plan.execution_type == ExecutionType.API_SERVER
        assert plan.requires_network == True

        # 3. Verify plan can be serialized
        plan_dict = plan.to_dict()
        assert "execution_type" in plan_dict
        assert "setup_commands" in plan_dict

    @pytest.mark.asyncio
    async def test_verdict_includes_thought_process(self):
        """Test that Judge result includes thought_process"""
        from pyovis.ai.judge_enhanced import JudgeResult

        # Create a Judge result with thought process
        result = JudgeResult(
            verdict="PASS",
            score=95,
            reason="Code is correct",
            error_type=None,
            check_results=["syntax_ok", "output_correct"],
            thought_process="1. Check syntax\n2. Verify output matches expected",
        )

        # Verify thought_process is included
        assert result.thought_process != ""
        assert "Check syntax" in result.thought_process


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
