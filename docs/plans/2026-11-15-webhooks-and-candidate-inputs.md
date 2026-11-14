# 消息接入与维护输入并行计划

基础为main提交779b8308317243c7c94a604943aecb579afe17b7，其代码树与已审查PR头一致，四版本CI通过。完整SDK覆盖、自动补丁及自进化目标继续保留；本批不执行真实消息配置或模型补丁。

## S021：消息接入纯函数

复用标准库HMAC/JSON及现有SDK错误，不开发Web服务器、消息队列或新的加密协议。依据归档566的HMAC-SHA1(UTF8(secret), UTF8(body+t))及ACK要求；1384/1307031用于确认body可变和原始字段保留。官方向量及t单位仍缺失，签名匹配不等于防重放或实时性保证。

公开模块级API，不挂到Client、不自动使用AppSecret：

- `verify_webhook_signature(raw_body: bytes, *, timestamp: str, signature: str, webhook_secret: str, max_bytes: int = 1048576) -> bool`。
- `parse_webhook(raw_body: bytes, *, timestamp: str, signature: str, webhook_secret: str, max_bytes: int = 1048576) -> WebhookMessage`：先验签，失败抛AuthenticationError，再解析。
- `parse_unverified_webhook(raw_body: bytes, *, max_bytes: int = 1048576) -> WebhookMessage`：显式未核验入口，不输出“已认证”标志。
- WebhookMessage保留message_id、message_type、body、raw、raw_body；所有字段不进入repr，`ack()`返回新的`{"messageId": ...}`字典，HTTP 200由调用方框架返回。

raw_body必须bytes且为UTF-8；大小上限为SDK可配置保护，max_bytes严格正int拒绝bool。secret须非空白str但不修改原值；缺失/空timestamp或格式错误signature返回False。签名只接受40位ASCII hex，用compare_digest比较digest bytes；大小写等价属于本地明确选择。时间戳按原始字符串拼接，不strip、不解释时间单位；JSON也不能重序列化后验签。

JSON拒绝重复key、非有限值、非法编码和超深结构。要求顶层object、header为object、messageId/type为非空白str，body字段存在但其值可为任意合法JSON类型。保留未知字段和类型，不自动解析payload字符串、改时区或下载媒体。错误及异常链不保留源正文/secret；返回数据归调用方所有，验证只针对原始字节。

S021A生成实现/导出与说明，S021B独立生成测试，不接收实现。测试使用RFC2202 HMAC-SHA1 case2与主导方用OpenSSL交叉核对的合成UTF-8消息向量，均不冒充真实萤石测试向量。此类入站helper不伪装成新的HTTP传输契约计数，当前自动影响绑定仍只覆盖传输登记。

## H010：维护候选输入包

新增`harness.candidate_input`，复用run_review完成完整归档/登记/SDK基线验证，再为显式选中的review任务打包前后HTML、全部相关契约、源问题和直接SDK模块；额外上下文模块必须由调用者显式指定且位于已核验SDK源码清单。只提供模型输入，不调用模型、不执行或应用补丁、不授权发布。

包内来源和代码均重新核验哈希；路径不得逃出各自根目录。完整保留一页多操作、selector及缺失/移除状态，不自动删API或扩大权限。不得截断材料后宣称完整；默认最大序列化包2MiB，超过则明确失败。维护资料是数据，不能改变工具权限。

H010独立Claude任务生成工具与测试，Codex负责源边界检查和真实归档/合成变化集成验证。已有H008合成副本继续标注synthetic，不把它当官网真实更新。
