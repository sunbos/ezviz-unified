# 有界 Webhook 接收示例实施计划

> 执行分工：Claude Code 经 Magpie 固定 `devin/swe-2`、effort=max，按本文件的唯一文件范围实现；Codex 冻结合同、审查和验收。实施前须登记对应具体案例和 cohort 准入。本文件本身不是实现或实测通过证据。

**Goal：** 将已有纯函数验签/解析助手接到可显式启动、可停止、有界的真实 HTTP 接收链，交付本地合成链路验收与使用说明。

**Architecture：** 保留 SDK 纯函数不变，在 `examples/` 提供前台标准库 HTTP 接收器与有界内存 inbox。HTTP 受理是将已验证消息放入 inbox；应用取出后如何处理由调用方负责。接收路径不执行任意应用 callback，不新建 SDK 后台线程，不向云平台请求。

**Tech Stack：** Python 3.12+ 标准库 `http.server`、`socket`、`queue`、`time`、`hashlib`，复用 `ezviz_unified.webhooks.parse_webhook` 和 `WebhookMessage.ack`。不增加运行依赖。

基线：`9285fcfd87b0074021f3e5dcd997bc22d9b828b8`。协议事实见 [消息资料扩展审查](../research/2026-11-17-message-scope-review.md)、[消息源核对](../research/2026-11-15-message-ingestion.md) 与 [helpers 说明](../research/2026-11-15-webhook-helpers.md)。官方已有 `http.server` Quickstart；本次增量是原始 bytes 验签、有界接收、明确受理/去重语义及不泄露报文的输出。

## 1. 来源事实与本次设计选择

- 国内 5130：HMAC-SHA1 对原始收到的 body bytes 与头 `t` 的 UTF-8 字节直接拼接；头名为 `t`，其单位为毫秒。ACK 为 HTTP 200 与原样 messageId。展示示例的字节不一致不能用于线上重序列化验签。
- 海外 2392：JSON 正文可使用 `Content-Type: text/plain`；配置、签名及重试政策与国内不同。原 DOM e29/e81 明确 HMAC-SHA1，e99 的无凭据值通用 Java 函数明确以 UTF-8 对 `body + timestamp` 计算 HMAC，timestamp 对应头 `t`。根已定点审核这些字段；`t` 单位仍未核定。
- 本 cohort 只接收显式配置签名 secret 的投递。两地区都必须验签；这是一项示例的接入策略，不把海外官方“签名可选”改成“官方强制”。未签名接入与其它消息协议仍属全 Goal 的待核范围。
- 国内使用本地设计的正负 300000 毫秒容差；海外只验证签名，不解释时间戳单位、不宣称新鲜度。该容差不是官方重试保证。
- 先前来源中的超时和重试口径冲突继续保留。本接收器的网络读取 deadline 是本地保护，不替平台选择 2 秒或 5 秒的优先级。
- 不保证消息顺序、持久化、跨重启去重、exactly-once 或应用业务成功。HTTP 200 只表示已经受理到本进程内存，或在当前去重窗口内发现已受理的相同消息。

## 2. 冻结公共接口与文件职责

`examples/_webhook_receiver.py`（A 独占）：

```python
@dataclass(frozen=True, kw_only=True)
class ReceiverConfig:
    profile: Literal["domestic", "overseas"] = "domestic"
    max_body_bytes: int = 65536
    max_header_bytes: int = 16384
    max_request_seconds: float = 1.5
    max_run_seconds: float = 60.0
    max_connections: int = 100
    inbox_capacity: int = 32
    dedup_capacity: int = 128
    dedup_ttl_seconds: float = 300.0
    timestamp_tolerance_ms: int | None = 300000

@dataclass(frozen=True)
class ReceiverResponse:
    status: int
    body: bytes  # repr=False，可能携带协议 ACK 的 messageId
    outcome: str  # 固定枚举，不接受调用方任意文本

class ReceiverProcessor:
    def __init__(self, config: ReceiverConfig, *, webhook_secret: str,
                 monotonic: Callable[[], float] = time.monotonic,
                 wall_time_ms: Callable[[], int] | None = None): ...
    @property
    def config(self) -> ReceiverConfig: ...
    def process(self, raw_body: bytes, *, timestamp: str, signature: str,
                deadline: float | None = None) -> ReceiverResponse: ...
    def take_message(self) -> WebhookMessage | None: ...
    def public_summary(self) -> dict[str, int]: ...
    def close(self) -> None: ...
```

