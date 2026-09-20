"""Live tool-use test against the real DeepSeek API.

Skipped by default; run with:  ONEAI_LIVE_TEST=1 pytest tests/test_live.py
Uses the real vault (read-only question; no drafts are created).
"""
from __future__ import annotations

import os

import pytest

from oneai.config import Config
from oneai.runtime import Runtime

pytestmark = pytest.mark.skipif(
    os.environ.get("ONEAI_LIVE_TEST") != "1", reason="set ONEAI_LIVE_TEST=1 to run")


def test_agent_uses_vault_search_and_cites():
    rt = Runtime(Config.load())
    tools_used = []
    answer = rt.run_agent(
        "我的教育背景是什么？",
        on_event=lambda k, d: tools_used.append(d["name"]) if k == "tool_start" else None,
    )
    assert "vault_search" in tools_used
    assert "[[" in answer and ".md#L" in answer  # citation present
