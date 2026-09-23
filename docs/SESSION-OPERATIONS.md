# 会话服务运行与验收

## 当前公网入口（2026-09-23）

手机 oneAI → 会话，已注册两个在线的 Linux 托管会话：

| 会话 | 运行位置 | 实测 |
| --- | --- | --- |
| oneAI 自开发 · Linux Codex | `~/oneai-data/owner/workspaces/oneAI`，`codex/mobile-self-development` | 公网发消息、读取交接文档、在被忽略的 state 目录创建验收文件成功 |
| Proxy-GS · pi / Kimi | `~/Proxy-GS`，Kimi Coding 订阅 | 公网读取 README 与文本回传成功；关闭后恢复同一会话 |

Codex 运行时 ID `01a0cd85-1a65-7991-b206-26a10f3b78e3`，这是 Linux 专用自开发会话；不是当前 Mac 桌面任务的共享控制连接。Linux 已迁移的旧任务快照仍保留，不让两端同时续写同一 ID。

Linux 用户服务：`oneai-linux-codex`、`oneai-linux-pi`，均 active/enabled；linger 已开启。Host 配置在 `~/.config/oneai/linux-{codex,pi}.json`，0600，运行状态在 `~/oneai-data/owner/state/`。pi 包为 0.87.1；专用 wrapper 使用已有 Node 22.23.2，避免系统 Node 20 不兼容。

```bash
ssh_debian
systemctl --user status oneai-linux-codex oneai-linux-pi
```

不要另开 Codex/pi TUI 写入这些托管运行时；从手机、Mac 或 oneAI TUI 操作。Host 重启后按“停止并核对”恢复，未确认的写入不会自动重放。

香港运维 SSH 已经用户明确授权，仅 Mac 原公钥可用，禁止 PTY 和各种转发，私钥没有传到 Linux：

```bash
ssh -T -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=~/.ssh/oneai_hk_known_hosts root@47.82.117.21 'systemctl is-active oneai-web'
```

当前依旧单用户；Codex 额外权限审批尚无手机 UI、pi 仅开放只读项目工具。扩展与自更新 review 见 [MOBILE-DEVELOPMENT-REVIEW.md](MOBILE-DEVELOPMENT-REVIEW.md)。

## 历史验收结果（2026-09-21）

- 全部主路径 Python 测试 90 项通过；新增会话测试 13 项覆盖两端冲突、重复提交、过期、丢失派发、主机凭证隔离、重启待上传队列、pi 停止顺序、只读绑定及过滤 reasoning。
- 实际 Codex：登录账号类型 ChatGPT；发送固定无工具测试文本，收到 `ONEAI_SESSION_OK`，关闭进程后成功恢复同一 thread。
- 实际 pi：RPC 握手及持久化路径恢复通过。
- 两个独立 HTTP 客户端通过中转调用真实 Codex/pi，分别收到 `SECOND_CLIENT_OK`、`PI_RELAY_OK`；两端重复提交同一 ID，均只有一条 prompt 记录，无错误输出。
- Mac 原生客户端创建 Codex 会话、发送测试文本；iPhone 17 Pro Simulator 读到同一会话与 `MAC_TO_PHONE_OK`。
- Simulator 已绑定真实桌面任务 **Review 个人助手实现方案**，thread ID `01a0c2de-1980-7a73-abab-2102c5d7e91d`，显示已记录消息的后续更新；只读输入及控制按钮正确禁用。App 终止并重新启动后登录和选中绑定仍保留。
- 尚未验收真实 iPhone、Linux 交互式会话 TUI、额外权限审批、图片或自定义设备。当前桌面绑定不支持手机发送/停止。

这些测试使用本机 `127.0.0.1:8765` 的隔离验收服务。真实香港云端会话 API 尚未部署/启用，原收信服务没有改动。当前后台验收进程不是已安装的正式开机服务。

## 注册托管执行主机

在**中转服务器**运行注册命令。工作目录和二进制路径都填写目标执行主机上的实际值；以下路径仅为示例：

```sh
ONEAI_STATE_PATH=/var/lib/oneai python -m oneai.sessions.admin \
  --name '我的执行主机' \
  --server https://your-relay.example \
  --workspace '项目=/home/oneai/projects/example' \
  --codex /usr/local/bin/codex \
  --pi /usr/local/bin/pi \
  --output /private/location/host.json
```

配置文件含独立主机凭证，创建权限为 `0600`。通过已有受信通道转移到目标执行主机，不提交 Git、不放入云盘资料目录、不复制到聊天消息。注册并不会自动开启公网端口，也不代表该主机已经连接。

在**执行主机**运行：

```sh
python -m oneai.sessions.host \
  --config /private/location/host.json \
  --state /private/location/session-host
```

Host 仅接受 HTTPS 中转；本地测试可使用 localhost / 127.0.0.1。每台主机单独配置凭证和状态目录，不能让多台主机共用一张凭证。运行时使用执行主机已有登录；云服务器不会自动继承 Mac 的模型授权。

在确认访问范围、TLS 和部署条件后，为中转 Web 服务配置 `ONEAI_ENABLE_SESSIONS=1`。生产环境以 systemd / launchd 管理 Host，并限制其系统账户和目录权限；启动命令不依赖聊天窗口。

## 绑定已有桌面会话（只读）

在中转侧注册一个仅观察指定 thread 的主机配置：

```sh
python -m oneai.sessions.admin \
  --name '已有桌面会话' \
  --server https://your-relay.example \
  --workspace '项目=/absolute/original/workspace' \
  --codex /absolute/path/to/modern/codex \
  --observe-thread EXISTING_THREAD_ID \
  --output /private/location/desktop-binding.json
```

观察配置不公布可创建会话的 provider，且服务端记录不可由客户端切换的 `observe` 模式。启动 Host 后会检查已登录账号和原会话工作目录，仅读取指定 ID，不调用 resume。即使篡改手机请求或回传 idle 状态，也不能把绑定变成第二个执行者。

账号检查证明执行主机已有登录，不代表本机所有历史记录都按账号隔离；只注册用户指定的 thread，不能据此自动公开整台电脑的历史会话。

## 客户端

手机/Mac：打开“会话”，选择托管会话或只读绑定。托管会话可新建、发送、停止、关闭和恢复。断网时草稿留在当前设备；已提交但未确认的消息用原 ID 明确重试。

Linux/Mac TUI：

```sh
oneai sessions --server https://your-relay.example
```

首次输入配对码。`n` 新建，`Enter` 打开，`m` 发送，`s` 停止，`c` 关闭/恢复，`x` 重试未确认提交，`q` 退出。退出 TUI 不关闭主机上的运行时。

## 故障语义

- **执行主机离线**：中转可以显示历史，不能保证该主机继续工作。
- **需要核对状态**：指令已派发但确认丢失、运行时断开或主机重启。不会自动重发；先检查主机并停止/核对，再继续。
- **恢复失败**：保留错误与原会话身份，不用空会话代替可能已有工作的历史。
- **授权请求被拒绝**：首版没有远程审批界面；回到原执行环境核对，不能把审批默认设为允许。
- **接管现有桌面会话**：当前仅只读同步。共享控制端点或正式交接完成前，不支持手机对该活跃桌面会话发指令。

备份时保留中转 `sessions.sqlite`、Host 的身份数据库及待上传队列；主机私钥/令牌按凭证管理，不进入资料索引。版本回滚需要同时检查 SQLite schema；不能只覆盖代码而忽略状态兼容性。
