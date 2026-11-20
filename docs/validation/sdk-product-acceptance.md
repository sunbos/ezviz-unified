# SDK 产品验收范围与发布门槛（事前冻结）

机器可读结构见 [sdk-product-acceptance.json](sdk-product-acceptance.json)。`schema_version=1`，`baseline_commit=81b86b462825739db8f059a418d5de235db88c87`，`scope_status=provisional_pending_capability_review`，`product_complete=false`。本文与 JSON 同步维护；冲突时以 JSON 为准。

## 边界

本记录冻结验收结构与代表性场景，不宣称全部能力已冻结：能力矩阵语义审查（SP003）持续推进，矩阵覆盖与优先顺序核定前不批量增加业务代码。每行 `case_id` 明确 capability selector、地区/集群、账号角色/权限、设备要求（无真实标识）、surface（sync/async/CLI/installed）、风险、前提、读/写/物理证据、bounded 上限、授权状态、恢复方案、来源与代码证据及结果；`resource_state` 仅作资源就绪限定（如 `user_confirmed_no_bound_device`、`unknown_awaiting_configuration_check`），不把行标记为完成。未设计的 bounded 记 `null` 且 `freeze_required=true`；未知设备/账户/次数不编造值。新增业务能力必须先绑定具体验收行并先冻结后执行；`ACC-DOMAIN-OFFLINE-PER-NEW-001` 通配行仅是政策占位，不能替代逐项具体行，`needs_review`/`blocked_source` 不算准入也不算完成。

`case_schema` 增加 `capability_ids`/`operation_ids`/`scope_revision`/`freeze_state`/`template_case_id`/`criterion_assessment` 可选字段，供具体 case 绑定范围登记：`capability_id` 保留来源谱系（candidate ID 不等于准入），`operation_ids` 是 registry ID（区别于 `settings.list_areas` 这类 runtime/diagnostic entrypoint）；`freeze_state=frozen_for_implementation` 只冻结接口与判据为实施合同。全范围登记、单 cohort 实施准入、live 执行与发布是四件不同的事——本文件的 case 冻结属于第一件/第二件的输入准备，`pending_execution` 的 case 未执行，更未达发布门槛。

## 分层

- `required`：本轮必须项；未完成或未核定即不标 Goal complete。
- `scenario`：代表性与风险场景；写操作等高风险项另需逐案授权。

## 新加坡首页首台设备工作模式计划实测

认证和首页首台选择已完成，同步计划查询返回 SDK 错误。首次失败即停，异步未执行。未记录计划内容、业务码或原始异常，无法据此判定权限、设备状态或型号支持。 本次获准上限为 5 次 HTTP，实际放行 3 次。本项仍待通过。[脱敏回执](live/2026-11-20-singapore-first-device-working-mode-plan/receipt.json)

## 移动端 SDK 菜单资料核查小结

本轮核查梳理了 iOS 与 Harmony 两侧共六个菜单入口的对应关系，三对菜单分别指向三份资料，其中 5240 与 5241 指向 4148 Demo 使用说明，5242 与 5243 指向 37 原生错误码，5244 与 5245 指向 4146 常见问题和反馈。目前 37 错误码资料已审，而 4148 的安装包与版本及平台调用签名、4146 的预览与回放、tkToken 平台及版本适用仍待核，另从八处文字实际观察到七个待核原生方法名。本批没有构建或运行原生客户端，未作真机验收。 [移动端接入指南](../official-mobile-sdk.md) · [核查记录](2026-11-21-mobile-sdk-shared-references/checks.json)

## 国内账号通道流量明细实测

本次单独执行先前未开始的账号级通道流量明细。仅第一页、每页一条；同步与异步分别使用受限的认证和业务请求，首次失败即停。未重跑账号汇总或按日明细。 仅放行了一次同步认证尝试，未发出流量业务请求，异步未执行。回执没有保存具体错误原因。 本次获准上限为 4 次 HTTP，实际放行 1 次。本项仍待通过。[脱敏回执](live/2026-11-20-domestic-account-channel-traffic/receipt.json)

## 国内工作模式计划

国内 1723 接口用于查询设备的工作模式计划，1720 接口用于设置，其适用范围限定于文档所指的华数 4G 摄像头，且“执行后停用”标志与海外 3725 工作模式的语义不同，二者不可混用。上述两项已在同步与异步模拟传输中完成验证，过程中未读取或设置真实设备计划。[冻结契约](../coordination/domestic-working-mode-plans/admission.json) · [离线回执](2026-11-21-domestic-working-mode-plans/offline.json)

## 国内存储录像接口离线验收记录

1422 接受毫秒时间，返回文件数组或分页对象。1600 接受文本时间，列于智能电梯网络摄像机目录，正文提到安全帽相机，适用机型尚待实测。两种接口的同步、异步调用和 CLI 均通过模拟传输验收。本次未读取真实设备录像元数据或访问媒体。[冻结契约](../coordination/domestic-stored-recordings/admission.json) · [离线回执](2026-11-21-domestic-stored-recordings/offline.json)

## 国内设备 `list_by_id` 单页入口验收

国内接口 3947 采用独立单页入口 `devices.list_by_id`，同步和异步签名相同，`cursor`、`page_start`、`page_size` 均为可选关键字参数，返回 `ApiResponse`。对照记录中，E14 将四个字段全部列入 query，E48 以表单发送其中三个共同字段且未传 id。SDK 按参数表通过 query 发送，一次只取一页。原 673 的 `devices.list` 继续使用表单按页码查询。同步与异步实现均以模拟传输验证，未向真实账号核验远端兼容性。[冻结契约](../coordination/domestic-device-cursor/admission.json) · [离线回执](2026-11-21-domestic-device-cursor/offline.json)

## 国内视频编码配置只读查询验收

