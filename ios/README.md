# 手机入口

当前可通过 iCloud Drive 编辑同步的 vault 文件；尚未实现原生 iOS App、手机任务消费或审批服务。

下一版采用手机网页 / 快捷指令直连常在线服务，让 Mac 离线时仍可发任务、查看进度和编辑草稿。iCloud 作为资料入口与同步副本，不承担数据库锁或执行队列。

详见 `docs/SPEC-NEXT.md`。`oneai watch` 只同步 Markdown 索引，不会自动处理 inbox 任务或发送 approved 草稿。
