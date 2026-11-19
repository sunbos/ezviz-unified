# 本地 SDK 调用诊断（schema_version=1）

`DiagnosticRecorder` 是显式 opt-in 的进程内记录器：构造实例并传给 `Client`/`AsyncClient` 的 `diagnostic_recorder` 后，SDK 在覆盖范围内记录调用阶段事件。默认 `None` 时不产生事件、文件、上传、额外请求或全局 HTTPX 修改，相关埋点为无副作用 no-op。没有全局 recorder，每个实例独立启用。

报告只是一份本地 JSON 字符串，供调用方排查并自行决定是否导出：它不是防篡改证据，不是遥测（SDK 不自动上传任何内容），不构成 RSI 信号，也不能用来证明做过真实设备验证或获得官方支持。

## 公开接口

```python
from ezviz_unified import Client, DiagnosticRecorder

recorder = DiagnosticRecorder()                    # max_events 默认 256
client = Client(..., diagnostic_recorder=recorder)  # AsyncClient 同形

with recorder.capture():    # 显式应用作用域：普通 with，同步/异步相同，可跨 await
    ...

snapshot = recorder.report()   # 独立 JSON 兼容 dict 快照
text = recorder.to_json()      # 仅返回 str
```

- `DiagnosticRecorder(*, max_events=256)`：`max_events` 仅接受真正的 `int`，范围 1–4096，拒绝 `bool`。
- `diagnostic_recorder` 仅接受 `DiagnosticRecorder` 实例或 `None`，不接自定义 sink/callback。
- `report()` 返回独立快照，修改快照不会反向污染 recorder；`to_json()` 只返回字符串，不写文件、不上传。
- `capture()` 可包构造、Python 参数绑定与应用代码；异常按原对象原 traceback 重抛，不吞掉业务异常；`with` 退出后发生的错误不补记。
- 不公开任意事件注入、自由文本标签、自动日志或 exporter 服务。

可运行示例：`python -m examples.diagnostics_sync` / `python -m examples.diagnostics_async`，同步与异步同形。环境变量与设备列表示例相同；stdout 在成功与失败时都只输出一份报告 JSON，保存请显式重定向（`> report.json`）。CLI 边界对已知配置与 SDK 失败返回 1、对中断（KeyboardInterrupt/CancelledError）返回 130；其余任何未预期异常（含 SystemExit 与未知 BaseException）也在最外层收敛为返回 1。stderr 只有固定提示，不回显异常 message 或 traceback。

## 报告结构

顶层仅六个字段：`schema_version`（固定 1）、`scope`（固定 `local_sdk_diagnostics`）、`max_events`、`dropped_events`、`recording_errors`、`events`。每个 event 仅含下表白名单字段；未知值一律归 `other`/`unknown`，不适用的 error 类字段可省略，缺失按 `none` 处理。

| 字段 | 取值 / 语义 |
| --- | --- |
| `sequence` / `span_id` / `parent_span_id` | 整数标识：`sequence` 随每个事件单调递增；`span_id` 仅在分配新 span 时递增，同一 span 的 `started` 与终态事件复用同一值；`parent_span_id` 引用父级 `span_id`，无父级时为 `null`，可重复且不随事件顺序递增。均不承载任何用户或设备标识 |
| `kind` | `call` / `authentication` / `transport_setup` / `attempt` / `retry` / `close` / `cleanup` / `inspection` / `application` |
| `entrypoint` | 覆盖清单内的入口名，否则 `other` |
| `operation` | 覆盖清单内的操作名，否则 `other`；只取 SDK 固定的 `Operation.name`，不从异常文本反推 |
| `mode` | `sync` / `async` / `application` |
| `region` | `domestic` / `overseas` / `unknown`；`RegionConfig` 未知子类归 `unknown`，不额外调用其 getter |
| `outcome` | `started` / `succeeded` / `failed` / `cancelled` / `interrupted` / `scheduled` |
| `error_family` | `none` / `validation` / `authentication` / `permission` / `device` / `api` / `protocol` / `transport` / `application` / `unknown` / `interrupted` |
| `reason` | `none` / `timeout` / `network` / `closed` / `other` |
| `code_category` | `none` / `invalid_token` / `permission_denied` / `other`；仅把真正 `str`/`int` 的 10002→`invalid_token`、10031→`permission_denied` 映射入报告，其他业务码不输出。该字段按脱敏后的公开异常 `code` 投影；当请求标识与字符串业务码碰撞、码被遮蔽为 `***` 时此字段为 `other`，而 `error_family` 仍为 `authentication`——`AuthenticationError.invalid_token` 按原始分类决定是否允许现有只读重放 |
| `http_status_category` | `none` / `informational` / `success` / `redirect` / `client_error` / `server_error` / `other`；仅对真正 `int`（`bool` 除外）的 100–599 分桶，不输出原始数值 |
| `attempt` | `0` / `1` / `2`；`0` 表示不适用。**它不是 HTTP 请求总数**，也不是耗时统计 |

