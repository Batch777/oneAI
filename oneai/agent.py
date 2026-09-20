"""Agent helpers: draft pipeline, identity context. (Q&A lives in runtime.py.)"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config
from .events import EventLog
from .indexer import Index, SearchResult
from .llm import LLM
from .vault import new_id, now_iso, write_note


def build_context(results: list[SearchResult]) -> str:
    blocks = []
    for r in results:
        body = r.text or r.snippet
        blocks.append(f"--- {r.citation} ({r.heading or 'no heading'}) ---\n{body[:1500]}")
    return "\n\n".join(blocks)


def _identity_context(cfg: Config) -> str:
    """Load facts/identity.md so the assistant knows who it serves.
    Personal data lives in the vault, never in the codebase."""
    path = cfg.vault_path / "facts" / "identity.md"
    if not path.exists():
        return ""
    body = path.read_text(encoding="utf-8")
    return f"\n\nIdentity of the user you serve:\n{body}"


DRAFT_SYSTEM = """You are a writing assistant for the user.
Write a manuscript following the user's instruction, grounded in the context
notes below. Reply in the user's preferred language. Do not send anything;
output only the manuscript text."""


def draft_manuscript(cfg: Config, instruction: str, k: int = 6) -> Path:
    """Generate a manuscript from vault context and save it to inbox/drafts/.

    Called ONLY on explicit user action (TUI 'AI Draft' button / CLI),
    never automatically. The draft has status 'drafted' until the user edits
    it and sets 'approved'.
    """
    index = Index(cfg.index_db)
    results = index.search(instruction, k=k)
    index.close()
    context = build_context(results) if results else "(no vault context)"

    body = LLM(cfg).chat(
        DRAFT_SYSTEM + _identity_context(cfg),
        f"Context:\n{context}\n\nInstruction: {instruction}",
    )

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rel = f"inbox/drafts/{ts}-{new_id()}.md"
    path = write_note(
        cfg.vault_path,
        rel,
        {
            "id": new_id(),
            "title": instruction[:40],
            "status": "drafted",  # drafted -> approved -> sent
            "instruction": instruction,
            "created": now_iso(),
            "sources": [r.citation for r in results],
        },
        body + "\n",
    )
    EventLog(cfg.events_log).emit("draft.created", path=rel, instruction=instruction)
    return path
