"""Agent helpers: draft pipeline, identity context. (Legacy Q&A lives in legacy/runtime.py.)"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config
from .events import EventLog
from .indexer import Index, SearchResult
from .llm import LLM
from .vault import new_id, now_iso, write_note
from .context import context_text, load_context


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
    never automatically. This creates a local artifact, not send authorization.
    """
    index = Index(cfg.index_db)
    try:
        index.sync(cfg.vault_path)
        results = index.search(instruction, k=k)
    finally:
        index.close()
    context = build_context(results) if results else "(no vault context)"

    body = LLM(cfg).chat(
        DRAFT_SYSTEM + "\n\n" + context_text(cfg),
        f"Context:\n{context}\n\nInstruction: {instruction}",
    )

    return save_manuscript(cfg, instruction[:40], body, instruction=instruction,
                           sources=[{"path": r.path, "version": r.source_version,
                                     "lines": f"{r.start_line}-{r.end_line}"} for r in results])


def save_manuscript(cfg: Config, title: str, body: str, *, instruction: str = "",
                    sources: list[dict] | None = None) -> Path:
    """Save the active agent's exact text. No second model call or external send."""
    from .vault import select_lines
    if not isinstance(title, str) or not isinstance(body, str) or not body.strip():
        raise ValueError("A title and non-empty draft body are required")
    sources = [] if sources is None else sources
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    index = Index(cfg.index_db)
    try:
        for source in sources:
            if not isinstance(source, dict) or not all(isinstance(source.get(k), str)
                                                     for k in ("path", "version", "lines")):
                raise ValueError("Each source needs path, version and lines")
            raw = index.read_version(cfg.vault_path, source["path"], source["version"])
            select_lines(raw, source["lines"])
    finally:
        index.close()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rel = f"inbox/drafts/{ts}-{new_id()}.md"
    path = write_note(
        cfg.vault_path,
        rel,
        {
            "id": new_id(),
            "title": title,
            "status": "drafted",  # never interpreted as authorization to send
            "instruction": instruction,
            "created": now_iso(),
            "sources": sources,
            "context_versions_at_save": [{"path": e["path"], "version": e["version"]}
                                 for e in load_context(cfg)["entries"]],
        },
        body + "\n",
    )
    EventLog(cfg.events_log).emit("draft.created", path=rel, instruction=instruction)
    return path
