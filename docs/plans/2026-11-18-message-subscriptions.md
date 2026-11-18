# message_subscriptions 域实施计划（有界纯文本候选）

- 性质：候选规划文本，非实现证明、非执行证明；不探索全仓、不切换模型。

## 1. 范围与边界
- 本域唯一可写文件：
  - src/ezviz_unified/message_subscriptions.py
  - tests/test_sdk_message_subscriptions.py
  - tests/test_sdk_message_subscriptions_behaviour.py
  - examples/message_subscriptions.py
  - tests/test_example_message_subscriptions.py
- 公共 client.py、__init__.py、errors.py、_diagnostic_registry.py 及 diagnostics/contracts/evals 由独立唯一集成 writer 负责，本域不写、不预留接口承诺。
- facade 固定 message_subscriptions；类固定 MessageSubscriptions/AsyncMessageSubscriptions；不得更名 messages/poll，不得引入去重器或托管 consumer 对象。
- baseline 95aa8f8e0040e86a53dc87adc92089a8d79ebc85；admission sha256 5f6f658193a55760468d1dbb4f058bd097776a653b5a0d92fd709510d48464a6。

## 2. 操作契约
### 2.1 cn.message_subscriptions.create_consumer（runtime message_subscriptions.create_consumer）
- 签名：create_consumer(group: Literal["group1","group2","group3","group4","group5"]) -> ApiResponse，sync/async 同形。
- POST /api/lapp/mq/v2/consumer/{consumerName}；token 位于 form；body form_urlencoded 且显式 Content-Type。
- 响应策略 code/msg/data，成功值 "200"；read_only=false。
- group 必填无默认：运行时严格 str 且精确等于五枚举之一，其余取值（含 None/非 str/大小写变体）本地拒绝。
- consumerName 经既有 path template 绑定；返回完整 ApiResponse，不抽取或保存 consumerId。
- region=domestic：region guard 在 diagnostic scope 内、先于全部参数校验/auth/provider/factory；Operation.regions 仅 domestic。
- 返回契约：原样 ApiResponse，保留 data/raw/未知字段与底层异常身份；无 DTO/转换/轮询/任务句柄/响应字段强制。
- 重放策略：10002/HTTP/API/超时/断连/取消不做业务重放；保留既有 token 失效语义，结果可能未知。
- 保留未知：consumer 配额、创建唯一性/幂等与 group 归属、租户服务可用性、错误码表仅含成功。

### 2.2 cn.message_subscriptions.consume（runtime message_subscriptions.consume）
- 签名：consume(consumer_id: str, *, offset: int | None = None) -> ApiResponse，sync/async 同形。
- POST /api/lapp/mq/v2/consumer/messages；token 位于 form；body form_urlencoded。
- read_only=false：一次读取即提交上一批；超时/取消/断连后提交与投递结果均可能未知。
- consumer_id 非空白 UTF-8 str，opaque 原样透传，不臆造长度/正则；consumerId 加入既有敏感键列表。
- offset 为 None 时不携带该参数；提供值须 int 且非 bool，零/负/大值原样传递且不声称服务端接受；绝不派生 offset+1。
- 不提供 peek/ack 辅助、隐式最终读、自动重启/重试、去重、缓冲、检查点、定时器或后台消费者。
- 调用方持有 consumer 身份与同 owner 串行序列；async 方法在调用方任务内 await，不派生线程/任务。
- region guard 位置、返回契约、重放策略与 2.1 逐条一致。
- 保留未知：offset 默认/方向/含端点语义及与 commit 的交互、独立最终批 commit 未载、空首读与无消息不可区分、长轮询/超时行为与字节上限含义、header.channelNo String 表与整数样例不一致。

## 3. 模块设计（src/ezviz_unified/message_subscriptions.py）
- 仅实现两类共四个公开方法，不导出其他业务符号。
- 每方法顺序：diagnostic scope → region guard（domestic）→ 参数校验 → auth/provider/factory → form 请求 → 返回 ApiResponse。
- 契约 ID 使用 cn.message_subscriptions.create_consumer/consume；Operation.name 与 entrypoint 使用不带 cn. 的 message_subscriptions.create_consumer/consume，诊断白名单按冻结分别登记。
- 新 scope 包裹 region 与校验（含 pre-auth 错误）；不改既有操作响应/异常/顺序/重试。
- 无自动收集/上传/文件日志/全局 HTTPX 修改/后台 SDK 业务请求。
- 复用既有请求脱敏，仅新增 consumerId；报告不含原始消息/头/body、值、ID、offset、URL 与异常细节；未知错误保留现行通用分类。
- None 即不携带全部可选参数；不注入服务端默认，不做隐式资源发现或选择。
- 已知请求值脱敏且不改变原始业务码分类（含短数字样 ID）；不声称任意未知错误文本被普遍清除。
- 源事实、本地校验选择、跨源证据、LIVE 结果相互分离；本域不新增 live 调用或副作用授权。

## 4. wire/校验测试（tests/test_sdk_message_subscriptions.py）
- direct facade 经 real Client 调用；Client 配真实 auth_transport + transport_factory；RegionConfig 固定 .china()/.overseas()。
- 无全局 sdk fixture、无 transport= 或构造器探测、无导入 fallback；async 用普通 with diagnostic scope，仅 client 一个 positional。
- 断言方法/路径/path template 绑定、form token 位置、form_urlencoded Content-Type、body 键集合。
- create_consumer：五枚枚举各通过；None/非 str/空串/group0/group6/大小写变体在 auth 前抛本地校验错且零请求。
- consume：consumer_id 空串/纯空白/非 str 拒绝；offset None 不携带、int 透传、bool/非 int 拒绝；不验证服务端对零/负值的接受。
- 响应：String200 code/msg/data；非 "200" 走既有错误分类；ApiResponse 保留 data/raw 全字段。
- region guard：RegionConfig.overseas() 下两操作先于校验拒绝且零请求。

