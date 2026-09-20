# Architecture

See the design discussion in `docs/`. Core principles:

1. **Filesystem is the source of truth** — all personal data is Markdown in
   `vault/`; indexes and state are derived and rebuildable.
2. **Provenance** — every answer cites `path#Lstart-Lend` of raw notes.
3. **Idempotency** — the ledger (`state/ledger.sqlite`) guarantees each email
   is read/drafted/sent exactly once. The event log (`state/events.jsonl`)
   records every agent action for audit.
4. **Portability** — the `oneai/` core is pure Python, no macOS APIs. Platform
   glue (iCloud, Apple Notes) lives in `connectors/`. Move to a Linux server
   by setting `ONEAI_VAULT_PATH` / `ONEAI_STATE_PATH`.

## Layout

- `oneai/runtime.py` — extensible agent runtime (pi-style): tool/command
  registries, lifecycle hooks, auto-discovery of extensions, permission gates
- `oneai/tui.py` — standalone TUI: slash commands, Tab completion, Markdown
  rendering, confirm modal for gated tools
- `oneai/` (其余) — vault, indexer (FTS5), ledger, events, LLM, CLI
- `extension/oneai/` — optional pi bridge (legacy)
- `connectors/outlook/` — Microsoft Graph — **deferred** (2026-09-20 decision:
  drafting strictly user-triggered, never automatic)

## Extension system

Extensions live in `~/.oneai/extensions/*.py` (global) or `.oneai/extensions/*.py`
(project). Each exposes `setup(rt)`. Hooks: `before_agent_start` (append system
prompt), `tool_call` (return `{"block": True, "reason": ...}` to block),
`tool_result`, `agent_end`. Tools with `confirm=True` pop a user approval gate.
`/reload` hot-reloads.
- `ios/` — SwiftUI app (thin client over the iCloud-synced vault)
- `docs/` — design docs and ADRs

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | LLM auth (required for `ask`) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible endpoint |
| `ONEAI_MODEL` | `deepseek-flash` | model name |
| `ONEAI_VAULT_PATH` | iCloud Drive `oneAI/vault` | vault location |
| `ONEAI_STATE_PATH` | `~/.oneai/state` | index/ledger/events |
| `ONEAI_CLI` | repo venv path | pi extension: path to the `oneai` binary |
| `ONEAI_GRAPH_CLIENT_ID` | — | Azure app registration for Outlook (deferred) |
