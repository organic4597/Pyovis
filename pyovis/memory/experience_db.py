"""
Pyvis v5.1 — Experience DB for Experiential Co-Learning

Stores and retrieves past success/failure patterns to enable experiential learning.
Uses FAISS for semantic similarity search.

References: PHASE4_PLAN.md section 4.1
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Dict

logger = logging.getLogger(__name__)

_EXPERIENCE_PERSIST_DIR = (
    Path(os.environ.get("PYOVIS_MEMORY_DIR", "/pyovis_memory")) / "experience"
)


class TaskType(str, Enum):
    """Types of tasks for pattern categorization"""

    PYTHON_SCRIPT = "python_script"
    API_SERVER = "api_server"
    CLI_TOOL = "cli_tool"
    TEST_FILE = "test_file"
    REFACTOR = "refactor"
    DEBUG = "debug"
    UNKNOWN = "unknown"


@dataclass
class ExperienceEntry:
    """
    Single experience record for the Experience DB.
    
    Represents a single execution cycle: task description, code generated,
    execution result, and Judge evaluation.
    """
    
    # Task description (used for semantic search)
    task_description: str
    
    # Execution result
    success: bool
    
    # Code that was generated
    code_snippet: str
    
    # Judge evaluation (all with defaults)
    judge_verdict: str = ""  # "PASS", "REVISE", "ESCALATE"
    judge_score: int = 0
    judge_feedback: Optional[str] = None
    
    # Error information (if failed)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    
    # Execution metadata
    execution_plan: Optional[Dict[str, Any]] = None
    tokens_saved: int = 0
    
    # Categorization
    task_type: str = TaskType.UNKNOWN.value
    
    # Timing
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    duration_sec: float = 0.0
    
    # Pattern metadata
    techniques_used: List[str] = field(default_factory=list)
    skills_applied: List[str] = field(default_factory=list)
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExperienceEntry:
        """Create from dictionary"""
        return cls(**data)

    def to_faiss_text(self) -> str:
        """Convert to text for FAISS embedding"""
        parts = [
            f"Task: {self.task_description}",
            f"Type: {self.task_type}",
            f"Result: {'SUCCESS' if self.success else 'FAILED'}",
        ]
        if self.error_type:
            parts.append(f"Error: {self.error_type}")
        if self.judge_feedback:
            parts.append(f"Feedback: {self.judge_feedback}")
        if self.techniques_used:
            parts.append(f"Techniques: {', '.join(self.techniques_used)}")
        return " | ".join(parts)


class ExperienceDB:
    """
    Experience database with FAISS indexing for semantic search.

    Provides:
    - Add experiences (success/failure records)
    - Search similar experiences
    - Extract success/failure patterns
    - Persist to disk
    """

    def __init__(self, persist_dir: Path = _EXPERIENCE_PERSIST_DIR) -> None:
        self.persist_dir = persist_dir
        self.model: Any = None
        self.index: Any = None
        self.dimension: int | None = None
        self.experiences: List[ExperienceEntry] = []
        self._initialized = False

        # Metadata index for fast filtering
        self._success_by_task_type: Dict[str, List[int]] = {}
        self._failure_by_error_type: Dict[str, List[int]] = {}

    def _ensure_initialized(self) -> None:
        """Lazy initialization of FAISS index and embedding model"""
        if self._initialized:
            return

        sentence_transformers = importlib.import_module("sentence_transformers")
        faiss = importlib.import_module("faiss")
        numpy = importlib.import_module("numpy")

        self.model = sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
        self.dimension = self.model.get_sentence_embedding_dimension()

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.persist_dir / "experience_faiss.index"
        metadata_path = self.persist_dir / "experience_metadata.json"

        if index_path.exists() and metadata_path.exists():
            try:
                self.index = faiss.read_index(str(index_path))
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.experiences = [
                        ExperienceEntry.from_dict(e) for e in data["experiences"]
                    ]
                    self._rebuild_metadata_index()
                logger.info(
                    "Loaded Experience DB: %d experiences from %s",
                    len(self.experiences),
                    self.persist_dir,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load persisted Experience DB, starting fresh: %s", exc
                )
                self.index = faiss.IndexFlatL2(self.dimension)
                self.experiences = []
        else:
            self.index = faiss.IndexFlatL2(self.dimension)
            self.experiences = []

        self._initialized = True

    def _rebuild_metadata_index(self) -> None:
        """Rebuild in-memory metadata indexes for fast filtering"""
        self._success_by_task_type = {}
        self._failure_by_error_type = {}

        for idx, exp in enumerate(self.experiences):
            # Index by task type
            if exp.task_type not in self._success_by_task_type:
                self._success_by_task_type[exp.task_type] = []
            if exp.success:
                self._success_by_task_type[exp.task_type].append(idx)

            # Index by error type
            if exp.error_type:
                if exp.error_type not in self._failure_by_error_type:
                    self._failure_by_error_type[exp.error_type] = []
                self._failure_by_error_type[exp.error_type].append(idx)

    async def add_experience(self, entry: ExperienceEntry) -> int:
        """
        Add a new experience to the database.

        Args:
            entry: ExperienceEntry to add

        Returns:
            Index of the added experience
        """
        self._ensure_initialized()
        numpy = importlib.import_module("numpy")

        # Add to experiences list
        idx = len(self.experiences)
        self.experiences.append(entry)

        # Generate embedding and add to FAISS
        text = entry.to_faiss_text()
        embedding = self.model.encode([text])
        vector = numpy.array(embedding).astype("float32")
        self.index.add(vector)

        # Update metadata indexes
        if entry.task_type not in self._success_by_task_type:
            self._success_by_task_type[entry.task_type] = []
        if entry.success:
            self._success_by_task_type[entry.task_type].append(idx)

        if entry.error_type:
            if entry.error_type not in self._failure_by_error_type:
                self._failure_by_error_type[entry.error_type] = []
            self._failure_by_error_type[entry.error_type].append(idx)

        # Persist
        self._persist()

        logger.info(
            "Added experience %d: task_type=%s, success=%s",
            idx,
            entry.task_type,
            entry.success,
        )

        return idx

    async def search_similar(
        self,
        query: str,
        k: int = 5,
        task_type_filter: Optional[str] = None,
        success_only: bool = False,
    ) -> List[ExperienceEntry]:
        """
        Search for similar experiences using semantic similarity.

        Args:
            query: Search query (task description)
            k: Number of results to return
            task_type_filter: Optional filter by task type
            success_only: If True, only return successful experiences

        Returns:
            List of most similar ExperienceEntries
        """
        self._ensure_initialized()

        # Encode query
        query_vec = self.model.encode([query]).astype("float32")

        # Search FAISS
        distances, indices = self.index.search(
            query_vec, k * 2
        )  # Get more for filtering

        results = []
        seen_count = 0

        for rank, idx in enumerate(indices[0]):
            if idx < 0 or idx >= len(self.experiences):
                continue

            exp = self.experiences[idx]

            # Apply filters
            if task_type_filter and exp.task_type != task_type_filter:
                continue
            if success_only and not exp.success:
                continue

            results.append(exp)
            seen_count += 1

            if seen_count >= k:
                break

        return results

    async def get_success_patterns(self, task_type: str) -> List[Dict[str, Any]]:
        """
        Extract success patterns for a specific task type.

        Args:
            task_type: Task type to filter by

        Returns:
            List of success patterns with code snippets and techniques
        """
        self._ensure_initialized()

        patterns = []

        for exp in self.experiences:
            if exp.success and exp.task_type == task_type:
                patterns.append(
                    {
                        "task_description": exp.task_description,
                        "code_snippet": exp.code_snippet[:500],  # Truncate for prompt
                        "judge_feedback": exp.judge_feedback,
                        "techniques_used": exp.techniques_used,
                        "skills_applied": exp.skills_applied,
                        "tokens_saved": exp.tokens_saved,
                    }
                )

        # Sort by score descending
        patterns.sort(key=lambda x: x.get("judge_score", 0), reverse=True)

        return patterns[:10]  # Return top 10

    async def get_failure_patterns(
        self, error_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract failure patterns, optionally filtered by error type.

        Args:
            error_type: Optional error type filter

        Returns:
            List of failure patterns with error details
        """
        self._ensure_initialized()

        patterns = []

        for exp in self.experiences:
            if not exp.success:
                if error_type and exp.error_type != error_type:
                    continue

                patterns.append(
                    {
                        "task_description": exp.task_description,
                        "error_type": exp.error_type,
                        "error_message": exp.error_message,
                        "code_snippet": exp.code_snippet[:500],
                        "judge_feedback": exp.judge_feedback,
                    }
                )

        return patterns[:10]  # Return top 10

    async def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the Experience DB.

        Returns:
            Dictionary with various statistics
        """
        self._ensure_initialized()

        total = len(self.experiences)
        successes = sum(1 for e in self.experiences if e.success)
        failures = total - successes

        # Count by task type
        task_type_counts: Dict[str, int] = {}
        for exp in self.experiences:
            task_type_counts[exp.task_type] = task_type_counts.get(exp.task_type, 0) + 1

        # Count by error type
        error_type_counts: Dict[str, int] = {}
        for exp in self.experiences:
            if exp.error_type:
                error_type_counts[exp.error_type] = (
                    error_type_counts.get(exp.error_type, 0) + 1
                )

        # Average score
        avg_score = 0
        if self.experiences:
            avg_score = sum(e.judge_score for e in self.experiences) / total

        return {
            "total_experiences": total,
            "successes": successes,
            "failures": failures,
            "success_rate": successes / total if total > 0 else 0,
            "task_type_distribution": task_type_counts,
            "error_type_distribution": error_type_counts,
            "average_judge_score": avg_score,
        }

    def _persist(self) -> None:
        """Persist the experience database to disk"""
        faiss = importlib.import_module("faiss")

        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)

            # Save FAISS index
            faiss.write_index(
                self.index, str(self.persist_dir / "experience_faiss.index")
            )

            # Save metadata
            metadata = {
                "experiences": [e.to_dict() for e in self.experiences],
                "saved_at": datetime.now().isoformat(),
            }
            with open(
                self.persist_dir / "experience_metadata.json", "w", encoding="utf-8"
            ) as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

        except Exception as exc:
            logger.error("Failed to persist Experience DB: %s", exc)

    async def clear(self) -> None:
        """Clear all experiences (for testing)"""
        self._ensure_initialized()

        faiss = importlib.import_module("faiss")
        
        self.experiences = []
        self.index = faiss.IndexFlatL2(self.dimension)
        self._success_by_task_type = {}
        self._failure_by_error_type = {}

        self._persist()

        logger.info("Cleared Experience DB")

# Global singleton instance
_experience_db: Optional[ExperienceDB] = None


def get_experience_db() -> ExperienceDB:
    """Get the global ExperienceDB instance"""
    global _experience_db
    if _experience_db is None:
        _experience_db = ExperienceDB()
    return _experience_db


# Convenience functions for easy import
async def add_experience(entry: ExperienceEntry) -> int:
    """Add a new experience"""
    return await get_experience_db().add_experience(entry)


async def search_similar(
    query: str,
    k: int = 5,
    task_type_filter: Optional[str] = None,
    success_only: bool = False,
) -> List[ExperienceEntry]:
    """Search for similar experiences"""
    return await get_experience_db().search_similar(
        query, k, task_type_filter, success_only
    )


async def get_success_patterns(task_type: str) -> List[Dict[str, Any]]:
    """Get success patterns for a task type"""
    return await get_experience_db().get_success_patterns(task_type)


async def get_failure_patterns(
    error_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get failure patterns for an error type"""
    return await get_experience_db().get_failure_patterns(error_type)