- secret 为显式参数，不纳入 config、repr、回执或异常文本；保持 helpers 的非空 UTF-8 语义，不把平台配置字符集约束写进 helper。
- 所有数值拒绝 bool。body 范围 1..1048576，headers 512..65536，连接数 1..10000，inbox 1..1024，dedup 1..4096；读取时限须有限且 0 < value <= 60 秒，总运行时限和 TTL 须有限且 0 < value <= 3600 秒。国内容差为 0..3600000 的整数，海外必须传 None。
- 构造及导入不监听、不读环境、不读文件。processor 面向顺序处理；不宣称可并发调用 process/close。take_message 使用非阻塞有界 inbox；close 清空 inbox/cache 中的引用并拒绝后续 process，take_message 返回 None；不声称 Python 可安全擦除已分配内存或调用方保存的消息。
- process：拒绝超限 → 调用原始 bytes 的 parse_webhook → 国内才校验非负 ASCII 十进制毫秒值和容差（最多 16 字符）→ messageId UTF-8 长度至多 256 字节 → 到期去重项清理 → 检查冲突/容量/deadline → 非阻塞入队 → 记录去重 → ACK。海外 `t` 只作为签名输入，HTTP 层限制其长度，不新增时间单位推断。
- 同 ID、同 raw bytes 的已受理重复投递继续 ACK，不重复入队；同 ID、不同 raw bytes 返回 409。重复投递仍先过签名、时间窗和 deadline 检查，在新增消息容量检查之前判定，不能因 inbox 已满而拒绝一个已受理重复。内部 SHA-256 仅作 bytes 相等性比较，绝不导出或宣称匿名化。不得重序列化后比较。
- 不淘汰仍有效的去重项。inbox 或去重容量已满时返回 503、不 ACK、不记作已受理；失败后可重试。TTL 用 monotonic，国内新鲜度用 wall time；重复投递也先过签名和新鲜度校验。
- deadline 到达时不得新入队或记成功。网络 server 传入该连接的绝对 deadline。
- 认证错误为 401，信封/JSON/messageId 错误为 400，超 body 为 413，ID 冲突为 409，容量/关闭为 503，deadline 为 408。固定错误体使用 `{"error":"固定枚举"}`；配置/程序错误不转换成成功，未知异常向调用方传播。
- outcomes 和计数字段固定为 accepted、duplicate、authentication_failed、invalid_message、body_too_large、id_conflict、capacity_rejected、deadline_exceeded、closed。public_summary 只含 schema_version=1 与上述非负整数，不含 ID、类型、body、哈希、secret、地址或异常原文。

`examples/webhook_receiver.py`（B 独占）：

```python
class BoundedWebhookReceiver:
    def __init__(self, processor: ReceiverProcessor, *, port: int = 8765): ...
    @property
    def port(self) -> int: ...
    def run(self) -> dict[str, int | str]: ...
    def close(self) -> None: ...

def main(argv: list[str] | None = None) -> int: ...
```

为减少单次交付体积，socket 读写边界拆到 `examples/_webhook_http_io.py`（D 独占），不改变以上公共接口和验收范围：

