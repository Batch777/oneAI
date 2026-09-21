# 可独立运行的任务闭环

当前功能：Outlook 已持久化的邮件事件、手机文件指令 → 待办 → 本地资料检索 → 待填回复模板 → 人工修订、核对和归档。`python -m oneai.worker` 独立运行，不需要 pi 或桌面聊天窗口。

这版不会自动理解邮件全部语义：采用保守的本地规则，按标题词查资料，明确保留待核对问题。它不调用外部模型，不自动推断日期，不发送邮件。模板不是模型写好的最终回复。已有单次 `oneai draft` 仍可以按用户明确指令调用配置的模型。

## 桌面

```sh
oneai task create 'Gaussian 论文回复' --body '整理相关研究并准备回复'
python -m oneai.worker --once
oneai task list
oneai task show <task_id>
oneai task revise <task_id> --revision 1 --body '我修改后的草稿'
oneai task approve <task_id> --revision 2
oneai task complete <task_id> --revision 2
```

`approve` 仅记录“当前版本已核对”，没有任何发送权限。修订会增加版本并清空旧核对状态；旧版本操作会被拒绝。`complete` 表示用户归档，不代表邮件已经发出。原始任务、草稿和操作记录保存在 `tasks.sqlite`，应与 vault 一同备份。

## 手机文件入口

iCloud Drive 中 `oneAI/vault/inbox/commands/` 接收 UTF-8 JSON 文件。每个操作使用唯一 `id` 和唯一文件名；相同 id 重放不会重复操作，但内容改变而复用 id 会被拒绝。

```json
{"id":"phone-20260921-001","action":"create","title":"Gaussian 论文回复","body":"整理相关研究并准备回复"}
```

用快捷指令的“询问输入”获取标题/正文，用“生成 UUID”生成 id，用“词典”构建上述字段，再把词典的 JSON 文本保存到该目录，避免手动拼接引号。现阶段未自动安装 iOS 快捷指令。

后台把进度写入 `inbox/tasks/<task_id>.md`，该文件是重新生成的只读视图；草稿修改通过新的 revise 文件提交：

```json
{"id":"phone-20260921-002","action":"revise","task_id":"从任务文件读取","revision":1,"body":"我修改后的完整草稿"}
```

核对使用 `action: approve`；归档使用 `action: complete`，都携带 task_id 和当前 revision。每个指令旁产生 `.receipt`，明确 accepted/rejected 和原因。同步半写文件可能暂时 rejected，文件完成后下一轮会重新校验。不要把邮箱附件自动存入这个受信任的指令目录。

## 云端与 Mac

云端用 `oneai-worker.service`；Outlook 同步由独立 timer 执行。即使重新授权暂时未完成，worker 也能继续处理已有任务。Linux 不会自动读写 iCloud，因此云端和 Mac 的 vault/state 是两个独立节点；尚未实现云端任务到 iCloud 的双向同步，不能宣称 Mac 离线时手机已经能直接看到云端任务。

当前手机文件通道需要 Mac 上的 worker 运行及 iCloud 完成同步。后续云端手机入口需使用经过身份验证的 HTTPS API，再实现同步与冲突检查。不要直接暴露 SQLite、vault 或无认证端口。

## 可靠性与边界

- 收信事件先幂等写入任务库，再标记已接收；两个 SQLite 之间崩溃后重放不会重复创建。
- 初始批次中已过时的邮件版本与已移出收件箱的事件不创建新任务；已有任务不会随删除邮件而丢失。
- 待办、资料和用户规则分离，生成的任务文件不再次进入知识索引。
- 当前任务模板记录本次使用的规则版本；它不解释任意自然语言规则，也不声称所有规则都被自动执行。复杂规则执行需要模型适配与评测。
- 本地进程文件锁只允许一个 worker；失败由服务管理器重启，pending 任务可再次准备。生成结果写入数据库后，文件视图可重建。

## Mac 后台安装

在已建立 `.venv` 的项目根目录执行 `python scripts/install_macos_worker.py`，安装当前登录用户的 LaunchAgent；每 30 秒扫描一次，登录后启动，不依赖聊天窗口。休眠、关机或退出用户登录期间不能处理 iCloud 指令。日志在项目 `state/worker-logs/`。停止服务：

```sh
launchctl bootout gui/$(id -u)/com.oneai.worker
```

停止后保留 plist，重新登录仍可能启动；如需永久停用，再移走 `~/Library/LaunchAgents/com.oneai.worker.plist`。云端停止任务服务使用 `sudo systemctl disable --now oneai-worker.service`，不影响已有任务数据。
