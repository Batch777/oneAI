# oneAI

Local-first AI personal assistant. Your data stays as plain Markdown you can
read and edit; agents index it, cite their sources, and never send anything
without your approval.

## 主界面：pi 扩展（推荐）

oneAI 作为 pi 的扩展运行，复用 pi 的 TUI（命令补全、/copy、Markdown 渲染、图片）：

```bash
pi                 # 启动后默认使用 deepseek-flash
```

- 直接对话提问 —— agent 自动调用 `vault_search` / `vault_read` 工具，回答附 `[[path#Lx-Ly]]` 引用
- `/draft <指令>` — 起草手稿（唯一入口；对话中触发 draft_create 也会弹窗确认）
- `/inbox` `/reindex` — 查看收件箱 / 重建索引

扩展源码在 `extension/oneai/`，软链安装于 `~/.pi/agent/extensions/oneai`。

## Python CLI（核心接口，独立可用）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export DEEPSEEK_API_KEY=sk-...        # DeepSeek API key
oneai init                            # creates vault in iCloud Drive
oneai index                           # build the search index
oneai search "topic" [--json]
oneai read facts/education.md [--lines 8-16]
oneai inbox
oneai ask "question"                  # standalone Q&A with citations
oneai draft "instruction" [--json]    # manuscript → inbox/drafts/ (manual only)
oneai tui                             # legacy Textual UI (superseded by pi)
```

See `docs/ARCHITECTURE.md` for the design and configuration reference.