国内 1595 接口提供只读查询能力，用于获取指定设备所选码流的编码配置，托管设备须先获得 CONFIG 权限方可调用。同步和异步查询及两个 CLI 模式均通过合成传输验证。本次未访问真实设备、读取媒体流或验证播放器兼容性。[冻结契约](../coordination/domestic-video-codec-read/admission.json) · [离线回执](2026-11-21-domestic-video-codec-read/offline.json)

## 国内移动侦测与 PIR／人形检测灵敏度

萤石国内协议的移动侦测灵敏度接口中，714 查询的契约表格标注 channel 为字符串、value 为整数，而其示例却把 channel 写成整数、value 写成字符串，SDK 按原样保留字段及类型，不做归一化处理。715 设置允许省略通道参数，省略时作用于设备本身。1733 与 1734 分别按指定通道查询与设置 PIR 或人形检测灵敏度。本批四项操作在模拟传输下同步与异步调用均通过验收，期间未访问真实设备，也未验证设置在设备上的实际效果。[冻结契约](../coordination/domestic-detection-sensitivity/admission.json) · [离线回执](2026-11-21-domestic-detection-sensitivity/offline.json)

## 区域移动侦测验收记录

国内 1731 用于查询人形检测区域，1732 经表单正文写入 area。海外 3729 将 area 放入 query，适用于画面变化检测与人形检测。海外 3728 的 GET 原文指向国内主机，与海外令牌区域限制冲突，海外读取保留缺口。前三项操作的同步与异步调用均经模拟传输通过，未访问真实设备，亦未确认设置在设备上的实际效果。[冻结契约](../coordination/regional-motion-area/admission.json) · [离线回执](2026-11-21-regional-motion-area/offline.json)

## 海外 FLV/H.264 本地预览

海外 SDK 通过合成 HTTP 响应取得回环预览地址，随后在 Chromium 143.0.7499.4 中运行官方 ezuikit-flv 2.1.2-beta.2。三色正例实际解码 19 帧，媒体时间推进 2000 毫秒，分辨率为 320×180。单色反例仍可解码，在颜色检查处被拒绝。显式停止、离页和自然结束均在浏览器上下文仍存活时观察到相应 Worker 退出。真实云端签发与设备播放单列为待授权案例。[使用指南](../official-flv-preview.md) · [验收回执](2026-11-21-overseas-flv-preview/checks.json)

## 国内异步设备只读验收

使用国内主分支 SDK 和真实本机 TTY 的隐藏输入。认证与首页列表成功，在首页首台设备的详情步骤未取得成功回执。额外的进程内传输守卫检查此前诊断失败并拒绝进一步外发；通道、状态、能力与版本请求均未发出。未记录平台业务码或原始错误，不能断定失败原因。 本次放行 3/7 次 HTTP，客户端已关闭，诊断完整。该案例仍待通过。[执行记录](live/2026-11-21-domestic-async-first-device/execution.json) · [脱敏回执](live/2026-11-21-domestic-async-first-device/receipt.json)

## 海外告警音模式与试音

同步和异步的模式设置、显式试音，以及两种模式的合成示例均已通过离线验收。设置与试音各自仅发出一次 POST。模式写入不触发试音，试音的静音音色也不能用来确认播放已停止。真实设备发声、配置生效与恢复仍需分别授权和验证。[协议与来源](../coordination/overseas-alarm-sound/admission.json) · [离线回执](2026-11-21-overseas-alarm-sound/offline.json)

## 海外移动侦测联动灯

查询固定发送 type=301，并完整保留返回数据。只读示例仅在返回类型为 301 时解释开关值。设置在 query 中提交两个字符串字段，成功回执可只有 body.meta。设备同步时点、实际灯效和恢复情况仍待真实验收。[来源及类型差异](../coordination/overseas-motion-linked-light/admission.json) · [离线验收](2026-11-20-overseas-motion-linked-light/offline.json)

## 海外设备工作模式计划

同步和异步的查询、设置及单设备只读示例均通过模拟传输验收。读取保留周计划和各时段的原始层级。设置提交一组平铺的表单字段，编码选择参考国内同路径文档。时区、整表替换或合并规则和真实设备生效情况仍待核实。读取结果不能直接充当恢复表单。[冻结的区域差异](../coordination/overseas-working-mode-plans/admission.json) · [离线回执](2026-11-20-overseas-working-mode-plans/offline.json)

## 海外用户 OAuth 令牌刷新

§1.3 的同步、异步入口和合成示例均通过离线验收。传输评测单独核对向授权 origin 发送的一次表单请求，以及刷新材料失效、绑定不匹配和 HTTP 故障。请求选择示例的 `open_id` 和表格要求的 `bundleId`。服务端实际接受的组合、有效期单位与旧凭据是否失效尚未实测。真实刷新需要已有合法用户刷新材料和具体授权。[冻结的请求格式](../coordination/overseas-oauth-refresh/admission.json) · [离线回执](2026-11-20-overseas-oauth-refresh/offline.json)

## 海外用户 OAuth 登录与回跳

469 §1.1 的同步与异步入口均通过本地验收。SDK 构造全球登录地址，只解析与预置授权源匹配的区域 HTTPS 回跳，诊断仅记录本地调用。示例使用合成数据。真实用户授权、浏览器交互与令牌签发仍待验收。§1.3 用户令牌刷新继续单独登记。[接口及来源差异](../coordination/overseas-oauth-login/admission.json) · [离线回执](2026-11-20-overseas-oauth-login/offline.json)

## 国内 RTMP 云端音频转 AAC

已在模拟传输中验收同步和异步的能力查询、云端转码开关及只读示例。查询返回布尔支持值。设置接受平台仅含 meta 的成功回执，一次业务调用只发送一次设置请求。当前开关值、真实账户权限、费率、RTMP 音频格式和恢复效果仍待核验。归档中 HLS 暂不支持这项服务。[接口与证据边界](../coordination/domestic-rtmp-aac/admission.json) · [离线回执](2026-11-20-domestic-rtmp-aac/offline.json)

## 国内暖光和夜视配置

