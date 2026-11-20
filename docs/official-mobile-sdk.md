# 萤石移动端 EZOpenSDK 接入与排障

## 选对官方 Demo

官网 Android、iOS、Harmony 三端 SDK 均不按国内与海外区分版本，发布时按平台和使用区域分别选择对应的官方 Demo 分发。[Demo 使用指南](https://open.ys7.com/help/4148)

| 平台 | Demo 发布物 | 初始化入口 |
|---|---|---|
| Android | 同一个官方 APK，登录时选择区域 | [Android 初始化](https://open.ys7.com/help/4151) |
| iOS | 国内与海外两个 Demo 工程，需公司开发者证书构建 IPA | [iOS 初始化](https://open.ys7.com/help/4077) |
| Harmony | 仅国内版 Demo，需公司证书构建 HAP | [Harmony 初始化](https://open.ys7.com/help/4191) |

初始化页仅作接入方向参考，不代表版本锁定。

## 登录与首次验证

登录需要服务器区域、AppKey、AccessToken。区域对应的 ApiUrl 与 AuthUrl 只按 Demo 表格用于原生 Demo 配置，不要抄录数值，也不要套用为 Python 服务端路由。在官方 Demo 中填写有效输入后点击“开始体验”，随后调用 EZOpenSDK.getDeviceList，成功只能说明当时这组凭据、区域和设备列表动作可用，不代表单台设备的播放、音频或全部权限。结论随受影响版本与区域而变。

## 错误码

[EZOpenSDK 错误码](https://open.ys7.com/help/37) 覆盖 Android、iOS、Harmony、Mac 原生 SDK。保留含正负号的原始数字码，如 110013（应用没有权限调用此接口）、340402（回放找不到录像文件）。查不到的码先私下保留报错文字与数字元数据，再按对应 SDK 版本查官方说明，不要凭猜映射到 Python SDK 异常或 HTTP 状态码。页面所称 iOS 负值码是手机或平台侧网络错误的 native SDK 码，先查网络与配置的源地址，并非涵盖所有系统级负值码。

## 常见问题排查

按 [常见问题与反馈](https://open.ys7.com/help/4146) 逐项核对。

- 预览失败时用已授权官方 Demo 与自家 App 对比，在接入边界核对 setPlayVerifyCode 输入，取值不写日志。
- 回放倍速或拖动异常时结合错误码检查 setPlaybackRate 与 seekPlayback 的调用与返回。
- 语音对讲先确认设备型号和通道能力。配网问题先按错误码和 [4147 说明](https://open.ys7.com/help/4147) 定位。
- tkToken 是按功能与通道受限的小权限令牌，SDK 只透传，由服务端创建校验。不要申请更大范围 token，不要打印密钥。

仍未解决就反馈平台与版本、区域名称、模块、错误码、频率、复现步骤与观察到的现象。

## 日志与提单

对外只分享脱敏摘要，正式排查走官方私有渠道。工单需附官方 Demo 从启动到复现的完整日志，而非公开原始日志。提交前取得授权，删去密钥与 token、设备与用户标识、地址与媒体、敏感本地路径。

Android 开发者用 Android Studio Logcat，非开发者安装 APK 后可取 demo SDK 日志。iOS 用 Xcode 调试控制台或签名包内日志工具。Harmony 仅有文档列出的文件位置，收集流程未经验证。不要收集或索取屏幕上解密后的口令。

## 仍待确认

4148 仍缺确切发布物与版本、签名产物、各平台 getDeviceList 规范签名。4146 的预览回放调用细节与 tkToken 平台版本绑定规范也待验证。本批未做原生构建运行、未接真机、未收集上传日志、未提交工单，见本地 [来源核对记录](validation/2026-11-21-mobile-sdk-shared-references/review.json)。另有 6 个 iOS、Harmony 菜单项指向 Android 菜单下的 3 篇共享页面，读者在各受支持平台均可参考这些页面。
