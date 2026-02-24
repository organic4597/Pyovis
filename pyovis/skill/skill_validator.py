from __future__ import annotations

from pathlib import Path


class SkillValidator:
    NOT_FIXABLE_BY_SKILL = {
        "docker_error", "unknown_error", "environment_error", "network_error",
    }

    def should_add_skill(self, current_record: dict, history: list[dict]) -> bool:
        fail_reasons = [f["reason"] for f in current_record.get("fail_reasons", [])]
        if not fail_reasons:
            return False

        for reason in set(fail_reasons):
            if self._check_all_conditions(reason, current_record, history):
                return True
        return False

    def _check_all_conditions(
        self, reason: str, current: dict, history: list[dict]
    ) -> bool:
        other_task_count = sum(
            1
            for record in history
            if record["task_id"] != current["task_id"]
            and any(reason in f["reason"] for f in record.get("fail_reasons", []))
        )
        if other_task_count < 2:
            return False

        task_ids_with_reason = [
            record["task_id"]
            for record in history
            if any(reason in f["reason"] for f in record.get("fail_reasons", []))
        ]
        if len(set(task_ids_with_reason)) < 3:
            return False

        if reason in self.NOT_FIXABLE_BY_SKILL:
            return False

        if self._already_exists(reason):
            return False

        return True

    def _already_exists(self, reason: str) -> bool:
        skill_dir = Path("/pyovis_memory/skill_library/verified")
        if not skill_dir.exists():
            return False
        normalized = reason.lower().replace(" ", "_")
        return any(normalized in f.stem for f in skill_dir.glob("*.md"))
