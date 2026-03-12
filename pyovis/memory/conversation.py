"""Conversation history store — per-chat persistent memory.

Stores recent message exchanges per chat_id as a JSON file on disk.
Provides formatted history for LLM prompt injection so the bot can
remember previous conversations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MEMORY_DIR = (
    Path(os.environ.get("PYOVIS_MEMORY_DIR", "/pyovis_memory")) / "conversations"
)

KST = timezone(timedelta(hours=9))


class ConversationMemory:
    """Per-chat conversation history with disk persistence.

    Stores the last *max_turns* exchanges (user + assistant pairs) per chat.
    Each entry includes a KST timestamp for temporal queries like
    "어제 뭐라고 했지?" or "아까 물어본 곳이 어디?".
    """

    def __init__(
        self,
        persist_dir: Path = _MEMORY_DIR,
        max_turns: int = 30,
    ) -> None:
        self._persist_dir = persist_dir
        self._max_turns = max_turns
        self._histories: dict[str, deque[dict[str, str]]] = {}


    _REFERENCE_PATTERN = re.compile(
        r"(아까|이전에|전에|그때|다시|아까\s*그|방금|좀\s*전|저번|지난번"
        r"|어제|그저께|며칠\s*전|일전에"
        r"|계속|이어서|마저|아까\s*말한|아까\s*물어본|전에\s*말한"
        r"|그거|그것|그\s*작업|그\s*코드|그\s*게임|그\s*프로젝트"
        r"|했던|만들던|물어봤던|알려줬던|보여줬던)"
    )

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract meaningful Korean/English keywords (2+ chars) from text."""
        # 한국어: 2자 이상 한글 단어 추출
        ko = set(re.findall(r"[가-힣]{2,}", text))
        # 영어: 2자 이상 영문 단어 추출 (소문자 정규화)
        en = {w.lower() for w in re.findall(r"[a-zA-Z]{2,}", text)}
        # 불용어 제거
        stopwords = {
            "만들어",
            "해줘",
            "알려줘",
            "보여줘",
            "해줄래",
            "할래",
            "어때",
            "부탁",
            "하나",
            "싶어",
            "있어",
            "없어",
            "이거",
            "저거",
            "the",
            "is",
            "are",
            "was",
            "were",
            "and",
            "for",
            "that",
            "this",
            "with",
            "from",
            "have",
            "has",
            "not",
            "but",
        }
        return (ko | en) - stopwords



    def add_exchange(
        self,
        chat_id: str | int,
        user_message: str,
        assistant_response: str,
    ) -> None:
        """Record one user→assistant exchange."""
        key = str(chat_id)
        history = self._ensure_history(key)
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        history.append(
            {
                "role": "user",
                "content": user_message,
                "timestamp": now,
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": assistant_response,
                "timestamp": now,
            }
        )
        while len(history) > self._max_turns * 2:
            history.popleft()
        self._save(key)

    def get_history(
        self,
        chat_id: str | int,
        last_n: int = 10,
    ) -> list[dict[str, str]]:
        """Return the most recent *last_n* exchanges (up to last_n*2 messages)."""
        key = str(chat_id)
        history = self._ensure_history(key)
        msgs = list(history)
        return msgs[-(last_n * 2) :] if len(msgs) > last_n * 2 else msgs

    def format_for_prompt(
        self,
        chat_id: str | int,
        last_n: int = 10,
    ) -> str:
        """Format recent history as text for LLM prompt injection.

        Returns empty string if no history exists.
        """
        msgs = self.get_history(chat_id, last_n=last_n)
        if not msgs:
            return ""
        return self._format_messages(msgs)

    def filter_relevant(
        self,
        chat_id: str | int,
        current_request: str,
        last_n: int = 10,
        min_keyword_overlap: int = 1,
    ) -> str:
        """Return formatted history filtered by relevance to current request.

        Two-stage filter:
          1. Reference signal → include ALL recent history (user explicitly
             refers to past conversation).
          2. Keyword overlap → include only exchanges whose user message
             shares at least *min_keyword_overlap* keywords with the current
             request.

        Returns empty string if nothing is relevant.
        """
        msgs = self.get_history(chat_id, last_n=last_n)
        if not msgs:
            return ""

        # ── 1단계: 참조 신호 감지 ──
        if self._REFERENCE_PATTERN.search(current_request):
            logger.info("💬 대화 필터: 참조 신호 감지 → 전체 히스토리 포함")
            return self._format_messages(msgs)

        # ── 2단계: 키워드 유사도 필터링 ──
        req_keywords = self._extract_keywords(current_request)
        if not req_keywords:
            logger.info("💬 대화 필터: 키워드 추출 불가 → 히스토리 스킵")
            return ""

        # 메시지를 교환 단위(user+assistant 쌍)로 묶어서 필터링
        relevant: list[dict[str, str]] = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            if msg["role"] == "user":
                user_keywords = self._extract_keywords(msg["content"])
                overlap = req_keywords & user_keywords
                if len(overlap) >= min_keyword_overlap:
                    relevant.append(msg)
                    # 바로 다음 assistant 응답도 함께 포함
                    if i + 1 < len(msgs) and msgs[i + 1]["role"] == "assistant":
                        relevant.append(msgs[i + 1])
                        i += 2
                        continue
            i += 1

        if relevant:
            matched_kw = req_keywords & self._extract_keywords(
                " ".join(m["content"] for m in relevant)
            )
            logger.info(
                "💬 대화 필터: %d/%d 메시지 관련 (키워드: %s)",
                len(relevant),
                len(msgs),
                matched_kw,
            )
            return self._format_messages(relevant)

        logger.info("💬 대화 필터: 관련 히스토리 없음 → 스킵")
        return ""



    @staticmethod
    def _format_messages(msgs: list[dict[str, str]]) -> str:
        """Format a list of messages into prompt-ready text."""
        lines: list[str] = []
        for msg in msgs:
            ts = msg.get("timestamp", "")
            role = msg["role"]
            content = msg["content"]
            if len(content) > 500:
                content = content[:500] + "..."
            if role == "user":
                lines.append(f"[{ts}] 사용자: {content}")
            else:
                lines.append(f"[{ts}] 봇: {content}")
        return "\n".join(lines)



    def _ensure_history(self, key: str) -> deque[dict[str, str]]:
        if key not in self._histories:
            self._histories[key] = self._load(key)
        return self._histories[key]

    def _path_for(self, key: str) -> Path:
        return self._persist_dir / f"chat_{key}.json"

    def _load(self, key: str) -> deque[dict[str, str]]:
        path = self._path_for(key)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    logger.info(
                        "conversation: loaded %d messages for chat %s",
                        len(data),
                        key,
                    )
                    return deque(data, maxlen=self._max_turns * 2)
            except Exception as exc:
                logger.warning("conversation: failed to load chat %s: %s", key, exc)
        return deque(maxlen=self._max_turns * 2)

    def _save(self, key: str) -> None:
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            path = self._path_for(key)
            path.write_text(
                json.dumps(list(self._histories[key]), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("conversation: failed to save chat %s: %s", key, exc)