在模拟传输中验证了同步和异步读取、设置，以及只读 CLI。读取保留平台返回的对象或字符串。设置按文档示例传递一次查询令牌，将三个完整配置值编码为表单内的 JSON 字符串。亮度和时长的单位、支持机型、实际照明效果仍待核实。真实配置读取需要明确通道和权限，写入另需确认测试值与恢复安排。[范围和来源差异](../coordination/domestic-night-vision/admission.json) · [离线验收](2026-11-20-domestic-night-vision/offline.json)

## 国内开发者设备添加链接

已用合成请求验证同步和异步签发、读取添加记录、查询已签发链接，以及仅显示条数的只读示例。签发按天提交有效期，列表返回毫秒时间戳。测试分别覆盖签发失败后不重放、只读令牌刷新、手动翻页以及错误与 HTTPX 日志脱敏。真实签发仍需指定开发者账号、授权与处置方案，两项读取仍待确认开发者资格和可用资源。客户经链接添加设备的结果未经实测。[冻结范围](../coordination/domestic-device-add-links/admission.json) · [离线回执](2026-11-20-domestic-device-add-links/offline.json)

## 海外 A1 探测器清单

同步、异步及单次查询示例已离线验收。481 §1.1 按示意的 result 布局取数组，§1.4 和 §1.5 按各自的根层布局处理。候选和已关联的 IPC 分开验证，行中存在矛盾的名称类型和标识键保留原值。海外 A1 主机、账号权限及实际可见关系仍需逐项真实验收；其余六节保持原状态。[范围与来源差异](../coordination/overseas-a1-detector-queries/admission.json) · [离线记录](2026-11-20-overseas-a1-detector-queries/offline.json)

## 海外设备上线事件

