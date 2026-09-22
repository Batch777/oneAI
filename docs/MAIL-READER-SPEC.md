# 邮件阅读与按需回复

2026-09-22。本模块复用现有 Outlook Mail.Read 和登录设备权限。

## 用户流程

打开邮件任务立即显示同步文本；随后加载 Outlook 原文、发件人、收件人、抄送、回复地址、时间和附件列表。正文保留段落、标题、列表、引用及表格；网址、邮箱地址可点击。邮件原文默认展开，处理建议、检索依据和身份范围仍分级折叠。

点击「需要回复」后，沿用确认生成草稿流程；仅阅读邮件不生成模板，不发送邮件。原始 HTML 以不可执行文本折叠展示，另有「在 Outlook 中打开」入口。

附件列表先获取名称、大小、类型和 ID。PNG/JPEG/GIF/WebP 图片附件在进入可视区域时请求云端缩略图（最长边 480px、JPEG 质量 65、移除原始元数据）。云端首次生成需读取一次原附件，后续共享缓存；客户端此时只收到缩略图。点击缩略图后展开「查看大图」与「下载 / 打开原文件」。其他附件仍按需下载。iPhone 原生客户端通过系统分享面板打开或存储文件，Mac 使用存储面板，浏览器使用常规下载。

## 接口

- `GET /api/tasks/{task_id}/mail`：登录后读取该任务对应邮件的原文、语义节点、地址和附件元数据。
- `GET /api/tasks/{task_id}/attachment?attachment_id=...`：校验附件属于该邮件后下载。
- `GET /api/tasks/{task_id}/thumbnail?attachment_id=...`：登录、邮件删除状态及附件归属校验后返回压缩 JPEG。
- 添加 `preview=true`：仅对按文件头验证的位图返回 inline 图片。
- 草稿生成沿用已有确认接口；附件与原始 HTML 不发送给 Jev。

任务先映射到 Outlook 同步事件与 immutable message ID；不能由客户端传入 Graph URL。同步与阅读共享 token cache 锁，忙时立即提示重试。原文与附件列表按邮件事件缓存；删除标记阻止缓存读取。所有接口保留登录认证、no-store 和 nosniff。

## 安全与边界

HTML 转为白名单语义节点，并通过 DOM textContent 渲染。移除脚本、表单、样式、事件和活动嵌入，拒绝危险链接。外部图片默认不请求，防止邮件追踪。点击「加载外部图片」会展示 IP、打开记录与链接标识的隐私说明，再点击「确认加载」才请求 HTTPS 图片；取消不请求。授权仅限本次打开该邮件，重新打开恢复不加载。发送 no-referrer；不能保证远端不追踪。HTTP 图片不加载。内嵌 CID 图片目前以占位符显示，可在附件区预览，尚未恢复到原位置。

这是可阅读的语义排版，不复刻 Outlook 原始 CSS。宽表格在自身容器滚动。附件上限 25 MiB；云端引用附件及更大的文件转到 Outlook。Office/PDF 使用系统或浏览器打开，暂不内建 Office 预览。HTML 最大 100 万字符、1 万节点和 40 层；附件列表最多 5 页；网络读取设超时及响应大小上限。缓存目前按事件失效，无独立时间过期机制。缩略图最多 100MiB（按最旧文件淘汰），只允许 PNG/JPEG/GIF/WebP 解码，最大 2000 万像素；单进程串行转换避免小主机内存峰值，不持久化原始二进制。超限或失败时仍可展开原文件操作。

## 实际邮件日期

任务保存独立 `mail_received` 字段，从 Graph `receivedDateTime` 标准化为 UTC；界面按设备时区显示并保留年份。历史任务从原同步事件回填，新邮件在摄取时写入。列表按实际收信时间排序，非邮件仍使用任务更新时间。草稿编辑仅改变 `updated`，不改变收信日期。缺失日期显示「收信时间未知」，不伪装为今天。新增表达式索引支持排序。

## 验证

新增 Python 测试覆盖 HTML 安全解析、链接策略、元数据与下载分离、缓存、删除保护、附件归属、认证及下载/预览响应。新增客户端测试覆盖安全链接和语义 DOM 渲染。全量本地检查：155 个 Python、5 个扩展、10 个客户端测试通过。iPhone Simulator 和 macOS release 构建通过。香港主机 14 个邮件专项测试通过，新版已发布。真实邮件 17 个图片附件元数据、下载事件和缩略图加载验证通过；该邮件列表显示 2012 年 10 月 6 日，与原邮件一致。390/768/1280 宽度在第一版阅读器中无横向溢出。iPhone 模拟器已验证原文、邮件头和附件卡片；新增缩略图交互已在公网浏览器验证，iPhone 原生分享面板尚未完成端到端验收。Mac 已通过登录、重启保持登录、实际收信日期、缩略图展开、大图加载、系统保存面板及真实 PNG 文件落盘验证。验收中发现下载导航被误报断网，已修复：仅在主动转为 WKDownload 时忽略 WebKit policy interruption（102），其他网络错误仍提示。修复后第二次下载成功且邮件界面保持连接。两次下载文件均通过 PNG 解码校验，保存在未纳入 Git 的 state/validation/mac-mail-download/。

## 参考

- [Microsoft Graph message](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0)
- [Microsoft Graph attachment retrieval](https://learn.microsoft.com/en-us/graph/api/attachment-get?view=graph-rest-1.0)
- [Thunderbird remote content policy](https://support.mozilla.org/kb/remote-content-in-messages)

- [Pillow thumbnail 与图片解码限制](https://pillow.readthedocs.io/en/stable/reference/Image.html)

## 验证码置顶与正文首屏优化（2026-09-22）

验证码和账户验证入口位于详情最上方（返回按钮之后、邮件标题之前），由同步文本直接提取，不等待 Outlook 原文。

客户端先请求 `GET /api/tasks/{id}/mail?include_attachments=false`，只读取并缓存邮件头、原文及语义排版；正文显示后再请求 `GET /api/tasks/{id}/attachments`。附件失败只影响附件区，可独立重试。原有 `/mail` 默认完整响应保持兼容。旧的完整缓存也可直接复用。

token cache 锁仅在获取/刷新访问令牌期间持有，随后释放；正文及附件的网络传输不再占用该锁。响应超过 1000 字节启用 Gzip（客户端需支持），降低重复 HTML/语义节点的传输体积。原文和附件元数据仍使用事件版本缓存，不自动加载外部图片。

新增测试证明：正文请求不调用附件 API；附件失败后正文缓存仍可读；网络传输期间授权锁已释放；大响应可压缩并正确解码。全量本地检查 158 个 Python、15 个 JS 测试通过。

线上验收：17 个专项测试通过。取一封最近未缓存邮件单次测量：正文与格式 896ms、独立附件列表 675ms、正文缓存读取 1.36ms；不含客户端网络/渲染，不能作为所有邮件的延迟保证。公网 DOM 与 Mac 原生客户端均确认验证码在标题和邮件原文之前，Mac 原文还在加载时复制验证码按钮已可见；390px 视口无横向溢出。
