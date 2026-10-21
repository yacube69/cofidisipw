"""Structured log of one pipeline run (CIPW-45): JSON lines in outputs/logs/, one event per line.

    {"ts": "2026-10-21T20:41:07.123+02:00", "run_id": "a1b2c3d4e5f6", "event": "stage", "stage": "read", ...}

Events: `run_start` (file, options, versions, settings hash), `stage` (status, seconds, note), `run_end`
(summary). Read with any JSON-lines tool, e.g. pandas.read_json(path, lines=True).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:60]


class RunLog:
    def __init__(self, run_id: str, file: str, folder: Path | None):
        """`folder` None: logging off (nothing is written, `path` is None)."""
        self.run_id = run_id
        self.path: Path | None = None
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.path = folder / f"{stamp}_{_safe(Path(file).stem)}_{run_id}.jsonl"

    def event(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        record = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), "run_id": self.run_id,
                  "event": event, **fields}  # fmt: skip
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
