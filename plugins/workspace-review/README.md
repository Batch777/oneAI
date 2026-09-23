# 项目概况

owner-trusted Python 插件示例。读取所选 workspace 的 Git 元数据，结果使用 JSON-RPC 2.0；不修改项目。插件程序仅安装在 Linux，香港只保留 manifest、内容摘要、状态与调用记录。

安装：`python -m oneai.plugins.runner install plugins/workspace-review --root ~/.local/share/oneai/plugins --trust-owner-code`。安装后在 oneAI 设置 → 扩展中启用。详情见 docs/PLUGIN-SPEC.md 的已实现切片。
