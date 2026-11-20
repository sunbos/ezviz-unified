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

登录需要服务器区域、AppKey、AccessToken，`指定设备` 为可选。选择区域后登录界面会自动填入对应的 `ApiUrl` 与 `WebUrl`。官方《Demo 使用指南》的区域表另列 `ApiUrl` 与 `AuthUrl`，仅用于各区域的原生配置。请按官方原生区域设置填写。在官方 Demo 中填写有效输入后点击“开始体验”，随后调用 `EZOpenSDK.getDeviceList`，成功表明此次凭据在该区域下的设备列表请求可用。单台设备的播放、音频及其它操作仍需按其自身权限验证。

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

官方 FAQ 要求工单附 Demo 从启动到复现的日志。先审查并脱敏，确认授权后经官方工单渠道提交。公开反馈仅使用摘要。脱敏时应移除令牌与验证码、设备与用户标识、媒体与接口地址、本地路径。

Android 开发者用 Android Studio Logcat，非开发者安装 APK 后可取 demo SDK 日志。iOS 用 Xcode 调试控制台或签名包内日志工具。Harmony 仅有文档列出的文件位置，收集流程未经验证。不要收集或索取屏幕上解密后的口令。

## iOS 5.34 的 Swift Package Manager 集成

集成环境需要 Xcode 15 或更高版本，包清单采用 Swift tools 5.9。清单声明的最低系统版本为 iOS 12.0，而 5.34 实际二进制框架的最低要求为 iOS 13.0，本批接入工程采用 iOS 13.0 部署目标。发行包只有 iphoneos arm64 真机切片，未提供模拟器切片。

本批固定 tag `5.34` 对应的提交 `60990dde343272f4b6d30b0893c094c0869c5f32`。SPM 产品 `EZOpenSDK` 的源目标 `EZOpenSDKWrapper` 依赖二进制模块 `EZOpenSDKFramework`。Objective-C 代码使用 `@import EZOpenSDKFramework;` 导入，Swift 代码使用 `import EZOpenSDKFramework` 导入。[官方 SPM 安装指南](https://open.ys7.com/help/5357) · [5.34 发行包](https://github.com/Ezviz-OpenBiz/SDK-iOS/releases/tag/5.34)

从 `dist/EZOpenSDK/dynamicSDK/` 手动加入 7 组资源，合计 20 个文件。它们包括播放器的 `com.hri.hpc.mobile.ios.player.metallib`、4 个 `CNN_SR_*.bin` 模型、`Ezviz_Audio_Enhance_Model_Up_16K.bin` 和完整的 `EZAICloud.bundle`。在应用 target 的 Copy Bundle Resources 中加入这些资源，并保持 bundle 的文件夹结构不变。

5.34 的 iOS Objective-C 公开头文件中可以确认以下五个选择器。

| 类 | 选择器 |
| --- | --- |
| EZOpenSDK | `+setAccessToken:` |
| EZOpenSDK | `+getDeviceList:pageSize:completion:` |
| EZPlayer | `-setPlayVerifyCode:` |
| EZPlayer | `-seekPlayback:` |
| EZPlayer | `-setPlaybackRate:mode:` |

setServerUrl 与 setSurfaceHold 未出现在这批 iOS 公开头文件中，不排除其他版本或平台提供它们。Android、iOS、Harmony 三端文档中的 AccessToken 与 TKToken 模式互斥，受限 token 依赖后端签发，三端对 setAccessToken 与 getDeviceList 的展示方式不同，Android 还额外展示了 setServerUrl。运行期行为以及平台相关的账号切换与调试能力尚未验证。

先在仓库根目录按下方命令取得官方源码与 ZIP。验证器读取这两个本地输入，在隔离目录中调用 Xcode 和 SwiftPM。示例中的 `python3` 需为 Python 3.12 或更高版本。如果系统默认版本较低，请在最后一行用项目虚拟环境的 `.venv/bin/python` 运行验证器。ZIP 的 SHA-256 为 `f7d9043c781910cab519b5cb89508917d849a094f1df11507eec02d495e2742d`。[版本与资源清单](coordination/native-ios-initialization/scope.json)

可选参数 `--package-cache "$SDK_PACKAGE_CACHE"` 指向已有的 Xcode `-packageCachePath` 根目录，其 `artifacts/` 目录内需存有同一版本与 URL 的已校验 ZIP，省略该参数时验证器会将已校验的 `--xcframework-archive` 预载到私有临时缓存中。该工具在关闭签名的前提下编译并链接通用 arm64 iphoneos 目标并检查 bundle 与资源，签名设备上的安装启动以及用户权限与媒体链路仍需单独验收。

```sh
(
  set -eu
  SDK_WORK="$(mktemp -d "${TMPDIR:-/tmp}/ezviz-ios-5.34.XXXXXX")"
  SDK_CHECKOUT="$SDK_WORK/SDK-iOS"
  SDK_ZIP="$SDK_WORK/EZOpenSDKFramework.xcframework.zip"
  git clone --depth 1 --branch 5.34 https://github.com/Ezviz-OpenBiz/SDK-iOS.git "$SDK_CHECKOUT"
  test "$(git -C "$SDK_CHECKOUT" rev-parse HEAD)" = 60990dde343272f4b6d30b0893c094c0869c5f32
  curl --fail --location --output "$SDK_ZIP" https://github.com/Ezviz-OpenBiz/SDK-iOS/releases/download/5.34/EZOpenSDKFramework.xcframework.zip
  python3 tools/native/verify_ios_spm.py --repo . --source-checkout "$SDK_CHECKOUT" --xcframework-archive "$SDK_ZIP"
)
```

## 仍待确认

4148 涉及的官方 Demo 仍需分别固定工程版本并验证签名产物。上节补充了 iOS SDK 5.34 发布物的公开声明。Android 与 Harmony 的对应版本、三端受限令牌和真实宿主行为仍待核实。上一批只检查共享文档，未连接真机、收集日志或提交工单。[共享资料审查记录](validation/2026-11-21-mobile-sdk-shared-references/review.json)另有 6 个 iOS、Harmony 菜单项指向 Android 菜单下的 3 篇共享页面，读者在各受支持平台均可参考这些页面。
