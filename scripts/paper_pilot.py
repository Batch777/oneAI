"""Local PDF baseline. Originals are read-only; all derived data stays in --output.

Usage: python scripts/paper_pilot.py ROOT --output state/paper-pilot
Requires pypdf. This is a lexical baseline, not a semantic benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def build(root: Path, output: Path) -> dict:
    from pypdf import PdfReader

    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    db = sqlite3.connect(output / "pages.sqlite")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(version UNINDEXED, path UNINDEXED, page UNINDEXED, text, tokenize='trigram')")
    seen = set()
    files, errors = [], []
    with db:
        db.execute("DELETE FROM pages")
        for path in sorted(root.rglob("*.pdf")):
            with path.open("rb") as source:
                version = hashlib.file_digest(source, "sha256").hexdigest()
            if version in seen:
                files.append({"path": str(path), "version": version, "duplicate": True})
                continue
            try:
                reader = PdfReader(path)
                raw_pages = [(page.extract_text() or "").replace("\x00", "") for page in reader.pages]
                # Broken PDF font maps can produce isolated Unicode surrogates.
                pages = [text.encode("utf-8", "replace").decode("utf-8") for text in raw_pages]
                db.execute("SAVEPOINT document")
                db.executemany("INSERT INTO pages VALUES (?,?,?,?)", [(version, str(path), n, text) for n, text in enumerate(pages, 1)])
                db.execute("RELEASE document")
                seen.add(version)
                files.append({"path": str(path), "version": version, "pages": len(pages), "encoding_repaired": pages != raw_pages, "short_pages": [n for n, text in enumerate(pages, 1) if len(text.strip()) < 100]})
            except Exception as error:
                try:
                    db.execute("ROLLBACK TO document")
                    db.execute("RELEASE document")
                except sqlite3.OperationalError:
                    pass
                errors.append({"path": str(path), "error": str(error)})
    report = {"files": files, "errors": errors, "unique_documents": len(seen), "indexed_pages": db.execute("SELECT COUNT(*) FROM pages").fetchone()[0], "seconds": round(time.monotonic() - started, 2), "limitation": "pypdf text order and tables are not verified; trigram search requires >=3 characters"}
    db.close()
    (output / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return {key: value for key, value in report.items() if key not in ("files", "errors")}


def search(output: Path, query: str, k: int = 8) -> list[dict]:
    with sqlite3.connect(output / "pages.sqlite") as db:
        rows = db.execute("SELECT version,path,page,snippet(pages,3,'[',']','…',40) FROM pages WHERE pages MATCH ? ORDER BY rank LIMIT ?", ('"' + query.replace('"', '""') + '"', k)).fetchall()
    return [dict(version=v, path=p, page=n, snippet=s, citation=f"{p}@{v}#page={n}") for v, p, n, s in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query")
    args = parser.parse_args()
    print(json.dumps(search(args.output, args.query) if args.query else build(args.root, args.output), ensure_ascii=False, indent=2))
