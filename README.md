# oneAI

Local-first AI personal assistant. Your data stays as plain Markdown you can
read and edit; agents index it, cite their sources, and never send anything
without your approval.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export DEEPSEEK_API_KEY=sk-...        # DeepSeek API key
oneai init                            # creates vault in iCloud Drive
oneai index                           # build the search index
oneai search "topic"
oneai ask "question"                  # answers with [[path#Lx-Ly]] citations
oneai draft "instruction"             # manuscript → inbox/drafts/ (manual only)
oneai tui                             # terminal UI: chat / search / AI Draft / Inbox
```

See `docs/ARCHITECTURE.md` for the design and configuration reference.
