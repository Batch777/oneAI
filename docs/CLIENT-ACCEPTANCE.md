# 客户端实现与验收 · 2026-09-21

## 已实际验证

- iPhone 17 Pro Simulator，iOS 26.2：一次性配对登录、创建任务、后台生成待填模板、修改草稿、生成第 2 版、核对第 2 版、重载及应用重启后保持登录；服务停止时显示重连页面，服务恢复后无需重新配对。
- Mac 原生 SwiftUI + WebKit 客户端：登录并读到 iPhone 保存的第 2 版内容与已核对状态，重启后保持会话；宽屏布局已验证。
- 上述 UI 验证连接隔离的本机验收服务，不代表公网部署或真机验收完成。
- 77 项 Python 与 5 项扩展测试通过；iOS 和 Mac Xcode Release 工程均构建成功。自动测试覆盖配对码单次使用、CSRF/Origin/Host 校验、设备撤销、重复请求幂等、旧版本拒绝、历史引用、请求体上限、终端断网请求恢复。
- 验证数据在 `state/app-validation/`，不会混入真实邮箱任务。真实 Outlook 已在香港主机完成只读授权；收信、任务准备和 Markdown 同步与客户端分开运行。

## 实现取舍

iPhone/Mac 是原生应用容器，共享一个轻量网页工作台与云端 API。界面无需 pi、Node 构建器或独立模型进程。服务端使用 SQLite 持久化，客户端通过一次性配对码建立可撤销会话。当前所有已配对设备权限相同；不存在多人角色隔离。

当前后台生成检索材料和规则模板，并未接入自动模型起草；核对、归档不会发出邮件。论文 PDF 解析和向量检索仍处于实验阶段，主检索只有 Markdown。

离线时已打开的编辑页可以保存本机草稿；提交失败保留请求 ID，可显式重试。应用冷启动离线会显示重连界面，不提供完整离线任务库。服务端版本发生变化时，旧版本提交拒绝；用户必须比较后明确选择基于新版本保存。未提交草稿保留在当前设备 WebKit 存储中。

## 构建

```sh
# 无需开发者账号，在当前 Apple Silicon Mac 构建本机验收包
apps/apple/build.sh simulator debug
apps/apple/build.sh mac debug
# Debug 脚本构建默认连接 http://127.0.0.1:8765，仅用于隔离验收
# Release 默认连接香港主机 HTTPS，不包含本机 HTTP 例外
apps/apple/build.sh simulator release
apps/apple/build.sh mac release
```

构建输出：`state/build/simulator/oneAI.app`、`state/build/mac/oneAI.app`。不要把 Debug 包作为云端正式版本分发。

真机：打开 `apps/apple/oneAI.xcodeproj`，选择 `oneAI-iOS`，在 Signing & Capabilities 选择自己的开发团队，选择连接的 iPhone 后运行。Xcode 工程不设置本机测试地址，默认连接 HTTPS 云端。App Store/TestFlight 分发仍需用户自己的 Apple 开发者签名与发布流程。

Linux/Mac 终端：安装 Python 3.11+，运行 `oneai` 或 `oneai tui --server https://47.82.117.21`；首次输入配对码。`n` 创建、`Enter` 查看、`e` 用 `$EDITOR` 编辑、`a` 核对、`d` 归档、`r` 刷新、`x` 重试未确认请求、`q` 退出。`oneai pi` 保留旧扩展入口。终端支持任务工作流，资料全文检索当前通过网页或原有 CLI 使用。

## 真机待验收

1. 通过蜂窝网络登录、创建任务，Mac 关闭后仍可查看后台结果。
2. 中文键盘输入、长草稿滚动、横竖屏切换、不同字号。
3. 编辑过程中断网，恢复后提交；两台设备修改同一版本时明确提示冲突。
4. 强制退出与重新打开后会话和未提交草稿恢复。
5. 在另一台设备撤销登录后，旧设备不能继续读取任务。

当前没有 iOS 推送、Share Extension、完整离线数据库、系统级 Keychain 配对凭据管理；这些不是本轮验收通过项目。

## 云端准备状态

部署包已经上传到已有香港主机，尚未启动公网工作空间。Ubuntu 24.04 上 Python/curses 导入和 105 条任务的三页分页验证通过。校验还发现并修复了新服务的数据目录配置：网页服务改为读取与收信/worker 相同的 `/etc/oneai/outlook.env`，不再另设一份数据路径。

最终云端包为 `oneai-app-ui-v2.tar.gz` 和 `oneai-app-service-v3.tar.gz`，由 `deploy/stage-client-release.sh` 校验。`deploy/activate-client-release.sh` 会先备份代码，再启动新服务；需要用户确认公网入口和证书条款之后才能执行。现有云端收信与 Mac 同步继续运行。

最终云端校验：2026-09-21 21:06，两个程序包哈希匹配，Linux 分页测试通过，邮件与任务各 6,758 条。初次导入目前会为每封邮件建立待核对模板；历史邮件分流与降低任务噪声仍需改进，不能把这批模板当作已经自动完成的事项。
