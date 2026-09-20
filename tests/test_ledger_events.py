"""Unit tests: ledger (once-only guarantee) + events log."""
from __future__ import annotations

from oneai.events import EventLog
from oneai.ledger import Ledger


class TestLedger:
    def test_claim_only_once(self, tmp_path):
        l = Ledger(tmp_path / "l.sqlite")
        assert l.claim("email", "msg-1") is True
        assert l.claim("email", "msg-1") is False  # never processed twice
        assert l.claim("email", "msg-2") is True

    def test_kinds_are_independent(self, tmp_path):
        l = Ledger(tmp_path / "l.sqlite")
        assert l.claim("email", "x") is True
        assert l.claim("task", "x") is True  # same id, different kind

    def test_status_machine(self, tmp_path):
        l = Ledger(tmp_path / "l.sqlite")
        l.claim("email", "m1")
        l.set_status("email", "m1", "drafted", detail="inbox/drafts/x.md")
        assert l.status("email", "m1") == "drafted"
        l.set_status("email", "m1", "sent")
        assert l.status("email", "m1") == "sent"

    def test_recover_stale(self, tmp_path):
        l = Ledger(tmp_path / "l.sqlite")
        l.claim("email", "m1")
        l.set_status("email", "m1", "drafting")  # crash mid-processing
        assert l.recover_stale("email", "drafting", "seen") == 1
        assert l.status("email", "m1") == "seen"

    def test_persistence(self, tmp_path):
        Ledger(tmp_path / "l.sqlite").claim("email", "m1")
        assert Ledger(tmp_path / "l.sqlite").status("email", "m1") == "seen"


class TestEvents:
    def test_append_and_tail(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        log.emit("a", x=1)
        log.emit("b", y="二")
        entries = log.tail()
        assert [e["type"] for e in entries] == ["a", "b"]
        assert entries[1]["y"] == "二"  # unicode preserved
        assert "ts" in entries[0]

    def test_tail_limit_and_missing_file(self, tmp_path):
        log = EventLog(tmp_path / "e.jsonl")
        assert log.tail() == []
        for i in range(30):
            log.emit("x", i=i)
        assert len(log.tail(10)) == 10
