# 个人 Outlook：只读接入与低预算部署

状态（2026-09-21）：代码和本地回归测试已准备；微软 oneAIDemo 应用已注册成功，仅支持个人 Microsoft 账户；设备代码登录开关与邮箱授权待完成。用户自行购买的阿里云香港主机已部署，独立任务 worker 已 active/enabled；Outlook 收信 timer 尚未启用。

## 选择

当前采用个人账户的 Microsoft Graph delegated `Mail.Read` + device-code 登录 + 后台 delta 轮询。用户的需求是让 oneAI 持续读取邮箱，不是邮件自动转发规则。没有 `Mail.Send`，不自动发送邮件，不需要先开放公网回调地址、购买域名或租用 URL 转发服务。

每月预算上限人民币 30 元。用户自行开通香港 Ubuntu 24.04 主机（2 vCPU / 1 GiB / 30 GiB），本次没有代购或新增付费项目。此前核对的新加坡 28 元/月仅为未购买方案，不能当作香港主机的实付或续费价格。云端只运行收信与轻量任务，不安装论文模型。

[AWS 官方套餐说明](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)列出的低价方案包含 IPv6-only 限制，不能只比较标价：还要核验 Microsoft 登录和 Graph 的出站可达性、汇率与税费。因此暂不把它作为已验证的替代部署。

## 注册与授权

1. 微软应用注册选择“仅 Microsoft 个人账户”。当前表单名为 `oneAIDemo`；public client/device flow 不需要 Web redirect URI 或 client secret。
2. 记录 Application (client) ID；在 Authentication 配置允许 public client flows。注册和授权须在浏览器实际显示对应条款/权限时确认。
3. 仅请求 delegated `Mail.Read`。通过 MSAL 交互登录建立可续期的缓存。登录信息与刷新令牌只保存在运行 worker 的机器，不纳入 Git。
4. 在云端以 `oneai` 系统用户执行一次 `auth`，由用户完成微软登录与同意。不要复制浏览器 cookie，也不要在聊天中粘贴 token。

## Linux 部署准备

提供了 `deploy/install-outlook.sh`，在可信 checkout 根目录由 root 执行，只安装依赖和单元，不提前启动收信。其步骤为：安装 Python 3.11+、venv 和可信的项目 checkout 到 `/opt/oneai`，创建无登录系统用户 `oneai`，它拥有 `/var/lib/oneai`（0700）。在项目中创建 `.venv` 并安装 `.[outlook]`；不安装 `legacy`、Docling 或 embedding 模型。

将 `deploy/outlook.env.example` 的内容填写到 `/etc/oneai/outlook.env`，权限 root:oneai 0640。该文件只需要 client ID 和路径。systemd 单元的 EnvironmentFile 不会被交互命令自动加载，初次授权时须明确传入同样的变量，例如：

```sh
sudo -u oneai env ONEAI_GRAPH_CLIENT_ID='<client-id>' ONEAI_STATE_PATH=/var/lib/oneai /opt/oneai/.venv/bin/python -m connectors.outlook.connector auth
sudo -u oneai env ONEAI_GRAPH_CLIENT_ID='<client-id>' ONEAI_STATE_PATH=/var/lib/oneai /opt/oneai/.venv/bin/python -m connectors.outlook.connector sync
```

确认首次同步成功后，安装 `deploy/systemd/oneai-outlook.{service,timer}` 到 systemd 的单元目录，然后 reload 并 enable --now 对应 timer。timer 每次完成后约两分钟再次运行。无需打开公网 HTTP 端口。

## 已实现的可靠性

- 遍历 `nextLink`，保存 `deltaLink`；每次最多 100 页，下次从已落盘位置继续。
- 邮件版本事件、邮件缓存、游标在同一 SQLite 事务提交，进程失败不会出现游标已前进而邮件未持久化。
- 相同版本重复送达不重复入队；部分属性更新保留此前正文；删除事件保留删除标记。
- 数据绑定 client ID 与 MSAL account ID，防止不同邮箱共用一个游标。
- HTTP 超时 30 秒；拒绝向非 Graph 地址传递凭证或跟随重定向。
- 后台仅 silent refresh；需要重新授权时退出码 2，不启动交互登录。token 原子写入 0600，CLI 文件锁防止并发刷新。

## 尚未实现／必须验证

`work` 表已由独立 worker 幂等消费，生成有来源的待填模板，支持版本修订、人工核对及归档，见 [任务流程](TASK-WORKFLOW.md)。尚无自动研究、模型语义处理、手机推送或对外发送实现。不能把“模板已生成”当成“事项已办结”。

生产验收仍需：真实账号初次同步、断网续传、授权撤回、Mac 离线时定时器继续运行。Graph 429/5xx 当前由下一次 timer 重试，没有精确遵守 Retry-After 的退避调度；410/delta token 失效会失败保留状态，尚无自动全量重建与缺失邮件核对。没有公网手机界面，iCloud 也不是 Linux 可依赖的同步 API。以上限制应在无人值守上线前补齐，而非仅启动 timer 即宣称完成。

## 官方依据

- [Device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Message delta query](https://learn.microsoft.com/en-us/graph/delta-query-messages)
- [Outlook immutable IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)
- [阿里云轻量服务器](https://www.aliyun.com/product/swas)


## 2026-09-21 部署记录

- 主机：香港 `Ubuntu-blgl`，Ubuntu 24.04，Python 3.12.3。
- 使用阿里云命令助手上传程序包；包内无私人资料、令牌、模型或旧实现。
- SHA-256：`c719c02c6574899d8756e476e62e78194660ac675c3e386bd77f2c70629f32ab`。
- 安装执行 ID：`t-hk06xpztpz0xm2o`；17:10:29 完成，退出码 0。
- 验证输出：校验和 OK、`ONEAI_SMOKE_OK`、服务 `active`、开机启动 `enabled`、`ONEAI_DEPLOY_OK`。
- 实际任务测试使用临时目录，验证 pending → needs_review 后自动清理，不向正式任务库加入测试事项。
- `/etc/oneai/outlook.env` 已配置注册的应用 ID；没有邮箱令牌，收信 timer 保持未启用。
- Mac 同时安装用户 LaunchAgent `com.oneai.worker`，已观察多个正常处理周期。iCloud 指令目录已创建。
- 云端和 Mac 目前是独立节点，尚未双向同步；云端也尚未导入个人资料或论文索引。
