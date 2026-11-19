# 有界 Webhook 接收示例

本示例把已有的纯函数验签/解析助手接到一个可显式启动、可停止、有界的本地 HTTP 接收链上。设计与验收依据见 [实施计划](plans/2026-11-18-webhook-receiver.md)，协议来源事实见 [消息资料扩展审查](research/2026-11-17-message-scope-review.md)。本文不是平台实测证据。

## 运行前提

- Python 3.12+；需要本仓库的源码 checkout（`examples/` 目录随源码分发，**不属于** SDK wheel 的导入面，已安装的 `ezviz_unified` 包内没有该模块）。在 checkout 内按开发环境装好 SDK 即可运行；示例自身无新增运行依赖，仅用标准库——普通 SDK runtime 不需要 LLM/维护环境依赖，本示例也不引入它们。
- webhook secret 只在终端通过 getpass 隐藏输入；非 TTY 环境直接拒绝，不会回退到可回显的 stdin，也不读取 `.env`、环境变量或 App 密钥。

## 启动

在仓库 checkout 根目录运行（`examples` 是仓库内模块，不是已安装包的一部分）：

```bash
python -m examples.webhook_receiver --profile domestic
```

- 仅绑定 IPv4 `127.0.0.1`；可选参数 `--port`、`--max-seconds`、`--max-connections`、`--max-body-bytes`，`--port 0` 用于本地测试自动选空闲端口。
- `--profile overseas` 时对 `t` 只做签名输入，不解释时间单位——其单位目前未核定；国内档使用本地 ±300000 毫秒容差，这不是官方重试保证。
- 本 cohort 只接收显式配置签名 secret 的投递（signed-only 是本示例的接入策略，并非官方必选签名）。运行结束后打印固定字段的 JSON 计数摘要，不含报文、ID 或密钥。
- 嵌入式调用：`ReceiverProcessor(config, webhook_secret=...)` → `BoundedWebhookReceiver(processor, port=...)` → 前台 `run()` 直到时限/连接上限/中断；之后仍可用 `processor.take_message()` 取出 inbox，`processor.close()` 后拒绝新受理并清空其内部引用。

## 海外设备上线事件

在仓库根目录运行下面的示例。程序生成临时签名密钥，在 `127.0.0.1` 的临时端口向现有接收器两次发送相同的合成上线通知。两个 HTTP 响应都应为 200，并包含相同的 `messageId`；应用从 inbox 中只取到一条消息。终端仅显示模拟标志、地区 profile 和计数。

```bash
python -m examples.overseas_device_online
```

