from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass

from pyovis.ai.response_utils import message_text, parse_json_message
from pyovis.ai.brain import strip_cot


# ---------------------------------------------------------------------------
# response_utils
# ---------------------------------------------------------------------------

class TestMessageText:
    def test_returns_content(self):
        assert message_text({"content": "hello"}) == "hello"

    def test_falls_back_to_reasoning_content(self):
        assert message_text({"content": "", "reasoning_content": "think"}) == "think"

    def test_returns_empty_when_both_missing(self):
        assert message_text({}) == ""

    def test_prefers_content_over_reasoning(self):
        assert message_text({"content": "main", "reasoning_content": "alt"}) == "main"


class TestParseJsonMessage:
    def test_extracts_json_from_text(self):
        msg = {"content": 'Some text {"key": "value"} more text'}
        result = parse_json_message(msg)
        assert result == {"key": "value"}

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="Empty model response"):
            parse_json_message({"content": ""})

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            parse_json_message({"content": "no json here"})

    def test_handles_trailing_comma(self):
        msg = {"content": '{"a": 1, "b": 2,}'}
        result = parse_json_message(msg)
        assert result == {"a": 1, "b": 2}

    def test_nested_json(self):
        msg = {"content": '{"plan": "x", "items": [{"id": 1}]}'}
        result = parse_json_message(msg)
        assert result["items"] == [{"id": 1}]

    def test_uses_reasoning_content_fallback(self):
        msg = {"content": "", "reasoning_content": '{"ok": true}'}
        result = parse_json_message(msg)
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# strip_cot
# ---------------------------------------------------------------------------

class TestStripCot:
    def test_removes_think_block(self):
        text = "<think>internal reasoning</think>Final answer"
        assert strip_cot(text) == "Final answer"

    def test_removes_multiline_think(self):
        text = "<think>\nline1\nline2\n</think>\nOutput"
        assert strip_cot(text) == "Output"

    def test_no_think_block(self):
        assert strip_cot("plain text") == "plain text"

    def test_multiple_think_blocks(self):
        text = "<think>a</think>mid<think>b</think>end"
        assert strip_cot(text) == "midend"

    def test_empty_string(self):
        assert strip_cot("") == ""


# ---------------------------------------------------------------------------
# Hands — code fence stripping
# ---------------------------------------------------------------------------

class TestHandsCodeFenceStripping:
    def test_strips_opening_fence(self):
        import re
        from pyovis.ai.hands import _CODE_FENCE_RE, _CODE_FENCE_CLOSE_RE

        raw = "```python\nprint('hello')\n```"
        result = _CODE_FENCE_RE.sub("", raw)
        result = _CODE_FENCE_CLOSE_RE.sub("", result)
        assert result.strip() == "print('hello')"

    def test_strips_bare_fence(self):
        from pyovis.ai.hands import _CODE_FENCE_RE, _CODE_FENCE_CLOSE_RE

        raw = "```\ncode\n```"
        result = _CODE_FENCE_RE.sub("", raw)
        result = _CODE_FENCE_CLOSE_RE.sub("", result)
        assert result.strip() == "code"

    def test_no_fence_passthrough(self):
        from pyovis.ai.hands import _CODE_FENCE_RE, _CODE_FENCE_CLOSE_RE

        raw = "just plain code"
        result = _CODE_FENCE_RE.sub("", raw)
        result = _CODE_FENCE_CLOSE_RE.sub("", result)
        assert result.strip() == "just plain code"


# ---------------------------------------------------------------------------
# SwapManagerConfig defaults
# ---------------------------------------------------------------------------

