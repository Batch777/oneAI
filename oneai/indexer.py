"""Vault indexer: SQLite FTS5 full-text search with file:line provenance.

The index is a *derived* artifact — delete index.sqlite and `oneai index`
rebuilds it fully from Markdown. Every search result carries the source path
and line range so answers can always be traced to raw data.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# trigram tokenizer: substring matching, works for unsegmented CJK text.
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    text,
    heading,
    path UNINDEXED,
    start_line UNINDEXED,
    end_line UNINDEXED,
    tokenize = 'trigram'
);
"""

MAX_CHUNK_CHARS = 1200


@dataclass
class SearchResult:
    path: str        # vault-relative path
    start_line: int
    end_line: int
    heading: str
    snippet: str

    @property
    def citation(self) -> str:
        return f"{self.path}#L{self.start_line}-L{self.end_line}"


def _strip_frontmatter(text: str) -> tuple[str, int]:
    """Remove YAML frontmatter; return body and its 1-based starting line."""
    m = re.match(r"\A---\n.*?\n---\n", text, re.DOTALL)
    if m:
        stripped = text[m.end():]
        return stripped, text[: m.end()].count("\n") + 1
    return text, 1


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
    return chunks


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
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)

    def rebuild(self, vault: Path) -> int:
        # Drop and recreate so tokenizer changes take effect on rebuild.
        self.conn.execute("DROP TABLE IF EXISTS chunks")
        self.conn.executescript(SCHEMA)
        n = 0
        for md in sorted(vault.rglob("*.md")):
            n += self.index_file(vault, md)
        self.conn.commit()
        return n

    def index_file(self, vault: Path, path: Path) -> int:
        rel = str(path.relative_to(vault))
        body, offset = _strip_frontmatter(path.read_text(encoding="utf-8"))
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        chunks = chunk_markdown(body)
        self.conn.executemany(
            "INSERT INTO chunks (text, heading, path, start_line, end_line) VALUES (?,?,?,?,?)",
            [(t, h, rel, s + offset - 1, e + offset - 1) for s, e, h, t in chunks],
        )
        self.conn.commit()
        return len(chunks)

    def search(self, query: str, k: int = 8) -> list[SearchResult]:
        rows = self.conn.execute(
            """
            SELECT path, start_line, end_line, heading,
                   snippet(chunks, 0, '«', '»', '…', 32) AS snip
            FROM chunks
            WHERE chunks MATCH ?
            ORDER BY bm25(chunks)
            LIMIT ?
            """,
            (_safe_query(query), k),
        ).fetchall()
        results = [SearchResult(p, s, e, h or "", sn) for p, s, e, h, sn in rows]

        # CJK fallback: substring LIKE matching for Chinese bigrams.
        seen = {(r.path, r.start_line) for r in results}
        for bg in _cjk_bigrams(query):
            rows = self.conn.execute(
                """
                SELECT path, start_line, end_line, heading,
                       substr(text, max(1, instr(text, ?1) - 30), 90)
                FROM chunks
                WHERE text LIKE ?2 ESCAPE '\\'
                LIMIT ?3
                """,
                (bg, f"%{_escape_like(bg)}%", k),
            ).fetchall()
            for p, s, e, h, sn in rows:
                if (p, s) not in seen:
                    seen.add((p, s))
                    results.append(SearchResult(p, s, e, h or "", sn.replace("\n", " ")))
        return results[:k]

    def close(self) -> None:
        self.conn.close()
