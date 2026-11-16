# Webhook 验签与解析助手（S021A）

验证范围：本批依据国内归档协议实现，海外回调格式与真实平台推送尚未验收。`raw_body`保留收到的原始字节，`raw`和`body`是标准JSON解析后的调用方数据，不能用重序列化结果替代原文验签。原始算法向量参考[RFC 2202](https://www.rfc-editor.org/rfc/rfc2202.txt)，UTF-8合成向量另用OpenSSL交叉核对；两者都不冒充萤石真实回调样本。

范围：`ezviz_unified.webhooks` 提供三个模块级纯函数，仅用标准库 [Python hmac](https://docs.python.org/3/library/hmac.html)/hashlib/json，依据[官方566](https://open.ys7.com/help/566)归档的公式 `hmac_sha1(UTF8(secret), UTF8(body+t))` 与[消息源核对](2026-11-15-message-ingestion.md)实现。不联网、不启动 Web 服务、不使用 AppKey/AppSecret、不挂接 Client，本批也未进入传输契约自动绑定计数。

## 三个入口的语义区别

- `verify_webhook_signature` 只做 HMAC-SHA1 核验：原始 `raw_body` 字节与 `timestamp` 的 UTF-8 编码直接拼接参与计算，不重序列化、不 strip；`timestamp`/`signature` 视为外部头值，缺失或格式错误（非 str、空、非 40 位 ASCII hex、`timestamp` 不可 UTF-8 编码）一律返回 False；`webhook_secret`/`raw_body`/`max_bytes` 属调用方配置，类型或取值错误（含正文超过 `max_bytes`）抛固定 ValueError 且不回显原值；非 UTF-8 正文抛 ProtocolError（operation=webhooks.verify）。本函数从不解析 JSON，因此也可用于合法 UTF-8 非 JSON 正文的算法向量核对。
- `parse_webhook` 先验签，失败抛 AuthenticationError（code=None，不冒称平台 10002），通过后才解析 JSON——错误签名不会提前暴露 JSON 层面的差异。
- `parse_unverified_webhook` 是显式不做认证的入口，只做信封结构解析；产物不含任何“已认证”含义，仅供联调与诊断。

## 错误分类速查

- ValueError（调用方配置错误，消息固定且不回显原值）：`max_bytes` 非正 int、`raw_body` 非 bytes 或超过 `max_bytes`、`webhook_secret` 非 str/空白/不可 UTF-8 编码。
- 返回 False（仅 `verify_webhook_signature`）：`timestamp`/`signature` 头值缺失或格式错误——非 str、空、非 40 位 ASCII hex、`timestamp` 不可 UTF-8 编码。
- ProtocolError（对端数据问题，operation 标注入口）：正文非 UTF-8、JSON 语法错误、重复 key、嵌套超 64 层、非有限值、整数位长超 Python 解释器限制、信封结构不符。
- AuthenticationError（仅 `parse_webhook`）：签名核验失败，`code=None`，先于任何 JSON 解析。

## 解析与消息对象

JSON 拒绝重复 key、非有限值、非法 UTF-8 与超深结构（ProtocolError，operation=webhooks.parse）；深度上限 `_MAX_JSON_DEPTH=64` 与整数位长拦截均为本地 SDK 保护，不是官方平台限制。信封要求顶层 object、`header` 为 object、`messageId`/`type` 为非空白 str；`body` 字段必须存在但可为任意 JSON 类型（[官方566](https://open.ys7.com/help/566) 测试消息的 body 就是字符串）。未知字段与类型原样保留在 `raw`，`payload` 字符串不二次解析，不改时区，不下载媒体。`WebhookMessage` 为冻结 dataclass，全部字段 `repr=False`；`raw` 中即便出现 `verified` 字段也只是数据，不作权限依据。

`ack()` 每次返回新的 `{"messageId": ...}` 字典；HTTP 200 由调用方框架返回，本模块不提供“未验签自动 ACK”的默认用法，也不应被包成无验签接收示例。

## 调用方责任与已知缺口

签名匹配只证明给定正文与时间戳在配置密钥下的 MAC 一致——重放的旧报文同样能通过，不证明当前发送者身份、实时性或防重放；本次核查的六篇归档材料当时未提供测试向量、HTTP 头 `t` 携带的时间戳单位亦未注明，因此不构成防重放或时效保证。2026-11-17 扩展审查（见 [message-scope-review](2026-11-17-message-scope-review.md)）后限定该结论：国内 5130 已明确 `t` 时间戳为毫秒并给出展示级官方签名示例，但离线核算仅在 minified 派生字节下匹配（展示原字节验不过），不构成线上行为已核定证据；海外 2392 的 `t` 单位仍未核定，本模块不含时间窗或防重放语义。调用方须按业务窗口自行评估 `t` 的新鲜度、用 `messageId` 去重，并对未知 `type` 保持宽容。hex 大小写不敏感属本地明确选择。`max_bytes`（默认 1 MiB）是可配置的本地 SDK 输入限制，不代表平台消息上限。若实际收到非明文 JSON 正文，应停下核查（[官方566](https://open.ys7.com/help/566) 开通文案中“解密”措辞与签名机制表述存在矛盾），而非强行解析。