class TestSwapManagerConfig:
    def test_default_ctx_sizes(self):
        from pyovis.ai.swap_manager import SwapManagerConfig
        cfg = SwapManagerConfig()
        assert cfg.ctx_size_planner == 32768  # v5.1 reduced
        assert cfg.ctx_size_brain == 32768    # v5.1 reduced
        assert cfg.ctx_size_judge == 16384    # v5.1 reduced
        assert cfg.ctx_size_hands_normal == 32768  # v5.1: symbol extraction success mode
        assert cfg.ctx_size_hands_fallback == 58368  # v5.1: fallback mode
        # Backward compatibility alias
        assert cfg.ctx_size_hands == 16384

    def test_default_ngl(self):
        from pyovis.ai.swap_manager import SwapManagerConfig
        cfg = SwapManagerConfig()
        assert cfg.n_gpu_layers == 60
        assert cfg.n_gpu_layers_hands == 40
        assert cfg.n_gpu_layers_planner == 60

    def test_default_kv_cache(self):
        from pyovis.ai.swap_manager import SwapManagerConfig
        cfg = SwapManagerConfig()
        assert cfg.cache_type_k == "q8_0"
        assert cfg.cache_type_v == "q8_0"
        assert cfg.cache_type_k_hands_normal == "q8_0"  # v5.1
        assert cfg.cache_type_k_hands_fallback == "q4_0"  # v5.1
        # Backward compatibility
        assert cfg.cache_type_k_brain == "q4_0"
        assert cfg.cache_type_v_brain == "q4_0"

    def test_jinja_roles(self):
        from pyovis.ai.swap_manager import SwapManagerConfig
        cfg = SwapManagerConfig()
        assert "hands" in cfg.jinja_roles
        assert "brain" not in cfg.jinja_roles

    def test_fallbacks(self):
        from pyovis.ai.swap_manager import SwapManagerConfig
        cfg = SwapManagerConfig()
        assert cfg.fallbacks == {"planner": "brain", "hands": "brain"}

    def test_model_paths(self):
        from pyovis.ai.swap_manager import SwapManagerConfig
        cfg = SwapManagerConfig()
        assert len(cfg.models) == 4
        assert "planner" in cfg.models
        assert "brain" in cfg.models
        assert "hands" in cfg.models
        assert "judge" in cfg.models


# ---------------------------------------------------------------------------
# ModelSwapManager — _ctx_size_for_role / _ngl_for_role
# ---------------------------------------------------------------------------

