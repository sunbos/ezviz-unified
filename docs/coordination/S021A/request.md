# S021A：Webhook纯函数实现

devin/swe-2、effort=max，零工具，只输出JSON代码块 `{"files":{"src/ezviz_unified/webhooks.py":"...","docs/research/2026-11-15-webhook-helpers.md":"中文说明"},"edits":[{"path":"src/ezviz_unified/__init__.py","old":"唯一原文","new":"替换"}],"notes":[]}`。不得生成测试（由独立会话编写），不修改Client或契约登记，不执行或声称通过。

严格按附带计划API实现。使用标准库hmac/hashlib/json，不自行写密码算法，不使用AppKey/AppSecret，不联网。正文必须按原始UTF-8 bytes参与HMAC，再拼接timestamp的UTF-8字节；secret UTF-8编码，40位hex解析为20字节后用hmac.compare_digest。signature和timestamp是外部header值：非str/空/非法hex等返回False；secret/body/max_bytes配置或类型错误抛固定ValueError，不打印原值。UTF-8验证失败和JSON/结构/重复key/非有限/深度错误抛ProtocolError，operation固定webhooks.parse或webhooks.verify；签名不匹配抛AuthenticationError(code=None，不要假装10002)。在except块外抛新的SDK错误，避免__context__持有JSONDecodeError.doc或UnicodeDecodeError.object。不要宽泛吞编程错误。

verify仅校验签名（允许UTF-8非JSON正文用于算法向量）；parse_webhook必须先核验再解析，错误签名时不得先暴露JSON解析差异。parse_unverified是明确的原文解析工具，不声称安全。WebhookMessage用冻结dataclass，字段repr=False；仅message_id/message_type为str，其余body/object、raw/dict、raw_body/bytes如计划。raw/header/body额外字段保留，不递归做业务类型转换，不存自报verified字段作权限依据；ack()每次返回新dict。max_bytes配置只限制输入字节长度，不猜平台消息上限。

为避免UTF8前置验证与“签名先于JSON解析”混淆：签名函数可先做bytes/长度/UTF8有效性检查，但不能json.loads。解析后的对象不是重放防护，也不是设备权限证明。模块有strict mypy所需类型标注与精简docstrings。

说明约500–800汉字，准确区分签名匹配、显式未核验解析、ACK及调用方去重/时效策略；不要启动Flask/FastAPI，不给无验签自动ACK的默认示例。说明官方t单位/向量缺失、hex大小写本地选择、入站helper尚未进入传输契约自动绑定。引用已有消息源审查与官方566，不抄真实设备/密钥示例。