教程用 `header.type="ys.onoffline"` 和 `body.msgType="ONLINE"` 表示上线。原文中 JSON 前的日志前缀不属于 HTTP 正文。应用应在接收器完成验签、受理后再判断这两个字段；该判断只说明收到一条上线通知，不能证明设备此刻仍在线。[海外消息推送教程 1000007](https://open.ys7.com/help/en/1000007)

协议 2392 将 `ys.onoffline` 列为可推送类型。此示例选择已配置签名的海外 profile，以独立的 `webhook_secret` 为 HMAC-SHA1 密钥，将原始正文与 `t` 的 UTF-8 字节按顺序拼接后验签。海外 `t` 按不透明文本处理。平台将签名列为可选；本地示例只受理签名通过的消息。相同字节的重复通知在当前去重窗口内再次获得 ACK。教程及本地模拟均不能证明平台重投次数、加密行为或时间戳单位。[海外推送协议 2392](https://open.ys7.com/help/en/2392)

嵌入应用时，在 `receiver.run()` 返回后、`processor.close()` 之前调用 `processor.take_message()`。关闭 receiver 释放监听连接；关闭 processor 后 inbox 和去重缓存均被清空。本页示例无需外部地址、平台账号或设备。真实平台投递另需开通服务、独立 secret、可达回调和明确的触发及恢复授权。 此教程场景的本机证据单列在[上线事件验收回执](validation/2026-11-20-overseas-device-online-event/offline.json)。

## 本地有界默认值

以下均为本示例的本地设计取值（可用 CLI 旗标或 `ReceiverConfig` 字段程序化覆盖），不是平台官方限制：

| 项 | 默认值 | 调整方式 |
| --- | --- | --- |
| 单请求 body | 64 KiB（硬上限 1 MiB） | `--max-body-bytes` |
| 请求头总量 | 16 KiB | `ReceiverConfig` |
| 单请求超时 | 1.5 s | `ReceiverConfig` |
| `run()` 总时限 | 60 s | `--max-seconds` |
| 最大连接数 | 100 | `--max-connections` |
| inbox 容量 | 32 条 | `ReceiverConfig` |
| 去重窗口 | 128 条 / TTL 300 s | `ReceiverConfig` |

## 计数口径

`run()` 回执与 `processor.public_summary()` 是两组口径不同的固定计数，**不能相加**当成"总消息量"：

- `connections`：已接受的 TCP 连接数，不只计合法消息。
- `request_errors`：非 200、响应发送未正常完成、或观察到截止超限的连接数；**每个连接最多计一次**——即使该连接的消息已被受理进 inbox，只要响应层出问题仍计入。
- `accepted`/`duplicate`/`authentication_failed` 等其余计数：只统计**已进入 `processor.process` 的结果**。HTTP 前置拒绝（如路径不符、header 超限、重复 Content-Length）根本没进 processor，不会出现在这些 core 计数中。

CLI 结束时只打印这份固定字段的安全统计 JSON；协议 ACK 中必须带回的 `messageId` 属于必要响应，不算可导出的日志/诊断字段。

## 语义边界

- HTTP 200 只表示消息已受理进本进程内存 inbox（或在去重窗口内识别为已受理重复件），**不等于**业务处理成功，也不等于持久化；不保证顺序、跨重启去重或 exactly-once。
- 去重窗口内同 ID 同 raw bytes 的重复投递仍 ACK 但不重复入队；同 ID 不同 bytes 返回 409。inbox 或去重容量满、已关闭返回 503，可重试且不丢旧项；连接 deadline 耗尽返回 408 或直接关闭。去重 TTL 从**实际受理时刻**起算（monotonic），与请求是否传入 `deadline` 无关；同 ID 且同 raw bytes 的重复投递**不续期**（不同 raw 为 409 冲突），TTL 到期后同一消息可重新受理。
- 受理失败不占用去重额度；显式 `processor.close()` 后 `take_message()` 返回 None。本示例不执行应用回调、不建后台线程、不向云平台发请求。
- `receiver.close()` 只关闭监听 socket 与连接等网络资源；inbox 与去重缓存引用由 `processor.close()` 负责清空。两者都是普通资源释放：Python 层清空引用不等于对密钥或已分配报文内存的安全擦除，也不处理调用方自行保存的副本。

## 平台边界

- 本地 inbox 合成联调数据与真实平台是两层：要走到平台验收，需自行提供 HTTPS 反代与萤石侧服务开通/回调入口等资源；示例不自动部署、不开隧道、不代办服务开通，也不宣称这些外部 case 已通过。
- 平台侧回调配置会影响 App 提醒与恢复，改动前须另行授权评估；本示例不执行其中任何动作，仅记录已知事实：国内开通消息推送服务或修改消息类型约 2 小时生效；同一手机号开通后 App 设备消息提醒会停止，关闭服务不会自动恢复，需联系客服/工单处理；在控制台发起回调测试会暂停原回调地址并临时开启全部消息类型，测试结束后恢复。
- webhook helpers 不在 SDK 诊断 v1 自动采集范围内；本例输出的是独立固定安全计数，不是原始日志，也不扩大诊断白名单。
- 本地合成测试只使用合成密钥与数据；经授权的平台验收中，真实 secret 仅在本机 TTY 通过 getpass 输入，不进入模型上下文、日志或测试 fixture。
- 本 cohort 的验收分五项登记在 [sdk-product-acceptance.json](validation/sdk-product-acceptance.json)，状态以该文件为准：本地 case `ACC-WEBHOOK-CORE-001`（receiver_core）、`ACC-WEBHOOK-HTTP-001`（loopback_http）、`ACC-WEBHOOK-CLI-001`（cli_example），平台 case `ACC-WEBHOOK-PLATFORM-CN-001`（domestic）与 `ACC-WEBHOOK-PLATFORM-OS-001`（overseas，集群未冻结）。五项均**未**标完成，没有任何平台 case 已通过；本地通过也不升级为平台或设备事件验收。