class TestSwapManagerRoleParams:
    def test_ctx_size_for_each_role(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig, ModelRole

        with patch("pyovis.ai.swap_manager.Path") as mock_path:
            mock_path.return_value.mkdir = MagicMock()
            mgr = ModelSwapManager.__new__(ModelSwapManager)
            mgr.config = SwapManagerConfig()

        assert mgr._ctx_size_for_role(ModelRole.PLANNER) == 32768  # v5.1
        assert mgr._ctx_size_for_role(ModelRole.BRAIN) == 32768    # v5.1
        assert mgr._ctx_size_for_role(ModelRole.JUDGE) == 16384    # v5.1
        # HANDS uses dual mode via config, _ctx_size_for_role returns ctx_size_hands
        assert mgr.config.ctx_size_hands_normal == 32768
        assert mgr.config.ctx_size_hands_fallback == 58368
        assert mgr._ctx_size_for_role(ModelRole.HANDS) == 16384  # backward compat field

    def test_ngl_for_each_role(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig

        mgr = ModelSwapManager.__new__(ModelSwapManager)
        mgr.config = SwapManagerConfig()

        assert mgr._ngl_for_role("hands") == 40
        assert mgr._ngl_for_role("planner") == 60
        assert mgr._ngl_for_role("brain") == 60
        assert mgr._ngl_for_role("judge") == 60


# ---------------------------------------------------------------------------
# ModelSwapManager — ensure_model fallback
# ---------------------------------------------------------------------------

class TestSwapManagerFallback:
    @pytest.mark.asyncio
    async def test_fallback_when_model_missing(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig, ModelRole
        import asyncio

        mgr = ModelSwapManager.__new__(ModelSwapManager)
        mgr.config = SwapManagerConfig()
        mgr.config.models["planner"] = "/pyovis_memory/models/NonExistent-Model.gguf"
        mgr._current_role = None
        mgr._process = None
        mgr._swap_count = 0
        mgr._base_url = "http://localhost:8001"
        mgr._lock = asyncio.Lock()
        mgr._http_client = AsyncMock()

        with patch("pyovis.ai.swap_manager.Path") as mock_path:
            exists_calls = []

            def path_init(p):
                m = MagicMock()
                m.exists.return_value = not p.endswith("NonExistent-Model.gguf")
                m.__str__ = lambda self: p
                m.mkdir = MagicMock()
                m.parent = MagicMock()
                m.parent.mkdir = MagicMock()
                return m

            mock_path.side_effect = path_init

            mgr._swap_to = AsyncMock(return_value=True)

            result = await mgr.ensure_model("planner")

            # planner model doesn't exist → falls back to brain
            mgr._swap_to.assert_awaited_once()
            call_args = mgr._swap_to.call_args[0]
            assert call_args[0] == ModelRole.BRAIN

    @pytest.mark.asyncio
    async def test_no_fallback_returns_false(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig
        import asyncio

        mgr = ModelSwapManager.__new__(ModelSwapManager)
        mgr.config = SwapManagerConfig()
        mgr.config.fallbacks = {}
        mgr._current_role = None
        mgr._process = None
        mgr._swap_count = 0
        mgr._base_url = "http://localhost:8001"
        mgr._lock = asyncio.Lock()
        mgr._http_client = AsyncMock()

        with patch("pyovis.ai.swap_manager.Path") as mock_path:
            m = MagicMock()
            m.exists.return_value = False
            mock_path.return_value = m

            result = await mgr.ensure_model("planner")
            assert result is False


# ---------------------------------------------------------------------------
# ModelSwapManager — health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_ok(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig
        import asyncio

        mgr = ModelSwapManager.__new__(ModelSwapManager)
        mgr.config = SwapManagerConfig()
        mgr._base_url = "http://localhost:8001"
        mgr._http_client = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mgr._http_client.get = AsyncMock(return_value=mock_resp)

        assert await mgr._health_check(retries=1) is True

    @pytest.mark.asyncio
    async def test_health_check_not_ok_status(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig

        mgr = ModelSwapManager.__new__(ModelSwapManager)
        mgr.config = SwapManagerConfig()
        mgr._base_url = "http://localhost:8001"
        mgr._http_client = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "loading"}
        mgr._http_client.get = AsyncMock(return_value=mock_resp)

        assert await mgr._health_check(retries=1) is False

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self):
        from pyovis.ai.swap_manager import ModelSwapManager, SwapManagerConfig

        mgr = ModelSwapManager.__new__(ModelSwapManager)
        mgr.config = SwapManagerConfig()
        mgr._base_url = "http://localhost:8001"
        mgr._http_client = AsyncMock()
        mgr._http_client.get = AsyncMock(side_effect=ConnectionError("refused"))

        assert await mgr._health_check(retries=1) is False


# ---------------------------------------------------------------------------
# ModelRole enum
# ---------------------------------------------------------------------------

class TestModelRole:
    def test_all_roles(self):
        from pyovis.ai.swap_manager import ModelRole
        assert ModelRole.PLANNER.value == "planner"
        assert ModelRole.BRAIN.value == "brain"
        assert ModelRole.HANDS.value == "hands"
        assert ModelRole.JUDGE.value == "judge"

    def test_str_enum(self):
        from pyovis.ai.swap_manager import ModelRole
        assert str(ModelRole.BRAIN) == "ModelRole.BRAIN"
        assert ModelRole("brain") == ModelRole.BRAIN


# ---------------------------------------------------------------------------
# Judge._parse
# ---------------------------------------------------------------------------

class TestJudgeParse:
    def _get_judge_parse(self):
        from pyovis.ai.judge import Judge
        j = Judge.__new__(Judge)
        return j._parse

    def test_parse_valid_json(self):
        parse = self._get_judge_parse()
        resp = '{"verdict": "PASS", "score": 90, "reason": "good", "error_type": null}'
        result = parse(resp)
        assert result.verdict == "PASS"
        assert result.score == 90
        assert result.reason == "good"
        assert result.error_type is None

    def test_parse_json_in_code_fence(self):
        parse = self._get_judge_parse()
        resp = '```json\n{"verdict": "REVISE", "score": 60, "reason": "bad", "error_type": "type_error"}\n```'
        result = parse(resp)
        assert result.verdict == "REVISE"
        assert result.error_type == "type_error"

    def test_parse_invalid_returns_escalate(self):
        parse = self._get_judge_parse()
        result = parse("not json at all")
        assert result.verdict == "ESCALATE"
        assert result.score == 0

    def test_parse_empty_string(self):
        parse = self._get_judge_parse()
        result = parse("")
        assert result.verdict == "ESCALATE"

    def test_parse_none(self):
        parse = self._get_judge_parse()
        result = parse(None)
        assert result.verdict == "ESCALATE"


# ---------------------------------------------------------------------------
# Brain._call / Hands._call / Judge._call_fresh (mocked httpx)
# ---------------------------------------------------------------------------

class TestBrainCall:
    @pytest.mark.asyncio
    async def test_brain_call_ensures_model(self):
        from pyovis.ai.brain import Brain

        brain = Brain.__new__(Brain)
        brain.swap = AsyncMock()
        brain.swap.ensure_model = AsyncMock()
        brain.swap.api_url = "http://localhost:8001/v1/chat/completions"
        brain.system_prompt = "You are brain"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "response"}}]
        }

        brain.client = AsyncMock()
        brain.client.post = AsyncMock(return_value=mock_resp)

        result = await brain._call("test message")

        brain.swap.ensure_model.assert_awaited_once_with("brain")
        assert result == ("response", "")

    @pytest.mark.asyncio
    async def test_brain_call_uses_reasoning_content(self):
        from pyovis.ai.brain import Brain

        brain = Brain.__new__(Brain)
        brain.swap = AsyncMock()
        brain.swap.ensure_model = AsyncMock()
        brain.swap.api_url = "http://localhost:8001/v1/chat/completions"
        brain.system_prompt = "You are brain"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": None, "reasoning_content": "thought"}}]
        }

        brain.client = AsyncMock()
        brain.client.post = AsyncMock(return_value=mock_resp)

        result = await brain._call("test")
        assert result == ("thought", "thought")


