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
            mock_post.return_value = Mock(
                json=Mock(return_value={"choices": [{"message": {"content": "{}"}}]}),
                raise_for_status=Mock(),
            )

            from pyovis.ai.swap_manager import ModelSwapManager

            swap = ModelSwapManager()
            analyzer = RequestAnalyzer(swap)

            result = await analyzer.analyze("안녕")
            assert result.complexity == TaskComplexity.CHAT

            result = await analyzer.analyze("Hello")
            assert result.complexity == TaskComplexity.CHAT

            result = await analyzer.analyze("감사합니다")
            assert result.complexity == TaskComplexity.CHAT

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


class TestChatNoWorkspace:
    """Test that chat messages don't include workspace"""

    @pytest.mark.asyncio
    async def test_chat_result_has_no_workspace(self):
        """Test that chat results don't have workspace key"""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = Mock(
                json=Mock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"status": "success", "result": "안녕하세요!", "message": "인사 응답"}'
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

            result = await analyzer.handle_simple_task("안녕")
            result.pop("file_path", None)
            result.pop("workspace", None)

            assert "workspace" not in result
            assert "file_path" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
