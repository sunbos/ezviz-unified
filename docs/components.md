# 国内组件目录

`client.components` 与 `AsyncClient.components` 是一小批已审阅 open.ys7.com 文档的登记入口。这批文档把 `deviceSerial` 放在请求路径里（`/api/v3/otap/{prop,action}/{deviceSerial}/...`），与头部式 `client.otap`（`/api/v3/device/otap/...`）是两套独立契约：没有来源声明二者等价或可替代，因此不合并、不互推，共享的只是传输、认证、编码和响应检查。

目录是文档登记，不是设备能力检测：SDK 不查询型号、不枚举设备、不尝试替代路径。通用路径模板能力不等于全部 API 支持；文档列举的型号不代表其他型号不可用，真实设备与型号可用性未验证。

## 本地元数据

`describe(operation_id)` 与 `list_documented()` 完全本地执行，不发请求、不读凭据。返回的 `ComponentInfo` 提供 `operation_id`、`source_doc_id`、可点击的 `source_url`、`raw_path`、`method`、`documented_models`、`whitelist_required`（源文档未声明时为 `None`，表示未知而非否）、`read_only`、`read_only_evidence`、`callable`、`limitations`。

```python
from ezviz_unified import Client, RegionConfig

# 构造客户端本身不发起认证；describe 与 list_documented 无网络 I/O。
with Client("placeholder-key", "placeholder-secret", region=RegionConfig.china()) as client:
    info = client.components.describe("cn.components.smoke_countdown.get")
    print(info.source_url)  # https://open.ys7.com/help/1447
    print(info.documented_models, info.callable)
    for entry in client.components.list_documented():
        print(entry.operation_id, entry.callable)
```

## 可调用条目

`call(operation_id, device_serial, *, payload=...)`：`device_serial` 为路径参数，`payload` 仅关键字传入，省略时表示"未提供"（与 `None` 含义不同）。调用返回 `ApiResponse`。

