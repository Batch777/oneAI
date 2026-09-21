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

## 主界面：pi + oneAI 扩展

TUI 直接使用 [pi](https://pi.dev)（内建：多行编辑器、拖拽选择复制、Kitty 内联图片、会话树、主题），oneAI 以扩展形式挂载：

```bash
oneai              # 直接启动 pi（自动挂载 oneAI 扩展，默认 deepseek-flash）
oneai tui          # 同上
oneai tui-legacy   # 实验性 Textual TUI（参考实现）
```

- 直接对话提问 —— agent 自动调用 `vault_search` / `vault_read`，回答附 `[[path#Lx-Ly]]` 引用
- `/draft <指令>` — 起草手稿（对话中触发 draft_create 会弹窗确认）
- `/inbox` `/reindex` — 收件箱 / 重建索引
- 键位：pi 默认（Ctrl+C 清空、Ctrl+D 空输入退出、Esc 中断）+ vim 模式（Esc / Ctrl+J 切 NORMAL）

安装状态：
- 扩展：`~/.pi/agent/extensions/oneai` → 软链到 `extension/oneai/`
- vim：`pi install npm:pi-vimmode`
- 配置：`~/.pi/agent/settings.json`（deepseek-flash 默认模型 + vim escape 别名）

## 自研 TUI（实验性，保留参考）

`oneai tui` —— 基于 Textual 的独立实现（vim 子集、拖拽复制、半块图像渲染），
用于验证交互设计；日常使用以 pi 为准。

## Python CLI（核心接口，独立可用）

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
