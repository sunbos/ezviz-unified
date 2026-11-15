# 海外设备设置与公开时区目录（2026-11-15）

本批依据海外文档477的1.14–1.19、1.21–1.22、1.24–1.25小节，为`Settings`/`AsyncSettings`增加九项设备设置读写和一项公开时区目录；同步/异步同名，均返回`ApiResponse`，`.data`保留官方字段。十项全部仅限overseas，国内客户端调用会在请求前被拒绝。设计决定与来源清单见[实施计划](../plans/2026-11-15-overseas-settings-and-public-requests.md)。

## 入口与字段

| 方法 | 参数 | 路径 | 字段与说明 |
| --- | --- | --- | --- |
| `get_pir_area` | `device_serial, *, channel=None` | `/api/lapp/device/pir/get` | `deviceSerial`、可选`channelNo` |
| `set_pir_area` | `device_serial, area, *, channel=None` | `/api/lapp/device/pir/set` | `area`为官方原始字符串，SDK不解析、不重组、不改写 |
| `get_chime` | `device_serial, *, channel=None` | `/api/lapp/device/chime/get` | 只读 |
| `set_chime` | `device_serial, chime_type, duration, *, channel=None` | `/api/lapp/device/chime/set` | `type`、`duration`；`chime_type`仅限非bool整数1/2/3 |
| `get_infrared` | `device_serial, *, channel=None` | `/api/lapp/device/infrared/switch/get` | 只读 |
| `set_infrared` | `device_serial, enabled, *, channel=None` | `/api/lapp/device/infrared/switch/set` | `enabled`仅bool，映射为`enable`整数0/1 |
| `list_timezones` | `*, language=None` | `/api/lapp/timezone/list` | 公开请求，无token；`language`仅`ENGLISH`/`SIMPCN`，`None`由服务端默认 |
| `set_timezone` | `device_serial, timezone, *, time_format, daylight_saving=None` | `/api/lapp/device/timezone/set` | `timezone`、`timeFormat`、可选`daylightSaving` |
| `get_language` | `device_serial` | `/api/lapp/device/language/get` | 只读 |
| `set_language` | `device_serial, language` | `/api/lapp/device/language/set` | `language`取值来自设备能力字段，不复用时区目录枚举 |

公共规则：

- 全部POST，成功`code`为字符串`"200"`；除`list_timezones`外均为form位置token。get/list为`read_only=True`，写方法为False且不自动重放。
- `device_serial`、`area`、`timezone`、设备`language`为非空UTF-8字符串，大小写与内容原样保留。
- `channel=None`时省略`channelNo`，显式`0`会保留发送；`channel`与`duration`只校验非bool整数，不猜范围、不设默认通道、不假设duration单位（官方未给出）。
- `timezone`必须传时区目录返回的`tzCode`，SDK不做IANA名称转换；`daylight_saving`仅bool或`None`，映射为字符串`"0"`/`"1"`，`None`省略该字段。
- `time_format`仅非bool整数0/1/2，且必须显式传入——这是SDK的本地设计；官方必填性未标明，契约中记为`required: null`加非空`required_uncertainty`，不写成已知必填或已知可选。

## 来源差异

官方页面中声明路径与示例路径不一致、示例额外出现未在声明参数表中的`enable`参数、`devceSerial`/`Strring`等字段拼写typo、duration单位缺失、`timeFormat`必填标记缺失等冲突原样保留在[source-issues.json](../../contracts/source-issues.json)；SDK一律使用接口声明的路径与字段，不照抄错误示例。

## 公开请求与匿名模式

- `Operation.token_location="none"`把操作标记为显式公开请求：完全不调用token provider，不获取/刷新/失效token，不向任何字段注入token；`10002`按原响应错误抛出，不重试。
- `Client`/`AsyncClient`新增`anonymous: bool = False`：默认仍要求成对应用凭据或provider，原凭据初始化规则不变。匿名模式必须不带应用凭据、token_provider和auth_transport，且`RegionConfig.api_base_url`已显式配置；匿名客户端调用需认证的Operation时在请求前抛出固定`ValueError`。
- 公开操作即使由普通认证客户端调用，也只使用已配置的`api_base_url`；缺失时在调用provider/网络前抛出固定`ValueError`。`auth_base_url`不会被当作业务区域，不凭空创建token，不猜测地域。
- 匿名模式复用现有传输缓存、同步锁、异步关闭与取消语义，不允许绕过受管`accessToken`/`Host`字段限制；既有校验、日志脱敏、区域校验、参数拷贝与凭据生命周期规则不变。

## 验证状态

- 2026-11-15重新采集国内774、775、1512–1514与海外476、477、3722–3730共16份来源，正文哈希与归档一致。
- 九项设备调用仅完成离线测试（MockTransport与契约评测）；没有可用海外设备，未做任何真实设备写入，不能声称设备动作已生效。
- 仅新加坡公开时区目录完成真实验收：直接HTTP与SDK同步/异步`list_timezones`无凭据查询均返回`code "200"`及105项时区；默认与`ENGLISH`数据相同，`SIMPCN`的105项名称不同。记录见[singapore.json](../validation/live/2026-11-15-public-timezones/singapore.json)。
- 该结果只证明此区域的公开目录可查，不推广到其他海外区域、全部设备或任何设备写入；SDK仍未发布PyPI。
