# 海外能力审查候选（SP014）

本批把 16 篇已核定的海外官方来源逐场景审查转换成可审查能力候选。**这 16 篇只是本批审查输入，不是海外总量**；不计算覆盖率或 API 总数。机器可读清单见同目录 [`overseas-review-candidates.json`](overseas-review-candidates.json)。

## 边界

- 输入：`portal-2026-11-17` 快照中 16 篇海外文档的已验证正文 SHA 与 element/declaration 指针；不读取真实数据或凭据。
- 唯一写文件：`docs/capabilities/overseas-review-candidates.json` 与本文；未改 `src/`、`contracts/`、`evals/`、`tests/` 或共享矩阵。
- 所有新能力均为 `candidate:`，SDK 入口名仅为 proposal，不是已批准签名；未执行任何 wire/参数/响应/重试/诊断自测或 live 调用。

## 主要复用与缺口

**可登记为已有契约的重复/变体来源**（不增加 API 数量）：

| 候选 | 已有契约 / 绑定 | 说明 |
| --- | --- | --- |
| `candidate:overseas:469:1.2` | `global.token.get` → `TokenManager.get_token` | 第二份官方来源；相同 origin、form、expireTime ms、areaDomain 语义 |
| `candidate:overseas:469:1.10` | `global.devices.bind` → `devices.bind` | 第二份官方来源；相同 form accessToken/deviceSerial/validateCode |
| `candidate:overseas:534:1.2` | `global.playback.invalidate` → `playback.invalidate` | `source_alignment`：已登记主来源上的 `e152` 单侧反引号 parser 例外，非第二来源、非来源缺方法；保留 `global.playback.invalidation_scope` source issue |
| `candidate:overseas:2868:d0` | `global.security.update_password` → `security.update_password` | `source_alignment`：已登记主来源上的 `e7` 单侧反引号 + 示例 Host 异常，非第二来源；保留示例 origin 的 source issue |
| `candidate:overseas:476:1.4` | — | 新增 `snapshots` UUID 查询提案；与 `snapshots.capture` 不合并（后者是触发抓拍） |
| `candidate:overseas:477:1.20` | `global.devices.get`（同路由变体） | `version=utc` 为必需鉴别器；标 `needs_review`，不标 duplicate |

处置分布：`implement` 40、`duplicate` 2、`source_alignment` 2、`needs_review` 5；`nature` 分布：`server_api` 46、`authorization_flow` 2、`message_protocol` 1、`other` 0。以上为审查记录统计，不是 API 总量。

**主要新缺口**（需先冻结接口再实现）：

