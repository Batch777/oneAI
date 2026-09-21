"""Vault indexer: SQLite FTS5 full-text search with file:line provenance.

The index is a *derived* artifact — delete index.sqlite and `oneai index`
rebuilds it fully from Markdown. Every search result carries the source path
and line range so answers can always be traced to raw data.
"""
from __future__ import annotations

import re
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .vault import iter_markdown, resolve_note

# trigram tokenizer: substring matching, works for unsegmented CJK text.
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    text,
    heading,
    title,
    source_version UNINDEXED,
    path UNINDEXED,
    start_line UNINDEXED,
    end_line UNINDEXED,
    tokenize = 'trigram'
);
"""

MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 60  # only merge near-empty chunks (lone headings) into the next

# Generated artifacts are NOT knowledge sources — never index them,
# otherwise stale drafts feed back into answers.
EXCLUDED_PREFIXES = ("inbox/drafts", "inbox/tasks", "inbox/cloud-tasks", "inbox/commands")


@dataclass
class SearchResult:
    path: str        # vault-relative path
    start_line: int
    end_line: int
    heading: str
    snippet: str
    text: str = ""   # full chunk text (for LLM context; snippet is display-only)

    source_version: str = ""

    @property
    def citation(self) -> str:
        return f"{self.path}@{self.source_version}#L{self.start_line}-L{self.end_line}" if self.source_version else f"{self.path}#L{self.start_line}-L{self.end_line}"


def _strip_frontmatter(text: str) -> tuple[str, int]:
    """Remove YAML frontmatter; return body and its 1-based starting line."""
    m = re.match(r"\A---\n.*?\n---\n", text, re.DOTALL)
    if m:
        stripped = text[m.end():]
        return stripped, text[: m.end()].count("\n") + 1
    return text, 1


def _frontmatter_title(text: str) -> str:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return ""
    t = re.search(r"^title:\s*(.+)$", m.group(1), re.MULTILINE)
    return t.group(1).strip().strip('"\'') if t else ""


def chunk_markdown(text: str) -> list[tuple[int, int, str, str]]:
    """Split into (start_line, end_line, heading, text) chunks.

    Chunks break at headings, and at paragraph boundaries when a section
    exceeds MAX_CHUNK_CHARS.
    """
    lines = text.splitlines()
    chunks: list[tuple[int, int, str, str]] = []
    heading = ""
    buf: list[str] = []
    start = 1
    size = 0

    def flush(end_line: int) -> None:
        nonlocal buf, size
        body = "\n".join(buf).strip()
        if body:
            chunks.append((start, end_line, heading, body))
        buf, size = [], 0

    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            flush(i - 1)
            heading = line.lstrip("#").strip()
            buf, start, size = [line], i, len(line)
            continue
        if not line.strip() and size > MAX_CHUNK_CHARS:
            flush(i - 1)
            start = i + 1
            continue
        buf.append(line)
        size += len(line) + 1
    flush(len(lines))
    return _merge_small(chunks)


def _merge_small(chunks: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """Merge a too-small chunk (e.g. a lone heading) into the next one."""
    merged: list[tuple[int, int, str, str]] = []
    for chunk in chunks:
        if merged and len(merged[-1][3]) < MIN_CHUNK_CHARS:
            s, _, h, t = merged.pop()
            e2, h2, t2 = chunk[1], chunk[2], chunk[3]
            merged.append((s, e2, h2 or h, t + "\n" + t2))
        else:
            merged.append(chunk)
    return [(s, e, h, t) for s, e, h, t in merged if t.strip()]


def _safe_query(query: str) -> str:
    """Turn free text into a safe FTS5 query: quoted tokens joined by OR."""
    tokens = re.findall(r"[\w]+", query.lower())
    return " OR ".join(f'"{t}"' for t in tokens) or '""'


def _cjk_bigrams(query: str) -> list[str]:
    """Overlapping 2-char substrings of CJK runs, for LIKE fallback matching.
    Trigram FTS cannot match 2-char Chinese words (e.g. 用户); these can."""
    bigrams: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        bigrams.extend(run[i : i + 2] for i in range(len(run) - 1))
    return bigrams


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Index:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(chunks)")}
        if columns and "source_version" not in columns:
            self.conn.execute("DROP TABLE chunks")  # derived v1 index; refreshed on search
        self.conn.executescript(SCHEMA + """
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        # Historical evidence is authoritative data, separate from the disposable index.
        self.sources = sqlite3.connect(db_path.parent / "sources.sqlite", timeout=30)
        self.sources.execute("""CREATE TABLE IF NOT EXISTS snapshots(
            root TEXT, path TEXT, version TEXT, raw TEXT NOT NULL,
            PRIMARY KEY(root, path, version))""")
        self.sources.commit()

    @staticmethod
    def excluded(rel: str) -> bool:
        return any(rel == prefix or rel.startswith(prefix + "/") for prefix in EXCLUDED_PREFIXES)

    def _prepare(self, vault: Path, path: Path):
        rel = str(path.relative_to(vault))
        safe = resolve_note(vault, rel)
        raw = safe.read_text(encoding="utf-8")
        version = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        body, offset = _strip_frontmatter(raw)
        title = _frontmatter_title(raw)
        body_lines = body.splitlines()
        chunks = [("\n".join(body_lines[a - 1:b]), h, title, version, rel, a + offset - 1, b + offset - 1)
                  for a, b, h, t in chunk_markdown(body)]
        return rel, version, raw, chunks

    def _store(self, vault: Path, prepared) -> int:
        rel, version, raw, chunks = prepared
        with self.sources:
            self.sources.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)",
                                 (str(vault.resolve()), rel, version, raw))
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        self.conn.executemany(
            "INSERT INTO chunks(text,heading,title,source_version,path,start_line,end_line) VALUES (?,?,?,?,?,?,?)",
            chunks)
        self.conn.execute("INSERT OR REPLACE INTO files VALUES (?,?)", (rel, version))
        return len(chunks)

    def _bind(self, vault: Path) -> None:
        root = str(vault.resolve())
        row = self.conn.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if row is None or row[0] != root:
            self.conn.execute("DELETE FROM chunks")
            self.conn.execute("DELETE FROM files")
            self.conn.execute("INSERT OR REPLACE INTO metadata VALUES ('root',?)", (root,))

    def sync(self, vault: Path, *, force: bool = False) -> int:
        if not vault.is_dir():
            raise FileNotFoundError(f"Vault unavailable: {vault}")
        # One transaction: readers see either the old or the complete new index.
        changed = 0
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._bind(vault)
            known = dict(self.conn.execute("SELECT path, version FROM files"))
            seen = set()
            for path in iter_markdown(vault):
                rel = str(path.relative_to(vault))
                if self.excluded(rel):
                    continue
                prepared = self._prepare(vault, path)
                seen.add(rel)
                if force or known.get(rel) != prepared[1]:
                    changed += self._store(vault, prepared)
            for rel in known.keys() - seen:
                self.conn.execute("DELETE FROM chunks WHERE path=?", (rel,))
                self.conn.execute("DELETE FROM files WHERE path=?", (rel,))
        return changed

    def rebuild(self, vault: Path) -> int:
        return self.sync(vault, force=True)

    def index_file(self, vault: Path, path: Path) -> int:
        rel = str(path.relative_to(vault))
        safe = resolve_note(vault, rel)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._bind(vault)
            if self.excluded(rel) or not safe.exists():
                self.conn.execute("DELETE FROM chunks WHERE path=?", (rel,))
                self.conn.execute("DELETE FROM files WHERE path=?", (rel,))
                return 0
            return self._store(vault, self._prepare(vault, path))

    def read_version(self, vault: Path, rel: str, version: str) -> str:
        resolve_note(vault, rel)
        row = self.sources.execute(
            "SELECT raw FROM snapshots WHERE root=? AND path=? AND version=?",
            (str(vault.resolve()), rel, version)).fetchone()
        if row is None:
            raise ValueError("Unknown source version for this document")
        return row[0]

    def search(self, query: str, k: int = 8) -> list[SearchResult]:
        if not 1 <= k <= 100:
            raise ValueError("k must be between 1 and 100")
        rows = self.conn.execute(
            """SELECT path, start_line, end_line, heading,
                snippet(chunks, 0, '«', '»', '…', 32), text, source_version
                FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?""",
            (_safe_query(query), k)).fetchall()
        results = [SearchResult(p, a, b, h or "", sn, t, v) for p, a, b, h, sn, t, v in rows]
        seen = {(r.path, r.start_line) for r in results}
        for bg in dict.fromkeys(_cjk_bigrams(query)):
            rows = self.conn.execute(
                """SELECT path,start_line,end_line,heading,substr(text,1,90),text,source_version
                   FROM chunks WHERE text LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\'
                   ORDER BY path, start_line LIMIT ?""",
                (f"%{_escape_like(bg)}%", f"%{_escape_like(bg)}%", k)).fetchall()
            for p, a, b, h, sn, t, v in rows:
                if (p, a) not in seen:
                    seen.add((p, a))
                    results.append(SearchResult(p, a, b, h or "", sn, t, v))
        return results[:k]

    def close(self) -> None:
        self.conn.close()
        self.sources.close()
