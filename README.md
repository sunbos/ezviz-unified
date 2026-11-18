# EZVIZ Unified

面向萤石国内与海外平台的第三方 Python SDK，目标是统一调用体验、明确地区与权限边界，并以可验证的契约持续维护。

项目遵循[优先复用官方生态的边界](docs/research/2026-11-15-official-ecosystem-reuse.md)：服务端接入与维护验证是主要投入方向；播放、编解码和RTC由适用的官方组件承担。官方已有海外播放器、Token工具和接入skills，新增能力须证明具体使用价值。

当前为开发中的 `0.1.0a1`：发行名 `ezviz-unified`，导入名 `ezviz_unified`，要求 Python 3.12+，仓库为 [sunbos/ezviz-unified](https://github.com/sunbos/ezviz-unified)，尚未发布到 PyPI。已有同步/异步客户端、区域认证、设备查询和诊断脱敏；各业务域按[实施路线](docs/plans/2026-11-14-delivery-roadmap.md)逐域推进。

本地录像、云台、巡航、诊断、设备开关、管理、图像参数、视频配置、播放地址、抓拍、告警、云存储、统一存储、云录制1.0文件与云点播、RAM子账户、维护、补光、加密/布撤防、单步确权、OTAP物模型与B1/B2账户托管接口已加入，国内/海外共用入口并保留协议差异（云录制1.0文件操作与云点播VOD仅国内；OTAP与账户托管目前仅国内有据）；当前登记数量、地区分布与验证状态见[机器生成的覆盖清单](contracts/coverage.json)，真实支持情况另列。

产品能力范围的权威登记见 [docs/capabilities/README.md](docs/capabilities/README.md)：按 `capability_id` → `acceptance_case_refs` 定位验收证据，并区分 `implementation.status`、`review`、`result`、`freeze_state` 与 `requirement=required` 口径；登记数量与 wire 证据不代表覆盖率或产品级验收通过。

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

国内分页页码不超过400；海外文档未声明相同限制。每页1–50条。国内 `channels`/`status` 要求序列号英文字母大写。SDK不自动翻页，不自行解释有文档矛盾的海外状态字段。[国内单设备记录](docs/validation/live/2026-11-14-domestic-device/README.md)已验证基础信息、通道和状态查询；[新加坡记录](docs/validation/live/2026-11-14-overseas/README.md)仍仅证明对应账号认证和空列表成功。结果不推广到所有型号或其他海外集群。

国内托管设备使用 `client.devices.list_managed(page_start=0, page_size=10)`；与普通列表分开，不套用400页上限。接入设备后的[范围复核](docs/validation/live/2026-11-14-domestic-device/README.md)中，普通列表首个分页返回一条，托管列表分页为空；这不代表全量设备计数，其他授权主体仍未验证。

`client.devices`另提供bind/unbind、rename/rename_channel、link_ipc/unlink_ipc、set_channel_visible、cameras和connection_info；写请求不会自动重放。仅国内提供rename_channel/set_channel_visible/connection_info，后者不支持子账户和托管权限。`client.ownership`提供国内三步确权调用，不自动串联、操控设备上下线或修改安全设置；具体约束见[管理与确权说明](docs/research/2026-11-14-management-ownership.md)。这些变更操作仍未实测。

`inspect_device(client, device_serial, channel=1)` 与 `inspect_device_async` 按固定顺序组合设备信息、通道、状态、能力、版本五次只读调用，逐项返回 `InspectionResult`，并提供不含序列号、通道、载荷或异常原文的 `public_summary()`；认证级错误立即中断。示例 `examples.inspection_sync`/`inspection_async` 用 `EZVIZ_DEVICE_SERIAL` 与 `EZVIZ_CHANNEL` 显式指定一台已知设备，不枚举账号设备，退出码 0/2/1 分别对应全部成功、部分失败、配置或 SDK 级失败；用法与输出契约见[接入指南的单设备检查节](docs/getting-started.md#单设备检查)。

## 告警与云存储

`client.alarms.list(AlarmQuery(status=2))`查询全部状态告警，`for_device(serial, query)`按设备查询；不自动翻页、标记已读或下载图片。省略status时由平台默认查询未读。`client.cloud_storage.info`默认国内v2、海外v1，另有套餐类型、开通状态查询及显式开通/兑换/暂停/试用/转移方法。服务变更不自动重放，也不自动轮询或更换付款方式。版本、时间、权限和原文缺项见[告警与云存储说明](docs/research/2026-11-14-alarms-cloud-storage.md)，未产生实际订单或服务变更。

## 消息回调

模块级`parse_webhook`使用原始UTF-8正文、`t`、`Signature`与单独配置的`webhook_secret`先核验MAC再解析；`parse_unverified_webhook`是显式不做认证的入口。返回值保留未知body字段与类型，`ack()`只构造messageId字典，HTTP响应和去重由应用负责。签名匹配不证明当前发送者身份或消息新鲜度；本批依据国内归档协议离线验证，未接收真实回调，也不自动使用AppSecret、配置消息服务或下载媒体。详见[消息助手](docs/research/2026-11-15-webhook-helpers.md)及[源资料差异](docs/research/2026-11-15-message-ingestion.md)。

仓库内另附一个把这些纯助手接到本地有界HTTP链上的[独立接收示例](docs/webhook-receiver.md)：需在仓库checkout内显式启动，受理范围仅是配置签名secret的明文JSON投递（signed-only是本示例接入策略，非官方必选）。上述纯helpers本身的语义不变，也不能称它们已借此真实接入平台。平台消息/设备事件的国内与海外验收状态各自单列，见[验收登记](docs/validation/sdk-product-acceptance.json)中的webhook行；当前均未标完成。

## 统一存储

统一存储入口为`client.storage`，提供空间列表/创建/更新/删除、文件搜索/保留期修改及签名下载地址查询，目前仅国内。文件查询使用`StorageFileQuery`，保留官方选择器优先级；`None`不发送，更新不注入创建默认值。统一存储永久保留使用`-1`，与旧云录制空间的`0`不同。下载地址仅返回，不获取媒体；[完整规则与来源限制](docs/research/2026-11-14-unified-storage.md)列明单位、编码推导及权限，全部新能力仍为离线验证。

## 云录制2.0

云录制2.0单列为`client.cloud_recording`，提供空间/一次性计划/任务查询、录像日历、片段与HLS信息、模板列表等10个只读入口，另有一次性计划创建/更新/停止/删除与即时计划创建共5个写入口，目前仅国内。时间使用整秒naive datetime；不自动翻页、下载、轮询或串联写操作；写接口需显式调用，可能创建真实录制并消耗套餐。详见[云录制约束](docs/research/2026-11-14-cloud-recording.md)与[计划管理写接口](docs/research/2026-11-15-cloud-recording-plans.md)，不等同于设备云存储套餐或本地录像。

## 云录制1.0与云点播

旧版云录制1.0文件操作单列为`client.cloud_recording_v1`，仅国内：`unfreeze_file(project_id, file_id)`触发归档解冻（写操作，不自动重放；仅HTTP成功且业务meta.code=200的响应证明触发已被受理，不证明解冻完成——HTTP 200本身可能携带业务失败，不能单独据此推导）、`file_info(project_id, file_id)`查询单个文件详情、`download_urls(project_id, file_id, content_type=..., expire_seconds=...)`获取官方下载/播放地址（不下载媒体）。云点播文件列表单列为`client.vod`，`files(file_node_ids=None, space_id=..., space_name=...)`至少需一个选择条件，仅国内。两套接口的`projectId`/`fileId`/`fileNodeId`互不相同，不做映射；详见[云录制1.0与VOD说明](docs/research/2026-11-18-cloud-recording-v1-and-vod.md)，本批接口均未做真实调用验证。

`client.vod`另提供四个仅国内的转封装与查询入口，同步/异步共用同名方法：`create_remux_task`（官方明确非幂等的异步任务创建，不自动重放，仅返回`ApiResponse`，不变成任务句柄）、`task`与`tasks`（只读的单任务查询与任务分页列表，不自动翻页或轮询）以及`recording_urls`（按设备、通道与时段签发录像地址；其`read_only=False`是SDK为禁自动重放的选择，来源未明确无副作用，非官方写/非幂等结论）。官方声明的范围/权限边界包括：`create_remux_task`限企业用户、不支持托管/子账号且需已有对应时段云录像；`recording_urls`要求设备开通云存储且对应时段有录像、同样不支持托管/子账号；两个查询接口的来源仅明确accessToken，额外账号或套餐权限未声明，由服务端判定；SDK不把这些前提互相移植。全部参数校验、Header字符串规则与时间边界见[云点播转封装说明](docs/research/2026-11-18-vod-remux.md)；任务状态编码、地址字段名与`fileNodeId`/`taskId`各自独立，SDK不发明映射或完成判据。本批四个入口仍未做真实调用验证；各能力的验收状态见[产品能力登记](docs/capabilities/README.md)与[验收登记](docs/validation/sdk-product-acceptance.json)中对应`candidate:domestic:*`行，登记数量不代表覆盖率或产品级验收通过。

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

## 云台、预置点与巡航

`client.ptz`提供start/stop/mirror、add_preset/move_preset/clear_preset，以及仅国内的presets/calibrate/reset。写操作不自动重放。start后需显式stop；关闭客户端不会停止设备。国内允许方向16，海外仅0–11且stop必须提供direction。型号能力与预置点上限由设备决定。

国内 `client.ptz.cruise` 提供get_enabled、get_schedule、set_schedule。设置时间计划使用 `CruisePeriod("08:00", "12:00", (0, 1, 2, 3, 4))`，其中0为周一；enabled须为bool，periods为时间段序列。设置会覆盖旧计划，并不自动配置巡航点或开关。开关设置文档存在编码矛盾，暂未提供set_enabled；[差异说明](docs/research/2026-11-14-ptz-contracts.md)保留待核实项。

## 设备开关

`client.settings.get_switch(device_serial, DeviceSwitch.INDICATOR_LIGHT)`读取状态；`set_switch(device_serial, switch, enabled, channel=None)`设置状态。enabled必须为bool，写请求不重放。省略channel表示设备本身，麦克风不接受channel。六类开关、地区限制和原文差异见[开关说明](docs/research/2026-11-14-switch-settings.md)。SOUND_LOCALIZATION是声源定位，与TLS无关；所有真实设置效果仍未验证。

海外`client.settings`另提供九项设备设置与两项公开目录：`get_pir_area`/`set_pir_area`（`area`为官方原始字符串）、`get_chime`/`set_chime`、`get_infrared`/`set_infrared`、`set_timezone`、`get_language`/`set_language`，以及无凭据的`list_timezones`时区目录和`list_areas`国家/地区目录；以上仅海外，写操作不自动重放。`Client(anonymous=True, region=RegionConfig.overseas(api_base_url=...))`提供显式匿名模式调用公开操作。`timezone`须用目录返回的`tzCode`，`timeFormat`必填性、duration单位等来源缺项与匿名约束见[海外设备设置与公开目录](docs/research/2026-11-15-overseas-settings.md)；`list_areas`为显式GET（源同时声明POST但未文档化其形状），契约与边界见[公开国家/地区目录](docs/research/2026-11-17-public-areas.md)。新加坡公开时区目录与`open.ezvizlife.com`公开国家/地区目录均已经SDK同步/异步真实查询验证（后者见[SDK实测记录](docs/validation/live/2026-11-17-public-area-directory/sdk.json)，仅GET、identity编码、固定origin、两次调用）；九项设备接口仍为离线验证，其他origin、POST变体、压缩响应路径与集群路由未实测。

`examples/public_areas`为无凭据示例，固定使用`https://open.ezvizlife.com`，stdout只输出顶层数组条数（`status=ok`、`items=<len>`），非数组顶层返回固定`unexpected_data_type`/`items=null`并以1退出；不输出字段级验收、原始值或未知key，无env读取、无凭据、无文件写入。该示例CLI本身的固定数量输出与上述验收证据相互独立；验收走独立origin构造与有界回执，不借CLI输出充当证据。

## 视频配置

`client.video`提供清晰度、预览编码、音视频codec、视频类开关的设置，以及本地录像编码和开关查询，仅国内。预览参数使用`PreviewEncoding`，分辨率/帧率是官方枚举代码；本地录像查询不视为预览设置的逆操作。`VideoSwitch`包含隐私遮蔽、休眠、人脸开锁、特写追踪和LOGO；设置不自动重放。JSON/form位置、权限、参数省略规则和源码缺项见[视频说明](docs/research/2026-11-14-video-configuration.md)，真实效果仍未验证。

## 图像参数

`client.imaging.get(device_serial, ImageSetting.EXPOSURE)`等入口查询六类图像参数；`set_backlight`、`set_denoising`、`set_exposure`、`set_anti_flicker`提供四类明确设置，仅国内。曝光参数单位为微秒；降噪等级为1–100且按模式检查必填字段。白平衡和图像风格setter因源拼写冲突暂缓；[完整约束](docs/research/2026-11-14-imaging.md)列明范围、权限和未验证项。本批没有改变真实设备。

## 设备诊断

诊断入口为 `client.diagnostics`：protocol_support、search、channel_status、capabilities、version、storage_status、disk_capacity。protocol_support需显式app_key；capabilities可传channel。仅capabilities/version/storage_status有海外契约，其他仅国内。字段和响应差异见[诊断说明](docs/research/2026-11-14-diagnostics.md)；不自动升级、格式化或关闭加密。公开型号查询曾实测返回HTTP500；[国内单设备能力和版本查询](docs/validation/live/2026-11-14-domestic-diagnostics/README.md)随后成功，其余诊断保持未验证。

## 加密与布撤防

`client.security`提供显式开启/关闭视频加密和修改视频密码；`client.arming`提供布撤防模式、时间计划和国内主动防御命令。计划使用`ArmingSchedule`，省略字段可能使用平台默认值（包括启用），不等于保留原计划。各地区验证码/长度约束、时间窗口及旧defence/defense的语义区别见[安全与布撤防说明](docs/research/2026-11-14-security-arming.md)。没有真实安全状态变更。

## 维护与补光

`client.maintenance`提供固件/模块查询与显式升级、指定介质格式化；查询不自动轮询，触发操作不自动重放或修改前置安全状态。`client.illumination`按地区处理补光模式、读取与开关，国内模式0–3、海外0–2。参数顺序、旧版迁移与[响应层级证据](docs/research/2026-11-14-maintenance-illumination.md)有明确说明；没有真实设备维护操作。

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
