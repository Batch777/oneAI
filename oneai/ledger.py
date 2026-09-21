"""Experimental unique-resource registry, not an exactly-once executor.

Not wired to a background worker. A claim records an ID; it does not prove a
remote action completed. recover_stale only changes labels and does not make
an existing ID claimable. Production retry/lease/outbox semantics are specified
in docs/SPEC-NEXT.md and must be implemented before enabling mail execution.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    resource_id TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'email', 'task', ...
    status      TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (kind, resource_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)

    def claim(self, kind: str, resource_id: str, status: str = "seen") -> bool:
        """Atomically claim a resource. Returns False if already handled."""
        try:
            now = _now()
            self.conn.execute(
                "INSERT INTO ledger (resource_id, kind, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (resource_id, kind, status, now, now),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def set_status(self, kind: str, resource_id: str, status: str, detail: str = "") -> None:
        self.conn.execute(
            "UPDATE ledger SET status = ?, detail = ?, updated_at = ? WHERE kind = ? AND resource_id = ?",
            (status, detail, _now(), kind, resource_id),
        )
        self.conn.commit()

    def status(self, kind: str, resource_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM ledger WHERE kind = ? AND resource_id = ?", (kind, resource_id)
        ).fetchone()
        return row[0] if row else None

    def recover_stale(self, kind: str, from_status: str, to_status: str = "seen") -> int:
        """Reset resources stuck in an intermediate state (e.g. after a crash)."""
        cur = self.conn.execute(
            "UPDATE ledger SET status = ?, updated_at = ? WHERE kind = ? AND status = ?",
            (to_status, _now(), kind, from_status),
        )
        self.conn.commit()
        return cur.rowcount

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT kind || ':' || status, COUNT(*) FROM ledger GROUP BY kind, status"
        ).fetchall()
        return dict(rows)
