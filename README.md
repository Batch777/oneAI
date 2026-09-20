# oneAI

独立的 AI 个人助理产品：本地优先、数据即 Markdown、回答可溯源、起草需审批。

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export DEEPSEEK_API_KEY=sk-...   # deepseek-flash（OpenAI 兼容接口）
oneai init                       # 在 iCloud Drive 创建 vault
oneai tui                        # 启动主界面
```

## 主界面（oneai tui）

```
┌─ oneAI ───────────────────────────────┐
│ 你: 我本科在哪读的？                    │
│ 🔧 vault_search(query=本科)            │
│ 助手: 中国海洋大学 物理学 [[facts/…]]    │
│                                       │
│ /draft …（Tab 补全提示）               │
│ > 直接输入提问；/ 开头为命令             │
└───────────────────────────────────────┘
```

- 对话式问答：agent 自主调用 `vault_search`/`vault_read`，回答附 `[[path#Lx-Ly]]` 引用
- `/draft <指令>` 或对话中要求起草 → 弹确认门（y/n）→ 手稿存 `inbox/drafts/`（status: drafted）
- `/help` `/inbox` `/reindex` `/reload` `/copy` `/new` `/clear` `/quit`
- 输入 `/` 实时显示命令提示，Tab 补全；Option+拖拽终端原生选择

## 扩展系统（pi 风格）

往 `~/.oneai/extensions/*.py`（全局）或 `.oneai/extensions/*.py`（项目级）放 Python 文件即可：

```python
from oneai.runtime import Command, Tool

def setup(rt):
    # 注册斜杠命令（出现在 /help 和 Tab 补全中）
    rt.register_command(Command("motivate", "来一句加油", lambda a: print("加油！")))

    # 注册 LLM 工具（confirm=True 启用确认门）
    rt.register_tool(Tool("my_tool", "描述", {"type": "object", "properties": {}},
                          func=lambda: "ok", confirm=True))

    # 钩子：tool_call（可 block）、before_agent_start、tool_result、agent_end
    rt.on("tool_call", lambda name, arguments: {"block": True, "reason": "no"}
          if name == "dangerous" else None)
```

`/reload` 热加载。示例见 `examples/extensions/`。

## CLI（核心接口，独立可用）

```bash
oneai search "关键词" [--json]     # 本地检索，不耗 API
oneai read facts/education.md [--lines 8-16]
oneai inbox                       # 列出捕获与手稿
oneai ask "问题"                   # 单轮问答（走 agent 工具循环）
oneai draft "指令" [--json]        # 独立起草管线（手动触发）
oneai status                      # ledger 与事件日志
```

## 可选：pi 扩展桥

`extension/oneai/` 可把 vault 工具挂进 pi（`ln -s` 到 `~/.pi/agent/extensions/`）。
已非主路径，仅作兼容保留。

## 文档

`docs/ARCHITECTURE.md` — 设计原则、目录结构、环境变量一览。
