# 本次变更审计：自迭代会话与模型控制

## 审计目的与决定范围

请核对：角色指令是否足以约束日常自开发；模型选择是否与执行端一致；用量是否诚实区分未知、估算和账号额度；审计入口是否提供真实 Git 文件及准确版本。

本次功能代码已按本任务授权部署到香港，版本 `a2f82ff523c6358d26016e50a11244a1cf5c4e45`。代码基线 `ff34002`，功能提交 `116bd43`，关闭状态修复 `9645a24`，模型下拉框修复 `a2f82ff`。后续验收文档单独提交。你的审查意见用于接受当前行为或提出具体修正，不被解释成未来任务自动合并/部署许可。

## 实际文件与作用

| 文件 | 作用 |
|---|---|
| `AGENTS.md` | 新会话基础工作规则、架构入口、测试命令、成本与审计约定 |
| `config/mix-model.json` | 声明 Astra/K3 角色、精确模型 ID、显式交接阶段及两轮修复建议上限；不自动调度 |
| `oneai/sessions/prompts/common.md` | 共用任务边界、Git 基线、数据和审计指令 |
| `oneai/sessions/prompts/self-develop.md` | 单会话自开发的有限任务与完成标准 |
| `oneai/sessions/prompts/supervisor.md` | Astra 负责合同/spec/独立审查及人类审计交接 |
| `oneai/sessions/prompts/implementer.md` | K3 按合同实施与测试，报告逐文件作用和证据 |
| `oneai/sessions/profiles.py` | 加载可版本控制提示词，给出正文/摘要与可用工作区 |
| `oneai/sessions/catalog.py` | 从已安装 Codex/pi RPC 发现模型和努力级别，过滤 pi 允许的提供商 |
| `oneai/sessions/telemetry.py` | 规范化 token、上下文与账号额度，不把缺失转 0、不把估算当账单 |
| `oneai/sessions/adapters.py` | 把角色/模型/努力级别传给正式 RPC，回传模型和用量，检查 pi 实际返回模型 |
| `oneai/sessions/host.py` | 发布目录、采集用量、保存审计基线、执行配置/审计命令，重启后保留关闭状态 |
| `oneai/sessions/store.py` | 增加目录、设置、最新指标附加表；模型/角色校验、空闲配置、关闭后移除及指令幂等 |
| `oneai/sessions/routes.py` | 增加经设备/Host 鉴权的模型目录读取与发布接口 |
| `oneai/sessions/audit.py` | 确定性 Git 比对：目的、基线/HEAD、文件列表、脏状态和摘要，不自动运行模型建议命令 |
| `oneai/web/static/session-controls.js` | 角色/模型/努力选择、用量展示、审计生成和复制、关闭会话移除；刷新保留原下拉框 |
| `oneai/web/static/sessions.js` | 接入目录与控制面板，清除已移除选择，保留用户下拉选择与原会话切换防竞态行为 |
| `oneai/web/static/index.html` | 新建角色/模型入口，详情中的模型、用量与审计折叠区 |
| `oneai/web/static/app.css` | 两列/单列响应式表单与可换行审计内容 |
| `oneai/web/static/sw.js` | 纳入新静态脚本，更新 shell cache 版本 |
| `oneai/web/app.py` | 静态资源白名单允许 session-controls.js |
| `pyproject.toml` | wheel 包含角色 Markdown，避免仅 editable 环境可用 |
| `tests/core/test_session_controls.py` | 配置并发/持久化、模型拒绝、指标去重隔离、关闭/移除、Git 审计与 RPC 参数测试 |
| `tests/client/session-controls.test.cjs` | 用量语义、审计提示词内容、模型下拉框刷新保持测试 |
| `docs/templates/MIX-TASK.md` | 监工交给实现者的可填写合同 |
| `docs/templates/CHANGE-REVIEW.md` | 目的、逐文件作用、测试、风险/回滚、具体决定范围的审计模板 |
| `docs/SELF-DEVELOPMENT.md` | 当前能力、接口、架构图、限制、真实验收与正式会话 ID |
| `docs/SELF-EVOLUTION-ARCHITECTURE.md` | 给旧提案增加当前进展入口，避免把历史状态当现状 |
| `docs/SESSION-OPERATIONS.md` | 运维文档增加模型与角色入口 |
| `docs/LINUX-HANDOFF.md` | 将后续工作引导到正式 Astra/K3 会话与当前规范 |
| `docs/REVIEW-SELF-DEVELOPMENT.md` | 本审计说明与逐文件作用清单 |

## 验证证据

- `.venv/bin/python -m pytest -q`：198 通过；仅既有 Starlette/anyio 弃用警告。
- `node --test tests/client/*.test.cjs`：31 通过。
- Linux：`python -m pytest tests/core/test_sessions.py tests/core/test_session_controls.py tests/core/test_web.py -q`：48 通过。
- 公网：创建正式 Astra/K3 会话；二者读取新规范、返回 READY 且 Git 工作区无改动。K3 的真实 K3-256K 调用成功。
- 公网：模型配置 Astra → Sol → Astra 回执确认，测试未向 Sol 发推理任务；最终配置 Astra/high。
- 公网：用量快照与账号剩余配额展示；真实审计返回精确基线/HEAD 与干净工作区。非空文件差异和摘要变化由 Git 集成测试覆盖。
- 桌面 1690px、手机 390px：无横向溢出，模型下拉框与审计提示词在容器内。Simulator 可加载新版，但后续点击遇到 macOS `noWindowsAvailable`，完整模拟器交互未计入通过；本次未重新安装真机二进制。

## 边界与回滚

Mix 目前为显式文件/会话交接，尚无自动 Coordinator、强制预算熔断、工作区写租约或强制发布审计闸门。角色提示词不构成 OS 沙箱，当前仍是用户的单人 steven 工作空间。Kimi 订阅剩余额度未接入；运行时 cost 不是账单。目录可见不能取代服务商套餐授权。

数据库只加表，不删旧字段；旧测试会话软移除，原始记录和部署前 sessions SQLite 私有备份保留。回滚时先停用新版 Host 的目录/指标能力，停止新角色会话，再切回先前 Web/Host 制品；不要覆盖整个运行数据库来回滚，以免丢失新事件。已有香港文件级自动回滚健康检查继续工作；本次备份记录位于服务器受限 `/root/oneai-settings-backup-*`，不是通用无人值守自更新发布器。
