# 论文数据试用（2026-09-21）

系统设置显示 iCloud 总容量 200 GB、已用 136.6 GB、剩余 63.4 GB。导入前的容量足够容纳本次数据；写入 iCloud 目录不代表 Apple 已完成全部上传。

Desktop 31 个 PDF 按 SHA-256 去重为 30 篇、795 页。原件 555,115,463 字节，提取 Markdown 2,258,400 字节。未改动 Desktop 原件。

- 原件：iCloud Drive/oneAI/papers/<SHA-256>.pdf。
- 提取文本：iCloud Drive/oneAI/vault/papers/imported/10579fce42fa62b0/。
- 导入记录：~/.oneai/state/paper-imports/10579fce42fa62b0.json。
- 香港主机已接收全部 30 个 Markdown 文件，逐文件哈希验证一致。只上传提取文本，PDF 原件通过 iCloud 保留。

## 试用

在客户端的资料检索中输入 `two-step prefetching`、`DrivingRecon` 或 `Street Gaussians`，点击“查看来源与页码”。页码是 PDF 的物理页序号。关键词可能匹配多个文档；本地查询中 FlashGS 的第 2 页和第 10 页均命中预取相关内容。

当前上线范围是关键词检索和提取文本引用，不包含完整论文语义向量检索、PDF 图片展示或自动 OCR。公式、图表和多栏阅读顺序需核对原件。语义试验结果见 PAPER-MEMORY-RESEARCH.md 和 PAPER-PILOT-2026-09-21.md。

## 重新导入

使用 `python -m oneai.papers <PDF目录> --originals <原件归档目录> --cache state/paper-pilot/pages.sqlite`。已校验的同内容缓存可复用；新 PDF 解析需要安装 `.[papers]`。文档解析失败会单独记录并保留上次成功版本。

原件按内容版本保留，提取 Markdown 可重建。同步协议目前不传播删除：本地撤下旧 Markdown 不能保证云端撤下，后续增量替换需要显式的云端版本退役协议；不要将本次首次批量导入视作完整的跨端删除功能。

## 验证

本地核心测试 92 项通过；香港 Ubuntu 24.04 上会话、Web、论文导入、索引、同步相关测试 44 项通过。云端测试有一条依赖库弃用提示，未影响结果。

## 公网状态

用户已确认公开登录入口及 Let's Encrypt 证书协议。香港主机 HTTPS 入口为 https://47.82.117.21/ 。IP 证书已签发，当前有效期至 2026-09-28；自动续期定时器已启动，每日检查两次。Web、后台任务、Outlook 收信及续期定时器均为 active。外部 Mac 验证 HTTPS 首页返回 200；未登录访问任务 API 返回 401。

服务器上已验证关键词查询及页码，结果与本地一致。云端搜索不依赖 Mac 在线；新增本地论文的提取和 iCloud 到中转的同步仍需要 Mac 运行。会话 API 已随版本部署，但 Mac 会话主机此前连接的是本地验证服务，不能据此宣称公网会话迁移也已完成。

客户端验收：Mac Chrome 完成公网配对、论文搜索及 FlashGS 第 2 页引用读取。iPhone 17 Pro / iOS 26.2 Simulator 安装 release 构建，完成公网配对并检索到 FlashGS 第 10 页；未使用本地 API。Mac release 构建已生成，原生 Mac 客户端的公网配对尚未在本轮验证。真机可通过公网 Safari 配对试用，一次性码不写入 Git。
