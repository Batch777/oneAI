"""oneai CLI."""
from __future__ import annotations

import argparse
import sys

from .config import Config
from .events import EventLog
from .indexer import Index
from .ledger import Ledger
from .vault import init_vault


def main() -> None:
    p = argparse.ArgumentParser(prog="oneai", description="AI personal assistant")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create vault skeleton and state dirs")

    p_index = sub.add_parser("index", help="rebuild the search index from Markdown")
    p_index.add_argument("--file", help="reindex a single file (vault-relative)")

    p_search = sub.add_parser("search", help="full-text search the vault")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=8)
    p_search.add_argument("--json", action="store_true", help="machine-readable output")

    p_read = sub.add_parser("read", help="read a note (optionally a line range)")
    p_read.add_argument("path", help="vault-relative path")
    p_read.add_argument("--lines", help="e.g. 8-16 (1-based, inclusive)")

    sub.add_parser("inbox", help="list inbox captures and drafts")

    p_ask = sub.add_parser("ask", help="ask a question, answered with citations")
    p_ask.add_argument("question")

    p_draft = sub.add_parser("draft", help="draft a manuscript into inbox/drafts/ (manual trigger only)")
    p_draft.add_argument("instruction")
    p_draft.add_argument("--json", action="store_true")

    sub.add_parser("tui", help="launch the TUI (pi with the oneAI extension)")
    sub.add_parser("tui-legacy", help="experimental Textual TUI (reference implementation)")
    sub.add_parser("wheel-debug", help="numeric wheel/trackpad scroll debugger")

    sub.add_parser("status", help="show ledger counts and recent events")

    args = p.parse_args()

    # bare `oneai` / `oneai tui` → pi with the oneAI extension (auto-discovered
    # from ~/.pi/agent/extensions/oneai)
    if args.cmd in (None, "tui"):
        import os
        import shutil

        pi = shutil.which("pi")
        if not pi:
            sys.exit("error: pi not found — install pi first (https://pi.dev)")
        os.execvp(pi, [pi])

    cfg = Config.load()

    if args.cmd == "init":
        cfg.ensure_dirs()
        init_vault(cfg.vault_path)
        print(f"Vault:  {cfg.vault_path}")
        print(f"State:  {cfg.state_path}")
        print("Next:   add notes, then `oneai index`")

    elif args.cmd == "index":
        index = Index(cfg.index_db)
        if args.file:
            n = index.index_file(cfg.vault_path, cfg.vault_path / args.file)
        else:
            n = index.rebuild(cfg.vault_path)
        index.close()
        print(f"Indexed {n} chunk(s)")

    elif args.cmd == "search":
        index = Index(cfg.index_db)
        results = index.search(args.query, k=args.k)
        index.close()
        if args.json:
            import json

            print(json.dumps(
                [{"path": r.path, "start_line": r.start_line, "end_line": r.end_line,
                  "heading": r.heading, "citation": r.citation, "text": r.text}
                 for r in results],
                ensure_ascii=False))
        else:
            for r in results:
                print(f"{r.citation}  [{r.heading}]\n  {r.snippet}\n")

    elif args.cmd == "read":
        import json

        if args.lines:
            # Line ranges refer to *file* lines (matching search citations),
            # which include the frontmatter block.
            raw = (cfg.vault_path / args.path).read_text(encoding="utf-8")
            a, _, b = args.lines.partition("-")
            body = "\n".join(raw.splitlines()[int(a) - 1 : int(b)])
            print(json.dumps({"path": args.path, "lines": args.lines, "body": body},
                             ensure_ascii=False))
        else:
            from .vault import read_note

            note = read_note(cfg.vault_path, args.path)
            print(json.dumps({"path": args.path, "metadata": note.metadata, "body": note.body},
                             ensure_ascii=False, default=str))

    elif args.cmd == "inbox":
        import json

        inbox = cfg.vault_path / "inbox"
        files = [str(f.relative_to(cfg.vault_path))
                 for f in sorted(inbox.rglob("*.md"))] if inbox.exists() else []
        print(json.dumps(files, ensure_ascii=False))

    elif args.cmd == "ask":
        from .runtime import Runtime

        rt = Runtime(cfg)
        rt.load_extensions()

        def ev(kind: str, data: dict) -> None:
            if kind == "tool_start":
                print(f"  [tool] {data['name']}({list(data['args'].values())[:1]})",
                      file=sys.stderr)

        try:
            print(rt.run_agent(args.question, on_event=ev))
        except RuntimeError as e:
            sys.exit(f"error: {e}")

    elif args.cmd == "draft":
        import json

        from .agent import draft_manuscript

        path = draft_manuscript(cfg, args.instruction)
        rel = str(path.relative_to(cfg.vault_path))
        if args.json:
            print(json.dumps({"path": rel, "status": "drafted"}, ensure_ascii=False))
        else:
            print(f"Draft saved: {rel}  (status: drafted)")

    elif args.cmd == "tui-legacy":
        from .tui import run

        run()

    elif args.cmd == "wheel-debug":
        from .wheel_debug import run

        run()

    elif args.cmd == "status":
        ledger = Ledger(cfg.ledger_db)
        print("Ledger:", ledger.counts() or "(empty)")
        print("Recent events:")
        for e in EventLog(cfg.events_log).tail(10):
            print(f"  {e['ts']}  {e['type']}")


if __name__ == "__main__":
    main()