## 隐私边界

报告禁止出现：密钥、token、验证码、设备序列号、任何 URL、路径参数、原始请求/响应、运行环境、异常消息/类名/异常链/locals、用户自定义标签及它们的普通 hash。事件不含时间戳、墙钟时间、耗时或环境采样，也没有隐式后台任务。分类只对类型精确匹配的 SDK 异常读取白名单标量属性（如 `code`、`http_status`），不调用 `str()`/`repr()`，不对未知子类执行任何 property/getter；只有真正的内建标量（非 `bool` 的 `int`、`str`）可写入字段，其他异常只归类，类名不外泄。

## 缓冲、截断与并发

- 事件缓冲是有界 deque：超过 `max_events` 丢弃最旧事件并累计 `dropped_events`。
- 截断可导致 `started` 或父级事件缺失：消费者不得假设 span 闭合，只能在可见事件内用 `parent_span_id` 关联。
- `recording_errors` 统计记录器内部失败：内部失败不替换 SDK 返回值或异常、不吞掉调用方异常，但计数明确可见；非 0 表示报告不完整。
- 线程间由锁同步；异步任务的 parent 用 ContextVar 隔离，跨 task 不串线。

## 覆盖范围

自动覆盖明细以冻结清单 `docs/diagnostics-v1-coverage.json` 为准：

- 165 个公共业务入口的同步/异步方法体（共 330 处，含设备管理继承方法、云录制计划继承方法与 `ptz.cruise`）：kind=`call`，记 `started` 与终态；作用域在 docstring 之后、首条校验之前，不改签名、不重复包装继承方法。`components.call` 在 `operation_id` 查找前进入作用域，未知 operation 记 `other`。新增的 `settings.list_areas`（公开国家/地区目录，GET `/api/lapp/area/list`）与其余入口相同，地区校验失败在子 scope 之前发生。云录制 1.0 的 `cloud_recording_v1.unfreeze_file`/`file_info`/`download_urls` 与云点播 `vod.files`/`create_remux_task`/`task`/`tasks`/`recording_urls` 八个国内入口同等计入，facade scope 覆盖各自的校验、认证、传输与业务终态。云抓图 `cloud_capture.create_interval_task`/`create_timing_task`/`task`、消息订阅 `message_subscriptions.create_consumer`/`consume` 与 HUB 子设备 `hubs.list_subdevices`/`subdevice_info`/`channels` 八个国内入口同样计入。
- `Client.request`/`AsyncClient.request`：含关闭拒绝与 `_prepare`。
- `authentication`：`get_token`、同步上下文对协程的拒绝、`await`、`validate_provider_token`；普通 provider 异常保留阶段信息。匿名路径（`anonymous=True`）不产生 authentication 事件。`authentication` 记 `succeeded` 可能命中既有 token 缓存，不代表本次发出过认证 HTTP 请求。
- `transport_setup`：仅 transport factory/origin 校验与注入，不含任何真实请求。
- `attempt`：仅 `transport.request` 的一次尝试；`started` 只表示进入发送路径，不证明 HTTP 请求已发出或被对端接收。
- `retry`：仅在既有 invalidate 与重试判定之后、确实进入第二次尝试前记 `scheduled`；不新增重试，写操作不因此重试。
- `close`/`aclose` 记调用结果；异步 `_cleanup` 是独立事件。
- `inspect_device`/`inspect_device_async` 在首条校验前进入 kind=`inspection` 作用域，子调用经 `parent_span_id` 关联。