```python
class RequestDeadlineExceeded(Exception): ...
class HeaderLimitExceeded(Exception): ...

class DeadlineSocketIO:
    def __init__(self, sock: socket.socket, *, deadline: float,
                 max_header_bytes: int,
                 monotonic: Callable[[], float] = time.monotonic): ...
    @property
    def closed(self) -> bool: ...
    def readline(self, size: int = -1) -> bytes: ...
    def read(self, size: int) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

此内部适配器可同时作为 handler 的 rfile/wfile。readline 累计实际 header 字节；read 仅读取调用方已按 body 上限核定的长度，不计入 header；不预读下一请求。每次 recv/send 前及返回后检查绝对 deadline，socket.timeout 转为自有 RequestDeadlineExceeded（不得继承 TimeoutError 被标准 handler 静默处理）。header 超限为 HeaderLimitExceeded；其它未知异常不吞掉。write 必须处理 partial sends；flush 不缓冲；close 仅关闭适配器引用状态，底层 socket 生命周期由 receiver 负责。helper 不监听、不解析 HTTP、不处理报文或打印日志。

- 显式构造才绑定 IPv4 `127.0.0.1`；允许端口 0 给本地测试选空闲端口，公开绑定端口通过 port 属性取得。不得支持自动公网地址、隧道、自动服务开通。不存在导入时监听或后台任务。
- 标准库 HTTPServer 只处理一个活动连接，backlog 请求值 4；不把 backlog 当操作系统所有 TCP 连接的绝对上限。只接收 `POST /ezviz/messages`，始终 `Connection: close`，不复用或处理 pipeline 中第二个请求。
- 必须以 monotonic 绝对时间限制整个连接读写和整个 run。每次 recv 或响应写入以前按剩余时限设置 timeout，并在读取返回及 ACK 前复查；仅设置一次 socket timeout 不合格。header 累计包含 request line 与实际交给解析器的全部 header bytes，不把预读 body 算 header；超限返回 431。禁止全局 signal/alarm 或修改 HTTPX。
- 优先复用 BaseHTTPRequestHandler 的 HTTP 解析；可以提供有限 read/readline 的 socket reader，内部按绝对 deadline 读取，捕获自有 deadline/header 超限异常后返回固定响应。网络断开结束该连接；未知服务器异常传播，禁止默认 traceback/访问日志。
- 要求恰一 Content-Length（ASCII 十进制、0..body 上限），拒绝 Transfer-Encoding 和重复 Content-Length/t/Signature。Content-Type 允许 application/json 或 text/plain（可带 charset 参数）；其它类型 415。t 长度 1..128 字符；缺失或坏签名为 401。路径不匹配 404，方法不支持 405，不反射 path/header/reason。
- 使用收到的精确 body bytes；短 body 为 400。认证、解析与受理只调用 processor。ACK 用其 body 原样输出，Content-Length 准确。HEAD 错误响应不发送正文。
- deadline 已耗尽时直接关闭连接，不为发送 408 延长预算；固定错误响应也受同一写入 deadline 约束。已入队而响应传输失败时不撤销受理，平台重投由同一去重规则处理。
- max_connections 计接受的连接，不只计合法消息；达到连接上限或 run deadline 退出并关闭监听和当前连接。run 正常返回后 processor/inbox 仍可由调用方显式 take_message；网络 close 不默默清空已受理消息。
- run 回执只含 schema_version、connections、request_errors、stop_reason（max_connections/deadline/interrupted/closed）及 processor 固定计数。不得输出任何报文或来自网络的字符串。显式 close 应释放监听与当前连接；因关闭引起的已知 socket 错误按 closed 结束，其余未知异常仍传播。
  connections 是已接受连接数；request_errors 是非200、响应发送未正常完成或观察到截止超限的连接数，每连接最多计一次（内存已受理仍可能计入）。processor 的计数只表示已进入其处理函数的结果，未进入 processor 的 HTTP 前置错误由 request_errors 体现，不能把两类计数相加当总消息量。已达到连接上限后正常结束时保留 max_connections 原因，最后的幂等 close 不覆盖正常结束原因。
- CLI 参数：--profile、--port、--max-seconds、--max-connections、--max-body-bytes；secret 只由 getpass 隐藏输入。海外 config 显式 timestamp_tolerance_ms=None。CLI 不读取 .env、不复用 APP 密钥，不打印用户参数。非 TTY 输入拒绝，不回退到可回显 secret 的 stdin。参数解析错误不得沿 argparse 默认错误文案回显参数值；--help 仅显示静态帮助并正常退出。只打印上述安全 JSON 摘要；正常退出0，配置或未知服务器错误固定短文且退出1，中断130。嵌入式 run 的未知异常原样传播并保证资源关闭；CLI 不输出 traceback。

## 3. 先冻结的验收条件

1. 核心：真实 HMAC-SHA1 的独立合成签名；raw bytes 的空白/次序变化不能借重序列化通过；坏签名先于 JSON 解析；合法未知消息类型仍可受理。
2. 国内毫秒边界、未来时间、超窗、非 ASCII/负数/超长时间戳；海外不按国内单位判断。所有 fixtures 均为合成数据，不采用官方展示或真实平台密钥。
3. inbox/去重各自容量满不 ACK且不丢旧项；same-ID/same-bytes只入队一次；same-ID/different-bytes为409；TTL 到期允许重新受理；失败不占去重；停止后可取队列，显式 close 后清空。
4. header/body/duration/connections 限制逐项验证，包括逐字节慢速 header 的总 deadline；不得仅断言每次 socket read timeout。超长、重复长度、TE、坏方法/路径/content-type、截断正文均固定响应且关连接。
5. 一次真正 loopback TCP 链：合成 sender → 接收器 → SDK验签解析 → inbox → HTTP ACK；同步发送重复/冲突/错误。测试仅临时放行本测试确定的 127.0.0.1:port，保留其它网络 deny，不使用 --run-live，不把本地合成链称平台实测。
6. 把合成敏感哨兵放入 path/header/body/messageId/secret，捕获 stdout/stderr/log/repr/公开 JSON 回执，均不得出现这些值或其摘要。ACK 中精确 messageId 是必要协议响应，单独断言，不当作公开诊断输出。
7. 导入/配置不得监听或发请求；run 前台、退出释放端口、无残留隐式线程。SDK源码、返回/异常语义、HTTPX、认证与请求重试不改。
   独立 webhook helpers 在诊断 v1 自动采集范围之外（见 diagnostics-v1-coverage 的 exclusions）。本示例的固定计数回执是独立接收器输出，不新增 SDK 业务 operation、不扩大 recorder 白名单，也不冒充覆盖所有应用异常。已有候选中“helpers already registered”的表述须按此纠正。
8. 平台模拟和真实设备事件另设 required case：国内服务开通/配置影响（App提醒与恢复）须具体授权；海外服务/账号/回调入口待核。缺资源不冒充完成，也不阻止本次纯本地实现。

## 4. 派单与集成顺序

- A 唯一可写：examples/_webhook_receiver.py、tests/test_webhook_receiver_core.py。
- B 唯一可写：examples/webhook_receiver.py、tests/test_webhook_receiver_http.py。依本冻结接口开发；测试执行等 A/B 均交付后，不能以 missing upstream 为由改公共接口。
- D 唯一可写：examples/_webhook_http_io.py。实现上述内部文件式 I/O 接口；其行为由既有 HTTP deadline/header/body/partial-close 验收覆盖，不另建框架。
- C 唯一可写：docs/webhook-receiver.md、tests/test_webhook_receiver_cli.py、README.md。说明平台边界、配置、显式运行和取 inbox 示例；单独测试 CLI 安全输出/返回码，禁止真实 secret/env/config。
- 三方输入均固定本计划与基线；不改 SDK、依赖、现有测试、矩阵或其它文件。写入会话无 Bash。常规测试由另一个 Claude 精确命令会话运行：`.venv/bin/python -m pytest -q tests/test_webhook_receiver_core.py tests/test_webhook_receiver_http.py tests/test_webhook_receiver_cli.py`，必要修订仍回唯一写入方。
- 新代码须 Ruff 格式与 lint 通过；集中关键审查处理协议、生命周期、隐私和边界，然后运行一次相关回归及当前提交 CI。无新变更/失败依据不重复已通过的检查。
- 全 Goal 继续保留所有未审核来源与其它消息类型/消费协议；本 cohort 完成也只代表接收示例及对应本地验收层完成。
