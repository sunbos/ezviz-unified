# 海外公开国家/地区目录（2026-11-17）

本批依据海外文档469（§1.12），为`Settings`/`AsyncSettings`增加公开国家/地区目录`list_areas`：同步/异步同名，返回`ApiResponse`，`.data`为原始目录数组，未经逐条校验、去重或改写。入口仅限overseas；国内客户端调用在请求前抛出固定`ValueError`。设计决定与来源清单见[实施计划](../plans/2026-11-17-public-area-directory.md)。

## 入口与字段

| 方法 | 参数 | 路径 | 字段与说明 |
| --- | --- | --- | --- |
| `list_areas` | 无 | `/api/lapp/area/list` | 公开请求，无token；响应`data`为目录数组，条目文档字段为`id`(int)、`name`(string)、`region`(string)、`telephoneCode`(int) |

公共规则：

- 每次调用恰好一次**GET**请求：无query、无body、无`accessToken`、无`Authorization`头，不复用`_overseas_op`（该构造器固定POST）。
- `Operation.token_location="none"`：不调用token provider、不取/刷新/失效token、不注入token；`10002`按原响应错误抛出，不进入刷新重试分支。
- `.data`保留原数组：额外字段与空数组原样透传；`id`/`name`/`region`/`telephoneCode`仅为文档记载，SDK不逐条断言类型、不写死条目数。
- 调用方经`RegionConfig.overseas(api_base_url=...)`显式指定业务origin；文档示例固定地址为`https://open.ezvizlife.com`。SDK不从响应中推导cluster、`apiDomain`或`authDomain`。

## 方法选择：GET是SDK的显式设计

文档声明同一路径同时支持GET与POST（e1463），HTTP示例只演示了一个**无参数GET**（e1468）；POST的请求形状——参数、体编码、是否需要token——未被文档化。SDK因此**只选择GET**：

- 未把POST伪造为`declared_pair`，未猜测其body编码或token语义；
- 该差异登记为`source-issues.json`中的`global.settings.list_areas.post_variant_undocumented`（`unresolved_source_semantics`），而非静默省略；
- 契约`body_encoding`记为`"unknown"`，同时在constraints中显式声明SDK选定的GET不携带query与body；注册表允许`unknown`，评测器据此只校验请求体为空。

## 官方事实、SDK设计与一次性观测的边界

- **官方事实**：路径`/api/lapp/area/list`，文档演示地址`https://open.ezvizlife.com`，成功`code`为字符串`"200"`，返回字段表列出`id`/`name`/`region`/`telephoneCode`四项。
- **SDK设计**：GET-only、`token_location="none"`、`read_only=True`、`fields={}`、origin由调用方配置；这些都是本地契约决定，不是文档对每种组合的逐一保证。
- **一次性观测（2026-11-16，SP016）**：匿名GET在`open.ezvizlife.com`返回HTTP 200、`code "200"`、240项。该观测是协议结构证据，不是SDK验收：`240`只是当时计数，不是固定目录大小；其他origin未经实测；不据此声称“全海外可用”或“已实机验证”。
- **未确认项**：其他海外origin是否同样提供该目录、服务是否容忍多余query、POST变体的真实形状，均未文档化；对应`uncertainties`保留在契约中。

## 验证状态

- 2026-11-17采集海外469（`https://open.ys7.com/help/en/469`），正文SHA与归档一致。
- **离线验证（已过，证据 [checks.json](../validation/2026-11-17-public-areas/checks.json)，代码 commit `58e7881`）**：初始 targeted 3 failed / 311 passed，经定向修订（修正 facade/request 诊断期望、隔离 CLI argv、补诊断失败等价性与有界 CLI 摘要；未删除断言换取通过，3 条历史失败记录保留于 `initial_targeted_result`）后全部通过——SDK/harness targeted 327、示例 22、全量 4580 + 35 subtests、registered contracts 204 / wire executions 1664 全过、mypy 43 文件、Ruff check/format 通过；wheel/sdist `0.1.0a1` 已构建、内容检查、Twine 校验与干净安装（sdist 2744 测试通过；安装依赖走网络，SDK smoke/测试不触真实 API）。`max_requests=0` 指真实云请求，MockTransport 调用不冒充真实操作。
- **SDK 真实验收（已过，证据 [sdk.json](../validation/live/2026-11-17-public-area-directory/sdk.json)，同一 commit）**：UTC 2026-11-17T02:52:27 对固定 `https://open.ezvizlife.com` 匿名 sync/async 各一次，合计 2 HTTP / 2 逻辑调用；两 mode 均 HTTP 200 + 业务码 `"200"`，`data` 为 240 项数组且全部 240 项 `id:int`/`name:str`/`region:str`/`telephoneCode:int` 四字段类型匹配；identity 编码、原始响应字节每 mode 16442 字节、客户端与 transport 均正确关闭。回执只保存计数/类型/状态/来源与代码 hash（46 项输入 pin 与 code58 Git 版本一致），报告 SHA `88cce20d…7adc`。
- **边界**：240 是当次观察值而非固定目录大小或保证；仅验证 GET + identity 协商，不证明其他 origin、POST 变体、压缩响应路径、集群路由、海外设备或硬件；SP016 的 raw HTTP 观察（`raw-http.json`）是历史协议结构证据，不充作 SDK 实测。该 cohort 通过不解除海外无绑定设备、消息配置未知等其它 required 行的阻塞。
- 产品范围仍 `provisional`：本方法所在 cohort `PUBLIC-AREAS-001` 已按 `scope_revision=2026-11-17-r1` 验证，但 scope-register 当前为 `2026-11-17-r2`（253 槽位，其中 5 条国内 voice 仅为 review 候选、无实现或 G1/live 授权），`product_complete=false`，未上 PyPI。