明确不覆盖：

- `Client`/`AsyncClient` 等构造函数本身、业务方法调用时的 Python 参数绑定错误（发生于函数体执行之前）——把它们放进 `with recorder.capture()` 可记为 application 事件。
- 创建后从未被等待或调度运行的 coroutine：方法体不执行，不产生事件。`capture()` 不收集脱离其退出链的应用异常（例如作用域内启动、退出后才运行的后台任务抛出的异常），也不补记；但此类任务内经 `diagnostic_recorder` 启用的 SDK 调用仍照常产生事件。
- 独立的 `TokenManager`/Transport/DTO/`decode_response`/`parse_webhook` 等模块级函数、`components.describe`/`list_documented`。
- `capture()` 作用域外的应用代码与外部进程。

## 时序与语义边界

- 同步：方法被调用即进入作用域；方法体内异常先记终态再原样传播。
- 异步：调用 async 方法先发生 Python 参数绑定（绑定错误在调用时抛出，只有 `capture()` 能记录）；coroutine 被等待或调度运行时才进入方法体与作用域；取消在 await 点记 `cancelled`；未 await 不虚构事件。
- `CancelledError`→`cancelled`，`KeyboardInterrupt`/`SystemExit`→`interrupted`，均原样重抛。
- 只有确切的内建 `ValueError`/`TypeError`（不含其子类）在尚未进入任何子 scope 的 `call`/`inspection` 本层抛出时才归 `validation`；其未知子类不归 `validation`，在 SDK 调用内归 `unknown`、在 `capture()` 内纯应用代码中归 `application`。provider、transport factory 等子 scope 抛出的异常向外传播时保留其原有阶段信息，外层 scope 不得重标为 `validation`；authentication 阶段的普通 provider 异常保留阶段信息，`error_family` 可为 `unknown`。
- `inspection` 事件 `succeeded` 只表示助手正常返回 `DeviceInspection`，不代表设备健康或五个子调用全部成功；逐项成败看各子 `call` 事件的终态，不要把 `public_summary()` 的聚合结论并入本严格报告。
- `aclose` 的 waiter 取消只表示该 waiter 被取消，不等于 `_cleanup` 完成；清理任务的完成/失败由独立 cleanup 事件记录。
- 记录器内部路径（scope 构造、context push/pop、事件写入、错误计数）只隔离普通 `Exception`：若该位置收到 `KeyboardInterrupt`/`SystemExit`/`CancelledError` 或其它非 `Exception` 的 `BaseException`，不当内部故障吞掉——进入阶段中断则业务体不执行；退出阶段中断可覆盖已完成的业务体，但原控制对象原样传播；中断前已安装的上下文先尽力恢复（根部回到 `None`，嵌套恢复原 parent），恢复或计数路径中再次到达的新控制异常同样不被吞。
- 因此控制异常可使报告缺 `started` 或终态事件，且计数本身也可能被中断：`recording_errors == 0` 不证明报告完整，只说明计数路径未遇到普通故障。

## 导出责任与报告效力

`to_json()` 只返回字符串：写文件、上传、进日志都是调用方的显式动作（示例 `print` 到 stdout，由读者自行重定向）。报告不含防篡改结构：不能凭它证明真实调用确实发生、证明覆盖数量、设备能力或官方支持。`entrypoint`/`operation` 落入 `other` 仅表示不在冻结清单内，不表示该调用不存在。

## 验证状态

验收案例与判定标准以冻结设计为准。截至 2026-11-16：离线全量测试、契约/传输评测与发行包检查已通过；国内单设备、单通道的单次同步只读实机验收完成（认证、设备列表与 inspect_device 固定五项，报告字段与值白名单检查通过），执行证据见[验收记录](validation/2026-11-16-diagnostics-v1.md)与[实机记录](validation/live/2026-11-16-diagnostics-v1/README.md)。该结果不代表异步或海外真实 API、全部型号/字段、硬件健康或示例 CLI 联网行为；Git 集成门槛单独核对，本文档不做完成声明。
