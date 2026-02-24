from pyovis.memory.graph_builder import KnowledgeGraphBuilder, chunk_text
from pyovis.memory.experience_db import (
    ExperienceDB,
    ExperienceEntry,
    TaskType,
    get_experience_db,
    add_experience,
    search_similar,
    get_success_patterns,
    get_failure_patterns,
)

__all__ = [
    # Knowledge Graph
    "start_kg_server",
    "KnowledgeGraphBuilder",
    "chunk_text",
    # Experience DB
    "ExperienceDB",
    "ExperienceEntry",
    "TaskType",
    "get_experience_db",
    "add_experience",
    "search_similar",
    "get_success_patterns",
    "get_failure_patterns",
]

__all__ = ["start_kg_server", "KnowledgeGraphBuilder", "chunk_text"]


def __getattr__(name: str):
    if name == "start_kg_server":
        from pyovis.memory.kg_server import start_kg_server
        return start_kg_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
