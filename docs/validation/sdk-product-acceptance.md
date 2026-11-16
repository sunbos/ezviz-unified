# SDK 产品验收范围与发布门槛（事前冻结）

机器可读结构见 [sdk-product-acceptance.json](sdk-product-acceptance.json)。`schema_version=1`，`baseline_commit=81b86b462825739db8f059a418d5de235db88c87`，`scope_status=provisional_pending_capability_review`，`product_complete=false`。本文与 JSON 同步维护；冲突时以 JSON 为准。

## 边界

本记录冻结验收结构与代表性场景，不宣称全部能力已冻结：能力矩阵语义审查（SP003）尚在进行，矩阵覆盖与优先顺序核定前不批量增加业务代码。每行 `case_id` 明确 capability selector、地区/集群、账号角色/权限、设备要求（无真实标识）、surface（sync/async/CLI/installed）、风险、前提、读/写/物理证据、bounded 上限、授权状态、恢复方案、来源与代码证据及结果；`resource_state` 仅作资源就绪限定（如 `user_confirmed_no_bound_device`、`unknown_awaiting_configuration_check`），不把行标记为完成。未设计的 bounded 记 `null` 且 `freeze_required=true`；未知设备/账户/次数不编造值。新增业务能力必须先绑定具体验收行并先冻结后执行；`ACC-DOMAIN-OFFLINE-PER-NEW-001` 通配行仅是政策占位，不能替代逐项具体行，`needs_review`/`blocked_source` 不算准入也不算完成。

## 分层

- `required`：本轮必须项；未完成或未核定即不标 Goal complete。
- `scenario`：代表性与风险场景；写操作等高风险项另需逐案授权。

## 已记录证据（仅复用条件，非新实测）

| case_id | 结论边界 |
| --- | --- |
| `ACC-CN-SYNC-READONLY-001` | 国内同步单设备只读链已合并证据：[live 记录](live/2026-11-16-diagnostics-v1/README.md)。2026-11-16 21:24:44（Asia/Shanghai），首页 `page_size=1` 首台普通设备、通道 1。6 个业务逻辑入口 = `devices.list` 1 次 + `inspect_device` 固定五项（`devices.get`/`devices.channels`/`devices.status`/`diagnostics.capabilities`/`diagnostics.version`）；认证另占 1 次 HTTP，共 7 次 HTTP。该回执执行器为 `tools/live/verify_diagnostics.py`（`--serve`，scope `bounded_domestic_diagnostic_v1`），非 `verify_devices.py`。64 事件、`dropped_events=0`、`recording_errors=0`，运行所用 SDK 源码与预检快照哈希一致（commit `99613ea`），main CI `43878741197` 已通过 3.12–3.15，回归 4419 tests + 11 subtests。仅同步、单设备、单通道样本；真实当前设备配置尚未提供，不得声称仍在线。 |
| `ACC-OS-PUBLIC-TIMEZONE-001` | 新加坡集群公开时区 sync/async 证据：[singapore.json](live/2026-11-15-public-timezones/singapore.json)。仅覆盖该集群匿名目录调用，不代表硬件或其他集群。 |
| `ACC-OS-AUTH-EMPTYLIST-001` | 新加坡账号显式区域入口认证 + 一页空设备列表：[overseas.json](live/2026-11-14-overseas/overseas.json)。默认入口对本账号返回业务码 10017；空列表只证明当次账号当次范围，不证明设备可用、其他集群或 async。 |

## 待设计/待资源（不算完成）

