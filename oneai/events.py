"""Append-only event log: every agent action is recorded for audit & replay."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def emit(self, event_type: str, **payload: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": event_type,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def tail(self, n: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines[-n:]]
