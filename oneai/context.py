"""Shared, inspectable context for every supported drafting entry point."""
from __future__ import annotations

import hashlib
from .config import Config
from .vault import iter_markdown, resolve_note


def load_context(cfg: Config) -> dict:
    entries = []
    identity = resolve_note(cfg.vault_path, "facts/identity.md")
    paths = [identity] if identity.exists() else []
    rules = cfg.vault_path / "rules"
    if rules.exists() and rules.resolve().is_relative_to(cfg.vault_path.resolve()):
        paths.extend(iter_markdown(rules))
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        entries.append({"path": str(path.resolve().relative_to(cfg.vault_path.resolve())),
                        "version": hashlib.sha256(raw.encode()).hexdigest(), "text": raw})
    return {"entries": entries, "policy": "Local drafts are editable artifacts, not approval to send."}


def context_text(cfg: Config) -> str:
    return "\n\n".join(f"--- {e['path']}@{e['version']} ---\n{e['text']}"
                       for e in load_context(cfg)["entries"])
