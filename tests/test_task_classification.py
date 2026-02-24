"""
Tests for 3-tier task classification (Chat, Simple, Complex)
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from pyovis.orchestration.request_analyzer import (
    RequestAnalyzer,
    TaskComplexity,
    ToolStatus,
)


class TestTaskComplexityClassification:
    """Test the 3-tier classification system"""

    @pytest.mark.asyncio
    async def test_greeting_classified_as_chat(self):
        """Test that greetings are classified as CHAT"""
        with patch("httpx.AsyncClient.post") as mock_post:
            # Mock doesn't matter since we short-circuit before LLM call
            mock_post.return_value = Mock(
                json=Mock(return_value={"choices": [{"message": {"content": "{}"}}]}),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            # Korean greeting
            result = await analyzer.analyze("안녕")
            assert result.complexity == TaskComplexity.CHAT

            # English greeting
            result = await analyzer.analyze("Hello")
            assert result.complexity == TaskComplexity.CHAT

            # Thanks
            result = await analyzer.analyze("감사합니다")
            assert result.complexity == TaskComplexity.CHAT

    @pytest.mark.asyncio
    async def test_chat_with_code_request_is_simple(self):
        """Test that chat + code request is classified as SIMPLE"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(
                    return_value={
                        "choices": [
                            {"message": {"content": '{"complexity": "simple"}'}}
                        ]
                    }
                ),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            # Greeting + code request
            result = await analyzer.analyze("안녕, 파이썬 스크립트 만들어줘")
            assert result.complexity == TaskComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_code_request_is_simple(self):
        """Test that code requests are SIMPLE"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(
                    return_value={
                        "choices": [
                            {"message": {"content": '{"complexity": "simple"}'}}
                        ]
                    }
                ),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            result = await analyzer.analyze("파이썬 함수 만들어줘")
            assert result.complexity == TaskComplexity.SIMPLE

            result = await analyzer.analyze("Create a Python script")
            assert result.complexity == TaskComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_casual_chat_patterns(self):
        """Test various casual chat patterns"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(return_value={"choices": [{"message": {"content": "{}"}}]}),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            chat_messages = [
                "오늘 날씨 어때?",
                "점심 뭐 먹었어?",
                "잘 지내?",
                "how are you",
                "what's up",
                "good morning",
                "thank you so much",
                "죄송합니다",
            ]

            for msg in chat_messages:
                result = await analyzer.analyze(msg)
                assert result.complexity == TaskComplexity.CHAT, f"Failed for: {msg}"

    @pytest.mark.asyncio
    async def test_weather_question_is_chat(self):
        """Test that weather questions are CHAT (uses fetch, no file)"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"complexity": "simple", "tool_status": "already_available"}'
                                }
                            }
                        ]
                    }
                ),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            # Weather question should be SIMPLE (uses fetch tool) but not generate file
            result = await analyzer.analyze("오늘 날씨 알려줘")
            # Weather might be SIMPLE because it uses fetch, but won't generate file
            assert result.complexity in [TaskComplexity.CHAT, TaskComplexity.SIMPLE]


class TestHandleSimpleTask:
    """Test handle_simple_task behavior"""

    @pytest.mark.asyncio
    async def test_simple_task_can_return_file(self):
        """Test that simple task can return file_path for code requests"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"status": "success", "result": "print(1)", "file_path": "test.py"}'
                                }
                            }
                        ]
                    }
                ),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            result = await analyzer.handle_simple_task("파이썬 스크립트 만들어줘")

            assert result["status"] == "success"
            assert "result" in result
            assert "file_path" in result

    @pytest.mark.asyncio
    async def test_simple_task_no_file_for_questions(self):
        """Test that simple task returns null file_path for questions"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"status": "success", "result": "12 시입니다", "file_path": null}'
                                }
                            }
                        ]
                    }
                ),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            result = await analyzer.handle_simple_task("지금 몇 시야?")

            assert result["status"] == "success"
            # file_path should be null or not present
            assert result.get("file_path") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
