# Apple 客户端与真机安装

`OneAI.swift` 为 SwiftUI + WKWebView 客户端。Release 默认连接公网 HTTPS；网页更新不需要重装，原生代码修改仍须重新构建、签名、安装。

## 首次配置

1. 在 Xcode → Settings → Apple Accounts 登录自己的 Apple 账号。
2. iPhone 连接 Mac、解锁并信任此电脑；开启「设置 → 隐私与安全性 → 开发者模式」，按系统提示重启确认。
3. 使用下面脚本，传入自己的 Team ID 和 iPhone UDID。自动签名允许 Xcode 创建开发证书、注册设备、生成描述文件；证书私钥保留在 Mac 钥匙串，不上传 Linux 或 Git。
4. 如首次启动被信任检查拒绝，在 iPhone「设置 → 通用 → VPN 与设备管理 → 开发者 App」信任自己的开发者账号，按系统提示处理，再打开 oneAI。

```bash
xcrun devicectl list devices
bash apps/apple/device.sh APPLE_TEAM_ID IPHONE_UDID
```

脚本执行 Release 编译、签名校验、安装和启动；任一步失败返回非零退出码。Team ID 通过参数传入，不将个人团队绑定到共享工程。也可以在 Xcode 的 oneAI-iOS → Signing & Capabilities 中选择自己的 Team 后运行。

## 2026-09-23 真机证据

- 连接的 iPhone 17 Pro 已启用开发者模式。
- Apple Personal Team 自动签名构建成功，开发证书和包含目标设备的描述文件已生成。
- `codesign --verify --deep --strict` 通过；`devicectl device install app` 安装成功，Bundle ID 为 `org.oneai.personal.ios`。
- 描述文件有效期至 **2026-09-30 08:49:38 UTC（北京时间 16:49:38）**。到期前后需通过 Xcode 重新签名安装；不要把开发证书本身的有效期误当作 App 的可用期。
- 首次启动被 iOS 信任检查拒绝；用户在手机确认信任并打开后，`devicectl device process launch` 复测成功。已完成安装及启动验收，尚未完整验收真机所有界面与业务功能。

构建产物在被 Git 忽略的 `state/build/device/`；不要提交 `.p12`、描述文件、Apple 账号凭证。当前原生 APNs 推送尚未配置，此次开发签名不意味着原生锁屏推送已经实现。
