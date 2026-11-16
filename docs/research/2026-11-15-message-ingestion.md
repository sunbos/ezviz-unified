# 消息接入源核对

范围：2026-11-14归档的566、982、1384、5328、1307031、1763六篇资料；本文件只覆盖该六篇，不能写成「全站所有消息文档均未核查」。2026-11-17 扩展审查新增 5127/5128/5129/5130 与海外 2392（含 566、5328 复检），见 [message-scope-review](2026-11-17-message-scope-review.md)；以下结论仍以六篇为限，新事实与修正以该记录为准。未接收真实回调。

## 总体结论
萤石 webhook 推送示例多采用信封 `{"header":{type,deviceId,channelNo,messageId,messageTime},"body":...}`，header.type 属 `ys.*` 命名空间（566 示例 `ys.test.msg`/`ys.open.isapi`，1384 为 `ys.open.cloud`，1307031 为 `ys.shadow.change`/`ys.iot`/`ys.onoffline`）。body 类型并不统一：多数示例为 object，566 测试消息 body 为字符串，故不能将 body 对象作为统一信封要求；未知 type 与设备上报值应原样保留。云录制消息在 body 内以 `messageType` 细分（1384）。1763 的 `eventType` 属另一套枚举，与推送 type 不可混用；物模型 `deviceId` 形如 `{ProductID}:{DeviceID}`，与经典序列号格式不同。

## ACK 与验签
成功 ACK = HTTP 200 且响应体含 `messageId`（566 明示，1307031 亦要求回传；5328 只写 200）。566 原文公式 `Signature.equals(hmacSha1(secret, body+timestamp, UTF8))` 明确 HMAC-SHA1 与 UTF-8，相关头 `t`/`message_type`/`Signature`。同页开通文案称密钥用于“解密”，与签名机制表述矛盾，无法保证在所有情形下均为措辞错误；若实际收到非明文 JSON 应停下核查。hex 大小写未注明，大小写不敏感比较可作为未来 SDK 设计选项，属本地选择而非官方保证；本六篇内无官方测试向量、头 `t` 携带的时间戳单位未注明，不能声称官方验签/防重放已验证。（2026-11-17 补充：国内 5130 已明确 `t` 为毫秒并有展示级签名示例，仅 minified 派生字节离线匹配；海外单位未核定，见 [message-scope-review](2026-11-17-message-scope-review.md)。）未配置签名密钥时，原文未给出替代的应用层鉴别方案（传输层不在本次审查范围），此情形下解析成功不代表消息已通过鉴别。

## 重试/超时与冲突
566：超时 2 秒（原文未声明是否可配置），重试次数为开通时配置项，达上限丢弃；降级期仅 10% 投递。5328 泛称“通常3次/一般5s”且示例扁平，与 566 不一致；不为来源擅自设定官方优先级，建议具体接入时以细节更完整的协议作为候选依据，冲突仍记录在案，实际服务配置未经验收。（2026-11-17 补充：5129 明确开通/修改消息类型约 2 小时生效、同手机号 App 提醒停止且关闭服务不自动恢复、控制台「消息测试」暂停原回调并临时打开全部类型——均属配置副作用，既有只读授权不覆盖；海外 2392 重试 1–3 次可配置、正文 JSON 但 `Content-Type: text/plain`，与 566 2 秒口径冲突保留。）982 旧订阅接口仅标注“后续不更新维护”，不等于下线或不可用：不作为新接入首选，保留历史兼容事实。

## 阻塞与未知
1763 声明 GET 却把必选参数（queryDate/pageStart/pageSize）标在 body，curl 示例又缺必选参数，传输方式未知 → 客户端实现阻塞。本次六篇材料未提供 `ys.alarm`/`ys.calling` 及 5328 所述抽帧/抓拍消息的完整 body schema，不能据此生成统一字段模型；2026-11-17 扩展审查补入 5130，`ys.alarm`/`ys.calling` 等国内类型字段已登记（见 [message-scope-review](2026-11-17-message-scope-review.md)），但其余类型仍各自待审，不等于「全归档消息均无 schema」。其他文档是否补齐仍待核查。1307031 仅声明 `body.payload` 为 String，示例值形似 JSON 字符串，不应强制二次解析；同页 statusValue 字段表标 String 而示例为 false(bool)，应记录差异而非强制转换。

## 可复用与最小切片
标准库可承担 HMAC-SHA1/JSON/恒定时间比较；现有 Java WebhookMessage 包的许可证是否允许适配尚未检查，不能保证可复用。建议最小范围：离线原始信封解析（保留 body 原文）、显式返回签名核验结果、ACK 生成 helper；不自动解析 payload、不下载媒体、不启动 Web 服务、不接受未鉴别消息。前置：调用方显式提供可选 secret。

## 来源

- [566 消息推送](https://open.ys7.com/help/566)
- [1384 云录制消息](https://open.ys7.com/help/1384)
- [1307031 物模型消息](https://open.ys7.com/help/1307031)
- [5328 接入指引](https://open.ys7.com/help/5328)
- [1763 事件查询](https://open.ys7.com/help/1763)
- [982 旧订阅接口](https://open.ys7.com/help/982)
