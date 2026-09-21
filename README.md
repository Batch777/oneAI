# oneAI

个人资料与任务助手。当前可用：Markdown 检索、版本化引用、持久规则和本地草稿。新增独立后台任务流程，支持待填模板、版本修订与核对，见 [使用说明](docs/TASK-WORKFLOW.md)。未来界面见 [下一版 spec](docs/SPEC-NEXT.md)。

## 当前入口

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
oneai init
oneai                         # 显式加载本仓库扩展，暂用 pi 交互
```

pi 为可选交互依赖，目前已用本机 0.86.1 验证。启动器传递当前 Python 环境，不再硬编码用户路径；旧的全局 oneAI 扩展软链不再必需。外部 pi 单独启动时，需正确配置 `ONEAI_PYTHON` 或 `ONEAI_CLI`。

## 数据与起草

```bash
oneai search '关键词' --json       # 检索前增量同步，包含编辑和删除
oneai read facts/education.md --lines 8-16
oneai read facts/education.md --version <source_version> --lines 8-16
oneai watch                       # 持续对账；不依赖 pi 窗口，进程需自行保持运行
oneai watch --once
oneai context                     # 查看每轮加载的 identity 与 rules/*.md
oneai index                       # 事务内完整重建
```

- `/draft <要求>` 在当前 pi 会话生成正文，`draft_create` 原样保存，不再另起一个模型请求。
- `oneai draft '<要求>'` 是明确的独立单轮 CLI：需 `DEEPSEEK_API_KEY`，读取同一套规则和最新资料，不具有 pi 当前对话。
- `oneai draft-save` 从标准输入接受 `{ "title": "标题", "body": "正文", "sources": [] }`，不调用模型。
- 来源格式为 `{ "path": "facts/a.md", "version": "<hash>", "lines": "1-3" }`，保存前验证版本与行范围。
- `vault/rules/*.md` 为用户维护的规则，每轮重新加载；这保证上下文注入，不承诺模型永不违反规则。
- 保存本地草稿不要求额外弹窗。当前没有发送/提交能力，修改 `status: approved` 不会执行发送。

引用采用 `[[path@version#Lx-Ly]]`。历史原文保存到 `state/sources.sqlite`，索引是可重建的 `state/index.sqlite`；前者必须备份。删除当前笔记会移除搜索结果，但不会抹去已保存的历史证据。

## 测试与隔离

```bash
python scripts/check.py                 # Python 主路径 + Node 扩展测试
python -m pytest                       # 默认只收集 tests/core
pip install -e '.[legacy]'
python -m pytest tests/legacy           # 显式运行已归档测试，部分依赖 macOS 剪贴板
oneai tui-legacy                        # 显式启用旧 Textual 界面
oneai ask-legacy '问题'                 # 已归档的 Python agent
```

旧 UI、图片渲染、滚轮调试和 Python Runtime 位于 `oneai/legacy/`，不会被主路径导入；说明见 [legacy](oneai/legacy/README.md)。原 `ask` 和 `wheel-debug` 命令改为明确的 `*-legacy` 名称。

Outlook 已有独立的只读授权与可续传 delta 收信入口；安装 `.[outlook]` 后按 [部署说明](docs/OUTLOOK-DEPLOYMENT.md) 操作。尚未完成真实邮箱授权；邮件事件已接入独立任务 worker，当前生成本地待填模板。ledger 仍为实验骨架。

本地论文实验见 `scripts/paper_pilot.py`（需 pypdf）、`scripts/paper_docling_pilot.py` 和 `scripts/paper_embedding_pilot.py`；原文与输出留在本机，尚未接入主检索。

## 文档

- [当前实现架构](docs/ARCHITECTURE.md)
- [下一版产品与服务 spec](docs/SPEC-NEXT.md)
- [论文索引与长期记忆选型研究](docs/PAPER-MEMORY-RESEARCH.md)
- [改动前的设计评审](docs/REVIEW-2026-09-21.md)
