# 有界国内 VOD 资源只读探测计划（SP130 冻结 + SP134 更正 + SP142 准入）

一次性候选探测设计，非产品验收完成。用户已允许"先试试现成账号功能"：现成资源、有界、只读。**无**创建/解冻/付费/媒体下载授权；不替代全产品验收，也不替代新四入口的全部 live 验收。本文件整合 SP130 冻结与 SP134 架构更正；实现仍是候选、尚未通过合成验收，不预写"已修"或"已执行 live"。

SP142（`docs/coordination/SP142/admission.json`，scope_revision `2026-11-18-r12-vod-resource-probe`）已公开批准新增 scenario case `ACC-CN-VOD-RESOURCE-DISCOVERY-001`：`bounded_resource_probe_frozen_not_executed`、`live_ready=false`、`authorized_readonly=true`、`max_requests=7`/`max_operations=2`，父 required `ACC-CANDIDATE-CN-5342-D0-LIVE-001` 仍未完成——本 probe 只做 sync 辅助资源发现，不替代 async 或其它方法的 live。

## 范围与边界

- **探测链（≤2 次逻辑调用）**：单 `Client`（`RegionConfig.china()`、`timeout=10`）认证一次 → `client.storage.spaces(start_time=…, end_time=…, page_size=50, biz_type='vod')` 一次逻辑调用（无 cursor、不翻页）→ 命中后对绑定的原 `spaceId` 执行 `client.vod.files(space_id=原值)` 一次逻辑调用。
- **不执行**：猜测 ID/名字、切换窗口、翻页、访问/下载任何 URL、`unfreeze_file` 或任何写操作、调用 1.0 任何方法、创建/开通/付费。
- **窗口**：`end` 取本地 UTC+8 墙钟（`_SHANGHAI_WALL`，naive 整秒），`start = end - 30 天`，`window_basis='createTime'`、`window_timezone_assumption='Asia/Shanghai_wall_time'`。这是本 SDK 探测的**设计选择**，不宣称官方时区保证。

## 资源选择（不猜 ID）

只取 spaces 单页返回 `data.result` 列表中**按服务端顺序首个**合格行：dict、`bizType=='vod'`、`type(namespaceType) is int` 且 `==1`、`type(spaceId) is int`（非 bool）。页行数须 `<=50`。找不到合格候选即 `not_verified`（`no_usable_vod_space_in_selected_page`），空页 `empty_space_page`，形状异常 `unexpected_space_page_shape`。命中后把原 `spaceId` 绑定到专用 guard，仅对绑定值发 `vod.files`；绑定值不入报告、不日志、不 hash。

## 网络硬界（唯一 origin + 精确契约）

- 唯一 origin `https://open.ys7.com:443`（HTTPS、精确 host、默认端口、无 userinfo/fragment、不跟 redirect）。factory 只接受该 root origin，不开放任意服务端/transport 用户配置。
- 三条 method/path 白名单路由、共享预算 `max_http_requests=7`，路由上限 `authentication≤3 / spaces≤2 / vod≤2`：
  - `POST /api/lapp/token/get`：无 query，urlencoded body 恰好 `appKey`/`appSecret` 单值。
  - `GET /api/service/open/storage/engine/space/listById`：body 为空，`accessToken` 仅在 header（单一值），query 键集恰好 `startTime`/`endTime`/`pageSize=50`/`bizTypeList=vod`，且 `startTime`/`endTime` 须等于 `_RequestBudget` 冻结窗口（含 invalid-token 重放也必须重发同一窗口）。
  - `GET /api/service/open/vod/files`：body 为空，`accessToken` 仅 header，query 键集恰好 `spaceId` 且等于绑定值。
- 拒绝重复 query/form key、额外字段、错误 Content-Type/Transfer-Encoding、超限 Content-Length。任一委托前先做契约检查再计预算。重放仅由既有 SDK invalid-token 策略决定，工具自身不重试。
- delegate 用 `httpx.HTTPTransport(retries=0, trust_env=False)`，仅本验收使用，不修改全局 HTTPX。请求强制 `Accept-Encoding: identity`；非 identity 响应拒绝并 close（本次不证明压缩路径）。每响应 stream 始终关闭。

