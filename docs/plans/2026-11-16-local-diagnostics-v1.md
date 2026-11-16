# 本地安全诊断报告 v1：固定设计与验收计划

基线：`b2b6dca`，已 fetch 核对 main 与 origin/main 一致、工作区干净。用户已授权 Codex 固定架构、Claude Code 实现和测试、PR 合并及只读验收。开发分支 `codex/local-diagnostics-v1`。本设计不涉及新的业务协议或官方能力声明。

## 决策与复用

仅 HTTP hook 无法观察 facade 校验；通用代理/动态 wrapper 可能改变异步参数绑定时序。采用**原方法体内作用域 + Client 阶段事件**，保留原签名、调用时序及异常传播；不修改 HTTPX、transport、auth 的现有策略。复用 errors.py 分类、Client 认证与重试、inspection 固定五步检查和 live 工具的本地凭据通道。已有脱敏继续用于原错误，不把其任意字符串输出直接当安全报告。

增量是可分享的调用故障分类与明确覆盖证据，不重造播放器、认证或设备检查，也不推导设备健康、安全配置或 RSI。官方生态边界见 `docs/research/2026-11-15-official-ecosystem-reuse.md`。

## 冻结公共接口

- `DiagnosticRecorder(*, max_events: int = 256)`；仅接受真正的 int，范围 1..4096，拒绝 bool。实例明确启用，无全局 recorder。
- `Client(..., diagnostic_recorder: DiagnosticRecorder | None = None)` 和 AsyncClient 同形；只允许本类实例或 None，不接自定义 sink/callback。
- `recorder.report() -> dict[str, object]` 返回独立 JSON 兼容快照；`recorder.to_json() -> str` 仅返回字符串，不写文件或上传。
- `with recorder.capture(): ...` 是显式应用作用域；同步/异步代码都用普通 with，支持跨 await。不会吞掉业务异常。此作用域可包构造、Python 参数绑定错误及应用代码。
- 不公开任意事件注入、任意文本标签、自动日志或 exporter 服务。

## 冻结报告 schema_version=1

报告顶层仅 `schema_version`、`scope`（固定 local_sdk_diagnostics）、`max_events`、`dropped_events`、`recording_errors`、`events`。事件固定字段：

| 字段 | 值白名单 / 语义 |
| --- | --- |
| sequence / span_id / parent_span_id | 本 recorder 单调整数，parent 无父级为 null；没有用户或设备标识 |
| kind | call / authentication / attempt / retry / close / cleanup / inspection / application |
| entrypoint | 覆盖清单 business_entrypoints + additional_entrypoints，否则 other |
| operation | 覆盖清单 operation_names，否则 other；只取固定 SDK Operation.name，不能从异常文本反推 |
| mode | sync / async / application |
| region | domestic / overseas / unknown |
| outcome | started / succeeded / failed / cancelled / interrupted / scheduled |
| error_family | none / validation / authentication / permission / device / protocol / transport / application / unknown / interrupted |
| reason | none / timeout / network / closed / other |
| code_category | none / invalid_token / permission_denied / other；仅 10002、10031 的真正 str/int 映射，其他码不输出 |
| http_status_category | none / informational / success / redirect / client_error / server_error / other；只对真正 int 的 100..599 分桶 |
| attempt | 0 / 1 / 2；0 表示不适用，不是 HTTP 总数 |

可省略不适用的 error 字段，但实现和文档须一致。不得新增自由文本。禁止密钥、token、验证码、设备序列号、任何 URL、路径参数、原始请求/响应、环境、异常消息/类名/链/locals、用户标签及其普通 hash。未知值归 other/unknown。只读异常的既有类型和基础属性来分类，不调用 str/repr；未知自定义异常不输出类名。报告不保证防篡改，不能凭报告证明实测或官方支持。

缓冲区为有界 deque，超限删除最旧事件并累计 dropped_events。截断可导致父级或 started 事件缺失，消费者不得假设闭合；线程同步保护，异步任务用 ContextVar 隔离 parent。只保留安全标量，不保留异常、客户端、参数和响应引用。无时间戳、墙钟、耗时、环境采样或隐式后台任务。快照修改不能反向污染 recorder。记录器内部失败不得替换 SDK 返回/异常；内部失败计数明确可见，但不捕获并吞掉调用方异常。

## 冻结内部协作接口

`src/ezviz_unified/_diagnostic_registry.py` 提供 `ENTRYPOINTS: frozenset[str]`、`OPERATIONS: frozenset[str]`，由冻结覆盖清单生成静态常量，不在运行时读取 contracts/docs 或扫描模块。

`src/ezviz_unified/diagnostic_report.py` 提供公开类，以及内部：

```python
_diagnostic_scope(client: object, *, entrypoint: str, mode: str,
                  kind: str = "call", operation: object = None,
                  attempt: int = 0) -> ContextManager[None]
_diagnostic_retry(client: object, *, mode: str, operation: object) -> None
```

内部只从 Client 的 `_diagnostic_recorder` 及 `_region.scope` 读取必要状态；无 recorder 或替身 client 缺字段时是无副作用 no-op，不新增校验失败。operation 传 Operation 对象，仅合法固定 name 经白名单投影。作用域分别记录 started 与终态；BaseException 保持原对象原 traceback 传播，CancelledError 为 cancelled，KeyboardInterrupt/SystemExit 为 interrupted。应用作用域中的 ValueError/TypeError 归 application，SDK调用体中的同类归 validation；authentication 阶段中的普通 provider 异常仍保留阶段信息，error_family 可为 unknown。

