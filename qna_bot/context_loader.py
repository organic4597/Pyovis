"""
Pyovis 프로젝트 문서를 로드하여 LLM 시스템 프롬프트용 컨텍스트를 구성한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent

# QnA에 유용한 문서 목록 (중요도 순)
_DOCS: list[str] = [
    "README.md",
    "ARCHITECTURE.md",
    "pyovis_v5_3.md",
    "pyovis_v5_3_ko.md",
    "IMPROVEMENTS.md",
    "TASK_TYPES_AND_ROUTING.md",
    "TASK_TYPES_INDEX.md",
    "ISSUE_LIST.md",
    "config/unified_node.yaml",
]

# 로드할 최대 파일 크기 (너무 큰 파일은 요약만 포함)
_MAX_FILE_CHARS = 8_000


def _load_file(rel_path: str) -> str | None:
    path = PROJECT_ROOT / rel_path
    if not path.exists():
        logger.warning("문서 파일 없음: %s", rel_path)
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) > _MAX_FILE_CHARS:
        logger.info("파일 크기 초과, 앞부분만 로드: %s (%d자)", rel_path, len(text))
        text = (
            text[:_MAX_FILE_CHARS]
            + f"\n\n... (이하 {len(text) - _MAX_FILE_CHARS}자 생략) ..."
        )
    return text


def _build_module_tree() -> str:
    """pyovis/ 패키지 디렉토리 트리 구조를 텍스트로 반환한다."""
    lines: list[str] = ["## pyovis 패키지 구조\n\n```"]
    pyovis = PROJECT_ROOT / "pyovis"
    if not pyovis.exists():
        return ""
    for item in sorted(pyovis.rglob("*.py")):
        # __pycache__ 제외
        if "__pycache__" in item.parts:
            continue
        rel = item.relative_to(PROJECT_ROOT)
        lines.append(f"  {rel}")
    lines.append("```")
    return "\n".join(lines)


def load_project_context() -> str:
    """
    프로젝트 문서 + 코드 구조를 하나의 컨텍스트 문자열로 반환한다.
    FastAPI 앱 시작 시 1회 호출하여 캐싱한다.
    """
    sections: list[str] = []

    # 문서 파일 로드
    for rel_path in _DOCS:
        content = _load_file(rel_path)
        if content:
            sections.append(f"## 📄 {rel_path}\n\n{content}")
            logger.info("컨텍스트 로드: %s", rel_path)

    # 모듈 구조 추가
    tree = _build_module_tree()
    if tree:
        sections.append(tree)

    full = "\n\n---\n\n".join(sections)
    logger.info("총 컨텍스트 크기: %d자", len(full))
    return full