## SP134 更正：响应体上限是"保留/接受 payload 上限"，不是网络字节上限

SP130 曾误写"网络/transport 实际读取最多 1MiB+1"。HTTPX `BaseTransport` 从 delegate 收到的已是成形 chunk，**不能**限制底层 socket/TLS 预读或单个已分配 chunk。实际可强制且本工具采用：

- 所有成功响应**保留并重组的 payload ≤ 1 MiB**（`_MAX_RESPONSE_BYTES`）。
- **首个**将使保留总量越界的 chunk 不进入 buffer、立即拒绝并 `close`，不再继续迭代；绝不先 read 全部再切片。
- `response_limit` 记为**接收/保留 payload 上限**，不声称网络字节数或进程 RSS 恰为 1 MiB。
- 须用流式多 chunk 与单个 oversize chunk 验证"拒绝 + 停止迭代 + close"；保留 1 MiB 边界通过、1 MiB+1 失败。
- 未删除任何失败测试、未降低 SDK 断言、不降低既有通过标准。真实 factory 保持 `HTTPTransport`。

## 时限（无隐式 timer/thread）

- **表单 intake 总体 600 s**（`_SERVE_TIMEOUT_SECONDS`）。
- **单连接**：请求行 + headers + body 共用一个**绝对 10 s** deadline（`_CONNECTION_DEADLINE_SECONDS`），且不得超过 intake deadline；不是每次 recv 刷新 10 s。用带绝对截止的 `_DeadlineReader`，每次 `socket.recv` 只取剩余 timeout，实现 `read`/`readline`；不设独立线程或定时器。
- **探测 120 s 软 deadline**（`_PROBE_DEADLINE_SECONDS`）：仅在**发送前**与**每个响应 chunk 之间**检查。HTTPX 读取慢响应头可能多次阻塞，每次阻塞都受既有 10 s I/O timeout 限制、且都可能越过 120 s 检查点，因此**不能保证总体最多 120 s+10 s**，也不声称"最多再一个 I/O timeout"——如实标注软截止性质，不伪称硬整体截止。
- **外层墙钟 900 s**：实际 live 运行时控制器必须在 probe 之外另施加 900 s 外层墙钟超时；超时即不计通过，不得臆测请求实际数为 0。这不是 SDK 运行时后台任务，是工具自身的执行边界。
- 完成或失败都在 `finally` 清理输入容器并结束 one-shot。

## 一次性本地输入（loopback，无默认启动）

- 随机高熵 `route`；`Host` 与 `Origin` 精确 `127.0.0.1:port`。GET 表单 `no-store` + CSP、无外部资源、`app_key`/`app_secret` 为 `type=password` 不回显。
- POST 仅 `application/x-www-form-urlencoded`、`Content-Length` 单值且 `≤8192`、无 Transfer-Encoding、UTF-8 strict、key 唯一且键集仅 `app_key`/`app_secret`、strict percent-escape。单次有效 POST 消费；`values`/`config` 在 `finally` 清空。不把 request body 或异常打印。
- POST 响应只回固定结果摘要（outcome/stage/reason），不回显表单或资源数据。服务器最长等 600 s，非后台隐式 task，完成/超时显式关闭。
- `--output` 必须 CLI 显式提供；拒绝覆盖已存在路径（`x` 模式），只写通过白名单校验的报告，不写任何凭据。仅 print 启动 loopback URL 与完成固定状态，不 print 异常。不自动开浏览器；用户在本地链接输入前不做任何网络调用。

## 报告字段（白名单）

`schema_version=1`、`scope='bounded_domestic_vod_resource_discovery'`、`region='domestic'`、`recorded_at_utc`、`window_start`、`window_end`、`window_basis='createTime'`、`window_timezone_assumption='Asia/Shanghai_wall_time'`、`max_http_requests=7`、`http_requests`(0..7)、`logical_calls`(0..2)、`route_counts`(固定 3 key)、`stage`(credential/authentication/spaces/vod/complete)、`outcome`(success/not_verified/failed)、`reason`(固定 enum)、`space_page_count`(0..50 或 null)、`space_has_next`(bool/null)、`ordinary_vod_candidate_found`(bool)、`vod_data_type`(array/object/null/other)、`vod_item_count`(非负 int/null)、`known_field_types`（固定字段名→固定类型桶集合，仅 `_KNOWN_VOD_FIELDS`）、`error_family`(none/authentication/permission/api/protocol/transport/unknown)、`cloud_recording_v1_discovery='not_available_in_current_sdk'`。

