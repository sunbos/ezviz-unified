# OTAP 物模型与 B1/B2 账户托管

本文说明 `client.otap` 与 `client.delegation` 的用法、参数规则与限制。九个接口仅有国内 `open.ys7.com` 契约依据；海外等价端点未在来源文档中出现，非国内 region 在发请求前抛出 `ValueError`。本批接口均未做真实调用验证。

## 来源文档

- OTAP：[1512](https://open.ys7.com/help/1512) 属性查询、[1513](https://open.ys7.com/help/1513) 属性设置、[1514](https://open.ys7.com/help/1514) 操作指令、[5891](https://open.ys7.com/help/5891) 期望值与最新上报值。[1307015](https://open.ys7.com/help/1307015)《物模型》仅作语义参考，不作为 HTTP 契约。
- 托管：[1890](https://open.ys7.com/help/1890) 申请授权码、[1891](https://open.ys7.com/help/1891) 账户级授权、[1892](https://open.ys7.com/help/1892) 资源列表、[1893](https://open.ys7.com/help/1893) 单设备授权、[1894](https://open.ys7.com/help/1894) 单设备解绑。

## `client.otap`（同步 `Otap` / 异步 `AsyncOtap`）

```python
resp = client.otap.get_property(
    "<deviceSerial>",
    resource_category="<category>",
    local_index="0",
    domain_identifier="<domain>",
    property_identifier="<prop>",
)
resp = client.otap.set_property(
    "<deviceSerial>",
    {"<attrKey>": "<value>"},  # 裸 JSON 属性对象，不包装 Body
    resource_category="<category>",
    local_index="0",
    domain_identifier="<domain>",
    property_identifier="<prop>",
)
resp = client.otap.invoke_action(
    "<deviceSerial>",
    {"<inputKey>": "<value>"},
    resource_category="<category>",
    local_index="0",
    domain_identifier="<domain>",
    action_identifier="<action>",
)
resp = client.otap.latest_property(
    "<deviceSerial>",
    resource_category="<category>",
    local_index="0",
    domain_identifier="<domain>",
    property_identifier="<prop>",
)
```

- **传输**：`deviceSerial`/`resourceCategory`/`localIndex`/`domainIdentifier`/`propIdentifier`（或 `actionIdentifier`）与 `accessToken` 全部置于 Header；两个 GET 无请求体、不声明 Content-Type；两个 PUT 发送 `application/json` 裸 JSON 对象。
- **payload**：调用方 `Mapping` 原样序列化，不包装 `Body` 键、不注入字段、不推断 `timeZone`；非字符串嵌套键、不可序列化值、`NaN`/`Infinity`、循环引用在发送前抛 `ValueError`；空对象本地放行。要求 JSON 对象为本地 SDK 形状（与文档示例一致），并非平台拒绝其他根类型的证明；属性 schema 与接受性由平台判断。
- **校验**：全部标识符要求非空白字符串，不做大小写规范化；`latest_property` 另按 5891 校验 `local_index`（`0` 或非零开头、不超过 10 位十进制）与三个标识符长度 <255。v3 三接口不套用这组限制——两套边界显式分离。
- **响应判定**：`meta.code==200`（整型）判成功；`invoke_action` 在 `meta.moreInfo.deviceMeta` 存在时校验 `code=="0x00000000"` 并保留 `errorMsg`，缺失时仅按平台层判定（`require_device_code=false`）。HTTP 状态与业务码不混用；仅 5891 声明了映射。
- **语义**：`get_property`/`latest_property` 读平台缓存/设备影子，不直达设备；`desired`/`reported` 的 `value` 为任意 JSON 类型或 `null`、`time` 为 ISO8601 或 `null`，两侧全 `null` 不是错误（`20002` 才是属性不存在）；不保证二者相等，也不据此推断在线状态。设备写入生效后须自行上报，SDK 不做写后自动核对。
- **超时**：服务调用为同步接口，平台最大等待 16 秒；门面不修改默认超时、不轮询、不重放写操作——需要完整等待请显式 `Client(timeout=大于16)`。

## `client.delegation`（同步 `Delegation` / 异步 `AsyncDelegation`）

B1 为设备归属账号、B2 为被托管账号；调用方客户端身份即 B1/B2 角色，SDK 不交换、不缓存、不代签对方凭据，也不从一方身份推导对方 appKey。

```python
# 以下均为独立的单次调用示例，文档未规定完整顺序或 sign 生命周期
grant = b2.delegation.apply_code("<B1的appKey>")            # 返回 data.sign / data.appKey
b1.delegation.authorize_account(grant.data["sign"])        # data 为 Boolean
items = b1.delegation.list_resources(
    grant.data["sign"],
    device_serial="<serial>",
    authorization_type="authorize",
    page_start=0,
    page_size=20,
)
b1.delegation.authorize_device(grant.data["sign"], "<serial>", state="<state>", local_index="<channel>")
b1.delegation.relieve_device("<B2的appKey>", "<serial>", "<state>", local_index="<channel>")
```

- **传输**：均 POST `application/x-www-form-urlencoded`，`accessToken` 在 Header；`deviceSerial`/`localIndex` 按参数表置于 Header（通道授权/解绑时 `localIndex` 必传），`sign`/`appKey`/`state`/`authorizeType`/`pageStart`/`pageSize` 在 form。
- **凭证不对称（合同事实）**：授权 `authorize_device` 用 `sign`；解绑 `relieve_device` 用 B2 的 `appKey` 加必填 `state`。`state` 无已声明枚举，SDK 不默认 `state=1`，仅要求非空白串并原样透传。
- **分页**：`page_size` 严格 int（非 bool）且不超过文档声明的最大值 20；下界未声明，不补造为官方限制；`None` 不发送以保留平台默认（pageStart=0/pageSize=10）；响应无 `total`/`hasMore`，SDK 不自动翻页。
- **Boolean data**：`authorize_account`/`authorize_device`/`relieve_device` 的 `data` 为"操作是否成功"，`false` 即操作未被确认成功；SDK 原样返回（含 `false`）而不强制改写，调用方须检查 `data`。未知的是 `meta.code=200` 且 `data=false` 时的失败原因与错误码映射。
- **敏感数据**：`sign` 登记为敏感字段，日志/异常脱敏而发送原文不修改；不解析 `appKey#hex#hex` 示例格式。
- **开通要求**：需平台人员后台开通且面向付费账号，属文档明确的前置条件；SDK 不激活服务、不计费、不预检资格，未开通时的具体错误码未列出。
- **无强制顺序**：完整调用顺序与 `sign` 生命周期均未在文档中规定，上方示例为可选的独立调用而非流程；每个方法对应一个逻辑端点；认证与既有只读重试可能增加HTTP请求数，SDK 不串联流程。仅 `list_resources` 为只读（可在令牌失效后刷新重试一次），其余写操作不自动重放。

## 与既有接口的边界

`delegation` 使用 `/api/v3/lapp/trust/sign/*` 与 meta 信封，属于不同 API 代际；与 `devices.list_managed`（`/api/lapp/trust/device/list`，form token + version=3.0 + code/msg 信封、pageSize≤50）及 `subaccounts`（`/api/lapp/ram/*`）不可复用，命名空间完全隔离。

## 已知文档冲突与未解决项

详见 `contracts/source-issues.json` 中 `cn.otap.*` 与 `cn.delegation.*` 条目：

- `meta.moreInfo.deviceMeta.code` 取值 `"0x00000000"` 仅见于示例，失败/超时路径是否出现未说明。
- 1513 成功示例 `meta.code=200` 却携带 `msgDetail="param error : 设备不支持该功能"`，不按其文本判失败也不丢弃。
- `localIndex` 格式约束仅 5891 明文；v3 接口是否同约束未定义。
- header 标识符与 JSON 键的大小写映射规则未定义（示例 `propIdentifier=TimeZone` vs 键 `timeZone`）。
- `sign` 有效期、单次使用性、跨调用复用未文档化。
- `state` 取值与语义未定义，授权可选/解绑必填的必填性相反。
- 1890 返回 `appKey` 标注“开发者B用户的应用key”与入参“B1账号的appKey”存在指代歧义。
- `authorizeType` 除 `authorize` 外取值、Header `deviceSerial` 过滤语义、翻页终止判断未说明。
- `isEncrypt` 粒度、`cameraInfo` 空值形态、`capturePic` 为 `null` 的条件未细分。

实施约定见 [OTAP与账户托管实施计划](../plans/2026-11-15-otap-delegation.md)；传输契约见 `contracts/reviewed-transport-contracts.json` 中 `cn.otap.*` 与 `cn.delegation.*` 条目。