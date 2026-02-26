"""
Pyvis v5.1 — User Profile Learning System

Learns and stores user preferences for personalized code generation.
Accumulates patterns from user feedback and automatically applies them.

Usage:
    # Get or create profile
    profile = await UserProfile.load("user123")

    # Learn from feedback
    await profile.learn_from_feedback(
        code="from fastapi import FastAPI",
        feedback="좋아요! FastAPI 를 선호합니다"
    )

    # Get preferences for code generation
    prefs = await profile.get_preferences()
    # {"style": "fastapi", "comments": "korean", "test_framework": "pytest"}
"""

from __future__ import annotations

import json
import logging
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from collections import Counter

logger = logging.getLogger(__name__)


@dataclass
class UserPreference:
    """Single preference item."""

    key: str
    value: Any
    confidence: float  # 0.0 to 1.0
    occurrences: int = 1


@dataclass
class CodePattern:
    """Learned code pattern."""

    pattern_type: str  # "import", "style", "naming", "structure"
    pattern: str
    frequency: int = 1
    last_used: float = 0.0


class UserProfile:
    """
    User profile with learning capabilities.

    Features:
    - Preference accumulation
    - Pattern recognition
    - Confidence-based suggestions
    - Persistent storage
    - Auto-application to code generation
    """

    PROFILE_DIR = Path("/pyovis_memory/profiles")

    def __init__(self, user_id: str) -> None:
        """
        Initialize user profile.

        Args:
            user_id: Unique user identifier
        """
        self.user_id = user_id
        self.preferences: Dict[str, UserPreference] = {}
        self.patterns: List[CodePattern] = []
        self.feedback_history: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @classmethod
    async def load(cls, user_id: str) -> "UserProfile":
        """
        Load or create user profile.

        Args:
            user_id: User identifier

        Returns:
            UserProfile instance
        """
        profile = cls(user_id)
        await profile._load_from_disk()
        return profile

    async def _load_from_disk(self) -> None:
        """Load profile from disk."""
        profile_path = self.PROFILE_DIR / f"{self.user_id}.json"

        if profile_path.exists():
            try:
                with open(profile_path, "r") as f:
                    data = json.load(f)

                # Load preferences
                for key, pref_data in data.get("preferences", {}).items():
                    self.preferences[key] = UserPreference(**pref_data)

                # Load patterns
                for pattern_data in data.get("patterns", []):
                    self.patterns.append(CodePattern(**pattern_data))

                # Load feedback history
                self.feedback_history = data.get("feedback_history", [])

                logger.info(
                    f"Loaded profile for {self.user_id}: {len(self.preferences)} prefs, {len(self.patterns)} patterns"
                )

            except Exception as e:
                logger.error(f"Failed to load profile: {e}")

    async def save(self) -> None:
        """Save profile to disk."""
        async with self._lock:
            self.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            profile_path = self.PROFILE_DIR / f"{self.user_id}.json"

            data = {
                "user_id": self.user_id,
                "preferences": {
                    key: {
                        "key": pref.key,
                        "value": pref.value,
                        "confidence": pref.confidence,
                        "occurrences": pref.occurrences,
                    }
                    for key, pref in self.preferences.items()
                },
                "patterns": [
                    {
                        "pattern_type": p.pattern_type,
                        "pattern": p.pattern,
                        "frequency": p.frequency,
                        "last_used": p.last_used,
                    }
                    for p in self.patterns
                ],
                "feedback_history": self.feedback_history[-100:],  # Last 100 feedbacks
            }

            with open(profile_path, "w") as f:
                json.dump(data, f, indent=2)

            logger.debug(f"Saved profile for {self.user_id}")

    async def learn_from_feedback(
        self, code: str, feedback: str, context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Learn from user feedback.

        Args:
            code: Code that received feedback
            feedback: User feedback text
            context: Additional context (task type, language, etc.)
        """
        import time

        feedback_entry = {
            "code": code,
            "feedback": feedback,
            "context": context or {},
            "timestamp": time.time(),
        }
        self.feedback_history.append(feedback_entry)

        # Analyze feedback for preferences
        await self._extract_preferences(code, feedback)

        # Save periodically
        if len(self.feedback_history) % 5 == 0:
            await self.save()

    async def _extract_preferences(self, code: str, feedback: str) -> None:
        """Extract preferences from feedback."""
        feedback_lower = feedback.lower()

        # Detect framework preferences
        if "fastapi" in code.lower() and (
            "좋아요" in feedback_lower or "선호" in feedback_lower
        ):
            self._update_preference("framework", "fastapi", 0.8)

        if "flask" in code.lower() and (
            "좋아요" in feedback_lower or "선호" in feedback_lower
        ):
            self._update_preference("framework", "flask", 0.8)

        # Detect comment language
        if "한국어" in feedback_lower or "korean" in feedback_lower:
            self._update_preference("comment_language", "korean", 0.9)
        elif "english" in feedback_lower or "영어" in feedback_lower:
            self._update_preference("comment_language", "english", 0.9)

        # Detect test framework
        if "pytest" in code.lower() and (
            "좋아요" in feedback_lower or "선호" in feedback_lower
        ):
            self._update_preference("test_framework", "pytest", 0.8)
        elif "unittest" in code.lower() and (
            "좋아요" in feedback_lower or "선호" in feedback_lower
        ):
            self._update_preference("test_framework", "unittest", 0.8)

        # Detect code style
        if "간결한" in feedback_lower or "concise" in feedback_lower:
            self._update_preference("style", "concise", 0.7)
        elif "자세한" in feedback_lower or "detailed" in feedback_lower:
            self._update_preference("style", "detailed", 0.7)

        # Save after learning
        await self.save()

    def _update_preference(self, key: str, value: Any, confidence_boost: float) -> None:
        """Update or create preference."""
        if key in self.preferences:
            pref = self.preferences[key]
            if pref.value == value:
                pref.occurrences += 1
                pref.confidence = min(1.0, pref.confidence + confidence_boost * 0.1)
            else:
                # Different value, decrease confidence
                pref.confidence = max(0.0, pref.confidence - 0.1)
        else:
            self.preferences[key] = UserPreference(
                key=key, value=value, confidence=confidence_boost
            )

    async def learn_from_code(self, code: str, context: Dict[str, Any]) -> None:
        """
        Learn patterns from generated code.

        Args:
            code: Generated code
            context: Generation context
        """
        import time
        import re

        # Extract imports
        import_pattern = r"^(from\s+\S+\s+import|import\s+\S+)"
        imports = re.findall(import_pattern, code, re.MULTILINE)

        for imp in imports:
            pattern = CodePattern(
                pattern_type="import", pattern=imp, frequency=1, last_used=time.time()
            )
            self._add_pattern(pattern)

        # Extract function naming style
        func_pattern = r"def\s+([a-z_][a-z0-9_]*)"
        funcs = re.findall(func_pattern, code)

        if funcs:
            # Detect naming convention
            if all("_" in f for f in funcs):
                self._update_preference("naming_style", "snake_case", 0.5)
            elif all(f.islower() for f in funcs):
                self._update_preference("naming_style", "lowercase", 0.5)

    def _add_pattern(self, pattern: CodePattern) -> None:
        """Add or update pattern frequency."""
        # Check if pattern exists
        for existing in self.patterns:
            if existing.pattern == pattern.pattern:
                existing.frequency += 1
                existing.last_used = pattern.last_used
                return

        # Add new pattern
        self.patterns.append(pattern)

        # Limit pattern count
        if len(self.patterns) > 1000:
            self.patterns = self.patterns[-500:]

    async def get_preferences(self) -> Dict[str, Any]:
        """
        Get current preferences.

        Returns:
            Dictionary of preferences with high confidence
        """
        return {
            key: pref.value
            for key, pref in self.preferences.items()
            if pref.confidence > 0.5
        }

    async def apply_to_prompt(self, prompt: str) -> str:
        """
        Apply preferences to code generation prompt.

        Args:
            prompt: Original prompt

        Returns:
            Enhanced prompt with preferences
        """
        prefs = await self.get_preferences()

        if not prefs:
            return prompt

        enhancements = []

        # Framework preference
        if "framework" in prefs:
            enhancements.append(f"Prefer {prefs['framework']} framework")

        # Comment language
        if prefs.get("comment_language") == "korean":
            enhancements.append("Write comments in Korean")
        elif prefs.get("comment_language") == "english":
            enhancements.append("Write comments in English")

        # Test framework
        if "test_framework" in prefs:
            enhancements.append(f"Use {prefs['test_framework']} for tests")

        # Style
        if "style" in prefs:
            enhancements.append(f"Code style: {prefs['style']}")

        if enhancements:
            prompt += "\n\n## User Preferences:\n" + "\n".join(enhancements)

        return prompt

    async def get_statistics(self) -> Dict[str, Any]:
        """Get profile statistics."""
        return {
            "user_id": self.user_id,
            "preference_count": len(self.preferences),
            "pattern_count": len(self.patterns),
            "feedback_count": len(self.feedback_history),
            "top_preferences": {
                key: pref.value
                for key, pref in sorted(
                    self.preferences.items(),
                    key=lambda x: x[1].confidence,
                    reverse=True,
                )[:5]
            },
        }


# Global profile cache
_profile_cache: Dict[str, UserProfile] = {}


async def get_user_profile(user_id: str) -> UserProfile:
    """Get or create user profile."""
    global _profile_cache
    if user_id not in _profile_cache:
        _profile_cache[user_id] = await UserProfile.load(user_id)
    return _profile_cache[user_id]


async def clear_profile_cache() -> None:
    """Clear profile cache."""
    global _profile_cache
    _profile_cache = {}