class TestHandsCall:
    @pytest.mark.asyncio
    async def test_hands_call_strips_fences(self):
        from pyovis.ai.hands import Hands

        h = Hands.__new__(Hands)
        h.swap = AsyncMock()
        h.swap.ensure_model = AsyncMock()
        h.swap.api_url = "http://localhost:8001/v1/chat/completions"
        h.system_prompt = "You are hands"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "```python\nprint('hi')\n```"}}]
        }

        h.client = AsyncMock()
        h.client.post = AsyncMock(return_value=mock_resp)

        result = await h._call("test")
        assert result == ("print('hi')", "")
        h.swap.ensure_model.assert_awaited_once_with("hands")


class TestPlannerCall:
    @pytest.mark.asyncio
    async def test_planner_ensures_planner_model(self):
        from pyovis.ai.planner import Planner

        p = Planner.__new__(Planner)
        p.swap = AsyncMock()
        p.swap.ensure_model = AsyncMock()
        p.swap.api_url = "http://localhost:8001/v1/chat/completions"
        p.system_prompt = "You are planner"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "response"}}]
        }

        p.client = AsyncMock()
        p.client.post = AsyncMock(return_value=mock_resp)

        result = await p._call("test")
        p.swap.ensure_model.assert_awaited_once_with("planner")
        assert result == ("response", "")
