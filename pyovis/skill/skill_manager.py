from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from pyovis.skill.skill_validator import SkillValidator


logger = logging.getLogger(__name__)

SKILL_BASE = Path("/pyovis_memory/skill_library")
VERIFIED_DIR = SKILL_BASE / "verified"
CANDIDATE_DIR = SKILL_BASE / "candidate"

_SKILL_DRAFT_PROMPT = """\
You are a skill extractor. Given a completed task record, produce a reusable skill document in Markdown.

The document MUST start with a YAML front-matter block containing:
  - id: skill_<task_id>
  - status: candidate
  - name: <short human-readable name>
  - category: <single category slug, e.g. python_debugging>
  - tags: <comma-separated keywords>
  - when_to_use: <one sentence describing trigger condition>

Then include:
## Problem
Brief description of the problem class.

## Solution
Step-by-step solution pattern with code examples where relevant.

## Notes
Edge cases, pitfalls, or context-specific caveats.

---
Task record (JSON):
{task_record_json}
"""


class SkillManager:
    def load_verified(self, task_description: str) -> str:
        relevant = self._find_relevant(task_description, status="verified")
        if not relevant:
            return "# 적용 가능한 Skill 없음"
        return "\n\n".join(skill["content"] for skill in relevant)

    def _find_relevant(self, task_description: str, status: str) -> list[dict]:
        skill_dir = VERIFIED_DIR if status == "verified" else CANDIDATE_DIR
        results = []
        if not skill_dir.exists():
            return results
        task_lower = task_description.lower()
        for skill_file in skill_dir.glob("*.md"):
            content = skill_file.read_text(encoding="utf-8")
            keywords = self._extract_keywords(content)
            if any(kw.lower() in task_lower for kw in keywords):
                results.append({"file": skill_file.name, "content": content})
        return results

    def _extract_keywords(self, skill_content: str) -> list[str]:
        keywords: list[str] = []
        for line in skill_content.split("\n")[:30]:
            line_lower = line.lower()
            if "category:" in line_lower:
                keywords.extend(line.split(":", 1)[1].strip().split("_"))
            if "tags:" in line_lower:
                tags = line.split(":", 1)[1].strip()
                keywords.extend([t.strip() for t in tags.split(",")])
            if "name:" in line_lower:
                keywords.extend(line.split(":", 1)[1].strip().split())
            if "when to use" in line_lower:
                pass
        return [kw for kw in keywords if kw and len(kw) > 2]

    async def evaluate_and_patch(self, ctx, loop_record: dict) -> None:
        validator = SkillValidator()
        needs_skill = validator.should_add_skill(loop_record, self._get_history())
        if needs_skill:
            await self._create_candidate(loop_record)

    def _get_history(self) -> list:
        records_dir = Path("/pyovis_memory/loop_records")
        records = []
        if not records_dir.exists():
            return records
        for path in sorted(records_dir.glob("*.jsonl"))[-50:]:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    records.append(json.loads(line))
        return records

    async def _create_candidate(self, loop_record: dict) -> None:
        skill_draft = await self._request_skill_draft(loop_record)
        task_id = loop_record.get("task_id", "unknown")
        candidate_path = CANDIDATE_DIR / f"skill_{task_id}.md"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(skill_draft, encoding="utf-8")
        self._notify_review_needed(candidate_path)

    async def _request_skill_draft(self, loop_record: dict) -> str:
        import os

        task_id = loop_record.get("task_id", "unknown")
        fallback = f"---\nid: skill_{task_id}\nstatus: candidate\n---\n"

        llm_base = os.environ.get("PYOVIS_LLM_BASE_URL", "http://localhost:8001")
        model = os.environ.get("PYOVIS_BRAIN_MODEL", "brain")

        prompt = _SKILL_DRAFT_PROMPT.format(
            task_record_json=json.dumps(loop_record, ensure_ascii=False, indent=2)
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1024,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{llm_base}/v1/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception:
            logger.warning(
                "skill_manager: LLM draft generation failed for task_id=%s, "
                "falling back to stub.",
                task_id,
                exc_info=True,
            )
            return fallback

    def _notify_review_needed(self, candidate_path: Path) -> None:
        logger.info(
            "skill_manager: new candidate skill written — review required: %s",
            candidate_path,
        )
