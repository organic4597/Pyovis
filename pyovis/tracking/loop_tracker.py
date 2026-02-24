from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


RECORDS_DIR = Path("/pyovis_memory/loop_records")


@dataclass
class LoopRecord:
    task_id: str
    task_description: str
    task_category: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_loops: int = 0
    total_time_sec: float = 0.0
    switch_count: int = 0
    escalated: bool = False
    fail_reasons: list = field(default_factory=list)
    final_quality: str = ""
    skill_patch_added: bool = False


class LoopTracker:
    def __init__(self) -> None:
        self._records: dict[str, LoopRecord] = {}
        self._start_times: dict[str, float] = {}
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    def start(self, task_id: str, task_description: str) -> None:
        self._records[task_id] = LoopRecord(
            task_id=task_id,
            task_description=task_description,
            started_at=datetime.now().isoformat(),
        )
        self._start_times[task_id] = time.time()

    def record_switch(self, switch_type: str, task_id: str | None = None) -> None:
        if task_id and task_id in self._records:
            self._records[task_id].switch_count += 1

    def record_fail(self, task_id: str, reason: str) -> None:
        if task_id in self._records:
            self._records[task_id].fail_reasons.append(
                {"reason": reason, "timestamp": datetime.now().isoformat()}
            )
            self._records[task_id].total_loops += 1

    def finish(self, ctx, final_result: dict) -> None:
        record = self._records.get(ctx.task_id)
        if not record:
            return
        record.finished_at = datetime.now().isoformat()
        record.total_time_sec = time.time() - self._start_times[ctx.task_id]
        record.total_loops = ctx.loop_count
        record.escalated = final_result.get("status") == "escalated"
        record.final_quality = "ESCALATED" if record.escalated else "PASS"
        self._save(record)

    def get_record(self, task_id: str) -> dict:
        record = self._records.get(task_id)
        return asdict(record) if record else {}

    def _save(self, record: LoopRecord) -> None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_path = RECORDS_DIR / f"{date_str}.jsonl"
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
