# 个人 Outlook：只读接入与低预算部署

状态（2026-09-21）：代码和本地回归测试已准备；微软 oneAIDemo 应用已注册成功，仅支持个人 Microsoft 账户；设备代码登录开关与邮箱授权待完成。云主机购买和线上运行尚未完成。

## 选择

当前采用个人账户的 Microsoft Graph delegated `Mail.Read` + device-code 登录 + 后台 delta 轮询。用户的需求是让 oneAI 持续读取邮箱，不是邮件自动转发规则。没有 `Mail.Send`，不自动发送邮件，不需要先开放公网回调地址、购买域名或租用 URL 转发服务。

每月预算上限人民币 30 元。已在登录后的阿里云购买页核对：新加坡国际型 2 vCPU / 1 GiB / 30 GiB / 1 IPv4，Ubuntu 24.04，1 个月应付 28 元，自动续费已关闭。购买协议确认仍待用户答复，未提交订单。页面长期时长也显示折合 28 元/月，未来续费仍以届时账单为准；不能用未核实的首年活动价当成长期成本，也不能把年付摊销当成已授权一次支付全年。仅运行收信、SQLite 和任务队列，不在小主机安装论文模型。若没有合适套餐，保持未购买状态。

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

`work` 表目前只持久化 pending 事件，尚无自动研究、草稿生成、手机通知或审批执行器。不能把“已收信”当成“已完成处理”。后续应消费事件生成有来源的建议，完成后单独更新任务状态。

生产验收仍需：真实账号初次同步、断网续传、授权撤回、Mac 离线时定时器继续运行。Graph 429/5xx 当前由下一次 timer 重试，没有精确遵守 Retry-After 的退避调度；410/delta token 失效会失败保留状态，尚无自动全量重建与缺失邮件核对。没有公网手机界面，iCloud 也不是 Linux 可依赖的同步 API。以上限制应在无人值守上线前补齐，而非仅启动 timer 即宣称完成。

## 官方依据

- [Device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Message delta query](https://learn.microsoft.com/en-us/graph/delta-query-messages)
- [Outlook immutable IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)
- [阿里云轻量服务器](https://www.aliyun.com/product/swas)
