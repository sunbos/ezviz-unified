# EZVIZ Unified

面向萤石国内与海外平台的第三方 Python SDK，目标是统一调用体验、明确地区与权限边界，并以可验证的契约持续维护。

项目遵循[优先复用官方生态的边界](docs/research/2026-11-15-official-ecosystem-reuse.md)：服务端接入与维护验证是主要投入方向；播放、编解码和RTC由适用的官方组件承担。官方已有海外播放器、Token工具和接入skills，新增能力须证明具体使用价值。

当前为开发中的 `0.1.0a1`：发行名 `ezviz-unified`，导入名 `ezviz_unified`，要求 Python 3.12+，仓库为 [sunbos/ezviz-unified](https://github.com/sunbos/ezviz-unified)，尚未发布到 PyPI。已有同步/异步客户端、区域认证、设备查询和诊断脱敏；各业务域按[实施路线](docs/plans/2026-11-14-delivery-roadmap.md)逐域推进。

本地录像、云台、巡航、诊断、设备开关、管理、图像参数、视频配置、播放地址、抓拍、告警、云存储、统一存储、云录制1.0文件与云点播、RAM子账户、维护、补光、加密/布撤防、单步确权、OTAP物模型与B1/B2账户托管接口已加入，国内/海外共用入口并保留协议差异（云录制1.0文件操作与云点播VOD仅国内；OTAP与账户托管目前仅国内有据）；当前登记数量、地区分布与验证状态见[机器生成的覆盖清单](contracts/coverage.json)，真实支持情况另列。

产品能力范围的权威登记见 [docs/capabilities/README.md](docs/capabilities/README.md)：按 `capability_id` → `acceptance_case_refs` 定位验收证据，并区分 `implementation.status`、`review`、`result`、`freeze_state` 与 `requirement=required` 口径；登记数量与 wire 证据不代表覆盖率或产品级验收通过。

查阅 [移动端 EZOpenSDK 接入与排障指南](docs/official-mobile-sdk.md) 可了解如何按 Android、iOS 或 Harmony 选择官方 Demo，并核对原生错误码。

## 设备查询

首次使用请按[接入指南](docs/getting-started.md)安装并运行同步/异步示例；示例显式选择地区，仅查询首个分页，默认不输出设备身份信息。

```python
import os
from ezviz_unified import Client, RegionConfig

with Client(
    os.environ["EZVIZ_APP_KEY"],
    os.environ["EZVIZ_APP_SECRET"],
    region=RegionConfig.china(),  # 海外改用 RegionConfig.overseas()
) as client:
    response = client.devices.list(page_start=0, page_size=10)
    devices = response.data
```

`client.devices` 提供 `list`、`get(device_serial)`、`channels(device_serial)` 和 `status(device_serial, channel=1)`；均返回 `ApiResponse`，`.data` 保留官方字段，`.raw` 保留完整响应。异步使用 `AsyncClient`、`async with` 及 `await client.devices.list()`。

客户端按契约携带token，复用当前区域的连接。仅明确只读的操作遇到平台 `10002` 时刷新并重发一次；写操作、网络失败、一般HTTP401及设备层错误不自动重放。自定义接口可用 `Operation` 和 `client.request(...)`，其 `read_only` 默认False。禁止调用方覆盖受管token或Host。

国内分页页码不超过400；海外文档未声明相同限制。每页1–50条。国内 `channels`/`status` 要求序列号英文字母大写。SDK不自动翻页，不自行解释有文档矛盾的海外状态字段。[国内单设备记录](docs/validation/live/2026-11-14-domestic-device/README.md)已验证基础信息、通道和状态查询；新加坡同步清单在 2026-11-14 返回空页。2026-11-19 确认可见设备后，另行验证了该应用首页首条设备的型号、列表在线状态与通道清单。[首台设备回执](docs/validation/live/2026-11-19-singapore-first-device/receipt.json) · [历史空页](docs/validation/live/2026-11-14-overseas/README.md) · [最新可见性回执](docs/validation/live/2026-11-19-singapore-inventory/receipt.json)

国内托管设备使用 `client.devices.list_managed(page_start=0, page_size=10)`；与普通列表分开，不套用400页上限。接入设备后的[范围复核](docs/validation/live/2026-11-14-domestic-device/README.md)中，普通列表首个分页返回一条，托管列表分页为空；这不代表全量设备计数，其他授权主体仍未验证。

`client.devices`另提供bind/unbind、rename/rename_channel、link_ipc/unlink_ipc、set_channel_visible、cameras和connection_info；写请求不会自动重放。仅国内提供rename_channel/set_channel_visible/connection_info，后者不支持子账户和托管权限。`client.ownership`提供国内三步确权调用，不自动串联、操控设备上下线或修改安全设置；具体约束见[管理与确权说明](docs/research/2026-11-14-management-ownership.md)。这些变更操作仍未实测。

`inspect_device(client, device_serial, channel=1)` 与 `inspect_device_async` 按固定顺序组合设备信息、通道、状态、能力、版本五次只读调用，逐项返回 `InspectionResult`，并提供不含序列号、通道、载荷或异常原文的 `public_summary()`；认证级错误立即中断。示例 `examples.inspection_sync`/`inspection_async` 用 `EZVIZ_DEVICE_SERIAL` 与 `EZVIZ_CHANNEL` 显式指定一台已知设备，不枚举账号设备，退出码 0/2/1 分别对应全部成功、部分失败、配置或 SDK 级失败；用法与输出契约见[接入指南的单设备检查节](docs/getting-started.md#单设备检查)。

### 国内设备列表游标分页

同步客户端可调用 `client.devices.list_by_id(page_size=1)`，异步客户端则使用 `await async_client.devices.list_by_id(cursor=cursor, page_size=1)`，二者都只发送单页请求且仅支持国内区域。翻页游标取自 `response.raw["page"]["nextId"]`，`response.data` 为服务端返回的原始行记录，当 nextId 为 None 或空字符串时即表示已到末页，应停止翻页。仅当 nextId 非空时，调用方才可在后续显式调用中将其作为 cursor 传入，SDK 不会自动翻页。可选参数 cursor、page_start、page_size 为 None 时一律不下发，page_start 取值 0 至 100 属于 SDK 侧的本地解释，page_size 允许 1 至 50 而服务端默认每页 10 条。若 id 与 pageStart 同时传入，二者的排序与优先级尚未经过实际验证，更多背景见 [国内文档 3947](https://open.ys7.com/help/3947)。

参数映射依据 3947 文档中唯一完整的参数表，该表将 accessToken、id、pageStart、pageSize 四项全部归入 query。文档示例的 POST curl 将 accessToken、pageStart、pageSize 三项以 form 形式提交且省略了 id。SDK 将调用方提供的所有参数均以 query 形式发送，与该表一致，目前无线上实测证据也没有回退方案。已有的 `devices.list`（见 673 文档）不受影响，本接口在子账号下只能列出 EZVIZ 协议设备。CLI 需配置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`，可选游标通过环境变量 `EZVIZ_DEVICE_CURSOR` 传入，每次运行只执行一次逻辑上的 list_by_id(page_size=1) 调用（如遇 10002 可能触发一次 token 刷新并重放该请求），输出仅包含 returned_items 与 has_next 两个字段，验证全部基于本地离线夹具完成。

```sh
python -m examples.device_cursor --mode sync
python -m examples.device_cursor --mode async
```

### 国内设备充电状态

`client.devices.charging_status(serial)` 和 `await async_client.devices.charging_status(serial)` 查询设备上报的充电状态。SDK 将原样保留大小写的设备序列号和访问令牌放进 GET 请求头。只读请求收到令牌过期码 `10002` 时，可刷新的令牌提供方最多刷新并重试一次。[充电状态（1739）](https://open.ys7.com/help/1739)

成功回执的顶层 `code` 为字符串 `"200"`。从 `response.data["valueInfo"]["powerStatus"]` 读取状态。官方定义的字符串 `"0"` 至 `"4"` 依次表示未充电、正在充电、充满、无电池和电池故障。SDK 保留未来新增的字符串值和扩展字段。状态缺失或类型错误会抛出 `ProtocolError`。

在本机配置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 和 `EZVIZ_DEVICE_SERIAL` 后，可以运行下面的单次查询。异步示例使用 `--mode async`。输出只显示官方状态值，遇到未知值时显示 `null`。

```bash
python -m examples.charging_status --mode sync
```

如需剩余电量，另行调用已有的 `devices.status(serial, channel=1)`。其官方字段拼写为 `data.battryStatus`，整数 1 至 100 表示百分比，`-1` 表示未上报或不支持。该国内入口要求序列号字母大写。两个接口分别返回平台状态，文档未给出二者的采样时间或刷新间隔。[设备状态（675）](https://open.ys7.com/help/675)

### 海外设备时区查询

`client.devices.get_utc(device_serial)` 与 `devices.get` 走同一路由，前者额外提交 `version=utc`。同步客户端和异步客户端均支持这项查询。响应的 `data` 对象可包含 `timeZone`、`wifiSsid` 以及其他设备信息，原始 `ApiResponse` 完整保留；字段缺失或未知时不补默认值、不解析偏移。SDK 保留序列号大小写。官方未指定时区字符串的格式。实际海外设备的返回情况待验收。[海外官方文档 §1.20](https://open.ys7.com/help/en/477)

在本机配置 `EZVIZ_REGION=overseas`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`、`EZVIZ_DEVICE_SERIAL`。

```bash
python -m examples.device_timezone --mode async
```

示例只报告 `timeZone` 是否为非空白字符串。合成输出示例如下。

```json
{"region": "overseas", "mode": "async", "status": "ok", "time_zone_present": true}
```

### 海外 A1 探测器清单

同步调用 `client.detectors`，异步调用 `async_client.detectors` 并加 `await`。每个方法只发起一次逻辑查询。SDK 向令牌返回的 `areaDomain` 发送 POST 表单；`accessToken` 由客户端注入，`deviceSerial` 指定 A1 主机。[海外接口文档 481](https://open.ys7.com/help/en/481)

| 方法 | 返回的清单 | 读取约定 |
|---|---|---|
| `list(a1_serial)` | 该 A1 已关联的探测器 | §1.1 的示意把业务码和数组放在 `result` 下。原示例不是合法 JSON；SDK 选择 `result.code` 和 `result.data`，真实网关布局待核。 |
| `bindable_cameras(a1_serial)` | 可与该 A1 关联的摄像机 | §1.4 从根层 `data` 取数组。资料未说明是否排除已关联设备；`cameraName` 在字段表和示例中的类型不同，SDK 保留原值。 |
| `linked_cameras(a1_serial, detector_serial)` | 指定探测器已关联的摄像机 | §1.5 额外发送 `detectorSerial`。根层 `data` 的字段表与示例分别使用 `deviceSerial` 和 `detectorSerial`，SDK 保留原键。 |

成功码为字符串 `"200"`；结果从 `response.data` 读取。空数组是有效响应，额外字段和原始响应保留给应用。序列号大小写按调用方提供的值发送。可刷新的令牌遇到已识别布局中的失效码 `10002` 时，现有只读策略最多重试一次。SDK 不会按一项结果继续查询其他设备。最低账号角色、真实可见范围和设备关联情况仍待验收。[来源差异及验收条件](docs/coordination/overseas-a1-detector-queries/admission.json)

在仓库根目录运行以下任一查询。配置 `EZVIZ_REGION=overseas`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 和 A1 主机的 `EZVIZ_DEVICE_SERIAL`；仅 `linked` 还需 `EZVIZ_DETECTOR_SERIAL`。需要指定认证入口时可设置 `EZVIZ_AUTH_BASE_URL`。示例仅输出查询名、条数和状态等固定字段。

```bash
python -m examples.a1_detectors --mode sync --query detectors
python -m examples.a1_detectors --mode async --query bindable
python -m examples.a1_detectors --mode sync --query linked
```

### 海外移动侦测联动灯光

`client.settings.get_light_linkage(serial)` 查询移动侦测联动灯光状态，`client.settings.set_light_linkage(serial, enabled=True)` 提交一次写入，异步版本均通过 `await` 调用。两个接口支持托管和子账号，读取要求 `Get` 权限，写入要求 `Config` 权限。请求发往令牌指定并经地区校验的业务域 `areaDomain`，`accessToken` 与 `deviceSerial` 放在请求头。GET 的查询字符串只有 `type=301`，POST 的查询字符串恰好是 `type=301` 与 `enable=1` 或 `enable=0`，`enabled` 仅接受布尔值。两个请求都没有业务正文和 `Content-Type`，海外接口未声明 `channelNo` 或验证码参数，SDK 一律不发送。[海外文档 3722](https://open.ys7.com/help/en/3722) [海外文档 3723](https://open.ys7.com/help/en/3723)

海外 3722 的参数表要求 `type=301`，返回示例却给出 `type=2`，这是官方文档自身的差异，SDK 不据此改写返回值。两个接口的成功响应都要求 `body.meta.code` 为整数 `200`。GET 还要求 `body.data` 为对象，其中 `type` 与 `enable` 必须是非布尔整数，`subSerial` 若存在须为字符串，其余扩展字段原样保留在 `ApiResponse` 中。POST 成功时可以没有 `data`，也可以带任意扩展 `data`，均按原始回执返回。示例 CLI 仅当读回 `type=301` 且 `enable` 为 `1` 或 `0` 时报告 `on` 或 `off`，`type` 不匹配或 `enable` 为未知整数时报告 `unknown`。写入回执中 `meta.code` 为 `200` 仅表示平台受理，不代表灯已物理点亮或生效。写入接口不自动预读、重放、读回或恢复，GET 遇到平台返回 `10002` 且令牌提供方支持刷新时最多刷新并重试一次。

目前未对真实海外网关、硬件、查询、写入或恢复做过验收。在获授权的设备上临时修改开关以验收写入时，应先获得明确批准，记录与请求一致的 `type=301` 读取结果，且原始 `enable` 须为可确认的 `0` 或 `1`，写入后恢复原值并独立复查。类型不符或读数未知时，不得用该结果推导写入值或恢复值。国内 1740 与 1741 在同一路径下另列 `channelNo` 等参数和响应类型，仅适用于各自文档。

本机配置 `EZVIZ_REGION=overseas`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`、`EZVIZ_DEVICE_SERIAL` 后可运行以下示例，需要指定认证集群时可设置 `EZVIZ_AUTH_BASE_URL`。CLI 只执行一次逻辑上的真实只读查询，仅输出 `region`、`mode`、`status`、`selector_matches` 和 `state`。

```bash
python -m examples.light_linkage --mode sync
python -m examples.light_linkage --mode async
```

### 国内设备工作模式计划

读取设备工作模式计划可调用 `client.settings.get_working_mode_plans(device_serial, channel=1, timing_plan_type=17)`，AsyncClient 中以 `await` 前缀调用同名方法。channel 为可选 int，timing_plan_type 可选 17 或 19，省略时由服务端默认取 17。响应里 response.data 是数组，每行包含 `weekPlans` 数组，数组中某个分组可再含 `timePlan` 数组，尽管字段表标注为 Object，eventArg 则作为不透明字符串透传。CLI 读取命令复用本地国内配置并显式要求 EZVIZ_DEVICE_SERIAL，可选 EZVIZ_CHANNEL_NO 与 EZVIZ_TIMING_PLAN_TYPE，只打印 plan_count 便于核对，相关证据仅来自离线记录。接口细节见 [国内文档 1723](https://open.ys7.com/help/1723)

写入仅覆盖华数 4G 工作模式计划，调用 `client.settings.set_huashu_4g_working_mode_plan(device_serial, enabled=True, start_time=start, end_time=end, week=week, stop_after_execution=True)`，AsyncClient 同样以 `await` 调用。enabled、start_time、end_time 与 week 即使在停用计划时也必须提供，`stop_after_execution` 为可选布尔：`True` 发送 `eventArg=1`（执行后停用），`False` 发送 `eventArg=0`（执行后不停用），省略则不传 `eventArg`，平台默认 `0`。该参数独立于海外工作模式 `0–3`，也不重新解释读侧不透明字符串。每次写入都对应一次显式调用，真实的计划下发与恢复需要另行授权。write-demo 完全在内存中以 mock 鉴权与业务响应运行，不触碰真实凭据或设备，只输出 simulated 与 acknowledged 状态。接口约束见 [国内文档 1720](https://open.ys7.com/help/1720)

```sh
python -m examples.domestic_working_mode_plans --mode sync --action read
python -m examples.domestic_working_mode_plans --action write-demo --mode async
```

### 海外设备工作模式计划

`client.settings.get_timing_plan(serial)` 查询设备工作模式计划，`client.settings.set_timing_plan(serial, enabled=True, start_time="08:00", end_time="18:00", week="0,2", mode=1)` 提交一次写入，异步版本中两者均通过 `await` 调用。两个接口均支持托管和子账号，读取要求 `Get` 权限、写入要求 `Config` 权限。SDK 向令牌指定并经地区校验的业务域 `areaDomain` 发送请求，`accessToken` 与 `deviceSerial` 放入请求头。[海外文档 3724](https://open.ys7.com/help/en/3724) [海外文档 3725](https://open.ys7.com/help/en/3725)

读取成功时 `response.data["weekPlans"]` 为按星期分组的对象列表，每组可以包含多个时段，`weekDay` 与各时段的 `beginTime`、`endTime`、`eventArg` 保留原始字符串，官方例示 `eventArg` 形如 `"mode:1"`。文档未列出 `enable`，额外字段原样保留。写入的 `enabled` 仅接受布尔值并映射为 `1` 或 `0`，`mode` 可选且仅接受非布尔整数 0–3，以字符串 `eventArg` 发送，例如 `mode=1` 发送 `eventArg="1"`，省略时不发送。每次显式写入提交一个表单，`enabled=False` 时同样发送 `enable`、`startTime`、`endTime`、`week` 四个字段。`week` 原样传递逗号连接的 0–6，0 表示周日，`start_time`、`end_time` 要求严格 `HH:mm`。3725 提醒，`enable` 对型号 `CS-CB3-R100-2D2WFL` 可能无效。海外写入页未标注媒体类型，SDK 依据国内同路径文档 1720 的 E12、E72 选择 `application/x-www-form-urlencoded`。[国内文档 1720](https://open.ys7.com/help/1720)

写入只执行一次，不自动重发、预读、读回或恢复，成功响应不证明设备物理生效。GET 遇到平台报告令牌失效（`10002`）且令牌提供方支持刷新时，最多刷新并重试一次。时区、跨日、重叠与替换语义、支持型号与恢复方式均未确认，不能从读取结果推导出可无损还原原计划的写入参数。真实海外网关、硬件、查询、写入与效果均未验证。

本机配置 `EZVIZ_REGION=overseas`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`、`EZVIZ_DEVICE_SERIAL` 后可运行以下只读示例，需要指定认证集群时可设置 `EZVIZ_AUTH_BASE_URL`。CLI 仅输出 `region`、`mode`、`status` 和 `week_plan_count`，最后一个字段是返回的分组数。

```bash
python -m examples.working_mode_plans --mode sync
python -m examples.working_mode_plans --mode async
```

### 海外设备告警音模式与试音

`client.settings.get_alarm_sound_mode(serial)` 和 `await async_client.settings.get_alarm_sound_mode(serial)` 读取设备上报的告警音配置。官方列明托管账号及子账号需要 `Get` 权限。SDK 先向配置的海外认证入口获取令牌，再向令牌返回的 `areaDomain` 发送 GET。令牌和设备序列号分别放在 `accessToken`、`deviceSerial` 请求头中，无需通道参数。[海外文档 3726](https://open.ys7.com/help/en/3726)

SDK 按该页字段表和示例采用 `body.meta.code` 为整数 200 的响应结构。读取 `response.data["alarmSoundMode"]` 可取得原始整数。官方示例的注释将 0、1、2 分别定义为短鸣、长鸣和静音。SDK 保留其他整数和扩展字段。读取这个值不会触发试音，不能据此判断设备实际发声。支持的设备型号和真实网关返回的结构仍待验收。[来源及验收边界](docs/coordination/overseas-alarm-sound-mode/admission.json)

本机配置 `EZVIZ_REGION=overseas`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`、`EZVIZ_DEVICE_SERIAL` 后可运行以下示例。需要指定认证集群时可设置 `EZVIZ_AUTH_BASE_URL`。CLI 仅输出地区、模式、结果状态及文档定义的模式码，遇到未知整数时显示 `null`。

```bash
python -m examples.alarm_sound_mode --mode sync
python -m examples.alarm_sound_mode --mode async
```

设置告警音模式调用 `client.settings.set_alarm_sound_mode(serial, 2)`，异步版本写作 `await async_client.settings.set_alarm_sound_mode(serial, 2)`。`mode` 仅接受非布尔的整数类型，作为查询字段 `type` 发送，0 表示短鸣、1 表示长鸣、2 表示静音，这是一次显式的设备配置变更，本身不会自动发出试音调用。[海外文档 3727](https://open.ys7.com/help/en/3727)

试音接口调用 `client.settings.play_audition(serial, 202, volume=1)` 提交一次请求，可能令设备立即开始物理播放，实际是否发声在观察前无法确定，异步同样用 `await` 前缀。`voice_index` 为非布尔整数，作为查询字段 `voiceIndex` 发送，200 表示提醒提示音、201 表示告警蜂鸣音，文档只将 202 定义为静音音色，没有规定它可以中止已开始的播放，也未提供时长或停止控制。`volume` 可选，必须为 1 到 100 的非布尔整数，传 `None` 则省略该字段，平台文档默认值为 1。[海外文档 3730](https://open.ys7.com/help/en/3730)

托管账号与子账号调用这两个接口都需具备 `Config` 权限。两者均为向 `areaDomain` 发起的单次 `POST`，`accessToken` 与 `deviceSerial` 放在请求头中，不带请求体与 `Content-Type`，只要 `body.meta.code` 为整数 200 即返回原始 `ApiResponse`，`data` 允许缺省或包含扩展内容。SDK 不会自动回读配置、设置后试音、失败重试、停止播放或恢复旧值。真实配置写入和实际试音须分别授权，未在真实网关、真实发声或恢复流程上验证。离线 CLI 硬编码内存中的鉴权与业务模拟，不使用账号、配置、设备或网络，仅报告 `mode`、`mode_set` 与 `audition_acknowledged`，与上文的真实获取器 CLI 相互独立。

```sh
python -m examples.alarm_sound --mode sync
python -m examples.alarm_sound --mode async
```

### 国内与海外设备侦测区域

国内官方文档把这项能力称为人形检测区域，海外的 detection area 则按设备能力覆盖画面变化与人形侦测，使用前请确认设备支持情况。SDK 在 settings 命名空间提供读取方法 `client.settings.get_motion_area(device_serial, channel_no=channel_no)` 与写入方法 `client.settings.set_motion_area(device_serial, channel_no=channel_no, area=area)`。AsyncClient 提供同名方法，例如 `await async_client.settings.get_motion_area(device_serial, channel_no=channel_no)`，两种客户端都返回 ApiResponse。device_serial 与 channel_no 是两个必填字符串，SDK 会将其放入请求头发送。[国内文档 1731](https://open.ys7.com/help/1731) [海外文档 3728](https://open.ys7.com/help/en/3728)

国内读取成功后可在 `response.data['area']` 取得整数列表，行数与列数随设备而异，SDK 不提供通用编解码，也不会把它转换成可回写的字符串。调用海外读取会在鉴权之前抛出 ValueError，因为文档 3728 标注的主机是 open.ys7.com，而文档 473 把海外凭证绑定到各自的 areaDomain。SDK 不会把海外凭证发往国内主机。[海外文档 3728](https://open.ys7.com/help/en/3728) [海外文档 473](https://open.ys7.com/help/en/473)

写入时 SDK 把调用方提供的非空白 UTF-8 字符串原样发出，不解析也不改写，国内放在表单字段，海外按所属区域放在查询参数。写入没有预读取、重试、回读或恢复环节。平台返回成功后，设备上的实际覆盖效果仍需单独确认，对真实设备执行写入与恢复请另行取得授权。离线演示中鉴权请求与业务请求都使用 MockTransport，只能展示离线调用流程，不能证明真机配置。运行下面两条命令可分别体验同步与异步调用。[国内文档 1732](https://open.ys7.com/help/1732) [海外文档 3729](https://open.ys7.com/help/en/3729)

```shell
python -m examples.motion_area --mode sync
python -m examples.motion_area --mode async
```

### 国内设备检测灵敏度

设备级移动侦测用 `client.settings.get_motion_sensitivity(device_serial)` 读取、`client.settings.set_motion_sensitivity(device_serial, value=3, channel_no=channel_no)` 写入，`value` 取 0–6 的整数，`channel_no` 为可选整数，省略表示设备本身，SDK 固定发送 `type=0`，子账户最小权限 714 为 Get、715 为 Config 且 Cam 资源限定到设备和通道。714 字段表把 `channel` 列为 String、`value` 列为 Int，官方示例却给出整数 channel 和字符串 value，SDK 两种表示都接受且不强转，读取结果保留各行 `channel`、`type`、`value` 原值与未知字段。见 [国内文档 714](https://open.ys7.com/help/714) 与 [国内文档 715](https://open.ys7.com/help/715)。

按通道的检测灵敏度用 `client.settings.get_detection_sensitivity(device_serial, channel_no=channel_no)` 读取、`client.settings.set_detection_sensitivity(device_serial, channel_no=channel_no, detection_type="3", value="50")` 写入，`channel_no` 为保留前导零的字符串，`detection_type` 只能取 `"3"`（PIR）或 `"4"`（人形），`value` 为 1–100 的十进制字符串，写入前提是设备能力 `support_sensibility_adjust=1`，SDK 不隐式查能力、读旧值或恢复原值，也不提供两族接口之间的量表换算。`AsyncClient` 的 `settings` 有同名方法，调用时加 `await`。见 [国内文档 1733](https://open.ys7.com/help/1733) 与 [国内文档 1734](https://open.ys7.com/help/1734)。

两个写入各只发一次业务请求且失败不重放，平台成功回执不代表设备实际生效或可恢复，需自行核实与还原，两个读取遇 `10002` 可刷新一次令牌,并返回 `ApiResponse`,原始数据行位于 `response.data`。示例离线运行，认证与四个业务传输全部本地模拟，不触达真实设备，行为对应 [国内文档 714](https://open.ys7.com/help/714)、[国内文档 715](https://open.ys7.com/help/715)、[国内文档 1733](https://open.ys7.com/help/1733)、[国内文档 1734](https://open.ys7.com/help/1734)。

```sh
python -m examples.detection_sensitivity --mode sync
python -m examples.detection_sensitivity --mode async
```

## 海外用户 OAuth

`ezviz_unified.oauth` 导出 `OAuth`、`AsyncOAuth` 与结果类型 `OAuthGrant`、`OAuthRefreshResult`，构造形如 `OAuth(app_key, auth_origin=…, api_origin=…)`。调用方须事先分别核实两个不同的区域 HTTPS origin，`auth_origin` 只绑定认证服务域名，`api_origin` 只绑定业务服务域名，本模块不推导这对 origin，不启动监听器，也不自动打开浏览器。

`build_login_url` 只在本地拼装固定全球登录地址 `https://openauth.ezvizlife.com`，`area_id` 为正整数，表示用户注册的国家或地区，可通过 `client.settings.list_areas` 查询，演示用的 `area_id` 是合成占位，不代表区域映射。`client_type` 区分 PC `19` 与移动端 `11`、`13`，仅移动端必须提供 `bundle_id`，`response_type` 可选 `token` 或 `refresh_token`。应用把生成的地址呈现给用户浏览器，SDK 不执行导航。交互完成后由宿主应用把实际得到的回跳地址交给 `parse_redirect`，它仅接受已配置 `auth_origin` 下的 `/oauth/success`，拒绝任何 `query` 包括空 `query`，只解码 `fragment`。结果须含非空 `access_token`，选 `refresh_token` 时还要求返回非空 `refresh_token`，解析本身不发起 §1.3 刷新。解码后若回跳携带 `areaDomain` 则与 `api_origin` 显式比较，若携带 `authDomain` 则与 `auth_origin` 显式比较，未知字段在解码后原样保留。

```bash
python -m examples.oauth_login --mode sync
python -m examples.oauth_login --mode async
```

`AsyncOAuth` 的两个方法均可 `await`，只做本地计算。示例全程使用合成数据，只输出 `mode` 字符串与 `login_url_built`、`redirect_parsed` 两个布尔值，不读取账户配置，不传输或持久化令牌。

`OAuthGrant` 暴露 `access_token`、`refresh_token`、`open_id`、`expires_in`、`area_domain`、`auth_domain` 具名字段，以及保存全部解码字段的不可变 `fields`。`repr` 隐藏凭据与扩展值，直接读字段仍属敏感操作。`expires_in` 如出现按非负整数保留，单位未经证实，不换算 TTL。`expected_state` 仅在调用方提供时做本地比对，登录请求没有官方记录的 `state` 透传，企业或移动端自定义回调协议亦未获验证。本交付未执行真实用户登录，也未验证真实令牌签发。`appSecret` 或既有新加坡只读设备授权均不能建立用户登录。

### 海外用户令牌刷新

请按应用、用户和地区保管刷新令牌与用户标识。同步调用 `oauth.refresh_token(saved_token, open_id=saved_id, bundle_id=bundle)`，异步调用 `await oauth.refresh_token(saved_token, open_id=saved_id, bundle_id=bundle)`。SDK 只向构造时预置的海外 `auth_origin` 发送一次刷新请求（完整 HTTPS 校验，不跟随重定向、不重试），`client_id` 取构造时的 `app_key`。表单无需旧访问令牌或开发者密钥。`scope`/`state` 为 `None` 时省略，显式空串保留。传入的底层 transport 在本次调用结束时关闭。

`OAuthRefreshResult` 提供`access_token`、`refresh_token` 及可选 `open_id`、`expires_in`、`scope`、`state`。返回的 `open_id` 取自响应的 `data.openId`。`expires_in` 保留原整数，官方资料未指明时间单位。显式传入 `state` 时必须精确匹配，缺失或不匹配会触发协议错误。此时服务端可能已经处理刷新，SDK 不会自动重试或覆盖已保存的凭据。请求采用 E319 示例的 `open_id`，同时按 E269 参数表发送必填的 `bundleId`。参数表把用户标识写作 `openId`，示例又省略了 `bundleId`，服务器对该组合的实际兼容性仍待验收。可运行 `python -m examples.oauth_refresh --mode sync` 或 `--mode async`（MockTransport，无需真实凭据）。[官方文档 469 §1.3](https://open.ys7.com/help/en/469)

## 海外 DDNS 只读查询

三个查询均可通过 `Client.ddns` 和 `AsyncClient.ddns` 调用。海外业务请求发往认证返回的 `areaDomain`，使用表单中的 `accessToken`，沿用统一的错误分类与只读重试。

```python
response = await client.ddns.list(page_start=0, page_size=1)
rows = response.data
page = response.raw.get("page")
```

- **选择器**：`device_serial` 与 `domain` 至少提供一个；同时给出时两者都发送，由服务端按序列号优先查询。
- **范围**：`list` 为账号 DDNS 列表（含分享设备）；`share_list` 仅含分享设备，与 `list` 可重叠。
- **本地校验**：选择器须为非空白 UTF-8 字符串，保留大小写与原文，不解析域名、不探测映射端口；`page_start` 非负、`page_size` 限 1..50，拒绝 bool；校验与编码在认证前完成，一次只取一页。
- **分页与响应**：省略分页参数时服务端默认 0/10。`get` 的 `data` 为对象，`list`/`share_list` 的 `data` 为对象数组（可为空）；`ApiResponse` 保留 `data` 与 `raw`，同层 `page` 对象经 `response.raw.get("page")` 原样透传，未知字段保留、缺省不补造；`page.size` 不作为翻页依据。

CLI（源码内模块 `examples.ddns`，无独立命令）：

```bash
# 在本机配置 EZVIZ_REGION=overseas、EZVIZ_APP_KEY 和 EZVIZ_APP_SECRET
python -m examples.ddns --mode async --query list   # 固定 page_start=0,page_size=1
# 单条查询还需要 EZVIZ_DEVICE_SERIAL 或 EZVIZ_DDNS_DOMAIN
python -m examples.ddns --mode sync --query get
```

仅输出 `region`/`mode`/`query`/`status` 四个键，不显示返回的网络/设备描述符与凭据。海外域未经授权与实连验证，以上为离线范围说明。

[海外官方文档 §1.4、§1.8、§1.11](https://open.ys7.com/help/en/469)

## 告警与云存储

`client.alarms.list(AlarmQuery(status=2))`查询全部状态告警，`for_device(serial, query)`按设备查询；不自动翻页、标记已读或下载图片。省略status时由平台默认查询未读。`client.cloud_storage.info`默认国内v2、海外v1，另有套餐类型、开通状态查询及显式开通/兑换/暂停/试用/转移方法。服务变更不自动重放，也不自动轮询或更换付款方式。版本、时间、权限和原文缺项见[告警与云存储说明](docs/research/2026-11-14-alarms-cloud-storage.md)，未产生实际订单或服务变更。

## 消息回调

模块级`parse_webhook`使用原始UTF-8正文、`t`、`Signature`与单独配置的`webhook_secret`先核验MAC再解析；`parse_unverified_webhook`是显式不做认证的入口。返回值保留未知body字段与类型，`ack()`只构造messageId字典，HTTP响应和去重由应用负责。签名匹配不证明当前发送者身份或消息新鲜度；本批依据国内归档协议离线验证，未接收真实回调，也不自动使用AppSecret、配置消息服务或下载媒体。详见[消息助手](docs/research/2026-11-15-webhook-helpers.md)及[源资料差异](docs/research/2026-11-15-message-ingestion.md)。

仓库内另附一个把这些纯助手接到本地有界HTTP链上的[独立接收示例](docs/webhook-receiver.md)：需在仓库checkout内显式启动，受理范围仅是配置签名secret的明文JSON投递（signed-only是本示例接入策略，非官方必选）。上述纯helpers本身的语义不变，也不能称它们已借此真实接入平台。平台消息/设备事件的国内与海外验收状态各自单列，见[验收登记](docs/validation/sdk-product-acceptance.json)中的webhook行；当前均未标完成。

在仓库根目录运行 `python -m examples.overseas_device_online` 可用两次合成 HTTP 投递验证海外上线通知的签名、ACK 和应用取件。示例仅监听本机临时端口，打印计数后退出。[海外设备上线事件示例](docs/webhook-receiver.md#海外设备上线事件)

## 统一存储

统一存储入口为`client.storage`，提供空间列表/创建/更新/删除、文件搜索/保留期修改及签名下载地址查询，目前仅国内。文件查询使用`StorageFileQuery`，保留官方选择器优先级；`None`不发送，更新不注入创建默认值。统一存储永久保留使用`-1`，与旧云录制空间的`0`不同。下载地址仅返回，不获取媒体；[完整规则与来源限制](docs/research/2026-11-14-unified-storage.md)列明单位、编码推导及权限，全部新能力仍为离线验证。

## 云录制2.0

云录制2.0单列为`client.cloud_recording`，提供空间/一次性计划/任务查询、录像日历、片段与HLS信息、模板列表等10个只读入口，另有一次性计划创建/更新/停止/删除与即时计划创建共5个写入口，目前仅国内。时间使用整秒naive datetime；不自动翻页、下载、轮询或串联写操作；写接口需显式调用，可能创建真实录制并消耗套餐。详见[云录制约束](docs/research/2026-11-14-cloud-recording.md)与[计划管理写接口](docs/research/2026-11-15-cloud-recording-plans.md)，不等同于设备云存储套餐或本地录像。

## 云录制1.0与云点播

旧版云录制1.0文件操作单列为`client.cloud_recording_v1`，仅国内：`unfreeze_file(project_id, file_id)`触发归档解冻（写操作，不自动重放；仅HTTP成功且业务meta.code=200的响应证明触发已被受理，不证明解冻完成——HTTP 200本身可能携带业务失败，不能单独据此推导）、`file_info(project_id, file_id)`查询单个文件详情、`download_urls(project_id, file_id, content_type=..., expire_seconds=...)`获取官方下载/播放地址（不下载媒体）。云点播文件列表单列为`client.vod`，`files(file_node_ids=None, space_id=..., space_name=...)`至少需一个选择条件，仅国内。两套接口的`projectId`/`fileId`/`fileNodeId`互不相同，不做映射；详见[云录制1.0与VOD说明](docs/research/2026-11-18-cloud-recording-v1-and-vod.md)，本批接口均未做真实调用验证。

`client.vod`另提供四个仅国内的转封装与查询入口，同步/异步共用同名方法：`create_remux_task`（官方明确非幂等的异步任务创建，不自动重放，仅返回`ApiResponse`，不变成任务句柄）、`task`与`tasks`（只读的单任务查询与任务分页列表，不自动翻页或轮询）以及`recording_urls`（按设备、通道与时段签发录像地址；其`read_only=False`是SDK为禁自动重放的选择，来源未明确无副作用，非官方写/非幂等结论）。官方声明的范围/权限边界包括：`create_remux_task`限企业用户、不支持托管/子账号且需已有对应时段云录像；`recording_urls`要求设备开通云存储且对应时段有录像、同样不支持托管/子账号；两个查询接口的来源仅明确accessToken，额外账号或套餐权限未声明，由服务端判定；SDK不把这些前提互相移植。全部参数校验、Header字符串规则与时间边界见[云点播转封装说明](docs/research/2026-11-18-vod-remux.md)；任务状态编码、地址字段名与`fileNodeId`/`taskId`各自独立，SDK不发明映射或完成判据。本批四个入口仍未做真实调用验证；各能力的验收状态见[产品能力登记](docs/capabilities/README.md)与[验收登记](docs/validation/sdk-product-acceptance.json)中对应`candidate:domestic:*`行，登记数量不代表覆盖率或产品级验收通过。

## 海外 FLV 预览

本地示例使用海外 SDK 的 FLV 预览参数（`protocol=4`），由官方 Web 播放器播放合成 H.264 画面。真实媒体地址签发和观看需另行授权。操作步骤详见 [FLV 本地预览指南](docs/official-flv-preview.md)。

## 官方 HLS 播放器接入

源码示例串联 `Client.playback.create` 和官方 `@ezuikit/player-hls@2.0.0`。默认使用 H.264，也可用 `--codec h265` 播放自行生成的 HEVC 测试片，验证官方 WASM 解码和 WebGL 画面。示例使用合成地址，真实设备预览另需授权。运行方式及可复现的浏览器验证见[HLS 预览指南](docs/official-hls-preview.md)。

## 国内设备 OSD 文字

同步和异步客户端均通过 `client.osd` 访问以下接口。

| 方法 | 路径 | 只读 |
| --- | --- | --- |
| `get_name(device_serial)` | GET `/api/v3/device/osd` | 是 |
| `set_name(device_serial, osd_name, *, channel_no=None)` | POST `/api/lapp/device/update/osd/name` | 否 |
| `set_lines(device_serial, *, osd_names, channel_no=None)` | POST `/api/v3/device/osd/multi/set` | 否 |

`get_name` 返回设备级名称，`response.data["osd"]` 可以是空字符串。它没有通道参数，无法确认任意通道的旧名称或多行配置。[读取名称（1721）](https://open.ys7.com/help/1721)

`set_name` 要求设备支持 `support_osd=1`。设备序列号中的英文字母须大写；SDK 拒绝空白名称，保留有内容字符串的原始空格和 Unicode。子账号需要目标通道的 `Config` 权限。[设置名称（725）](https://open.ys7.com/help/725)

`set_lines` 使用设备级 `Config` 权限，支持托管账号和子账号。请显式提供 `osd_names`。字符串原样发送，多项内容按官方约定用英文逗号分隔；`None` 省略该字段，`""` 发送空字段。官方未说明两者是否清屏。两种写入的 `channel_no` 均为可选字符串，省略时由平台取默认值 1。写入失败不会自动重发；真实修改前须能恢复所选通道的原始配置。[多行配置（5201）](https://open.ys7.com/help/5201)

本机设置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 和 `EZVIZ_DEVICE_SERIAL` 后，可从仓库运行只读示例：

```bash
python -m examples.osd_name --mode sync
```

示例只报告地区、模式、状态和名称是否非空。三项 SDK 行为均通过模拟传输验收，实际画面与配置恢复尚待真实设备验证。

## 国内智能设备的算法清单

`DeviceAlgorithms` 与 `AsyncDeviceAlgorithms` 提供同名的两个独立只读查询；异步版本仅增加 `await`，两个查询不会互相触发。

```python
# 列出一页智能设备及其已加载模型。
devices = client.device_algorithms.list_devices(page_start=0, page_size=8)

# 使用已明确选择的设备查询支持清单。异步客户端的同名方法使用 await。
supported = client.device_algorithms.list_supported(device_serial)
```

- `list_devices` → `GET /api/v3/intelligent/model/device`（731）：`device_serial`、`page_start`、`page_size` 均可省略；`device_serial` 是搜索关键字，并按 731 正式参数表与任一分页参数互斥。`page_start` 非负，缺省时由服务端取 0；`page_size` 限 8–50，服务端默认大小未说明。客户端不自动分页。
- `list_supported` → `GET /api/v3/intelligent/model/device/support`（730）：`device_serial` 指定设备，为非空白 UTF-8 原文；官方未说明长度、大小写或字符集限制，客户端不改写。“supported” 不表示算法已加载或正在运行。
- 请求：实现按参数位置 `Body` 及官方 `curl -X GET --data-urlencode` 的实际语义发送 GET 表单体，并显式设置 `Content-Type`；`accessToken` 仅放在 header。730 另以 `params` 描述，731 示例又与其互斥规则冲突；本文保留这些来源差异。已知代理或服务端可能不同地处理 GET body，SDK 沿用只读认证重试策略，不因参数位置争议而补发第二种请求；真实环境测试仍待补充。
- 响应：仅将顶层 `meta.code` 为整数 `200` 视为成功，并使用 `meta.message`；保留 `data`、`meta.moreInfo` 及未知字段。仅校验 `data` 为对象数组、允许字段缺失，不解释 `loadStatus`，也不依据单行推断全部设备。
- 区域与校验：两种模式共享现有只读认证；海外区域或无效输入在认证前失败。方法不做自动发现、算法下发、启停或其他推断。

CLI 使用本机的 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY` 和 `EZVIZ_APP_SECRET`；`supported` 另需本地 `EZVIZ_DEVICE_SERIAL`：

```bash
python -m examples.device_algorithms --mode async --query devices
python -m examples.device_algorithms --mode async --query supported
```

示例单次执行显式选择的查询，仅输出固定 JSON 状态（`region`、`status`、`mode`、`query`）。

来源：[设备算法支持查询（730）](https://open.ys7.com/help/730)、[设备算法列表查询（731）](https://open.ys7.com/help/731)

## 国内设备客流统计

客流统计走国内 LAPP 表单 POST，`accessToken` 放在请求体，成功以字符串 `code="200"` 判定，复用统一认证/响应策略。六个方法中 `switch_status`、`daily`、`hourly`、`get_config` 为只读，`set_switch`、`set_config` 会改变设备状态。[客流开关状态](https://open.ys7.com/help/688)

| `client.passenger_flow` 方法（AsyncClient 同名，前加 `await`） | 类型 | 返回 `data` 形态 | 通道参数 |
|---|---|---|---|
| `switch_status(device_serial)` | 只读 | 对象 | 无 |
| `set_switch(device_serial, *, enable, channel_no=None)` | 写 | 应答（`data` 可缺） | 可选 |
| `daily(device_serial, channel_no, *, day=None)` | 只读 | 对象 | 必填 |
| `hourly(device_serial, channel_no, *, date_ms=None)` | 只读 | 数组 | 必填 |
| `set_config(device_serial, *, line, direction, channel_no=None)` | 写 | 应答（`data` 可缺） | 可选 |
| `get_config(device_serial, *, channel_no=None)` | 只读 | 含 `line`/`direction` 的几何对象 | 可选 |

序列号保留原串；通道为整数（显式拒绝 `bool`，不猜取值上下界）；可选通道与日期仅在显式提供时才发送，省略不会被替换为 0。

只读示例（同步客户端；仅选择日报、明确目标设备/通道，不预检能力、不改变配置）：

```python
from datetime import datetime, timedelta, timezone

# day 必须是自带时区的当地午夜；SDK 用显式 UTC epoch 差精确换算毫秒
resp = client.passenger_flow.daily(
    "SYNTHFLOW001",
    1,
    day=datetime(2019, 6, 21, tzinfo=timezone(timedelta(hours=8))),
)
```

`day` 可省；提供时仅接受 tz-aware 的当地午夜 `datetime`，不使用主机时区，偏移无法精确表示为毫秒会被拒绝。`hourly` 的 `date_ms` 是可省的有符号 64 位整数毫秒。日报约于次日凌晨 2 点更新（以平台为准）；`hourly` 数组原样透传，不承诺固定行数，不补齐、不求和。[客流日报](https://open.ys7.com/help/690) [客流小时报](https://open.ys7.com/help/691)

仓库示例读取常规国内认证配置，还需显式设置 `EZVIZ_DEVICE_SERIAL` 和 `EZVIZ_CHANNEL`。运行时只返回地区、报表种类、模式和状态。

```bash
.venv/bin/python -m examples.passenger_flow --mode sync --report daily
```

日报可提供 `EZVIZ_FLOW_DAY`（例如 `2019-06-21T00:00:00+08:00`），小时报可提供 `EZVIZ_FLOW_DATE_MS`（毫秒整数）。两者省略时使用平台默认日期。示例也支持 `--mode async` 与 `--report hourly`。

`set_config` 的 `line` 与 `direction` 均为恰好含 `x1`/`y1`/`x2`/`y2` 四个字符串键的 `Mapping[str, str]`。`line` 取值须为 `[0,1]` 内 ASCII 非负普通十进制文本、至多 7 位小数、不强制补零；`direction` 为四个 ASCII 有限普通十进制字符串，原样保留、不套用 `line` 的范围与精度约束。SDK 按固定键顺序生成紧凑 JSON，交表单编码器仅编码一次（不预百分号编码、不双写、不回读暗改）；该单次编码是 SDK 侧的选择，设备端接受情况尚未实测。以下为合成示例值：

```python
line = {"x1": "0.0", "y1": "0.5", "x2": "1.0", "y2": "0.5"}
direction = {"x1": "0.0", "y1": "0.0", "x2": "0.5", "y2": "0.0"}
```

[客流配置写入](https://open.ys7.com/help/692)

写接口已在 SDK 实现但不自动重放（含 `10002`、超时、`60022`），成功应答允许缺 `data`。恢复差异：`switch_status`（688）不接受 `channelNo`，而 `set_switch`（689）可带可选通道；`get_config`（693）返回同一目标（设备+通道）当前的 `line`/`direction`，写前可先读取旧几何作为恢复依据。设备须支持客流统计。子账户读取使用 `Get`、设置使用 `Config`。688/689 声明设备级 `dev` 资源，690–693 声明通道级 `Cam` 资源。当前验收为离线模拟。真实配置须另行指定设备、目标通道、写入范围和可核实的恢复方法。[客流开关设置](https://open.ys7.com/help/689) [客流配置读取](https://open.ys7.com/help/693)

## 国内开发者设备添加链接

通过国内 `Client` 或 `AsyncClient` 的 `client.device_add_links` 可调用 `create`、`records`、`list` 三个方法，仅面向国内区域。请求头 `accessToken` 必须来自国内开发者账号，账户有余额并不能证明已经开通开发者权限。

### 签发

`create(expire_days, *, note=None)` 将整数 `expire_days`（取值 1–7）映射为 JSON 整数 `expireTime`。`note` 可省略或为最多 256 个 Unicode 字符的字符串，允许空串，原样提交不做规范化。每次调用只执行一次 POST，不自动重试。成功要求根层 `meta.code` 为整数 200，且 `data.url` 为非空字符串。SDK 不打开、不发送也不校验链接能否完成绑定，平台未确认吊销能力。

### 单页查询

`records(*, device_serial=None, cursor=None, page_size=10)` 查询添加记录，`list(*, cursor=None, page_size=10)` 查询链接列表。两者都是单次 GET 单页请求，`cursor` 为排他字符串 id，为 `None` 时省略并由平台决定起点，显式字符串原样提交。`pageSize` 为整数 1–50，默认 10，不接受 bool。仅 `records` 支持 `device_serial`，经 `deviceSerial` 请求头发送并复用既有 ASCII 校验，筛选与游标同时存在时两者都发送，优先级未定义。满足条件的 10002 只读失败最多重试一次，绝不自动翻页。

`records` 行 id 为整数，取下一页需显式传入 `str(last_id)`，而 `list` 行 id 为字符串，均不保证稳定排序。`list` 行内 `expireTime` 为毫秒时间戳整数，`expired` 若存在为布尔，`url` 若存在可为字符串或 `null`。官方资料没有规定它与 `expired` 的对应关系。两者返回 `response.data` 列表并保留未知行字段。`raw` 与 `data` 属调用方持有的敏感数据。

### 脱敏与日志

这三个入口显式启用默认关闭的 `ResponsePolicy.redact_device_add_details`。SDK 显示的非空业务消息和异常中的上游自由文本统一替换为 `***`，整数码保持原值，字符串码仅透出白名单 200、400、403、404、500、10002、10031，其余遮盖，分类仍按原始码进行。这三个 SDK 请求的标准 HTTPX INFO 日志保留方法、去查询串的路径与状态码，并遮盖 reason phrase，不影响其他 HTTPX 请求或应用自行记录 `raw`。

### CLI 示例

设置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 后运行

```bash
python -m examples.device_add_links --mode sync --query records
python -m examples.device_add_links --mode async --query links
```

CLI 查询一页（`pageSize=1`），输出的 JSON 仅含区域、模式、查询类型、状态和返回条数。该示例只读。

本批离线验收使用合成响应，真实开发者权限、签发及记录可见性仍待核验。[签发接口](https://open.ys7.com/help/2846) · [添加记录](https://open.ys7.com/help/2847) · [已签发链接](https://open.ys7.com/help/2848)

## 国内国标 License 列表

调用 `client.third_party_protocols.list_gb_licenses(product_key, page_index=0, page_size=1)` 分页查询国内设备的国标 License 列表，`AsyncClient` 提供同名方法，以 `await` 调用，一次调用只返回一页数据。底层是 GET 请求，请求体按 `application/x-www-form-urlencoded` 编码，必带 `accessToken` 与 `productKey`，可选的 `pageIndex` 和 `pageSize` 省略时服务端分别按默认值 0 和 10 处理，`pageIndex` 须为非负整数，`pageSize` 取值范围 1–50。响应字段、错误码与完整示例见 [国内文档 671](https://open.ys7.com/help/671)。

SDK 直接接收 `product_key`，无需环境变量，仅 CLI 在已获授权的境内配置下读取 `EZVIZ_PRODUCT_KEY`，不涉及设备序列号。671 没有文档载明最低授权要求，企业版、子账号或托管前提均未确认，也不能从四个独立协议端点推断。CLI 以第零页、页大小一发起请求，仅打印 `returned_items` 为 0 或 1，因为原始行可能含设备 License 与标识符。现有证据仅为合成离线数据。

```sh
python -m examples.gb_license_list --mode sync
python -m examples.gb_license_list --mode async
```

## 国内企业第三方协议接入

官方快速入门要求国内账号已购买企业版。每个账号按协议开通项目。同步客户端使用 `client.third_party_protocols`，异步客户端使用 `await async_client.third_party_protocols.…`。协议类型为 `dhreg`、`tiandy`、`huawei` 或 `jt808`。完整签名、参数和资料差异见[冻结契约](docs/coordination/domestic-third-party-protocols/admission.json)及[官方快速入门](https://open.ys7.com/help/5322)。

| 方法 | 用户可获得的结果和边界 |
|---|---|
| [`open_project(protocol)`](https://open.ys7.com/help/5364) | 按协议开通项目，返回项目标识与创建时间。该 POST 要求表单类型请求头，业务正文为空。重复开通和撤销的效果未说明。 |
| [`get_project(protocol)`](https://open.ys7.com/help/5365) | 查询项目及接入地址。端口在字段表为字符串、示例为整数；SDK 保留原值，不连接返回的地址。 |
| [`apply_device(protocol, device_name, …)`](https://open.ys7.com/help/5366) | 单台申请成功后从 `response.data["deviceSerial"]` 取得序列号。设备仍需配置并完成注册，才能确认在线。 |
| [`apply_batch(protocol, …)`](https://open.ys7.com/help/5367) | 按数量或设备编码序列申请，返回整数 `taskId`。批量上限 300；提供编码时按编码数量提交。只有平台接受申请才能获得任务 ID。 |
| [`get_task(protocol, task_id)`](https://open.ys7.com/help/5368) | 查看申请结果。状态 9 表示成功；状态 10 时，成功数 `deviceCount` 等于 0 表示全部失败，大于 0 表示部分成功。其他状态原样保留。 |
| [`list_devices(protocol, …)`](https://open.ys7.com/help/5369) | 查询一页注册设备。行数组位于 `response.data["data"]`。页大小 1–50，默认 10；如要继续，显式传入平台返回的 `nextIndex` 作为下一次 `start_index`。返回 `null` 表示末页。 |
| [`edit_device(protocol, device_serial, …)`](https://open.ys7.com/help/5370) | 修改指定设备的注册信息。设备名称见于功能说明和示例，正式参数表未列；仅设置 `device_name` 时才发送该字段。更换凭据可能中断真实设备的接入。 |

三项申请或修改接口的正式参数表使用表单正文，官方 cURL 示例却把部分字段放入 query。SDK 只按表单提交。访问令牌放入请求头。显式提供的 `device_password` 对应 `deviceLicense`，`device_username` 对应 `deviceUserName`。非 JT808 的单申请需要二者，JT808 单申请不填二者。批申请允许省略或传空，显式指定的凭据可能用于整批。JT808 的车牌和颜色必须成对提供，批量列表按调用方给定的顺序提交。能否指定设备编码取决于项目的协议能力。四项写操作不会自动重发；三项只读调用收到令牌过期码 `10002` 时，可刷新的令牌提供方最多刷新并重试一次。

单页注册设备数据可能含设备密码和车辆信息。成功回执的 `.data` 和 `.raw` 供有权限的应用显式使用，请勿将它们整体写入日志。以下示例在本机配置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 后只发起一次逻辑项目查询，终端仅显示地区、模式、协议和结果状态。

```bash
python -m examples.third_party_project --mode sync --protocol tiandy
python -m examples.third_party_project --mode async --protocol tiandy
```

## 国内云直播流管理

同步客户端通过 `client.live_streams` 调用，异步客户端通过 `await async_client.live_streams.…` 调用。访问令牌由 SDK 放入请求头。国内主机为 `open.ys7.com`；修改、删除、启停及推流记录四页只给出相对路径，主机依据同组绝对地址核定。完整签名、参数位置及来源差异见[接口清单](docs/coordination/domestic-live-streams/admission.json)。

| 方法 | 请求和结果 | 来源与限制 |
|---|---|---|
| `create_device(device_serial, *, start_time, end_time, local_index=None)` | 序列号放在 header，POST 表单固定 `accessType=1`，返回 `data.streamId`。通道可省略，默认 1。 | 支持具有 Real 权限的应用、托管或子账号令牌。时间窗至多 7 天。[4060](https://open.ys7.com/help/4060) |
| `create_rtmp(*, start_time, end_time, …)` | POST query 固定 `accessType=2`，返回字符串流 ID。是否录制、空间、分段时长、格式和延时均按显式参数发送。 | 列明具有 Real 权限的应用及托管令牌。正式表要求发送时间，但其说明只对设备接入生效，cURL 又省略 query。SDK 选正式字段位置。[4433](https://open.ys7.com/help/4433) |
| `update_window(stream_id, *, start_time, end_time)` | PUT query 更新明确选定的流，仅要求平台确认回执。 | 参数表写 720 天上限，错误表写 7 天。SDK 校验格式和先后，具体上限由平台裁定。[4062](https://open.ys7.com/help/4062) |
| `delete(stream_id)` | DELETE query 使用字符串 ID。 | 原 ID、已签发地址、归档及费用能否恢复，文档未说明。真实清理需指定可删除的流。[4063](https://open.ys7.com/help/4063) |
| `set_status(stream_id, status)` | PUT query，整数 0 禁用、1 启用。 | 平台确认收到变更，实际媒体通断仍需独立验证。[4065](https://open.ys7.com/help/4065) |
| `list(*, device_serial=None, stream_id=None, page_start=0, page_size=50, access_type=None, status=None)` | 单页列表，序列号筛选可省略，若提供则放入 header；其他字段为表单。结果数组在 `response.data`，分页在原始 `meta.moreInfo`。 | 声明为 GET，cURL 用 POST 表单；SDK 采用 POST。保留条目中的原始 ID 类型。[4066](https://open.ys7.com/help/4066) |
| `records(stream_id, *, start_time, end_time, page_start=0, page_size=50)` | GET query 按整数流 ID 查询推流记录，结果在 `response.data["recordList"]`。 | 显式发送时间及分页。间隔不超过 30 天，是否在服务端最近 30 天内由平台裁定。记录可能含推流客户端信息。[4068](https://open.ys7.com/help/4068) |
| `address(stream_id, protocol, *, address_type=1, …)` | POST 表单按整数 ID 签发地址，取 `response.data["address"]`。协议 1/2/3 对应 HLS/RTMP/FLV。默认类型 1 取播放地址，类型 2 取 RTMP 接入流的推流地址。 | 标题写 GET，正式方法和示例均为 POST。过期秒数适用于 RTMP **接入**流，最大 730 天。SDK 不连接或打开地址。[4064](https://open.ys7.com/help/4064) |

RTMP 建流的 `enable_record` 取 0 或 1，`record_duration` 为 900 至 7,200 秒，`record_format` 取 1（MP4）或 2（M3U8），`delay_time` 为 15 至 21,600 秒。未提供的录制选项不进入请求。取地址时，`quality` 取 1（高清）或 2（流畅），`support_h265` 和 `mute` 取 0 或 1；官方资料说明这三项对 RTMP 直推流不生效。

时间参数使用无时区、秒级精度的 `datetime`。SDK 保留创建返回的不透明字符串 ID。声明为整数的入口需要调用者明确提供相应整数 ID。成功码为整数 `meta.code=200`；SDK 校验业务数据的层级并保留扩展字段。列表与推流记录可在令牌过期时刷新并重试一次；创建、修改、启停、删除和签发地址均不自动重发。

单页 CLI 使用本机的 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY` 和 `EZVIZ_APP_SECRET`，只输出条数。离线示例使用固定 MockTransport，在同一个模拟流上依次执行创建、改时间、禁用及删除，后续三步都使用创建返回的 ID。

```bash
python -m examples.live_streams --mode sync
python -m examples.live_streams --mode async
python -m examples.live_stream_lifecycle
```

播放和推流地址都可能授予访问权限。取地址操作会在呈现的 SDK 消息、错误和该业务请求的标准 HTTPX 日志中遮盖 HTTP(S)、RTMP(S) 地址；应用仍可取得原始 `.data` 和 `.raw`。请仅记录业务结果或条数，避免输出原始数据、地址和推流记录中的客户端信息。

## 国内云直播归档查询

`client.vod.stream_source_files(stream_id, *, file_type, query_date=None)`（异步版本为 `async_client` 同名方法）使用请求头 `accessToken` 鉴权。`stream_id` 为不透明标识；`file_type=1`（录像）必须提供规范日期 `YYYY-MM-DD`，`file_type=2`（截图）可省略。返回 `.data["fileInfo"]` 数组，创建和更新时间保留原始字符串。官方明确该接口不支持子账户。[源文件查询](https://open.ys7.com/help/4751)

`client.vod.stream_index_file(stream_id, *, start_time, stop_time)` 接受不带时区、整秒的 `datetime`，开始与停止的差值允许为 0 至 24 小时，可跨午夜。`.data["fileInfo"]` 为对象，可能含敏感 `fileUrl`。SDK 不访问该地址。官方未说明这一接口的子账户权限和地址有效期。成功的原始响应完整返回给调用方；示例和本地诊断只记录有限字段。错误消息中的 HTTP(S) 地址会被遮蔽。[点播索引查询](https://open.ys7.com/help/4757)

```python
info = client.vod.stream_source_files("SYNTHETIC-STREAM", file_type=1, query_date="2026-01-01")
files = info.data["fileInfo"]
```

示例程序读取常规配置及 `EZVIZ_STREAM_ID` / `EZVIZ_QUERY_DATE` / `EZVIZ_STREAM_START` / `EZVIZ_STREAM_STOP`，`--kind` 取 `recording|snapshot|index`：

```bash
.venv/bin/python -m examples.stream_archive --mode sync --kind recording
```

存储对象过期或被删除后，即使已有节点 ID，也可能取不到文件。真实归档验收需另行确认可读的流和时间段。

## 国内账号取流并发限制

使用未欠费的 E 套餐主账号，可通过 `client.live_limits` 查询已配置的规则。同步和异步客户端均有 `get(live_type)`、`set(live_type, *, limit_type, limit_number)` 和 `delete(live_type)` 三个入口。查询结果只描述配置，实际并发用量需另行观测。

- 三种操作共用 `/api/v3/mall/online/user/limit/live`，令牌放在请求头中。GET 通过查询串发送 `liveType`，POST 和 DELETE 使用表单请求体。
- jsdecode 和私有流使用 `VTDU`，HLS、RTMP 和 FLV 使用 `RTMP`。参数区分大小写。
- 更新规则时，`limit_type=0` 按通道限制，`limit_type=1` 按用户限制。`limit_number` 要求整数，官方未给上下限。两个整数参数均在认证前校验，均拒绝布尔值。
- SDK 依据正式码表将整数 `meta.code=200` 判为成功。三个官方示例都写作 `0`，当前 SDK 遇到该值会抛出 `ApiError`。查询字段表列出规则对象数组，示例却给出 `[[]]`。SDK 原样返回数组，真实服务的响应形态待验证。
- 删除请求只指定 `live_type`。平台返回 `10005` 表示没有可删的限制，SDK 会保留该业务错误。官方未说明删除后会回退到什么配置。两个写入口出错后均不会自动重发。

```python
response = await client.live_limits.get("VTDU")
rules = response.data
```

在本机配置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY` 和 `EZVIZ_APP_SECRET` 后，可从仓库运行只读示例。

```bash
python -m examples.live_limits --mode async --live-type VTDU
```

示例输出仅含地区、模式、取流类型及状态。实际账号配置的更新或删除需要单独授权，并确认同一类型下受影响的范围、原值及恢复方法。[查询（1816）](https://open.ys7.com/help/1816) · [添加或更新（1817）](https://open.ys7.com/help/1817) · [删除（1818）](https://open.ys7.com/help/1818)

## 国内云直播流量查询

面向已认证的国内开发者主账户：`Client`/`AsyncClient` 使用客户端的既有认证配置注入 accessToken，查询方法无需接收 token。来源注明子账户 token 无权，且四页均未给出子账户权限的专用错误码。当前验证覆盖模拟传输；账号权限和实际用量仍待真实查询。

| 方法 | `.data` 内容 |
| --- | --- |
| `traffic.account_summary()` | 对象，含总流量 `totalFlow`、已用流量 `usedFlow`（字节）及 `averageConsume`（字节/天）（[1424](https://open.ys7.com/help/1424)） |
| `traffic.account_details(*, start_time=None, end_time=None, page_start=None, page_size=None)` | 数组，按日期列出设备数、通道数及流量（[1425](https://open.ys7.com/help/1425)） |
| `traffic.day_details(*, flow_time=None, page_start=None, page_size=None)` | 数组，指定日的设备与通道用量（[1426](https://open.ys7.com/help/1426)） |
| `traffic.device_details(device_serial, *, start_time=None, end_time=None, page_start=None, page_size=None)` | 数组，单设备按日期和通道列出的用量，`device_serial` 是唯一必填参数（[1427](https://open.ys7.com/help/1427)） |

复用现有客户端（同步/异步同一路径）：

```python
summary = client.traffic.account_summary()
rows = await async_client.traffic.day_details(page_size=1)
```

仓库检出后运行最小示例（仅调 1424 一次账号汇总，只打印 status/region/operation，不输出账户数值）：

```bash
.venv/bin/python -m examples.traffic_usage --mode sync   # 或 --mode async
```

明细查询中的时间与分页参数可分别省略，平台决定默认值。`page_size` 默认 10、服务端上限 50（本地校验 1..50），`page_start` 默认 0；每次只取固定一页，不自动翻页。时间为有符号 64 位毫秒整数。平台按最近一周、截至前一天提供数据，具体日界由平台决定。1425/1427 的 curl 示例中 startTime 晚于 endTime，故 SDK 原样保留显式传入值，不交换也不拒绝。

企业版按带宽计量，官方未规定 `totalFlow` 在此情况下的实际表示。SDK 保留平台的原值。三个明细接口的 `data` 为数组、无分页封套，不暗示总数或费用；原始字段（含未知字段）原样透传。`data` 缺失、为 null 或外层类型不符时抛 `ProtocolError`。

## 云抽帧、消息订阅与HUB（仅国内）

`client.cloud_capture`（interval/timing两种抽帧任务创建+`task(task_id)`查询）、`client.message_subscriptions`（`create_consumer('group1')`+`consume(cid, offset=None)`）、`client.hubs`（`list_subdevices`/`subdevice_info`/`channels`）均为国内入口；创建任务与消费是显式副作用动作，可能计费或提交上一批偏移，不自动重放。用法、选择器边界与示例见[国内域指南](docs/cloud-capture-messages-hubs.md)；`examples/cloud_capture.py`、`message_subscriptions.py`、`hub_discovery.py`仅在源码checkout内运行。

## 播放地址与抓拍

`client.playback.create(serial, PlaybackOptions(...))`按地区创建播放地址；`invalidate(serial, url_id=...)`显式失效地址。`client.snapshots.capture(serial, channel=...)`支持两地普通抓拍，国内另有`capture_to_space`。这些操作不自动重放，也不下载或播放媒体；返回URL与密码应按凭证处理。协议/时间/存储差异和未解决项见[媒体说明](docs/research/2026-11-14-media-access.md)，尚未进行真实媒体请求。

## 本地录像查询

`client.recordings.local(device_serial, start_time=..., end_time=..., channel=...)`支持同步/异步调用，返回ApiResponse。国内要求同一显式时区、同一天的aware datetime，按文档数值示例转换为秒；海外时间可分别省略，传入时须为naive datetime，以服务器wall time格式发送，不猜测时区。

国内支持 `record_type=1..5`、`page_size=1..500`、`query_by_nvr`、`location=1/2`；海外record_type仅1..4，其他三个参数会明确拒绝。国内data包含records与翻页游标，海外data为数组；不自动翻页、不获取媒体内容。[契约差异与待验证项](docs/research/2026-11-14-recording-contracts.md)中保留时间单位/时区及错误表的证据限制。

### 国内存储录像文件时间查询

国内录像按时间查询 `client.recordings.by_time(device_serial, rec_type=1, version="2.0", page_size=1)`，`rec_type` 取 0 自动、1 云存储、2 本地，同步与异步客户端均可用。`start_time_ms` 与 `end_time_ms` 为有符号 int64 毫秒值，各自独立可选，自动来源不携带分页大小，云存储 `page_size` 限 1–1000、本地限 1–500，`version="2.0"` 下云存储可不传大小翻页，本地录像须同时传 `version` 和 `page_size` 才返回分页结构。`response.data` 可能是文件数组，也可能是含 `files`、`isAll`、`nextFileTime` 的对象，字段均按原始行保留，SDK 不会自动续页或下载，`downloadPath` 不能用于保存录像，分页对象的 `isAll` 为 `false` 时，可显式再次调用并把该页 `nextFileTime` 作为 `start_time_ms` 传入。文档标注最低子账号权限为无，但仍需有效 token 与接口资格，交互式 CLI 要求已有国内 EZVIZ 配置及 `EZVIZ_DEVICE_SERIAL`，仅输出 `returned_files` 与 `has_more`，当 `data` 为列表时后者为 null。契约以 [国内文档 1422](https://open.ys7.com/help/1422) 为准。

另有按文本时间查询 `client.recordings.by_text_time(device_serial=device_serial, start_time=start, stop_time=stop)`，同样面向国内并支持同步与异步调用，起止时间为定长日历字符串 `YYYY-MM-DD HH:MM:SS`，可选 `channel` 须传字符串以保留前导零，`response.data` 只接受对象数组，SDK 的起止时间均可省略。CLI 的 `component` 模式为限制范围而要求 `EZVIZ_RECORD_START_TIME` 与 `EZVIZ_RECORD_STOP_TIME`。两接口均向同一路由发送 POST，文档记载的参数格式不同。1600 归档目录将其列于智能电梯网络摄像机下，正文提及安全帽相机，故实际适用机型与账号权限均未获文档确认。全部验证均基于离线合成数据，不涉及媒体文件或真实设备。契约以 [国内文档 1600](https://open.ys7.com/help/1600) 为准。

```sh
python -m examples.recording_files --mode sync --query cloud
python -m examples.recording_files --query local --mode async
python -m examples.recording_files --mode sync --query component
```

## 云台、预置点与巡航

`client.ptz`提供start/stop/mirror、add_preset/move_preset/clear_preset，以及仅国内的presets/calibrate/reset。写操作不自动重放。start后需显式stop；关闭客户端不会停止设备。国内允许方向16，海外仅0–11且stop必须提供direction。型号能力与预置点上限由设备决定。

国内 `client.ptz.cruise` 提供get_enabled、get_schedule、set_schedule。设置时间计划使用 `CruisePeriod("08:00", "12:00", (0, 1, 2, 3, 4))`，其中0为周一；enabled须为bool，periods为时间段序列。设置会覆盖旧计划，并不自动配置巡航点或开关。开关设置文档存在编码矛盾，暂未提供set_enabled；[差异说明](docs/research/2026-11-14-ptz-contracts.md)保留待核实项。

## 国内设备语音素材

`client.device_voice.list(device_serial)` 返回这台设备的语音素材元数据。同步和异步客户端都支持这个入口。列表位于响应顶层的 `voiceInfos`，可通过 `response.data` 访问。每行的 `status` 值 0、1、2 分别表示同步完成、正在同步和同步失败。`time` 的单位未说明，SDK 保留原值及未知字段。[素材列表（1259）](https://open.ys7.com/help/1259)

`client.device_voice.rename(device_serial, voice_id, *, voice_name, voice_url)` 更新已选素材的名称。请使用同一设备条目中的整数 ID 和原始 URL。官方示例把这两个字段、新名称、设备序列号和访问令牌都放入 PUT 查询串，且只对型号 `CSEPMR511` 明确适用。SDK 不解析或访问文件，也不为改名预读清单。平台收到请求后如何处理 URL，原文没有说明。真实改名需单独授权，并保留同一 ID 的旧名称和原 URL 以便恢复。[修改名称（1273）](https://open.ys7.com/help/1273)

两个接口都按整数 `meta.code=200` 判定成功。改名回执可以没有 `data`，失败不会自动重发。SDK 会遮盖这两项请求在 HTTPX 请求日志中的查询串，并对异常中已知的私有字段及其常用 URL 编码形式做脱敏。应用仍能从成功响应中取得素材名称与地址，记录 `response.raw`、`response.data` 或完整请求 URL 会暴露相关内容。第三方代理和自定义日志由调用方管理。

从仓库运行只读示例前，先在本机配置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 和 `EZVIZ_DEVICE_SERIAL`。

```bash
python -m examples.device_voice --mode sync
```

示例只请求一次列表并报告是否有素材。实际设备的素材同步、改名及恢复效果仍待验收。

新增与删除均支持同步和异步客户端。

- `client.device_voice.create(device_serial, *, voice_name, voice_url)` 用 POST 提交名称和素材 URL。成功响应的顶层 `voiceInfo` 对象位于 `response.data`，其中的整数 `voiceId` 由平台分配。素材是否已同步可从列表的 `status` 字段另行确认。[新增素材（1263）](https://open.ys7.com/help/1263)
- `client.device_voice.delete(device_serial, voice_id, *, voice_name, voice_url)` 用 DELETE 提交同一素材的 ID、名称和原 URL。成功示例仅含 `meta`，此时 `response.data` 为 `None`。真实删除需单独授权。即使用相同名称和 URL 重建，原 ID 及引用该 ID 的告警音配置也可能无法恢复。[删除素材（1281）](https://open.ys7.com/help/1281)

这两个写入口都通过查询串提交全部参数，请求正文为空。SDK 只传递 URL，由调用者决定何时查询同步状态。写入失败后 SDK 不会自动重发。

下列示例用合成数据和 `httpx.MockTransport` 模拟认证、新增，以及用新增返回的 ID 删除该素材。运行只需要本地 SDK，`--mode async` 可验证异步流程。

```bash
python -m examples.device_voice_lifecycle --mode sync
```

## 国内对讲扬声器音量

`client.settings.get_talk_speaker_volume(device_serial)` 读取设备**最近一次上报**的对讲下行扬声器音量，值位于 `response.data["talkSpeakerVolume"]`。平台返回缓存值，本次查询不实时采集设备音量。同步和异步客户端均支持这个入口。[读取接口（1719）](https://open.ys7.com/help/1719)

`client.settings.set_talk_speaker_volume(device_serial, volume)` 显式设置同一属性。HTTP 参数表只要求整数，设备端回调将音量定义为 0–100 的百分比，因此 SDK 对写入值按此范围校验，允许 0，拒绝布尔值。设置不会建立对讲、发送音频、自动回读或重放。设备可以不实现调节回调。真实写入前须确认目标设备、原音量与恢复方法。[设置接口（1718）](https://open.ys7.com/help/1718) · [设备端对讲说明（1307018）](https://open.ys7.com/help/1307018)

在仓库根目录运行只读示例前，在本机配置 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET` 和 `EZVIZ_DEVICE_SERIAL`。

```bash
python -m examples.talk_speaker_volume --mode sync
```

示例的 `reported_percent` 只输出 0–100 的整数，超出范围时为 `null`。SDK 保留服务器返回的原始整数和其他业务字段。本批采用模拟传输验收，实际设备上报时机与物理效果仍待验证。

## 设备开关

`client.settings.get_switch(device_serial, DeviceSwitch.INDICATOR_LIGHT)`读取状态；`set_switch(device_serial, switch, enabled, channel=None)`设置状态。enabled必须为bool，写请求不重放。省略channel表示设备本身，麦克风不接受channel。六类开关、地区限制和原文差异见[开关说明](docs/research/2026-11-14-switch-settings.md)。SOUND_LOCALIZATION是声源定位，与TLS无关；所有真实设置效果仍未验证。

海外`client.settings`另提供九项设备设置与两项公开目录：`get_pir_area`/`set_pir_area`（`area`为官方原始字符串）、`get_chime`/`set_chime`、`get_infrared`/`set_infrared`、`set_timezone`、`get_language`/`set_language`，以及无凭据的`list_timezones`时区目录和`list_areas`国家/地区目录；以上仅海外，写操作不自动重放。`Client(anonymous=True, region=RegionConfig.overseas(api_base_url=...))`提供显式匿名模式调用公开操作。`timezone`须用目录返回的`tzCode`，`timeFormat`必填性、duration单位等来源缺项与匿名约束见[海外设备设置与公开目录](docs/research/2026-11-15-overseas-settings.md)；`list_areas`为显式GET（源同时声明POST但未文档化其形状），契约与边界见[公开国家/地区目录](docs/research/2026-11-17-public-areas.md)。新加坡公开时区目录与`open.ezvizlife.com`公开国家/地区目录均已经SDK同步/异步真实查询验证（后者见[SDK实测记录](docs/validation/live/2026-11-17-public-area-directory/sdk.json)，仅GET、identity编码、固定origin、两次调用）；九项设备接口仍为离线验证，其他origin、POST变体、压缩响应路径与集群路由未实测。

`examples/public_areas`为无凭据示例，固定使用`https://open.ezvizlife.com`，stdout只输出顶层数组条数（`status=ok`、`items=<len>`），非数组顶层返回固定`unexpected_data_type`/`items=null`并以1退出；不输出字段级验收、原始值或未知key，无env读取、无凭据、无文件写入。该示例CLI本身的固定数量输出与上述验收证据相互独立；验收走独立origin构造与有界回执，不借CLI输出充当证据。

### 国内智能检测开关与人形/PIR 模式

`client.settings.get_intelligent_detection_switch(device_serial, detection_type="302")` 读取、`client.settings.set_intelligent_detection_switch(device_serial, enabled=True, detection_type="302", channel_no=channel_no)` 写入智能检测开关，异步客户端以 `await` 调用同名方法。字符串 `"302"` 表示人体检测，`"304"` 表示人脸抠图，省略 `detection_type` 时为画面变化检测。可选字符串 `channel_no` 仅 727 写接口接受，省略时作用于设备级。资料表明智能检测依赖布防且各检测类型互斥。SDK 仅查询或配置指定开关，绝不隐式读取或修改布撤防状态。子账号对 Cam 设备/通道至少需要 726 的 Get 与 727 的 Config 权限，见 [726](https://open.ys7.com/help/726) 与 [727](https://open.ys7.com/help/727)。

`client.settings.get_alarm_detection_mode(device_serial)` 读取当前告警检测模式，`client.settings.select_alarm_detection_mode(device_serial, detection_type="5")` 在 `"1"` AI 人形与 `"5"` PIR 之间选择并即时启用，接口无布尔关闭，读取端解析原始整数 `response.data['valueInfo']['type']`，灵敏度数字编码与布防、移动侦测开关均不可复用。1724/1725 文档未标注最低授权或能力标志，见 [1724](https://open.ys7.com/help/1724) 与 [1725](https://open.ys7.com/help/1725)。CLI 只读模式沿用现有国内显式配置与 `EZVIZ_DEVICE_SERIAL`，3 个开关读取动作仅输出 `enabled`。mode 读取仅输出 `mode`（human/pir）。未知整数枚举返回 `null`。写演示完全在内存中运行，使用合成鉴权与两次合成写入，不读环境变量也不触达真实设备。真实改动需所有者单独授权并具备恢复手段，SDK 不做自动化撤防、抓图或媒体操作。

```sh
python -m examples.intelligent_detection --mode sync --action read-scene-switch
python -m examples.intelligent_detection --action read-mode --mode async
python -m examples.intelligent_detection --mode sync --action write-demo
```

### 国内移动跟踪与人形追踪开关

移动跟踪按文档 722 与 723 区分读写权限。722 明确将能力位 `support_intelligent_track=1` 列为写入（Config）前置条件。723 仅声明读取侧 Get 权限，未就该能力位单独表态。异步客户端的方法同名，调用时加 `await`。读取用 `client.settings.get_motion_tracking(device_serial)`，写入用 `client.settings.set_motion_tracking(device_serial, enabled=False, channel_no=channel_no)`。可选 `channel_no` 仅在写入时传字符串，子账号写入需按 Cam 设备和通道授予 Config，读取需同范围授予 Get。`VideoSwitch.FEATURE_TRACKING=701` 是另有文档的特写追踪能力，SDK 不会把它作为回退检查。任何实际改动须先获授权、知晓原状态并备好恢复方案，setter 只发一次业务调用且不重试。[移动跟踪设置 722](https://open.ys7.com/help/722) 与 [移动跟踪读取 723](https://open.ys7.com/help/723)。

人形追踪对应 `client.settings.get_human_tracking(device_serial)` 和 `client.settings.set_human_tracking(device_serial, enabled=False)`，异步同样同名加 await。接口在请求头携带 token 和设备序列号，请求中没有通道参数，文档也未说明最低授权或托管账号要求，其中 728 的 GET 即使无请求体也按文档发送表单 content-type。命令行 `read-motion` 和 `read-human` 需要当前已授权的国内配置及 `EZVIZ_DEVICE_SERIAL`，只输出启用状态，0 和 1 分别打印 false 和 true，未知打印 null。`write-demo` 使用合成的认证与业务传输层，两次写入均不依赖真实环境、凭证或设备，相关校验全部为合成验证。详见平台文档 [728](https://open.ys7.com/help/728) 与 [729](https://open.ys7.com/help/729)。

```sh
python -m examples.tracking_switches --mode sync --action read-motion
python -m examples.tracking_switches --action read-human --mode async
python -m examples.tracking_switches --mode sync --action write-demo
```

## 视频配置

`client.video`提供清晰度、预览编码、音视频codec、视频类开关的设置，以及本地录像编码和开关查询，仅国内。预览参数使用`PreviewEncoding`，分辨率/帧率是官方枚举代码；本地录像查询不视为预览设置的逆操作。`VideoSwitch`包含隐私遮蔽、休眠、人脸开锁、特写追踪和LOGO；设置不自动重放。JSON/form位置、权限、参数省略规则和源码缺项见[视频说明](docs/research/2026-11-14-video-configuration.md)，真实效果仍未验证。

### 国内设备视频编码格式查询

通过 `client.video.get_video_codec(device_serial, stream=1)`（同步）或 `await async_client.video.get_video_codec(device_serial, stream=2, channel=1)`（异步）读取设备在平台侧配置的视频编码格式，其中 `stream=1` 为主码流、`stream=2` 为子码流，省略 `channel` 时服务端按默认通道 1 处理。该接口仅限国内设备，托管（hosted）设备需具备平台侧已授予的 `CONFIG` 权限。返回 `ApiResponse`，原始整数保留在 `response.data["videoCode"]` 中，未知整数原样透传，常见取值如 1=标准 H.264、5=标准 H.265。该值为平台配置，与本地录像或媒体探测得到的实际编码可能不同。写操作说明现为文档迁移公告，指向既有的 2377 写方法。参考 [国内文档 1594](https://open.ys7.com/help/1594)、[国内文档 2377](https://open.ys7.com/help/2377)、[国内文档 1595](https://open.ys7.com/help/1595)。

CLI 为单次显式读取，通过 `EZVIZ_REGION=domestic`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`、`EZVIZ_DEVICE_SERIAL` 与可选的 `EZVIZ_CHANNEL_NO` 配置，输出仅含 `video_code` 与 `codec` 字段，未知数值映射为 `null`/`unknown`，示例可使用真实授权配置，本批次仅以离线合成样本验证。

```bash
python -m examples.video_codec --mode sync --stream 1
python -m examples.video_codec --stream 2 --mode async
```

### 国内 RTMP 云端音频转码

同步与异步客户端都提供 `client.video.get_rtmp_aac_support(serial)` 和 `client.video.set_rtmp_aac_enabled(serial, True)`，用于将国内 RTMP 播放与取流的 G.711 音频在云端转码为 AAC。异步客户端对任一调用都加 `await`。`enabled` 须为严格布尔值，`channel` 只接受非负整数或 None 并拒绝布尔值，None 省略 `localIndex`，0 会显式发送。

`get_rtmp_aac_support` 返回的 `data.enable` 是布尔值，只描述平台报告的转码支持能力，不反映当前开关状态，也无法在改动前备份原值用于恢复。`set_rtmp_aac_enabled` 只控制云端 RTMP 的 G.711 转 AAC，SDK 不自动重放或读回，成功响应仅表示平台已受理。设备自身的音频编码设置与录像文件异步处理等 VOD 任务使用独立接口，HLS 暂不支持该转码。

查询接口列出应用、托管和子账户等令牌类型可用，`localIndex` 省略或为 0 要求设备级 CONFIG，为正整数时要求通道级 CONFIG。设置接口需要国内访问令牌及设备配置权限，官方未按查询接口的口径逐类列出令牌适用性，真实写入还需确认账户计费和操作授权。归档记有计费政策及 2024 年 12 月起的限时免费说明，未核定现行收费或账户余额能否覆盖费用。`localIndex` 省略时默认 0，使用该默认值时从未取流的全新设备立即生效，已取流设备最长可能延迟三天，官方未对显式通道给出同一时效。

只读示例 `python -m examples.rtmp_aac --mode sync` 和 `python -m examples.rtmp_aac --mode async` 需显式设置 `EZVIZ_REGION=domestic`、`EZVIZ_DEVICE_SERIAL`、`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`，`EZVIZ_CHANNEL_NO` 可省略或填写非负 ASCII 十进制，输出仅含 region、mode、status、supported。本批仅完成离线验证，未使用真实账户，未确认配置变更、转码后的音频或计费。官方文档见 [3735 设置](https://open.ys7.com/help/3735) 与 [3736 查询](https://open.ys7.com/help/3736)。

## 图像参数

国内同步与异步客户端均支持设备图像配置。`client.imaging.get_display_mode(serial)` 读取显示预设，`set_display_mode(serial, mode)` 设置标准、写实或艳丽。`imaging.get(serial, ImageSetting.IMAGE_PARAMS)` 等六项原有查询继续使用各自契约。

`set_image_params` 和 `set_white_balance` 支持手动参数，和背光、降噪、曝光、防闪烁设置均为显式写入。饱和度与手动白平衡的写请求各采用官方 PUT 示例的一种拼写。可用 `python -m examples.image_configuration --mode async --setting display` 运行只读示例。硬件效果和实际写入恢复仍待验收。[参数范围、权限与来源差异](docs/research/2026-11-14-imaging.md)

## 设备诊断

诊断入口为 `client.diagnostics`：protocol_support、search、channel_status、capabilities、version、storage_status、disk_capacity。protocol_support需显式app_key；capabilities可传channel。仅capabilities/version/storage_status有海外契约，其他仅国内。字段和响应差异见[诊断说明](docs/research/2026-11-14-diagnostics.md)；不自动升级、格式化或关闭加密。公开型号查询曾实测返回HTTP500；[国内单设备能力和版本查询](docs/validation/live/2026-11-14-domestic-diagnostics/README.md)随后成功，其余诊断保持未验证。

## 加密与布撤防

`client.security`提供显式开启/关闭视频加密和修改视频密码；`client.arming`提供布撤防模式、时间计划和国内主动防御命令。计划使用`ArmingSchedule`，省略字段可能使用平台默认值（包括启用），不等于保留原计划。各地区验证码/长度约束、时间窗口及旧defence/defense的语义区别见[安全与布撤防说明](docs/research/2026-11-14-security-arming.md)。没有真实安全状态变更。

## 维护与补光

`client.maintenance`提供固件/模块查询与显式升级、指定介质格式化；查询不自动轮询，触发操作不自动重放或修改前置安全状态。`client.illumination`按地区处理补光模式、读取与开关，国内模式0–3、海外0–2。参数顺序、旧版迁移与[响应层级证据](docs/research/2026-11-14-maintenance-illumination.md)有明确说明；没有真实设备维护操作。

国内夜视配置读写通过 `client.illumination` 上的独立入口完成，同步与异步客户端签名一致。读取调用形如 `client.illumination.get_night_vision(serial, channel_no)`，写入调用形如 `client.illumination.set_night_vision(serial, channel_no, luminance=..., duration=..., mode=...)`，真机写入与配置恢复尚未执行也未验证。

两个方法均返回 `ApiResponse`，`data` 属性为字典。读取响应的 `data["valueInfo"]` 保留官方原始值，字段表标注为字符串而示例为对象，SDK 接受两种形态。写入响应中含有 `data["result"]` 字符串，仅凭该字符串不能确认硬件已实际生效。`mode` 沿用 0–3 的 `FillLightMode`，官方未说明 `luminance` 与 `duration` 的单位或取值范围。写入为单次 PUT，不自动重试也不读回，当前已完成离线验证。

示例 `python -m examples.night_vision --mode sync` 或 `--mode async` 仅执行一次读取，需显式设置 `EZVIZ_REGION=domestic`、`EZVIZ_DEVICE_SERIAL`、`EZVIZ_CHANNEL_NO`、`EZVIZ_APP_KEY` 与 `EZVIZ_APP_SECRET`。输出仅含 `region`、`mode`、`status`、`value_info_type` 四项回执字段，设备配置值不展示。接口约定见官方文档[读取夜视配置](https://open.ys7.com/help/5061)与[设置夜视配置](https://open.ys7.com/help/5062)。

## OTAP物模型与账户托管

`client.otap`目前仅国内提供四个物模型入口：`get_property`与`latest_property`为只读查询（平台缓存值、`data.desired`/`data.reported`），`set_property`与`invoke_action`把调用方给定的裸JSON对象下发到设备；业务标识与令牌均置于Header，两个GET无请求体。服务调用为同步接口，平台等待上限16秒，需要完整等待请显式设置更大的`Client(timeout=...)`；写入后不自动再查询、不轮询，`desired`/`reported`中的`null`与`false`原样保留。`latest_property`按来源校验`local_index`（`0`或非零开头、不超过10位）与标识符长度<255，旧v3接口仅要求非空白。

`client.delegation`仅国内提供五个B1/B2托管入口：B2端`apply_code`，B1端`authorize_account`、`list_resources`（只读，`page_size`不超过文档最大20）、`authorize_device`、`relieve_device`。授权与解绑凭证不对称是合同事实：授权使用授权码`sign`，解绑使用B2的`appKey`与必填`state`；`state`无已声明枚举，SDK不设默认值。每个方法各发一次请求，官方文档未规定完整调用顺序；SDK不交换或缓存对方凭据、不从一方身份推导对方appKey、不代激活该付费能力，`sign`按敏感数据处理。

```python
import os
from ezviz_unified import Client, RegionConfig

# B1为设备归属账号、B2为被托管账号，是两个独立的客户端身份；
# 下列顺序仅为演示各入口，官方文档未规定完整调用顺序。
with Client(
    os.environ["EZVIZ_B1_APP_KEY"],
    os.environ["EZVIZ_B1_APP_SECRET"],
    region=RegionConfig.china(),
) as b1, Client(
    os.environ["EZVIZ_B2_APP_KEY"],
    os.environ["EZVIZ_B2_APP_SECRET"],
    region=RegionConfig.china(),
) as b2:
    grant = b2.delegation.apply_code("<B1的appKey>")          # 返回 data.sign
    b1.delegation.authorize_account(grant.data["sign"])
    resources = b1.delegation.list_resources(grant.data["sign"], page_size=20)
    prop = b1.otap.get_property(
        "<deviceSerial>",
        resource_category="<category>",
        local_index="0",
        domain_identifier="<domain>",
        property_identifier="<prop>",
    )
```

校验规则、Header位置、信封判定与未解决项见[OTAP与账户托管说明](docs/research/2026-11-15-otap-delegation.md)；本批接口均未做真实调用验证。

## 国内组件目录

`client.components`（异步为 `AsyncClient.components`）登记了一小批路径式 OTAP 官方文档——`deviceSerial` 位于请求路径中，与头部式 `client.otap` 是两套独立契约，来源未声明等价，不合并。`describe`/`list_documented` 纯本地返回元数据，`call(operation_id, device_serial, *, payload=...)` 调用其中 7 项可调用条目；另有 2 条文档仅保留元数据（破坏性动作无选盘规则、模板拼写含义未确认），在认证前拒绝。payload 逐文档约束：无正文 GET 拒绝 payload 但仍按文档要求发送 JSON Content-Type；语义只读的 PUT 查询需显式 `payload=None`。型号、白名单与设备成功码证据层均按源文档记录，目录是文档登记而非设备能力检测，真实型号可用性未验证。条目细节与 `path_params`/`JSON_NULL` 低层规则见[组件目录说明](docs/components.md)。

## 底层响应与传输

```python
from ezviz_unified import ResponsePolicy, decode_response

response = decode_response(
    {"meta": {"code": 200, "message": "success"}, "data": {"online": True}},
    policy=ResponsePolicy(code_path="meta.code", message_path="meta.message"),
    operation="example.read",
)
print(response.data)
```

解析器支持 `code`、`meta.code`、`result.code` 等显式策略，保留原始响应，区分认证、权限、设备和协议错误。可按接口明确声明envelope_paths，唯一匹配时解析，相互冲突的层级报错；默认只按根对象，不递归猜包。它本身不发起网络请求。

`SyncTransport` 与 `AsyncTransport` 提供显式的 query/form/JSON/header 请求入口，默认各 HTTPX I/O 阶段超时为10秒，不自动重试或跟随重定向，支持上下文管理关闭。该超时不是整个请求的总时限；有限认证重试由上面的客户端层控制。

SDK仅对自身请求上下文内的httpx logger请求日志记录脱敏，不改变应用日志等级或handler，也不添加全局日志钩子；用户自行记录的原始响应及其他依赖库的调试输出不在此脱敏保证范围内。自定义transport中的编程错误原样传播；网络失败不会被表述为“远端肯定没有执行”。

## 本地调用诊断

`diagnostic_recorder=DiagnosticRecorder()` 让 `Client`/`AsyncClient` 在进程内生成一份无凭据、无标识、无异常原文的本地调用诊断 JSON（默认关闭，不自动写文件或上传）；字段白名单、覆盖与未覆盖边界见[本地诊断说明](docs/diagnostics.md)。

## 区域与认证

`TokenManager` 与 `AsyncTokenManager` 惰性获取 token，按服务器返回的毫秒 `expireTime` 缓存，并合并并发刷新。国内使用 `RegionConfig.china()`；海外默认认证入口来自官方文档，业务地址绑定返回的 `areaDomain`，不假定所有海外账户属于同一区域。

```python
import os
from ezviz_unified import RegionConfig, TokenManager

with TokenManager(
    os.environ["EZVIZ_APP_KEY"],
    os.environ["EZVIZ_APP_SECRET"],
    region=RegionConfig.overseas(),
) as auth:
    token = auth.get_token()  # 此处才调用真实认证服务；不要记录 token.value
    api_base_url = token.api_base_url
```

可显式配置海外 `auth_base_url` 与 `api_base_url`，仅接受 EZVIZ 域名下的 HTTPS origin；显式业务地址必须与返回域名一致。刷新提前量默认60秒，最多为剩余寿命的一半。`invalidate(expected_value=旧token)` 只清除匹配缓存，防止迟到的失败响应清掉新 token。

异步管理器在同一事件循环内使用；单个等待方取消不会取消共享刷新。关闭先禁止新调用，再等待在途刷新并释放连接；取消一个关闭等待方时，清理任务仍会继续。国内与新加坡账号已完成同步只读联调；新加坡账号在文档默认认证入口返回10017，显式配置`RegionConfig.overseas(auth_base_url="https://isgpopen.ezvizlife.com")`后成功，SDK不自动回退区域。此配置仅适用于已确认区域的账号，详见[真实记录](docs/validation/live/2026-11-14-overseas/README.md)和[联调工具](tools/live/README.md)。

## RAM子账户

`client.subaccounts`提供显式账号、权限和子token请求；`RamStatement`保留权限拼写和资源结构，`ram_password_digest`按官网公式计算摘要并要求显式encoding。国内当前审核了create/add_statement，海外审核九项。获取子token不会替换父客户端身份，也不自动推断expireTime单位；[子账户说明](docs/research/2026-11-14-subaccounts.md)列明地区、编码与来源限制。本批未实际创建账号或修改权限。

## 外部令牌

已有合法外部token时，可以省略开发者密钥：

```python
import os
from ezviz_unified import Client, RegionConfig, StaticTokenProvider

provider = StaticTokenProvider(
    os.environ["EZVIZ_ACCESS_TOKEN"],
    expires_at=float(os.environ["EZVIZ_TOKEN_EXPIRES_AT"]),  # Unix秒
    api_base_url="https://open.ys7.com",
)
with Client(region=RegionConfig.china(), token_provider=provider) as client:
    response = client.devices.list_managed()
```

静态provider不刷新；异步对应AsyncStaticTokenProvider。也可实现TokenProvider/AsyncTokenProvider接入自己的签发服务，或借用已有TokenManager。Client不关闭借用provider，不允许混用appKey/appSecret或auth_transport，也不会自动回退到开发者凭据。SDK检查期限与域名；自定义provider负责维持授权主体，详见[授权范围](docs/research/2026-11-14-token-scopes.md)。

## 本地开发

要求 Python 3.12+，以下命令使用 uv：

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e '.[dev]' -r tools/documents/requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy src/ezviz_unified
.venv/bin/python -m ruff check src/ezviz_unified harness examples tools/release tests/test_sdk*.py tests/test_harness*.py tests/test_release*.py tests/test_examples*.py tests/test_live_verifier.py tools/live/verify_devices.py tests/conftest.py tests/test_offline_policy.py
.venv/bin/python -m build
```

默认测试均离线，socket连接与DNS被阻断，不加载真实应用密钥。明确标记的集成测试还需显式 `--run-live`，且调用范围需单独授权。原始文档归档保存在本地 `data/official-docs/`，不进入源码提交或 SDK wheel；采集和校验方法见[工具说明](tools/documents/README.md)。

## 契约与维护

- [架构设计](docs/design/2026-11-14-foundation-redesign.md)与[实施路线](docs/plans/2026-11-14-delivery-roadmap.md)。
- [已审核传输契约](contracts/reviewed-transport-contracts.json)：文档层面的证据，不表示已进行真实 API 验证。
- [官方目录关联](docs/research/2026-11-14-official-scope.md)：按地区和分类定位未关联文档；文档数量不等于接口总数或支持率。
- [HTTP候选清单](docs/research/2026-11-15-endpoint-inventory.md)：从归档官方文档提取的HTTP候选及同路由线索；候选数量不是SDK支持数量，不代表已验证或完整覆盖。
- [App 数据复用指南](docs/integrations/pyezvizapi.md)：按固定版本评估第三方 pyEzvizApi，保持消费者账号与开放平台身份分离；当前只提供集成说明，未内置 App adapter。
- [主导与执行分工](docs/coordination/workflow.md)：执行模型产出候选，主导方核对来源、代码和测试后集成。
- [技能包索引](skills/README.md)：面向 Agent 的技能包说明（设备只读检查、单契约审查、快照维护），随源码分发，非 SDK 运行依赖。
- [维护harness](harness/README.md)：用`python -m harness.review`组合快照完整性、来源关联、静态登记和[离线传输评测](evals/contracts/README.md)，输出待审查任务；当前不自动改代码或发布。
- [维护语义评测](evals/maintenance/README.md)：独立评分文档变化判断，保留字段类型、证据与不确定性；已有一次真实模型的8个公开合成案例记录，尚不代表补丁正确性或自动维护完成。
- [受控补丁演练](docs/validation/maintenance/2026-11-15-controlled-repair/README.md)：模型生成提案，经哈希、修改范围与独立Oracle检查后，仅在macOS隔离副本验证；单个合成分页案例通过1,978项回归，生产SDK与契约登记不变，不自动合入或发布。
- [发行检查](docs/releasing.md)：核验独立包的wheel/sdist、官方契约及实际验证范围。

`ezviz-unified` 是独立产品：不依赖旧 `ezviz-openapi-utils`，不提供旧导入路径、方法别名或兼容适配层。API按官方契约和当前使用场景设计，旧方法数量不作为范围或发布门槛。独立仓库已公开并完成安装与CI交接；旧 `ezviz-openapi-utils` 已归档，只读保留历史。来源与验收边界见[交接记录](docs/provenance.md)。

MIT License。项目为第三方实现，不属于萤石官方 SDK。