- 469：OAuth 浏览器登录入口（`oauth` 模块提案；入口 query 未列 accessToken、需交互登录、页面 GET 仅浏览器行为推断）、OAuth refreshToken（与开发者 TokenManager 分开）、完整 `ddns` facade（get/setDomain/setAutomatic/setManual/list/share/shareList）、公共 `area/list` 目录（实际 origin 为 `open.ezvizlife.com`，SDK 仅选 GET 提案，POST 参数/token 形态未知）。
- 481：独立 `detectors` facade（list、set_status、delete、list_bindable_ipc、list_bound_ipc、set_ipc_relation、rename、cancel_alarm、add）；不并入普通 devices/IPC。delete 需 host `deviceSerial`+`detectorSerial`；bindable-IPC 列表只带 `deviceSerial`；relation/set 必填集合严格为 `accessToken,deviceSerial,detectorSerial,ipcSerial,operation`（e687，三个独立身份字段非二选一）；rename 必填集合严格为 `accessToken,deviceSerial,detectorSerial,newName`（e799，wire 字段 `newName` 非 `name`）；cancelAlarm 只需 `deviceSerial`、无 `detectorSerial`；`deviceSerial` 均为 A1 host/gateway 设备身份、非 HTTP Host header。
- 1000007：独立 `locks` facade；14 个声明拆分前六项 keylock 路由与后八项共享 OTAP action/prop 的 DoorLock 场景。共享路由不合并能力；selector 经 wire header `domainIdentifier`/`actionIdentifier`（均已核定 header/String/Y）按声明写明（d6=TemporaryPassword/AddTemporaryPassword、d7=TemporaryPassword/DeleteTemporaryPassword、d8=TemporaryPassword/QueryTemporaryPassword、d11–d13=DoorLockMgr/*），各自独立不互引；承载它们的 SDK 签名仍为未批准 proposal 待冻结；`cn.otap.*` 为 domestic-only，不构成海外已有支持。消息段单列 `message_protocol` 集成 case，helper ≠ 接入完成。
- 3722–3730：九个 v3 设置场景；3728 declared origin 为 `open.ys7.com`（e5）的海外路由异常保留且路由方案待跨来源核定；3729 set 必须 header `channelNo`。
- 476 §1.4 UUID 查图（UUID 须来自 Linktom 设备的 device-SDK capture 流程，不泛化任意抓拍 UUID）、477 §1.20 `version=utc` 设备信息变体（响应含 `timeZone`/`wifiSsid`）。

## 主要疑义（保留，不静默修正）

- 469 refresh：`openId` vs `open_id`、bundleId 表标 Y 但示例缺失（冲突保留）、access_token/refresh_token 表标 int 而示例为 string（保留冲突，string 解析仅为 SDK 提案）、`expires_in` 单位未定、refresh 是否轮换/使旧凭据失效未声明（live 授权仅指执行 refresh，不暗示已实测轮换）。
- 469 §1.1：`client_type` 的“必填”格实际是异常 String 错误文本（不能声称必填）；已核定登录规则：`client_type` 为 11 或 13 时 `bundleId` 必填（e77，独立于 refresh 表规则，两流程不互抹）；入口 query 未列 accessToken、需交互登录；页面 GET 仅浏览器行为推断；未证明必须自托管 redirect endpoint（受控获得回跳结果的方式待核定）。
- 481：`cameraName` 类型 int vs 示例 string；已关联 IPC 返回表 `deviceSerial` vs JSON `detectorSerial`；detectorCode 固定值说明不等于真实验证码规则；add 错误码超时/离线描述与旧节冲突。
- 1000007：`lockUserIndex` 在 delete/detail 为 Int、expiryDate 为 String；d6 声明 areaDomain 但示例 host 为 `open.ys7.com`；`usageTimes=0` 语义未定义（仅限 d6 AddTemporaryPassword，已移至该 row）；d13 `streamToken` 仅出现在类型说明、参数表未定义（d13 保留该缺项）；wire header 键名已核定为 `domainIdentifier`/`actionIdentifier`（header/String/Y），承载它们的 SDK 签名仍待冻结；d9/d10 只写 access permission；d6–d8/d11–d13 的 deviceMeta 层由来源定义但其 optional 性未声明，不将 d9/d10 `moreInfo=null` 推广到该组。
- 3725：body 编码未声明、week 描述错位、覆盖/合并与时区未知。
- 3729：area 字符串完整网格/位图编码未定义，不与 PIR 格式合并；请求必须 header `channelNo`。
- 476 旧 status 枚举矛盾与 477 正常枚举不互相修正；476 §1.4 UUID 仅限 Linktom 设备 device-SDK capture 产生。
- 3728：declared origin `open.ys7.com`（e5）为海外路由异常，路由方案待跨来源核定，不把替代域当官方事实。

## 实测与授权状态

- 海外账号 `user_confirmed_no_bound_device`（2026-11-17 用户确认）；设备/硬件相关验收均 `blocked_no_bound_device`。公共目录/账号级场景不假装依赖硬件，但本轮未执行验证。
- 写入、权限变更、绑定/解绑、付费、开门、响铃均为 `not_authorized`；预算与恢复方案未冻结，不自动串联操作（如 OperateBindToDevice 后仍需真实开锁才生效）。
- 既有离线/历史 live evidence 仅如实引用：`global.token.get` 为 `authentication_succeeded_explicit_regional_origin` —— 新加坡账号当次经显式区域 origin（`auth_base_url=https://isgpopen.ezvizlife.com`）认证成功、同区域空设备列表返回；同账号当次文档默认 origin 返回 10017（见 `docs/validation/live/2026-11-14-overseas/overseas.json` 与 `overseas-default-auth.json`）。不泛化为所有账号默认域失败或成功。`global.devices.list` 空列表、公开时区探测同此原则。

## 下一批接口冻结需要

1. 先冻结重复来源映射（469 §1.2→global.token.get、469 §1.10→global.devices.bind）、`source_alignment` parser 例外登记（534 e152、2868 e7）、481 name/change 的 multiple_methods artifact、以及 3728 `open.ys7.com` origin 异常的路由方案（跨来源核定，不把替代域当官方事实）。
2. 冻结 `ddns`、`detectors`、`locks` 三个新 facade 的公开签名、参数位置与 `ResponsePolicy`（`meta.code` int 200 vs `code` string "200" 分层），再派实现；locks 的 OTAP selector 已由已核定 wire header `domainIdentifier`/`actionIdentifier` 承载，但其 SDK 签名（参数位置与命名）需先冻结，deviceMeta 层 optional 性未知需先解决。
3. 冻结 `snapshots.get_by_uuid`（UUID 限 Linktom device-SDK capture）与 `devices.get` UTC 变体的入口形状（新增方法 vs keyword-only `version` 参数）。
4. v3 设置（3722–3730）先解决 3725 编码与 3729 area 编码再冻结；3729 set 需 header `channelNo`。
5. 消息接入按 `ACC-PLATFORM-MESSAGE-001` 单独验收；本批只生成 `message_protocol` 候选，不标记完成。

## 链接

- 机器可读清单：[`overseas-review-candidates.json`](overseas-review-candidates.json)
- 既有契约：`contracts/reviewed-transport-contracts.json`、`contracts/sdk-bindings.json`、`contracts/source-issues.json`
- 验收范围：`docs/validation/sdk-product-acceptance.md` / `.json`（`ACC-OS-DEVICE-READONLY-001`、`ACC-WRITE-OPERATIONS-001`、`ACC-PLATFORM-MESSAGE-001`）
- 相关研究：`docs/research/2026-11-15-message-ingestion.md`、`docs/research/2026-11-17-message-scope-review.md`
- 产品矩阵说明：`docs/product-matrix.md`