- `ACC-CN-ASYNC-READONLY-001`：国内异步真实只读尚无执行记录；现有 bounded verifier 为同步。需先冻结 bounded async 场景（与同步同一 6 业务逻辑入口集合）。
- `ACC-CN-CLI-EXAMPLE-001`：示例 CLI 未在已记录联调中运行；verifier 结果不计为 CLI 联网验收。需冻结 CLI 场景与上限。
- `ACC-OS-DEVICE-READONLY-001`：海外有设备只读阻塞，`awaiting_resource_confirmation` + `resource_state=user_confirmed_no_bound_device`——用户 2026-11-17 明确该海外账号当前无绑定设备；账号集群与设备可见权限仍未核定，不编造设备/账号值。
- `ACC-DOMAIN-OFFLINE-PER-NEW-001`：各新增业务域逐项要求 offline contract + error path + diagnostics 覆盖，先行于任何 live 声明；`max_operations=null` 且 `freeze_required=true`。本通配行仅为政策占位——每个准入矩阵的具体能力都必须派生自己的具体验收行（具体 selector、地区/集群、bounded 请求/操作数、授权状态、判据）并先冻结后执行；`needs_review`/`blocked_source` 项不算准入也不算完成。
- `ACC-PLATFORM-MESSAGE-001`：平台消息真实入口/验签/ACK/重复投递/时限/错误处理/重放边界不能由 helper 向量替代；`src/ezviz_unified/webhooks.py` 的纯函数只证明给定 body+secret 的 MAC 匹配，不证明发送方身份、新鲜度或重放安全。需已开通服务、可达回调、独立 secret、目标类型与触发授权；去重与平台重复投递语义须在执行前定义。`resource_state=unknown_awaiting_configuration_check`：用户 2026-11-17 表示不清楚平台消息服务及可用回调入口。配置副作用需超出既有只读授权的显式授权：开通/修改消息类型约 2 小时生效、同手机号萤石云视频 App 设备消息提醒停止且关闭服务不自动恢复（需客服/工单）、控制台「消息测试」暂停原回调并临时打开全部类型。国内 HTTP 头 `t` 时间戳已确认为毫秒（5130）；海外 `t` 单位、时限（2 秒 vs「通常 3 次/一般 5 秒」冲突）与密文措辞仍未核定，不编造取值；仅一条成功回调不得关闭该 required 行。来源：[message-ingestion](../research/2026-11-15-message-ingestion.md)、[webhook-helpers](../research/2026-11-15-webhook-helpers.md)、[message-scope-review](../research/2026-11-17-message-scope-review.md)。
- `ACC-MEDIA-OFFICIAL-INTEGRATION-001`：官方媒体组件接入方案与真实集成验收分开；先交付接入方案文档，真实集成为后续独立验收行。播放/编解码/RTC 引擎维持官方组件承担，平台 token / 取流 token / RTC token 不混用。
- `ACC-WRITE-OPERATIONS-001`：写入、绑定/解绑、权限、付费、升级、删除、格式化需要具体资源范围、次数与成本上限、恢复方案授权，授权前不自动执行。

## 发布门槛（单列）

见 JSON `release_gates`。新增两条明确产品前置 gate（与工程 gate 为 AND 关系，工程 checks 全绿不能单独放行上传）：

- `REL-PRODUCT-SCOPE-RATIFIED`：范围/能力矩阵经审查并核定（无来源行未审），每个能力有已记录处置；`needs_review`/`blocked_source`/未核定项不得作为准入证据、不得被静默排除或计为完成，须先解决为具体处置。
- `REL-PRODUCT-REQUIRED-COMPLETE`：每条 required 验收行按自身判据完成实现、文档与验收（达 `recorded_evidence` 或有依据的 `not_applicable`），required 示例/CLI 行已实测；required 的来源语义问题已处置。

其后：版本与 PyPI 主体/权限/授权均待核定——PyPI 未执行发布，2026-11-17 公开页 404 仅反映当次可见性，不保证名字可用；可提前准备账号/Trusted Publisher（首次授权上传才创建项目，参考[官方文档](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)），用户无需现在提供 token。冻结 artifact/hash、Ruff/mypy/pytest/audit/contract_eval/诊断覆盖、wheel/sdist 内容秘密扫描 + 布局 metadata + Twine + 干净安装、当次 PR 与 main CI、发布索引下载 hash 与安装验证逐项待执行或待授权。`python -m build` 产出不等于发布；SDK 运行时独立于 harness/资料/LLM。已合并诊断 v1 的构建与安装检查只对当时产物有效，重建需重新核验。

## 第二轮保留项（deferred，不是完成）

App adapter、选择性遥测、独立模型评测、受控 RSI 均为第二轮范围，见 [计划](../plans/2026-11-17-sdk-product-completion.md)。

## 证据纪律

只引用仓库内真实存在的文件；不写入 token、序列号、URL 原始业务数据。blocked / awaiting_resource_confirmation 均不计为完成。旧 live index 与 roadmap 不在本单修改范围，后续单独派单。

## 未决问题

见 JSON `open_questions`：当前国内设备在线状态未确认；async/CLI bounded 场景未设计；海外账号当前无绑定设备（`user_confirmed_no_bound_device`），集群与设备可见权限仍未核定；平台消息服务/可用回调入口未知（`unknown_awaiting_configuration_check`），去重与重复投递语义须先定义，国内 `t` 毫秒已确认、海外单位/时限冲突/密文措辞仍待核定，开通与测试的配置副作用须显式授权；版本与 PyPI 账户/权限/上传授权未确认（未执行发布，2026-11-17 公开页 404 仅为当次可见性，可提前备账号/Trusted Publisher）；新业务域须先绑定具体验收行，通配行不替代逐项。
