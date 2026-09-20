"""Markdown vault: the source of truth for all personal data.

Notes are plain Markdown with YAML frontmatter. You can read/edit them in any
editor; the index and agents always derive from these files.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

VAULT_DIRS = [
    "people",       # one file per contact
    "projects",     # one folder per project
    "facts",        # atomic personal facts (preferences, accounts, history)
    "decisions",    # dated decision records
    "journal",      # daily notes
    "inbox",        # unprocessed captures (phone, email, quick notes)
    "inbox/drafts", # email manuscripts awaiting your approval
]


@dataclass
class Note:
    path: Path          # absolute path on disk
    rel_path: str       # path relative to vault root, used in citations
    metadata: dict
    body: str

    @property
    def title(self) -> str:
        return self.metadata.get("title") or Path(self.rel_path).stem


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_vault(vault: Path) -> None:
    """Create the directory skeleton and a README. Safe to re-run."""
    for d in VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    readme = vault / "README.md"
    if not readme.exists():
        write_note(
            vault,
            "README.md",
            {"id": new_id(), "title": "Vault README", "tags": ["meta"], "created": now_iso()},
            "# Personal Vault\n\n"
            "This directory is the source of truth for the oneAI assistant.\n\n"
            "- `people/` — one note per contact\n"
            "- `projects/` — one folder per project\n"
            "- `facts/` — atomic facts about you\n"
            "- `decisions/` — dated decision records\n"
            "- `journal/` — daily notes\n"
            "- `inbox/` — captures from phone/email, unprocessed\n"
            "- `inbox/drafts/` — email manuscripts awaiting your approval\n",
        )


def read_note(vault: Path, rel_path: str) -> Note:
    path = vault / rel_path
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    return Note(path=path, rel_path=rel_path, metadata=dict(post.metadata), body=post.content)


def write_note(vault: Path, rel_path: str, metadata: dict, body: str) -> Path:
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(body, **metadata)
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    return path


def iter_markdown(vault: Path):
    yield from sorted(vault.rglob("*.md"))