| operation_id | 来源 | 方法与路径 | 声明型号 | 白名单 | payload |
| --- | --- | --- | --- | --- | --- |
| cn.components.face_voice_video.get | [doc 774](https://open.ys7.com/help/774) | GET `/api/v3/otap/prop/{deviceSerial}/Video/1/DoorLockCtrl/FaceRecognitionVoiceTipSwitch` | CS-DL30-V100系列、CS-Y3000F-V100系列 | 文档要求配置 | 不提供 |
| cn.components.face_voice_video.set | [doc 775](https://open.ys7.com/help/775) | PUT 同上路径 | CS-DL30-V100系列、CS-Y3000F-V100系列 | 文档要求配置 | 映射，仅 `enabled`（Boolean，文档声明可省略） |
| cn.components.face_voice_lock.get | [doc 812](https://open.ys7.com/help/812) | GET `/api/v3/otap/prop/{deviceSerial}/DoorLock/0/DoorLockCtrl/FaceRecognitionVoiceTipSwitch` | CS-DL30-V100系列、CS-Y3000F-V100系列 | 文档要求配置 | 不提供 |
| cn.components.temperature.get | [doc 1134](https://open.ys7.com/help/1134) | GET `/api/v3/otap/prop/{deviceSerial}/global/0/Humiture/Temperature` | CS-T51C-BG | 未声明 | 不提供 |
| cn.components.smoke_countdown.get | [doc 1447](https://open.ys7.com/help/1447) | GET `/api/v3/otap/prop/{deviceSerial}/SmokeSensor/0/PowerMgr/CountdownInfo` | CS-T4C-BG | 未声明 | 不提供 |
| cn.components.gas_countdown.query | [doc 998](https://open.ys7.com/help/998) | PUT `/api/v3/otap/action/{deviceSerial}/GasSensor/0/PowerMgr/GetPowerSwitchCountdown` | CS-T8C-DG | 未声明 | 必须显式 `payload=None` |
| cn.components.chime_power.set | [doc 868](https://open.ys7.com/help/868) | PUT `/api/v3/otap/prop/{deviceSerial}/Default/0/PowerMgr/SurplusPower` | CS-CMT-CHIME-B | 未声明 | 裸整数 0–100，拒绝 bool |

payload 规则逐文档执行，不放宽：

- 四个 GET（774/812/1134/1447）不携带正文，传入任何 payload 都会在认证前报错；文档的参数表与 curl 均声明 `Content-Type: application/json`，因此即使无正文也按官方要求发送该头。
- doc 998 用 PUT 发送，但正文声明该接口用于查询倒计时信息——HTTP 方法只是文档记载的传输形式，语义只读（`read_only=True`）。它必须显式 `payload=None`，线上发送根级 JSON `null`；省略 payload 会被拒绝。
- doc 775 的 payload 必须是显式提供的映射，只接受 `enabled` 一个 Boolean 字段（文档声明该字段可省略）；空对象对设备的实际效果未验证。
- doc 868 的 payload 是 `[0, 100]` 的裸整数，`bool` 被拒绝；SDK 原样转发数值，不换算单位，也不把它解释为改变物理电池容量（物理效果未确认）。

各条路径除 `deviceSerial` 外保持文档固定字面段：Video/1、DoorLock/0、GasSensor/0、SmokeSensor/0 等资源路径各自保持文档固定写法，SDK 不会自动互换；来源未证明它们等价或可替代。

## 不可调用条目

九条目录中两条只保留元数据：

- `cn.components.hdd_format.invoke`（[doc 847](https://open.ys7.com/help/847)）：格式化本地磁盘的破坏性动作，且文档未给出明确选盘规则。
- `cn.components.human_detection.set`（[doc 3873](https://open.ys7.com/help/3873)）：`{{deviceSerial}}` 与 `["0"]` 的模板拼写含义未确认，也无型号适用性声明。

对二者调用 `call` 会在认证前抛出固定 message 为 `component operation is documented but not callable` 的 `ValueError`；未知 `operation_id` 也会抛 `ValueError`，但固定 message 为 `unknown component operation_id`——只是同一异常类型，不保证同一个 message，两种 message 均不回显用户传入的 ID。

## 只读调用示例

```python
import os
from ezviz_unified import Client, RegionConfig

with Client(
    os.environ["EZVIZ_APP_KEY"],
    os.environ["EZVIZ_APP_SECRET"],
    region=RegionConfig.china(),
) as client:
    response = client.components.call(
        "cn.components.temperature.get",
        os.environ["EZVIZ_DEVICE_SERIAL"],
    )
    print(response.data)
```

异步用法相同：`AsyncClient` 与 `await client.components.call(...)`，参数一致。

## 响应证据层

平台层按 `meta.code == 200` 判定；当响应携带 `meta.moreInfo.deviceMeta` 时，其 `code` 必须等于 `"0x00000000"`。九条文档中只有 doc 1447 的返回表明确定义 `0x00000000` 为设备成功码，其余仅在成功示例中出现该层。"该层可选"是 SDK 的处理策略，不是源文档的保证：层缺失时仅凭 `meta.code` 判定，证据更弱，不证明设备物理执行完成。

## 低层路径参数与 JSON null

- `Operation.path` 支持整段占位符 `{name}`（名字仅 ASCII 标识符）。`client.request(op, path_params={"deviceSerial": ...})` 传未编码的单个路径段，SDK 做一次 UTF-8 百分号编码；原始 `/`、`\`、单独的 `.`/`..`、控制字符、全空白值、键不匹配或非字符串值都会在认证前拒绝；非全空白字符串的首尾空格会保留并编码，不做 trim。不要传预编码值：`a%2Fb` 会按字面数据再编码为 `a%252Fb`。
- `path_params` 的原始值与编码值进入本次请求的脱敏上下文（SDK 错误与请求日志适用）；但自行拼接的固定路径无法被自动识别为敏感值，业务响应 `data` 的内容也不在该脱敏保证范围内。
- 根级 JSON `null` 使用公开常量 `JSON_NULL`：`json=JSON_NULL` 发送 `b'null'`，而 `json=None` 保持省略语义；嵌套 null 仍用普通 `None`。`form`/`json` token 位置不支持根 null，因为无法向 null 注入受管 token。
