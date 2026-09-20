"""oneai CLI."""
from __future__ import annotations

import argparse
import sys

from .agent import ask
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

    p_ask = sub.add_parser("ask", help="ask a question, answered with citations")
    p_ask.add_argument("question")

    sub.add_parser("status", help="show ledger counts and recent events")

    args = p.parse_args()
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
        for r in index.search(args.query, k=args.k):
            print(f"{r.citation}  [{r.heading}]\n  {r.snippet}\n")
        index.close()

    elif args.cmd == "ask":
        try:
            print(ask(cfg, args.question))
        except RuntimeError as e:
            sys.exit(f"error: {e}")

    elif args.cmd == "status":
        ledger = Ledger(cfg.ledger_db)
        print("Ledger:", ledger.counts() or "(empty)")
        print("Recent events:")
        for e in EventLog(cfg.events_log).tail(10):
            print(f"  {e['ts']}  {e['type']}")


if __name__ == "__main__":
    main()
