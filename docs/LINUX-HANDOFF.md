# Linux 工作交接 · 2026-09-23

仓库 `/home/steven/oneAI`，origin `https://github.com/Batch777/oneAI.git`，分支 `codex/personal-assistant-foundation`。旧依赖环境 `/home/steven/oneai-runtime/.venv`；论文目录 `/home/steven/oneai-data/owner/papers`。这些路径仅属于 owner，不能作为多租户隔离实现。

当前需求：手机号登录（阿里云 PNVS 每月最多 10 元），Outlook 独立 OAuth；每个用户默认隔离、未来多邮箱。详见 MULTIUSER-LINUX-SPEC.md；尚未开放多用户。论文为几百至几千篇本地 PDF / Zotero。邮件由 Jev 分类，敏感验证码和链接由确定性规则提取。邮件/论文内容不能授权执行代码或扩大权限。

SSH：Linux systemd 用户隧道经香港回环 22390；已启用 linger。Mac 的 ssh_debian 自动 LAN/香港中转。香港账号只能受限转发。不要复制认证密钥或私钥进仓库。

客户端是 SwiftUI + WKWebView，源文件 apps/apple/OneAI.swift，Mac 与 iOS 共用网页。网页/服务端可经发布更新；Swift 原生代码仍需 Xcode 签名和安装。下拉刷新及前台/手动刷新保留，不恢复 15 秒列表轮询；会话流单独处理。编辑中的草稿不能被后台刷新覆盖。

更新工具 deploy/releases/manager.py 只接受操作者明确选择的准确 Git SHA，先 stage，再 activate；记录完整性清单、独占切换锁、部署日志、失败回滚。只在 Linux 隔离 staging 验收；不是生产自动发布器。未完成可信 GitHub 制品签名、锁定依赖、数据库迁移备份及 worker 排空前，不启用无人值守生产自更新。测试服务回环 18765，无真实 Outlook/Jev 凭证及收信 worker。

持续工作须更新 Git、记录测试证据。先 simulator 验证更新及回滚，再尝试已连接 iPhone；原生安装必须具备开发签名。当前 Mac 无有效 codesigning identity，可能需用户登录 Xcode Apple Account。

完整原始 Codex 会话快照的传输被自动审批阻止，原因是历史中可能有凭证。此文为不含凭证的工作上下文，不能声称它与原始会话完全等价。当前 Mac 对话仍在运行，不从 Linux 同时写同一会话。
