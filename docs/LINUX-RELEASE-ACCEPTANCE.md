# Linux 更新与模拟器验收 · 2026-09-23

## 本轮结果

- `/home/steven/oneAI` 已具备完整 Git 历史，origin 为 `https://github.com/Batch777/oneAI.git`。GitHub CLI/keyring 令牌失效，公网仓库尚未同步本轮提交；两端通过 Git bundle 保留原始历史。
- Linux Codex 已独立登录；本次会话快照经用户明确授权复制，权限 0600 / steven。`thread/read` 成功识别 `01a0c2de-1980-7a73-abab-2102c5d7e91d`。这是时点快照；当前桌面任务不会自动迁移，未同时运行两个写入者。
- Linux 用户服务 `oneai-hk-tunnel` active、Linger=yes。Mac alias `ssh_debian` 已替换，旧 alias 注释，强制香港中转实测成功。
- Linux `oneai-staging` 只监听 127.0.0.1:18765，通过 Mac SSH 本地端口转发供 Simulator 访问；数据在 `~/oneai-staging/data`。无真实收信 worker。
- 模拟器已在测试服务完成配对、看到“Linux 热更新验收”任务。版本从 e4b11d5 更新至 ed703bc、9eae9ab；不重装 App。
- iPhone 17 Pro Simulator 显示设置页版本 9eae9ab。服务回退至 ed703bc 后，App 从后台返回显示“有新版本 · 重新加载”；取消保留页面，确认后重新加载、保持登录。服务随后恢复 9eae9ab。
- 故障注入：Linux 真实重启候选后强制健康门禁失败，发布器自动切回 9eae9ab 并恢复版本接口；未回滚任务数据库。
- 30 项发布管理器/Web Python 测试通过（Mac 和 Debian）；另增加 commit 选择器拒绝测试；26 项客户端测试通过（Mac，Linux 先跑 23 项既有测试，后跑 3 项更新提示测试）。
- 已连接真机 iPhone 17 Pro，开发者模式 enabled；Xcode 构建失败原因是尚无 Development Team。用户选择稍后配置签名，本轮未安装真机。

## 接口与操作

`GET /api/version` → `{ "commit": "完整40位Git SHA或development", "api": 1 }`，不返回路径、凭证或个人数据，no-store。页面 HTML 带初始 release meta；前台恢复、上线和手动刷新时比较版本。没有定时升级轮询，不自动重载草稿。

```bash
ssh_debian
cd ~/oneAI
~/.local/bin/codex -C "$HOME/oneAI" resume 01a0c2de-1980-7a73-abab-2102c5d7e91d
```

应在 Mac 上当前任务结束后再在 Linux 继续，且不要两边同时续写同一会话。完整快照仍含旧 Mac 路径；续写时以当前 Linux cwd 和 LINUX-HANDOFF.md 为准。账号授权使用 Linux 已有登录状态，未迁移 Mac auth.json。

```bash
# 操作者选择已经审阅、测试通过的完整 commit；不是任意远端最新分支。
python3 deploy/releases/manager.py stage --root "$HOME/oneai-staging" --repo "$HOME/oneAI" --commit FULL_SHA
python3 deploy/releases/manager.py activate --root "$HOME/oneai-staging" --commit FULL_SHA
# 中断部署显式恢复上版；不回滚用户数据库。
python3 deploy/releases/manager.py recover --root "$HOME/oneai-staging"
```

## 边界与后续

本轮是可运行的隔离更新/回滚集成，不是已开放的生产自迭代发布系统。共享既有 venv 仅适用于本轮无依赖变更的验收。生产化仍需：每版本锁定依赖环境、GitHub 可信发布证明、静态资源版本固定避免跨版本混载、租户隔离验收、worker 排空及兼容迁移、发布权限独立于生成代码的 Codex。不能把候选 hash 校验称作可信来源验证。

SSH 隧道只解决连接；Codex/pi 的跨端审批、唯一写入租约及 owner-bound Host 注册仍按 SELF-EVOLUTION-ARCHITECTURE.md 逐步实现。当前原始会话可以在 Linux Codex 恢复，并不代表已在手机端完成这个会话的可写绑定。

短信：已授权阿里云余额每月最多 10 元；控制台显示短信认证套餐余量 100 次。用户单独确认协议后，功能开启页面已确认“短信认证：已开启”。尚未发送测试短信，预算硬限制目前只有 spec、尚未接到生产身份服务，不能声称手机号登录已上线。

公网下拉动画单独发布：执行 `t-hk06xx14f7oaoe8` 成功，app.js/app.css/sw.js 的公网响应与 e7b832d 逐字节一致；生产未启用实验更新器。
