"""Markdown vault: the source of truth for all personal data.

Notes are plain Markdown with YAML frontmatter. You can read/edit them in any
editor; the index and agents always derive from these files.
"""
from __future__ import annotations

import uuid
import os
import re
import tempfile
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
    "rules",        # user-authored persistent instructions
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
    path = resolve_note(vault, rel_path)
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    return Note(path=path, rel_path=rel_path, metadata=dict(post.metadata), body=post.content)


def write_note(vault: Path, rel_path: str, metadata: dict, body: str) -> Path:
    path = resolve_note(vault, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(body, **metadata)
    atomic_write(path, frontmatter.dumps(post) + "\n")
    return path


def iter_markdown(vault: Path):
    for path in sorted(vault.rglob("*.md")):
        # External symlinks never become implicit knowledge sources.
        try:
            resolve_note(vault, str(path.relative_to(vault)))
        except ValueError:
            continue
        yield path


def resolve_note(vault: Path, rel_path: str) -> Path:
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Expected a vault-relative Markdown path")
    root = vault.resolve()
    path = (root / candidate).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".md":
        raise ValueError("Path must stay inside the vault and end in .md")
    return path


def select_lines(raw: str, lines: str | None) -> str:
    if not lines:
        return raw
    match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", lines)
    if not match:
        raise ValueError("Line range must be N or N-M (positive, inclusive)")
    start, end = int(match[1]), int(match[2] or match[1])
    if end < start or end > len(raw.splitlines()):
        raise ValueError("Line range is outside the document")
    return "\n".join(raw.splitlines()[start - 1:end])


def atomic_write(path: Path, text: str) -> None:
    """Publish complete UTF-8 files; never expose a partially written draft."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not path.is_symlink() and path.read_bytes() == text.encode("utf-8"):
        return
    fd, temporary = tempfile.mkstemp(prefix=".oneai-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
