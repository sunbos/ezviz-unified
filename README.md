# EZVIZ Unified

面向萤石国内与海外平台的第三方 Python SDK，目标是统一调用体验、明确地区与权限边界，并以可验证的契约持续维护。

项目遵循[优先复用官方生态的边界](docs/research/2026-11-15-official-ecosystem-reuse.md)：服务端接入与维护验证是主要投入方向；播放、编解码和RTC由适用的官方组件承担。官方已有海外播放器、Token工具和接入skills，新增能力须证明具体使用价值。

当前为开发中的 `0.1.0a1`：发行名 `ezviz-unified`，导入名 `ezviz_unified`，要求 Python 3.12+，仓库为 [sunbos/ezviz-unified](https://github.com/sunbos/ezviz-unified)，尚未发布到 PyPI。已有同步/异步客户端、区域认证、设备查询和诊断脱敏；各业务域按[实施路线](docs/plans/2026-11-14-delivery-roadmap.md)逐域推进。

本地录像、云台、巡航、诊断、设备开关、管理、图像参数、视频配置、播放地址、抓拍、告警、云存储、统一存储、RAM子账户、维护、补光、加密/布撤防、单步确权、OTAP物模型与B1/B2账户托管接口已加入，国内/海外共用入口并保留协议差异（OTAP与账户托管目前仅国内有据）；当前登记数量、地区分布与验证状态见[机器生成的覆盖清单](contracts/coverage.json)，真实支持情况另列。

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

## 统一存储

统一存储入口为`client.storage`，提供空间列表/创建/更新/删除、文件搜索/保留期修改及签名下载地址查询，目前仅国内。文件查询使用`StorageFileQuery`，保留官方选择器优先级；`None`不发送，更新不注入创建默认值。统一存储永久保留使用`-1`，与旧云录制空间的`0`不同。下载地址仅返回，不获取媒体；[完整规则与来源限制](docs/research/2026-11-14-unified-storage.md)列明单位、编码推导及权限，全部新能力仍为离线验证。

## 云录制2.0

云录制2.0单列为`client.cloud_recording`，提供空间/一次性计划/任务查询、录像日历、片段与HLS信息、模板列表等10个只读入口，另有一次性计划创建/更新/停止/删除与即时计划创建共5个写入口，目前仅国内。时间使用整秒naive datetime；不自动翻页、下载、轮询或串联写操作；写接口需显式调用，可能创建真实录制并消耗套餐。详见[云录制约束](docs/research/2026-11-14-cloud-recording.md)与[计划管理写接口](docs/research/2026-11-15-cloud-recording-plans.md)，不等同于设备云存储套餐或本地录像。

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

海外`client.settings`另提供九项设备设置与一项公开目录：`get_pir_area`/`set_pir_area`（`area`为官方原始字符串）、`get_chime`/`set_chime`、`get_infrared`/`set_infrared`、`set_timezone`、`get_language`/`set_language`，以及无凭据的`list_timezones`时区目录；以上仅海外，写操作不自动重放。`Client(anonymous=True, region=RegionConfig.overseas(api_base_url=...))`提供显式匿名模式调用公开操作。`timezone`须用目录返回的`tzCode`，`timeFormat`必填性、duration单位等来源缺项与匿名约束见[海外设备设置与公开目录](docs/research/2026-11-15-overseas-settings.md)；仅新加坡公开目录经SDK真实查询验证，九项设备接口仍为离线验证。

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