`known_field_types` 已固定字段名恰为 13 个：`folderNode`、`fileName`、`fileType`、`fileChildType`、`fileSize`、`totalSize`、`storageType`、`uploadStatus`、`createTime`、`expireTime`、`coverPic`、`coverPicUrl`、`urls`。每项只记录 presence/类型桶（如 `array/object/number/string/bool/null/absent`），不输出原值、不输出动态 map key、不设必填、不推导 URL 可用。

- 不输出任意 raw 字段名/动态 map key；可省略无依据字段，但不得多出未审字段。不存 ID/hash/name/token/key/secret/URL/data/raw/异常文本/locals/env。未知错误映射 `unknown`/`other` 而非 `str(exception)`；process-control `BaseException` 继续传播，ordinary unknown 明确 `failed`/`unknown`，不当 success。
- SDK `ApiResponse` 不写 repr/str 日志；禁用本工具请求访问日志。写文件前 `_report_allowed` 白名单校验。

## 语义边界（不夸大）

- spaces 成功但空页或无合法候选 → `not_verified`：30 天 `createTime` 窗口漏检更早创建的空间，权限错误不能断定"无此功能"。
- `vod.files` 返回 array（允许为空）→ `success`，**只证明该绑定空间本次 SDK 查询成功**；不证明媒体存在、全部账号、硬件或 1.0。
- `vod.data` 非 array → `not_verified`/`unexpected_vod_data_shape`，不擅自 flat `data.result`。
- 既有 401/403 等只经 `error_family` 报告，不引原始错误码文本。
- 未知字段或资源缺失不等于"无此功能"：本探测只覆盖 sync `spaces`+`vod.files` 两个入口的辅助资源发现；父 required 行 `ACC-CANDIDATE-CN-5342-D0-LIVE-001` 仍未完成，本 probe 不能替代 async 或 1.0 任何方法的 live 验收。

## 验收条目（先合成，后才允许真实表单）

实现仍是候选、未通过；以下未完成前**不**启动真实表单：

1. 预算/路由：`authentication≤3/spaces≤2/vod≤2`、合计≤7；额外 route/origin/query/body、无绑定资源的请求在发送前拒绝。
2. 契约：空页/错误形状/候选筛选（服务端顺序首个合格行）/窗口与时区序列化/invalid-token 重放须重发同一冻结窗口。
3. 响应界：流式多 chunk 与单 oversize chunk 验证拒绝+停止迭代+close；1 MiB 边界通过、1 MiB+1 失败；非 identity `Content-Encoding` 拒绝并 close。
4. 用真实 SDK `MockTransport` 精确 wire 验证三路由。
5. 报告：未知键/值拒绝、sentinel 隐私、固定枚举与类型桶、白名单校验先于写盘。
6. 表单：Host/Origin/Content-Type/重复字段/超限/二次提交/超时/日志不回显/`--output` 仅 1 份且不覆盖。
7. 云 behaviour、probe tests、完整回归与本批发布检查完成后，方可启动真实表单。

## 依赖与运行时

- 复用 `tools.live.verify_diagnostics._credentials` 纯 helper 校验 `app_key`/`app_secret`/`region='domestic'`；不执行其旧 `verify`/旧设备探测。不读取 env、本地 secret 文件或任何 `.env`；凭据仅进程内存，finally 清除。
- 本工具不进入 SDK 运行时，不新增 LLM 依赖，不改全局 HTTPX；SDK 运行时无本工具依赖。

## 不扩大授权

- `unfreeze_file` 等写操作不在本探测内；本次不创建/解冻/付费/下载媒体。
- 海外无绑定设备、平台消息服务未知、PyPI 权限/版本未冻结仍阻塞；本探测不解除任何 required 行。
- 通过本探测不等于整 cohort 通过或产品完成；`product_complete=false` 不变。