## 入口覆盖和边界

冻结明细见 `docs/diagnostics-v1-coverage.json`：132 个公共业务路径，每项同步/异步共 264 个方法体，含设备管理继承方法、云录制计划继承方法及 `ptz.cruise`。每个方法在 docstring 后、首条校验前加作用域，不包装/改签名。不重复包装继承的方法。`components.call` 先于 operation_id 查找捕获未知输入，未知 operation 记 other。

Client.request 包含关闭拒绝和 _prepare；authentication scope 包含 get_token、同步协程拒绝、await、validate_provider_token；attempt scope 包含 transport factory/origin 校验、注入和 transport.request。retry scheduled 仅在现有 invalidate 与重试判定之后且确实进入第二次尝试前记录，不新增重试。匿名路径不产生 authentication 事件。

close/aclose 记录调用结果；async `_cleanup` 独立记录既有清理任务的完成/失败。aclose waiter 取消只表示 waiter 取消，不宣称清理完成，不新增任务。inspect_device 两个助手在首条校验前加 inspection scope；部分失败仍保持助手原语义，子调用失败可见，外层 succeeded 不表示设备健康或所有子调用成功。

自动覆盖不含 constructor、函数体执行前的 Python 参数绑定、从未 await 的 coroutine、独立 TokenManager/Transport/DTO/解析/webhook 函数、Components.describe/list_documented、作用域外应用代码及外部进程。显式 capture 可覆盖构造、参数绑定和应用异常；普通 with 不记录退出后错误。对这些边界逐项文档化，不能称全 SDK / HTTP 全量诊断。

## 验收案例与证据

- A01 默认关闭：无事件、文件、上传、额外请求/任务或全局 HTTPX 修改。
- A02 容量与快照：1、256、4096；非法容量；截断计数；独立快照；线程和 async task 隔离。
- A03 隐私：在所有输入及错误属性放合成 sentinel，恶意 Operation.name/code/reason/http_status/异常子类归类；报告和 repr 不含 sentinel；不调用 str/repr/序列化任意对象。
- A04 覆盖：逐项 132×2 在认证前触发输入失败/绑定失败或受控调用，证明覆盖清单无漂移、实际方法进入作用域。合法参数路径由现有独立 wire eval 与新增代表调用联合核验；覆盖数不代表真实设备数。
- A05 分类：ValueError/TypeError、认证失败/异常 provider、10002恢复/重试失败、无刷新/写操作不重试、permission/device/protocol/API/timeout/network/closed/unknown。
- A06 行为一致：启用与关闭记录器对同一合成场景的响应身份、异常身份、实际 request 顺序与次数、token获取/失效顺序一致。保留 async 参数绑定发生在调用时，取消在 await 时，未 await 不虚构事件。
- A07 生命周期：close 幂等；aclose 取消与 cleanup 后完成独立；认证/请求中取消；KeyboardInterrupt/SystemExit；不吞异常；上下文异常与 close 异常仍按原规则。
- A08 inspection：固定五项顺序、部分失败、认证中止、前置校验、借用 client 不关闭；嵌套 parent 清楚，无串线。
- A09 应用：capture 内构造/参数绑定/应用异常重抛；capture 外不记录；未知异常仅归类。
- A10 示例/文档：同步/异步最小示例、显式导出、默认不开启、无import副作用、字段与未覆盖列表一致。
- A11 实机：复用已有授权国内账号，1页/1台/通道1/固定五步，只读白名单与有界次数；凭据仅本机官方认证，不进入Claude/日志/fixture。配置不可用记录阻塞，历史回执不替代本次。
- A12 发布：全量 pytest、mypy、Ruff、audit、全部契约 eval、build/artifact/安装检查、当前 SHA 的 PR CI；合并后 main 的 CI 与本地主分支核对，工作区干净。

## 批次和唯一写入方

Codex 保存本设计、覆盖快照、派单、审查和最终验收记录。Claude 固定 Magpie `devin/swe-2`、effort=max，零工具候选仅接收指定公开文本，Codex审核应用，常规测试由受限 Bash Claude 会话执行并核查 tool_result。

- DG001：recorder、静态registry；唯一文件 diagnostic_report.py、_diagnostic_registry.py。
- DG002：业务方法体插桩、client/inspection/__init__ 接入；不改 recorder、registry、错误/传输/认证策略。
- DG003：独立测试，唯一 tests/test_sdk_diagnostic_report.py、tests/test_sdk_diagnostic_coverage.py；不接实现候选，按冻结接口和基线编写。
- DG004（首批接口落地后）：docs/diagnostics.md、examples/diagnostics_sync.py、examples/diagnostics_async.py、README.md、README.zh-CN.md、docs/getting-started.md、tests/test_examples_diagnostics.py。
- DG005（首批接口落地后）：tools/live/verify_diagnostics.py、tests/test_live_diagnostics.py、tools/live/README.md，复用现有 inspect_device；仅本地验收工具，不成为 SDK 自动功能。

同类失败连续两轮先检查接口、任务权限与实现路线，不堆补丁或削弱断言。已通过检查只因新代码/失败依据重跑。
