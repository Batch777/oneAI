# 已归档的实验实现

这里保留 Textual TUI、Python agent runtime、终端图片与滚轮调试。默认主路径不导入这些模块，UI 依赖放在 `.[legacy]` 可选安装组。

- `oneai tui-legacy`
- `oneai ask-legacy '<问题>'`
- `oneai wheel-debug-legacy`
- `python -m pytest tests/legacy`

原扩展示例移到 `examples/legacy/extensions/`；第三方旧 Python 扩展需将 `oneai.runtime` 导入改成 `oneai.legacy.runtime`。这是明确隔离，不保留容易误用的顶层兼容导出。

只维护可导入性和必要安全边界，不继续开发 UI 功能。旧 Runtime 的确认工具无审批回调时现默认拒绝。旧剪贴板测试需要可用的 macOS 系统剪贴板；默认检查不会运行它。