已验证 1000007 教程中的上线通知经本机真实 HTTP 投递、签名校验、ACK 和应用取件的完整链路。四连接回归分别检查首次受理、原样重复、同 ID 不同字节和错误签名。平台消息服务开通、外部回调、真实设备触发和海外投递保留在 `ACC-WEBHOOK-PLATFORM-OS-001` 等平台案例中。[本地示例](../webhook-receiver.md#海外设备上线事件) · [离线记录](2026-11-20-overseas-device-online-event/offline.json)

## 海外设备告警音模式

已用模拟传输验证同步、异步查询和只读 CLI。SDK 使用海外令牌的区域业务地址，按 3726 展示的 body 包络读取整数模式。CLI 只回显文档明确说明的三个值。该离线记录不能证明真实设备当前配置、线上包络或实际发声。新加坡首台设备的既有回执覆盖型号、在线状态和通道，3726 查询单独登记。[冻结范围](../coordination/overseas-alarm-sound-mode/admission.json) · [离线记录](2026-11-20-overseas-alarm-sound-mode/offline.json)

## 国内企业第三方协议接入

同步和异步客户端已离线验证项目开通与查询、单设备和批量申请、任务结果、设备分页和注册信息修改。三项申请/修改接口按正式参数表发送表单；示例中的 query 差异保留在来源记录中。项目查询 CLI 只显示协议、模式和状态。未使用真实企业账号开通项目、申请许可证、读取注册凭据或改动设备。[冻结契约](../coordination/domestic-third-party-protocols/admission.json) · [离线记录](2026-11-20-domestic-third-party-protocols/offline.json)

## 国内云直播流管理

同步和异步客户端已离线验证两种建流方式、时间窗修改、删除、启停、列表、推流记录和地址签发。列表采用官方 cURL 示例的 POST 表单；来源另有 GET 声明。取地址采用正式方法和示例的 POST 表单。CLI 展示一页列表的条数，离线生命周期示例按创建回执中的 ID 完成模拟修改与清理。真实账号权限、流的可用性、计费、录制和删除结果仍待逐项授权及验收。[冻结范围与来源差异](../coordination/domestic-live-streams/admission.json) · [离线记录](2026-11-20-domestic-live-streams/offline.json)

## 国内设备充电状态

同步与异步入口已验证国内 GET 的请求头、字符串业务码、嵌套的充电状态，以及令牌刷新和重试边界。CLI 只回显文档枚举，未知字符串显示为 `null`。本批使用合成数据，实际设备、账号权限、状态新鲜度与物理充电行为仍待核实。[冻结范围](../coordination/domestic-charging-status/admission.json) · [离线记录](2026-11-20-domestic-charging-status/offline.json)

## 国内设备语音素材新增与删除

已用模拟传输验证同步和异步新增、删除，以及示例按新增回执中的 ID 清理同一素材。新增自顶层 `voiceInfo` 读取 ID，删除按设备序列号、ID、名称和原 URL 选择素材。真实设备的下发、同步、删除和已有告警引用的恢复仍待验收。[冻结范围](../coordination/domestic-device-voice-provision/admission.json) · [离线记录](2026-11-20-domestic-device-voice-provision/offline.json)

## 国内设备语音素材列表与改名

已离线验证列表的顶层 `voiceInfos` 数组、改名所需的五个查询字段、两种模式的认证重试边界，以及请求日志、异常、诊断和只读示例的脱敏。未访问真实音频文件，未下发、播放或改动设备素材。设备同步、1273 指定型号上的改名与恢复仍待独立授权。[冻结范围](../coordination/domestic-device-voice-catalog/admission.json) · [离线记录](2026-11-20-domestic-device-voice-catalog/offline.json)

## 国内对讲下行扬声器音量

同步和异步的音量读取、设置以及只读 CLI 已通过模拟传输验收。设备端资料明确平台返回最近一次上报值，部分设备可能没有实现音量调节回调。真实设备的读写、恢复与声学效果仍待对应授权及独立核实。[冻结范围](../coordination/domestic-talk-speaker/admission.json) · [离线记录](2026-11-20-domestic-talk-speaker/offline.json)

## 国内设备图像配置

显示预设的读取与设置、详细图像参数和白平衡的 PUT 已完成同步与异步离线验收。3678、3680 已有读取契约及证据，本批核对并关联原有来源。四项真实操作尚未获授权，手动拼写、硬件效果与恢复能力均待验证。[来源及冻结范围](../coordination/domestic-image-configuration/admission.json) · [离线验收记录](2026-11-19-domestic-image-configuration/offline.json)

## 国内账号取流并发限制

已离线验证按取流类型查询、添加或更新以及删除配置的同步与异步入口。正式返回码表的整数 200 与文档样例中的 0 存在差异，真实服务行为仍待 E 套餐主账号验收。写请求未在真实账号执行。[冻结范围](../coordination/domestic-live-limits/admission.json) · [离线记录](2026-11-19-domestic-live-limits/offline.json)

## 国内设备 OSD 文字

已离线验证设备名称读取、单行设置与多行配置的同步和异步请求，以及只读 CLI 的输出。两个写接口的认证位置与成功码类型不同，认证失效时均不重放。设备级名称读取不能核对任意通道或多行配置的原值，三个真实案例仍待独立授权与适用设备。[冻结范围](../coordination/domestic-osd/admission.json) · [离线记录](2026-11-19-domestic-osd/offline.json)

## 国内智能设备算法清单

设备已加载模型和指定设备支持的算法分列为两个查询。同步、异步、CLI 与诊断案例已用模拟传输通过。GET 表单体的真实服务兼容性、账号可见范围及实际设备清单仍待授权后核验。[冻结依据](../coordination/domestic-device-algorithms/admission.json) · [离线验收](2026-11-19-domestic-device-algorithms/offline.json)

## 国内账号流量的部分实测与海外设备时区

国内三项账号流量查询按约定遇到首次失败就停止。账号汇总的同步、异步读取均返回对象。按日明细同步未通过，随后三个逻辑调用跳过。共放行 6 次 HTTP，设备查询为零。回执记录请求数、状态及类型，未保存响应内容或异常，无法据此确定失败原因。[国内真实回执](live/2026-11-19-domestic-account-traffic/receipt.json)

海外设备时区查询 `devices.get_utc` 及其 CLI 已通过离线验收。新加坡应用库存已确认非空，尚未针对具体设备执行时区查询。[海外离线证据](2026-11-19-overseas-device-utc/offline.json)

## 海外 DDNS 三项只读查询

2026-11-19，`ddns.get`、`ddns.list`、`ddns.share_list` 的同步及异步离线案例全部通过。测试覆盖认证返回的海外业务地址、表单、选择器优先规则、分页数据保真、错误脱敏及 CLI 输出。对应三项真实查询仍待海外账号授权，单条映射另需账号可见的目标。国内账号流量验收单独记录。[冻结范围](../coordination/overseas-ddns-read/admission.json) · [离线验收记录](2026-11-19-overseas-ddns/offline.json)

## 已记录证据（含历史复用行与 SP036 新通过行，均不超出各自边界）

| case_id | 结论边界 |
| --- | --- |
| `ACC-CN-SYNC-READONLY-001` | 国内同步单设备只读链已合并证据：[live 记录](live/2026-11-16-diagnostics-v1/README.md)。2026-11-16 21:24:44（Asia/Shanghai），首页 `page_size=1` 首台普通设备、通道 1。6 个业务逻辑入口 = `devices.list` 1 次 + `inspect_device` 固定五项（`devices.get`/`devices.channels`/`devices.status`/`diagnostics.capabilities`/`diagnostics.version`）；认证另占 1 次 HTTP，共 7 次 HTTP。该回执执行器为 `tools/live/verify_diagnostics.py`（`--serve`，scope `bounded_domestic_diagnostic_v1`），非 `verify_devices.py`。64 事件、`dropped_events=0`、`recording_errors=0`，运行所用 SDK 源码与预检快照哈希一致（commit `99613ea`），main CI `43878741197` 已通过 3.12–3.15，回归 4419 tests + 11 subtests。仅同步、单设备、单通道样本；真实当前设备配置尚未提供，不得声称仍在线。 |
| `ACC-OS-PUBLIC-TIMEZONE-001` | 在新加坡公开时区目录使用匿名 SDK 同步和异步各查询一次，均返回列表和成功码。[当前代码实测](live/2026-11-20-public-timezones/singapore.json) · [先前记录](live/2026-11-15-public-timezones/singapore.json)。范围限该公开目录。 |
| `ACC-OS-AUTH-EMPTYLIST-001` | 新加坡账号显式区域入口认证 + 一页空设备列表：[overseas.json](live/2026-11-14-overseas/overseas.json)。默认入口对本账号返回业务码 10017；空列表只证明当次账号当次范围，不证明设备可用、其他集群或 async。 |
| `ACC-OS-PUBLIC-AREAS-OFFLINE-001` | 公开国家/地区目录离线验收已通过（`execution_outcome=passed`，verified code commit `58e7881`）：证据 [checks.json](2026-11-17-public-areas/checks.json)（说明见 [README](2026-11-17-public-areas/README.md)）。初始 targeted 3 failed / 311 passed，定向修订后 SDK/harness targeted 327、示例 22、全量 4580+35 subtests、registered contracts 204 / wire 1664 全过，mypy 43 文件、Ruff 通过；`0.1.0a1` wheel/sdist 构建、内容检查、Twine、干净安装（sdist 2744 测试）完成，安装依赖走网络、SDK smoke 不触真实 API。属 code58 开发候选验证，**非最终 SDK 产品 release 或 PyPI 发布**。 |
| `ACC-OS-PUBLIC-AREAS-SDK-001` | 公开国家/地区目录 SDK 真实验收已通过（`execution_outcome=passed`，同一 commit）：证据 [sdk.json](live/2026-11-17-public-area-directory/sdk.json)（说明见 [README](live/2026-11-17-public-area-directory/README.md)）。UTC 2026-11-17T02:52:27 固定 `https://open.ezvizlife.com` 匿名 sync/async 各一次，合计 2 HTTP/2 逻辑调用；两 mode HTTP 200 + 业务码 `"200"`，`data` 240 项且四字段类型全部匹配，identity 编码每 mode 16442 字节，client/transport 均关闭；回执只存计数/类型/状态/hash（SHA `88cce20d…7adc`），46 输入 pin 与 code58 一致。240 为当次观察值非保证；仅 GET+identity，未覆盖其他 origin/POST/压缩路径/集群路由/设备。 |

## 公开国家/地区目录 case 结果说明（SP036，scope_revision=2026-11-17-r1）

两 case 在 `scope_revision=2026-11-17-r1` 下冻结并在 code commit `58e7881a3e7aee4c3ffc431b5016b019cefb447c` 上通过：`result=recorded_evidence`、`execution_outcome=passed`、`blockers=[]`，criterion_assessment 各项均为 `met`（offline 12 项、SDK 8 项，明细见 JSON）。SP036 决策记录（[acceptance.json](../coordination/SP036/acceptance.json)，scope_revision `2026-11-17-r2`）只把这两条 case 记为通过；**其它旧 case 与 release gates 全部不变，不升级**。frozen 判据与历史注记保留于 JSON `historical_freeze_notes`/`limitations`（GET-only、POST 形状未核定保留为 source issue、240 非固定数量、SP016 raw HTTP 不继承为 SDK 证据等），此处不再复述。

- `ACC-OS-PUBLIC-AREAS-OFFLINE-001`（required, offline_only）：判据覆盖 sync/async 准确 GET 与空 query/body/无 token、provider 零调用、成功 `data` 额外字段与空数组原样保留、国内 region/缺 public `api_base_url`/closed client 前置失败、业务/传输/格式错误不重试、async 取消传播、diagnostic on/off 语义一致与白名单/覆盖审计更新。`max_requests=0`/`max_operations=0` 只指真实云请求/业务效果。
- `ACC-OS-PUBLIC-AREAS-SDK-001`（required, read_only）：匿名 `Client`/`AsyncClient` 各一次显式 `api_base_url=https://open.ezvizlife.com` 真实调用；无凭据、不跟 redirect、不重试、不访问设备，每阶段 timeout 10s、解压响应 1 MiB 上限由验收 transport 边界控制，各自正确 close/aclose。`limitations` 明确：GET-only、公共目录不证明集群路由/绑定设备/硬件、identity-only 协商未覆盖压缩响应与其他 origin。`resource_state=not_applicable` 不解除海外绑定设备必需 case 的 `user_confirmed_no_bound_device` 阻塞。

两 case 的 `capability_ids=[candidate:overseas:469:1.12]`、`operation_ids=[global.settings.list_areas]`；`settings.list_areas` 是 runtime/diagnostic entrypoint，**不是** registry operation_id。来源 overseas:469 §1.12（e1459 固定 origin、e1463 列 GET/POST、e1468 GET 示例、e1476 字段表）；SDK 明确只选 GET，POST 语义未核定保留为 source issue，不伪造 declared_pair 或提升 parser 恢复数。

## 国内设备客流统计

四个读取与两个配置写入的同步、异步入口均已通过离线验收。测试验证写失败不会被自动重放，日报拒绝不存在的当地午夜，坐标字段按 SDK 选择的一层表单编码发送。六条既有离线案例保留原 ID，另增六条独立的真实设备案例，必需案例由 405 项增至 411 项。设备读写均未在本批实际执行，平台对 692 编码的接受情况和恢复能力仍待分别确认。见[协议与权限判据](../coordination/domestic-passenger-flow/admission.json)及[离线回执](2026-11-19-domestic-passenger-flow/offline.json)。

## 国内直播流归档文件

源文件查询与点播索引查询已通过同步和异步离线验收，覆盖表单、响应层级、时间边界、错误消息中的媒体地址和示例输出。两项既有离线案例沿用原 ID，另增两项真实只读案例，必需案例分母从 403 增至 405。本轮账号流量授权不含归档查询。见[来源和判据](../coordination/domestic-stream-archive/admission.json)及[离线记录](2026-11-19-domestic-stream-archive/offline.json)。

## 国内云直播流量查询

四个国内接口均已通过同步和异步的离线验收。账号汇总、按日用量、指定日的通道明细已获本轮真实只读授权，指定设备明细另待授权。每项真实验收独立记录。四个既有离线案例继续使用原 ID，并为它们分别新增真实案例，必需案例分母从 399 变为 403。参见[离线结果](2026-11-19-domestic-traffic/offline.json)和[账号查询范围](../coordination/domestic-traffic/account-read.json)。

## 待设计/待资源（不算完成）

- 云录制 1.0 文件与 VOD 元数据（既有批 `b251b11`，已随 PR #16 合并入 main `254ea73`，历史结论保留）：四条 **OFFLINE** required（`ACC-CANDIDATE-CN-5341/1375/1377/5342-D0-OFFLINE-001`）已 `recorded_evidence`+`passed`，证据 [checks.json](2026-11-18-cloud-files/checks.json)；对应四条 **LIVE** required（`5341`/`1375`/`1377`/`5342` 的 `-D0-LIVE-001`）仍 `awaiting_authorization`/`awaiting_resource_confirmation`，`freeze_state=awaiting_resource_scope_and_authorization`、真实资源/权限/请求上限未冻结，`max_requests=null` 不可运行；截至当时的已核离线/历史记录中这些 case 未发真实云请求。scenario `ACC-CN-VOD-RESOURCE-DISCOVERY-001` `result=pending_execution`，仅离线安全判据 `met`；其 SP166 国内只读资源发现窗口实际**超时关闭**、无报告、`execution_outcome=not_verified`、实际 HTTP 数 `null`（不可声称 0 或账号无资源），见 [live/2026-11-18-vod-resource-discovery/attempt.json](live/2026-11-18-vod-resource-discovery/attempt.json)。**新增 VOD remux/任务批 `fce298b`**：四条 OFFLINE（`3971`/`3972`/`4941`/`3973`）已 `recorded_evidence`+`passed`（见文末当前批次），四条 LIVE 未执行/未授权或资源未齐。
- `ACC-CN-ASYNC-READONLY-001`：异步执行器随 [PR #19](https://github.com/sunbos/ezviz-unified/pull/19) 合并。当前国内真实核验已完成认证和首页列表，设备详情及后续步骤仍待通过。[最新真实回执](live/2026-11-21-domestic-async-first-device/receipt.json)。
- `ACC-CN-CLI-EXAMPLE-001`：CLI 联网验收范围已冻结为六个既有 CLI 主入口，上限 20 次 HTTP / 14 次业务调用；其中空首页的四个列表类 CLI 共 8 次 HTTP，另跳过两个 inspection 入口；冻结上限见 [admission.json](../coordination/domestic-cli-read/admission.json)；当前无真实运行，`result=pending_execution`，等待真实 CLI 回执，未实测不得报 passed。
- `ACC-OS-DEVICE-READONLY-001` 的完整设备检查仍待验收。另行授权的同步场景已验证首台设备的型号、列表在线状态与通道清单；通道状态接口、设备能力和版本均未调用。[基础读取回执](live/2026-11-19-singapore-first-device/receipt.json)
- `ACC-DOMAIN-OFFLINE-PER-NEW-001`：各新增业务域逐项要求 offline contract + error path + diagnostics 覆盖，先行于任何 live 声明；`max_operations=null` 且 `freeze_required=true`。本通配行仅为政策占位——每个准入矩阵的具体能力都必须派生自己的具体验收行（具体 selector、地区/集群、bounded 请求/操作数、授权状态、判据）并先冻结后执行；`needs_review`/`blocked_source` 项不算准入也不算完成。
- `ACC-PLATFORM-MESSAGE-001`：平台消息真实入口/验签/ACK/重复投递/时限/错误处理/重放边界不能由 helper 向量替代；`src/ezviz_unified/webhooks.py` 的纯函数只证明给定 body+secret 的 MAC 匹配，不证明发送方身份、新鲜度或重放安全。需已开通服务、可达回调、独立 secret、目标类型与触发授权；去重与平台重复投递语义须在执行前定义。`resource_state=unknown_awaiting_configuration_check`：用户 2026-11-17 表示不清楚平台消息服务及可用回调入口。配置副作用需超出既有只读授权的显式授权：开通/修改消息类型约 2 小时生效、同手机号萤石云视频 App 设备消息提醒停止且关闭服务不自动恢复（需客服/工单）、控制台「消息测试」暂停原回调并临时打开全部类型。国内 HTTP 头 `t` 时间戳已确认为毫秒（5130）；海外 `t` 单位、时限（2 秒 vs「通常 3 次/一般 5 秒」冲突）与密文措辞仍未核定，不编造取值；仅一条成功回调不得关闭该 required 行。来源：[message-ingestion](../research/2026-11-15-message-ingestion.md)、[webhook-helpers](../research/2026-11-15-webhook-helpers.md)、[message-scope-review](../research/2026-11-17-message-scope-review.md)。

  本地层补充（SP117，2026-11-18）：三条本地用例 `ACC-WEBHOOK-CORE-001`/`ACC-WEBHOOK-HTTP-001`/`ACC-WEBHOOK-CLI-001` 已在本机记录通过（见 [checks.json](2026-11-18-webhook-receiver/checks.json) 与 [README](2026-11-18-webhook-receiver/README.md)）。证据为 commit `11cd8af` 上 4705 passed + 35 subtests、真实 TCP loopback 合成链（sender→HTTP→SDK 验签/解析→有界 inbox→JSON ACK）与真实 CLI 子进程/PTY 检查；全部合成数据、零真实平台/设备业务。平台用例（CN/OS）及 `ACC-PLATFORM-MESSAGE-001` 仍 `awaiting_resource_confirmation`，未授权配置变更。signed-only 为本地设计，OS `t` 单位未推断；inbox 非持久、不提供跨重启 exactly-once；CLI 真实 TTY 只证明本机交互路径，不冒充平台回调。该本地通过不改变 SDK src/依赖/HTTPX/重试行为，不升级本 required 行。
- `ACC-MEDIA-OFFICIAL-INTEGRATION-001`：国内 HLS 有[官方组件接入示例](../official-hls-preview.md)。H.264 的 [MSE/video 本地验收](2026-11-19-official-hls-preview/checks.json)与 HEVC 的 [WASM/WebGL 本地验收](2026-11-19-official-hevc-preview/checks.json)分别通过。两种真实平台预览仍待授权，RTC、海外与原生组件另行核定。
- `ACC-WRITE-OPERATIONS-001`：写入、绑定/解绑、权限、付费、升级、删除、格式化需要具体资源范围、次数与成本上限、恢复方案授权，授权前不自动执行。

## 发布门槛（单列）

见 JSON `release_gates`。新增两条明确产品前置 gate（与工程 gate 为 AND 关系，工程 checks 全绿不能单独放行上传）：

- `REL-PRODUCT-SCOPE-RATIFIED`：范围/能力矩阵经审查并核定（无来源行未审），每个能力有已记录处置；`needs_review`/`blocked_source`/未核定项不得作为准入证据、不得被静默排除或计为完成，须先解决为具体处置。
- `REL-PRODUCT-REQUIRED-COMPLETE`：每条 required 验收行按自身判据完成实现、文档与验收（达 `recorded_evidence` 或有依据的 `not_applicable`），required 示例/CLI 行已实测；required 的来源语义问题已处置。

其后：版本与 PyPI 主体/权限/授权均待核定——PyPI 未执行发布，2026-11-17 公开页 404 仅反映当次可见性，不保证名字可用；可提前准备账号/Trusted Publisher（首次授权上传才创建项目，参考[官方文档](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)），用户无需现在提供 token。冻结 artifact/hash、Ruff/mypy/pytest/audit/contract_eval/诊断覆盖、wheel/sdist 内容秘密扫描 + 布局 metadata + Twine + 干净安装、当次 PR 与 main CI、发布索引下载 hash 与安装验证逐项待执行或待授权。`python -m build` 产出不等于发布；SDK 运行时独立于 harness/资料/LLM。已合并诊断 v1 的构建与安装检查只对当时产物有效，重建需重新核验。

## 第二轮保留项（deferred，不是完成）

App adapter、选择性遥测、独立模型评测、受控 RSI 均为第二轮范围，见 [计划](../plans/2026-11-17-sdk-product-completion.md)。

## 证据纪律

只引用仓库内真实存在的文件；不写入 token、序列号、URL 原始业务数据。blocked / awaiting_resource_confirmation 均不计为完成。旧 live index 与 roadmap 不在本单修改范围，后续单独派单。

## 未决问题

见 JSON `open_questions`：当前国内设备在线状态未确认；async bounded 场景已按 `2026-11-19-r18-domestic-async-read-design` 冻结（live 未执行）、六个 CLI 的 20 次 HTTP 上限已冻结，真实回执待执行；新加坡应用已通过同步认证、首页首条设备的型号、列表在线状态和通道清单核验，更广的设备权限和完整检查仍待独立验收；平台消息服务/可用回调入口未知（`unknown_awaiting_configuration_check`），去重与重复投递语义须先定义，国内 `t` 毫秒已确认、海外单位/时限冲突/密文措辞仍待核定，开通与测试的配置副作用须显式授权；版本与 PyPI 主体/账户/权限/上传授权未确认（未执行发布，2026-11-17 公开页 404 仅为当次可见性，可提前备账号/Trusted Publisher）；新业务域须先绑定具体验收行，通配行不替代逐项；公开区域目录 cohort（`candidate:overseas:469:1.12`）两条 case 已于 code58 通过（见上文），但该通过不扩展其边界：240 项非固定数量，`/api/lapp/area/list` 的 POST 形态/认证未核定保留为 source issue（SDK 仅选 GET），其他 origin/压缩路径/集群路由未实测，SP016 原始 HTTP 观察仅为协议证据。

scope-register 历史快照 `2026-11-17-r2` 曾记 253 槽位/204 已登记 operation，`2026-11-18-r11-cloud-file-admission` 为本批 G1 记录（354 槽位/373 cases），`2026-11-18-r12-vod-resource-probe` 登记 probe scenario；历史 `2026-11-18-r13-cloud-file-offline-acceptance` 为 354 槽位/374 cases/208 operation；**历史离线验收快照 `2026-11-18-r15-vod-remux-offline-acceptance`**：358 capability 槽位（仍非官方 API 总数/覆盖率）、**382 cases**、212 已登记 registry operation（contracts/bindings/wire cases：国内 145、海外 67）、107 条已登记 source issues；诊断 141 business / 282 sync+async bodies；source 身份集 4051/observation 3019 不变。新增 scenario `ACC-CN-VOD-RESOURCE-DISCOVERY-001`（SP142，`docs/coordination/SP142/admission.json`）：`bounded_resource_probe_frozen_not_executed`、`authorized_readonly=true`、`live_ready=false`、`max_requests=7`/`max_operations=2`，父 required `ACC-CANDIDATE-CN-5342-D0-LIVE-001` 仍未完成——probe 只覆盖 sync `spaces`+`vod.files` 辅助资源发现，不替代 async 或 1.0 任何方法 live。G0 范围责任/优先级已批准（SP030），但这不是全来源语义完成；G1 已允许云四入口（SP122）与 VOD remux/任务四入口（SP169）；全 scope `all_semantics_dispositioned`/`required_acceptance_complete`/`product_complete` 仍为 `false`。5 条国内 `device_voice` 仅为 review 候选、无实现或 G1/live 授权；七个官方 npm 版本 archive 完整性与包身份已核定，但 11 repo 内容/34 binary/许可证兼容与集成验收仍未完成。

main 已含 **PR #16 合并头 `254ea73acafc5dd2f81c90c4a867fa0fb56a2d26`**（既有云文件 cohort `b251b11` 已并入）；该 PR 的 CI run `44235604644` 与 main CI run `44236090511` 的 Python 3.12–3.15 四 job 均通过——此事实来自 root 已核 SP165，**仅对应已合并的云文件 code，不代表当前 `fce298b` VOD remux code 的 CI**。本离线验收快照记录时，VOD remux 实现 commit `fce298b34911eac89e6c5813b203c5b267e80095` 为本地提交，**未 push/未建 PR/无本批新 CI**；后续状态请读对应 PR/CI 真实记录，不预写未来 PR 号，也不能把 PR16/旧 CI 继承为本批通过。`fce298b` 是 SDK 代码 pin，不要求该 code commit 本身单独有 CI——未来 CI 验证的是当次 PR 或 main HEAD，且其中 SDK 哈希与此 code 一致。

当前批次（本离线验收快照记录时实现 commit `fce298b34911eac89e6c5813b203c5b267e80095` 为本地提交，未 push/未建 PR；后续状态以对应 PR/CI 真实记录为准）：四个国内入口 `vod.create_remux_task`/`vod.task`/`vod.tasks`/`vod.recording_urls` 已实现（`src/ezviz_unified/vod.py`）。**四条 OFFLINE required case（`ACC-CANDIDATE-CN-3971/3972/4941/3973-D0-OFFLINE-001`）已 source-pinned `recorded_evidence` + `execution_outcome=passed`；四条 LIVE required 未执行/未授权或资源未齐**。正式验收证据：[checks.json](2026-11-18-vod-remux/checks.json)。本机观察：全量 pytest 6023 + 35 subtests（46.29 s）、lint/format、mypy 45、wire 212 contracts/1728 executions 全过、诊断 141 business/sync+async body 282、定向 wire 246/behaviour 304/integration 507、构建/产物/Twine/干净 wheel+sdist 安装均过；sdist 3925 passed 11.75s。计数为已执行测试单元/已登记契约，**非覆盖率或 API 总数**；安装依赖走网络但 `real_api_used=false`。`0.1.0a1` 是开发构建、未冻结最终发行、未上传 PyPI。国内只读资源发现窗口（SP166）实际**超时关闭**、无报告、`execution_outcome=not_verified`；实际 HTTP 请求数 `null`，不可声称 0 或账号无资源，不继承为 remux 实测，公开记录 [live/2026-11-18-vod-resource-discovery/attempt.json](live/2026-11-18-vod-resource-discovery/attempt.json)；凭据从未进入本任务。不能宣称 Goal 完成、官方全部覆盖或实测完成。`product_complete=false`。

本批验收登记联动说明：上述 SP188 全量 pytest（6023 + 35 subtests）与 SP183 集成批（507）是 `fce298b` SDK 代码/测试字节上的历史通过记录。离线状态升级后原 harness 测试硬要求 `not_verified` 状态，已另单修正为带证据状态守卫；post-acceptance 定向执行结果与新测试 sha256 见 `checks.json` 的 `post_acceptance_validation`。修正后的新测试不属于 `fce298b` 代码字节，不能冒充其计数；后续定向检查 SP206 已通过：300 项测试及 lint；新测试 SHA 与实际回执见 `post_acceptance_validation`，不改写此前全量执行的版本或计数。SDK 源码与入包 SDK tests 未变、未重建。

**历史批次（云文件，commit `b251b11`，已随 PR #16 合并入 main `254ea73`）**：四个入口 `cloud_recording_v1.unfreeze_file`/`file_info`/`download_urls`、`vod.files` 已实现；四条 OFFLINE required（`5341`/`1375`/`1377`/`5342`）`recorded_evidence`+`passed`，四条 LIVE required 仍 `awaiting_*`。以下计数属于该旧 code 的当时观察，与上面 `fce298b` 的计数分开、不混用：全量 pytest 5448 + 35 subtests（45.39 s）、mypy 45、wire 208 contracts/1696 executions、诊断 137 入口/sync+async body 274、cloud-wire 190、Vod 145、云 behaviour+表单 312，sdist 3371 passed 10.39s；证据 [checks.json](2026-11-18-cloud-files/checks.json)（说明见 [README](2026-11-18-cloud-files/README.md)），计划 [2026-11-18-cloud-files-readonly-probe](../plans/2026-11-18-cloud-files-readonly-probe.md) 与 [2026-11-18-cloud-recording-v1-vod](../plans/2026-11-18-cloud-recording-v1-vod.md)。

## r16 三业务域实施准入（历史 G1 准入快照）

新增 G1（LEGACY-MESSAGE-001 消息订阅域，docs 983/984，facade message_subscriptions，2 SDK op + 1 integration）与公开 SP213 G1 及案例已登记 r16；367 slots/402 cases 为非覆盖计数。本轮 8 SDK op + 3 integration 属新增准入，983/984 复用原 ID。actual registry 212（145/67）、诊断 141/282/387；target 220（153/67）/149/298/403 仅为目标值而非支持事实。11 项新 LIVE required 的具体资源与次数未冻结，消息创建/消费未获授权；既有国内只读授权延用但不覆盖消息提交副作用。全部 product/发布未完成；r15 旧 VOD 证据仅作历史保留，不构成本域证据。

## r17 三业务域离线验收（当前段，实现 commit `dd105464bb68de358b555d94aff81c9689bc83b7`）

scope 推进至 `2026-11-19-r17-capture-messaging-hubs-offline-acceptance`。三 cohort（`CLOUD-CAPTURE-001`/`LEGACY-MESSAGE-001`/`HUB-DISCOVERY-001`）的 **11 条 required OFFLINE（8 SDK 操作 + 3 仓库示例）`recorded_evidence`+`passed`**；对应 **11 条 required LIVE 全未执行**（`awaiting_*`/资源与授权未齐），**本批未发真实云/设备/消息请求**。正式证据：[checks.json](2026-11-19-capture-messaging-hubs/checks.json)。

- 覆盖入口：云抽帧 `cloud_capture.create_interval_task`/`create_timing_task`/`task`，消息 `message_subscriptions.create_consumer`/`consume`，HUB `hubs.list_subdevices`/`subdevice_info`/`channels`；示例 `examples/cloud_capture.py`/`message_subscriptions.py`/`hub_discovery.py`。
- 该批交付时 registry 为 **220**（国内 153 / 海外 67），诊断 149 business / 298 sync+async bodies；本批新增 8 行 binding 均 `offline_status=passed`/`live_status=not_verified`。367 capability 槽位、402 cases、1792 传输检查计数为槽位/案例口径，非 API 总数或覆盖率。
- 本机观察（该 code commit 上）：全量 pytest **7220 + 35 subtests**（62.34 s）、lint/format/mypy、wire 220 contracts/1792 executions、构建/Twine/干净 wheel+sdist 安装均过；sdist **4827 tests**（18.70 s）。`0.1.0a1` 为开发构建、未冻结最终发行、未上传 PyPI；安装依赖走网络但 `real_api_used=false`。
- 限制沿用 checks.json `limitations`：真实抽帧任务、真实消息投递与提交、HUB 与实际子设备的对应关系尚待有界联调；账号套餐、设备型号覆盖、收费、媒体可用性与 PyPI 发布均未由本批离线结果验证。后续国内 async 为复用既有 SDK 的只读验收，不是本批新增实现。
- 本批已随 [PR #18](https://github.com/sunbos/ezviz-unified/pull/18) 合并入 main，其 Python 3.12–3.15 四版本 CI 已通过（[run 44372920559](https://github.com/sunbos/ezviz-unified/actions/runs/44372920559)）；该事实仅对既有三域本批代码有效，不覆盖 `tools/live/verify_diagnostics_async.py` 等新文件的本工具新 CI（尚待跑）。`product_complete`/`required_acceptance_complete`/`all_semantics_dispositioned` 仍 `false`；上述 OFFLINE 通过不等于 cohort 或产品完成。