## 5. 诊断对等/异常/副作用测试（tests/test_sdk_message_subscriptions_behaviour.py）
- sync/async 对等：scope 名称、region guard 时机、pre-auth 校验错误归类一致。
- 异常身份透传：Client.request 产生的 SDK 异常在 facade 层原样上抛，不新增包装/重试；HTTPX 到 TransportError 的既有转换保持；10002/超时/断连/取消后不产生第二次业务请求。
- 副作用边界：校验失败或 region 拒绝时零请求；consume 失败后不自动再读；无后台任务/线程残留。
- 脱敏：请求中 consumerId 被脱敏；报告无 ID/offset/URL/原始 body。
- 无 finally 提交读：异常路径 close 后请求计数不变。

## 6. 示例（examples/message_subscriptions.py）
- 仅仓库示例，非 SDK 运行时依赖；import 与 --help 零 env/config 读取、零网络、零任务。
- programmatic bounded run(sync)/arun(async)：入参 client、group、mandatory handler、max_batches int 1..10（示例上限，非官方 API 上限）。
- 流程：create_consumer 一次 → 本地校验 data.consumerId 形状 → 至多 max_batches 次显式 consume（offset 不携带）；每批 finish/await handler 后才下一次 consume。
- handler 失败或取消立即停止且不追加业务请求；finally/close 不执行提交读。
- 不提供静默 ack 的默认计数 handler；消息体 opaque，不打印身份。
- CLI 显式 sync/async 模式；--execute 为单批观察：create 一次、consume 一次，输出计数及 processing_performed=false、final_commit_confirmed=false，不做第二次 consume。
- 仅执行路径调用 examples._common.load_config；凭证/ID 不作 CLI 参数或输出；无 .env 自动加载、文件日志、上传。
- 成功/失败输出仅固定字段与枚举值/计数：不回显 argv、consumerId、offset、URL、token、raw body、环境值、任意错误文本/码/类名；不复用 print_failure 的动态 code 输出。
- 非法 CLI 参数/配置错误使用固定失败类别并返回非零，不输出原始 argparse 错误或异常文本。
- 首空批不证明无消息；末批不确认已提交；无 offset 算术或去重辅助；body 不记录日志。

## 7. 示例测试（tests/test_example_message_subscriptions.py）
- MockTransport 走真实 SDK auth/business transport 路径，精确断言请求顺序与次数。
- trace 证明 create/read/handler/read 顺序；handler 异常与取消均无额外业务请求；close 与 import 无清理/网络动作。
- CLI 错误返回非零且无异常吞噬伪成功；输出仅固定字段。
- LIVE 独立挂起待资源/授权；本地合成 HTTP 不算平台/设备成功。

## 8. 验证命令（均未执行，不得写 pass）
- `.venv/bin/python -m pytest -q --tb=short --maxfail=5 tests/test_sdk_message_subscriptions.py tests/test_sdk_message_subscriptions_behaviour.py tests/test_example_message_subscriptions.py`
- `.venv/bin/python -m pytest -q --tb=short --maxfail=5`
- `.venv/bin/python -m mypy src/ezviz_unified`
- `.venv/bin/python -m harness.audit --repo . --check-summary`
- `.venv/bin/python -m harness.contract_eval --repo . --require-all`
- `.venv/bin/python -m ruff check src/ezviz_unified/message_subscriptions.py tests/test_sdk_message_subscriptions.py tests/test_sdk_message_subscriptions_behaviour.py examples/message_subscriptions.py tests/test_example_message_subscriptions.py`
- `.venv/bin/python -m ruff format --check src/ezviz_unified/message_subscriptions.py tests/test_sdk_message_subscriptions.py tests/test_sdk_message_subscriptions_behaviour.py examples/message_subscriptions.py tests/test_example_message_subscriptions.py`
- `.venv/bin/python -m build`
- `.venv/bin/python tools/release/check_artifacts.py`
- `.venv/bin/python -m twine check dist/*`
- `.venv/bin/python tools/release/verify_installations.py --installer pip`

## 9. 集成与权限
- cohort LEGACY-MESSAGE-001；capability integration:message_subscriptions.bounded_consumer；owner_queue batch_1_A；case 前缀 ACC-LEGACY-MESSAGE-EXAMPLE；source_doc_ids 983/984。
- 旧服务 982 注明无未来更新/维护但未证停服；新应用优先官方 Webhook 路由。
- 不得占用生产 group：需专用授权 group/租户/受控事件及创建/消费范围。
- 官方建议单实例/单线程：创建/订阅/提交同线程；每 group 一个活跃订阅者且容量未知。
- 五分钟不活跃过期；at-least-once 投递与独立 group offset；重复由应用处理。
- 实际 consume 改变投递/提交状态，需单独显式有界授权，不继承设备只读授权。

## 10. 风险与保留
- 11 项新 LIVE required 的具体资源与次数未冻结；消息创建/消费未获授权。
- 本地合成 HTTP 与 MockTransport 不构成真实服务证据；保留未知不得以实现倒推。
- source anchors 固定：983 tables 1/2/3（element 14/50/74）、code 7/11/42/46；984 tables 1/2/3（element 14/56/100）、code 7/11/48/52。
- 全部 product/发布动作未完成；r15 旧 VOD 证据仅作历史保留。
