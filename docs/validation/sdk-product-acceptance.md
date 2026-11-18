# SDK 产品验收范围与发布门槛（事前冻结）

机器可读结构见 [sdk-product-acceptance.json](sdk-product-acceptance.json)。`schema_version=1`，`baseline_commit=81b86b462825739db8f059a418d5de235db88c87`，`scope_status=provisional_pending_capability_review`，`product_complete=false`。本文与 JSON 同步维护；冲突时以 JSON 为准。

## 边界

本记录冻结验收结构与代表性场景，不宣称全部能力已冻结：能力矩阵语义审查（SP003）持续推进，矩阵覆盖与优先顺序核定前不批量增加业务代码。每行 `case_id` 明确 capability selector、地区/集群、账号角色/权限、设备要求（无真实标识）、surface（sync/async/CLI/installed）、风险、前提、读/写/物理证据、bounded 上限、授权状态、恢复方案、来源与代码证据及结果；`resource_state` 仅作资源就绪限定（如 `user_confirmed_no_bound_device`、`unknown_awaiting_configuration_check`），不把行标记为完成。未设计的 bounded 记 `null` 且 `freeze_required=true`；未知设备/账户/次数不编造值。新增业务能力必须先绑定具体验收行并先冻结后执行；`ACC-DOMAIN-OFFLINE-PER-NEW-001` 通配行仅是政策占位，不能替代逐项具体行，`needs_review`/`blocked_source` 不算准入也不算完成。

`case_schema` 增加 `capability_ids`/`operation_ids`/`scope_revision`/`freeze_state`/`template_case_id`/`criterion_assessment` 可选字段，供具体 case 绑定范围登记：`capability_id` 保留来源谱系（candidate ID 不等于准入），`operation_ids` 是 registry ID（区别于 `settings.list_areas` 这类 runtime/diagnostic entrypoint）；`freeze_state=frozen_for_implementation` 只冻结接口与判据为实施合同。全范围登记、单 cohort 实施准入、live 执行与发布是四件不同的事——本文件的 case 冻结属于第一件/第二件的输入准备，`pending_execution` 的 case 未执行，更未达发布门槛。

## 分层

- `required`：本轮必须项；未完成或未核定即不标 Goal complete。
- `scenario`：代表性与风险场景；写操作等高风险项另需逐案授权。

## 已记录证据（含历史复用行与 SP036 新通过行，均不超出各自边界）

| case_id | 结论边界 |
| --- | --- |
| `ACC-CN-SYNC-READONLY-001` | 国内同步单设备只读链已合并证据：[live 记录](live/2026-11-16-diagnostics-v1/README.md)。2026-11-16 21:24:44（Asia/Shanghai），首页 `page_size=1` 首台普通设备、通道 1。6 个业务逻辑入口 = `devices.list` 1 次 + `inspect_device` 固定五项（`devices.get`/`devices.channels`/`devices.status`/`diagnostics.capabilities`/`diagnostics.version`）；认证另占 1 次 HTTP，共 7 次 HTTP。该回执执行器为 `tools/live/verify_diagnostics.py`（`--serve`，scope `bounded_domestic_diagnostic_v1`），非 `verify_devices.py`。64 事件、`dropped_events=0`、`recording_errors=0`，运行所用 SDK 源码与预检快照哈希一致（commit `99613ea`），main CI `43878741197` 已通过 3.12–3.15，回归 4419 tests + 11 subtests。仅同步、单设备、单通道样本；真实当前设备配置尚未提供，不得声称仍在线。 |
| `ACC-OS-PUBLIC-TIMEZONE-001` | 新加坡集群公开时区 sync/async 证据：[singapore.json](live/2026-11-15-public-timezones/singapore.json)。仅覆盖该集群匿名目录调用，不代表硬件或其他集群。 |
| `ACC-OS-AUTH-EMPTYLIST-001` | 新加坡账号显式区域入口认证 + 一页空设备列表：[overseas.json](live/2026-11-14-overseas/overseas.json)。默认入口对本账号返回业务码 10017；空列表只证明当次账号当次范围，不证明设备可用、其他集群或 async。 |
| `ACC-OS-PUBLIC-AREAS-OFFLINE-001` | 公开国家/地区目录离线验收已通过（`execution_outcome=passed`，verified code commit `58e7881`）：证据 [checks.json](2026-11-17-public-areas/checks.json)（说明见 [README](2026-11-17-public-areas/README.md)）。初始 targeted 3 failed / 311 passed，定向修订后 SDK/harness targeted 327、示例 22、全量 4580+35 subtests、registered contracts 204 / wire 1664 全过，mypy 43 文件、Ruff 通过；`0.1.0a1` wheel/sdist 构建、内容检查、Twine、干净安装（sdist 2744 测试）完成，安装依赖走网络、SDK smoke 不触真实 API。属 code58 开发候选验证，**非最终 SDK 产品 release 或 PyPI 发布**。 |
| `ACC-OS-PUBLIC-AREAS-SDK-001` | 公开国家/地区目录 SDK 真实验收已通过（`execution_outcome=passed`，同一 commit）：证据 [sdk.json](live/2026-11-17-public-area-directory/sdk.json)（说明见 [README](live/2026-11-17-public-area-directory/README.md)）。UTC 2026-11-17T02:52:27 固定 `https://open.ezvizlife.com` 匿名 sync/async 各一次，合计 2 HTTP/2 逻辑调用；两 mode HTTP 200 + 业务码 `"200"`，`data` 240 项且四字段类型全部匹配，identity 编码每 mode 16442 字节，client/transport 均关闭；回执只存计数/类型/状态/hash（SHA `88cce20d…7adc`），46 输入 pin 与 code58 一致。240 为当次观察值非保证；仅 GET+identity，未覆盖其他 origin/POST/压缩路径/集群路由/设备。 |

## 公开国家/地区目录 case 结果说明（SP036，scope_revision=2026-11-17-r1）

两 case 在 `scope_revision=2026-11-17-r1` 下冻结并在 code commit `58e7881a3e7aee4c3ffc431b5016b019cefb447c` 上通过：`result=recorded_evidence`、`execution_outcome=passed`、`blockers=[]`，criterion_assessment 各项均为 `met`（offline 12 项、SDK 8 项，明细见 JSON）。SP036 决策记录（[acceptance.json](../coordination/SP036/acceptance.json)，scope_revision `2026-11-17-r2`）只把这两条 case 记为通过；**其它旧 case 与 release gates 全部不变，不升级**。frozen 判据与历史注记保留于 JSON `historical_freeze_notes`/`limitations`（GET-only、POST 形状未核定保留为 source issue、240 非固定数量、SP016 raw HTTP 不继承为 SDK 证据等），此处不再复述。

- `ACC-OS-PUBLIC-AREAS-OFFLINE-001`（required, offline_only）：判据覆盖 sync/async 准确 GET 与空 query/body/无 token、provider 零调用、成功 `data` 额外字段与空数组原样保留、国内 region/缺 public `api_base_url`/closed client 前置失败、业务/传输/格式错误不重试、async 取消传播、diagnostic on/off 语义一致与白名单/覆盖审计更新。`max_requests=0`/`max_operations=0` 只指真实云请求/业务效果。
- `ACC-OS-PUBLIC-AREAS-SDK-001`（required, read_only）：匿名 `Client`/`AsyncClient` 各一次显式 `api_base_url=https://open.ezvizlife.com` 真实调用；无凭据、不跟 redirect、不重试、不访问设备，每阶段 timeout 10s、解压响应 1 MiB 上限由验收 transport 边界控制，各自正确 close/aclose。`limitations` 明确：GET-only、公共目录不证明集群路由/绑定设备/硬件、identity-only 协商未覆盖压缩响应与其他 origin。`resource_state=not_applicable` 不解除海外绑定设备必需 case 的 `user_confirmed_no_bound_device` 阻塞。

两 case 的 `capability_ids=[candidate:overseas:469:1.12]`、`operation_ids=[global.settings.list_areas]`；`settings.list_areas` 是 runtime/diagnostic entrypoint，**不是** registry operation_id。来源 overseas:469 §1.12（e1459 固定 origin、e1463 列 GET/POST、e1468 GET 示例、e1476 字段表）；SDK 明确只选 GET，POST 语义未核定保留为 source issue，不伪造 declared_pair 或提升 parser 恢复数。

## 待设计/待资源（不算完成）

- 云录制 1.0 文件与 VOD 元数据（既有批 `b251b11`，已随 PR #16 合并入 main `254ea73`，历史结论保留）：四条 **OFFLINE** required（`ACC-CANDIDATE-CN-5341/1375/1377/5342-D0-OFFLINE-001`）已 `recorded_evidence`+`passed`，证据 [checks.json](2026-11-18-cloud-files/checks.json)；对应四条 **LIVE** required（`5341`/`1375`/`1377`/`5342` 的 `-D0-LIVE-001`）仍 `awaiting_authorization`/`awaiting_resource_confirmation`，`freeze_state=awaiting_resource_scope_and_authorization`、真实资源/权限/请求上限未冻结，`max_requests=null` 不可运行；截至当时的已核离线/历史记录中这些 case 未发真实云请求。scenario `ACC-CN-VOD-RESOURCE-DISCOVERY-001` `result=pending_execution`，仅离线安全判据 `met`；其 SP166 国内只读资源发现窗口实际**超时关闭**、无报告、`execution_outcome=not_verified`、实际 HTTP 数 `null`（不可声称 0 或账号无资源），见 [live/2026-11-18-vod-resource-discovery/attempt.json](live/2026-11-18-vod-resource-discovery/attempt.json)。**新增 VOD remux/任务批 `fce298b`**：四条 OFFLINE（`3971`/`3972`/`4941`/`3973`）已 `recorded_evidence`+`passed`（见文末当前批次），四条 LIVE 未执行/未授权或资源未齐。
- `ACC-CN-ASYNC-READONLY-001`：国内异步真实只读尚无执行记录；现有 bounded verifier 为同步。需先冻结 bounded async 场景（与同步同一 6 业务逻辑入口集合）。
- `ACC-CN-CLI-EXAMPLE-001`：示例 CLI 未在已记录联调中运行；verifier 结果不计为 CLI 联网验收。需冻结 CLI 场景与上限。
- `ACC-OS-DEVICE-READONLY-001`：海外有设备只读阻塞，`awaiting_resource_confirmation` + `resource_state=user_confirmed_no_bound_device`——用户 2026-11-17 明确该海外账号当前无绑定设备；账号集群与设备可见权限仍未核定，不编造设备/账号值。
- `ACC-DOMAIN-OFFLINE-PER-NEW-001`：各新增业务域逐项要求 offline contract + error path + diagnostics 覆盖，先行于任何 live 声明；`max_operations=null` 且 `freeze_required=true`。本通配行仅为政策占位——每个准入矩阵的具体能力都必须派生自己的具体验收行（具体 selector、地区/集群、bounded 请求/操作数、授权状态、判据）并先冻结后执行；`needs_review`/`blocked_source` 项不算准入也不算完成。
- `ACC-PLATFORM-MESSAGE-001`：平台消息真实入口/验签/ACK/重复投递/时限/错误处理/重放边界不能由 helper 向量替代；`src/ezviz_unified/webhooks.py` 的纯函数只证明给定 body+secret 的 MAC 匹配，不证明发送方身份、新鲜度或重放安全。需已开通服务、可达回调、独立 secret、目标类型与触发授权；去重与平台重复投递语义须在执行前定义。`resource_state=unknown_awaiting_configuration_check`：用户 2026-11-17 表示不清楚平台消息服务及可用回调入口。配置副作用需超出既有只读授权的显式授权：开通/修改消息类型约 2 小时生效、同手机号萤石云视频 App 设备消息提醒停止且关闭服务不自动恢复（需客服/工单）、控制台「消息测试」暂停原回调并临时打开全部类型。国内 HTTP 头 `t` 时间戳已确认为毫秒（5130）；海外 `t` 单位、时限（2 秒 vs「通常 3 次/一般 5 秒」冲突）与密文措辞仍未核定，不编造取值；仅一条成功回调不得关闭该 required 行。来源：[message-ingestion](../research/2026-11-15-message-ingestion.md)、[webhook-helpers](../research/2026-11-15-webhook-helpers.md)、[message-scope-review](../research/2026-11-17-message-scope-review.md)。

  本地层补充（SP117，2026-11-18）：三条本地用例 `ACC-WEBHOOK-CORE-001`/`ACC-WEBHOOK-HTTP-001`/`ACC-WEBHOOK-CLI-001` 已在本机记录通过（见 [checks.json](2026-11-18-webhook-receiver/checks.json) 与 [README](2026-11-18-webhook-receiver/README.md)）。证据为 commit `11cd8af` 上 4705 passed + 35 subtests、真实 TCP loopback 合成链（sender→HTTP→SDK 验签/解析→有界 inbox→JSON ACK）与真实 CLI 子进程/PTY 检查；全部合成数据、零真实平台/设备业务。平台用例（CN/OS）及 `ACC-PLATFORM-MESSAGE-001` 仍 `awaiting_resource_confirmation`，未授权配置变更。signed-only 为本地设计，OS `t` 单位未推断；inbox 非持久、不提供跨重启 exactly-once；CLI 真实 TTY 只证明本机交互路径，不冒充平台回调。该本地通过不改变 SDK src/依赖/HTTPX/重试行为，不升级本 required 行。
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

见 JSON `open_questions`：当前国内设备在线状态未确认；async/CLI bounded 场景未设计；海外账号当前无绑定设备（`user_confirmed_no_bound_device`），集群与设备可见权限仍未核定；平台消息服务/可用回调入口未知（`unknown_awaiting_configuration_check`），去重与重复投递语义须先定义，国内 `t` 毫秒已确认、海外单位/时限冲突/密文措辞仍待核定，开通与测试的配置副作用须显式授权；版本与 PyPI 主体/账户/权限/上传授权未确认（未执行发布，2026-11-17 公开页 404 仅为当次可见性，可提前备账号/Trusted Publisher）；新业务域须先绑定具体验收行，通配行不替代逐项；公开区域目录 cohort（`candidate:overseas:469:1.12`）两条 case 已于 code58 通过（见上文），但该通过不扩展其边界：240 项非固定数量，`/api/lapp/area/list` 的 POST 形态/认证未核定保留为 source issue（SDK 仅选 GET），其他 origin/压缩路径/集群路由未实测，SP016 原始 HTTP 观察仅为协议证据。

scope-register 历史快照 `2026-11-17-r2` 曾记 253 槽位/204 已登记 operation，`2026-11-18-r11-cloud-file-admission` 为本批 G1 记录（354 槽位/373 cases），`2026-11-18-r12-vod-resource-probe` 登记 probe scenario；历史 `2026-11-18-r13-cloud-file-offline-acceptance` 为 354 槽位/374 cases/208 operation；**历史离线验收快照 `2026-11-18-r15-vod-remux-offline-acceptance`**：358 capability 槽位（仍非官方 API 总数/覆盖率）、**382 cases**、212 已登记 registry operation（contracts/bindings/wire cases：国内 145、海外 67）、107 条已登记 source issues；诊断 141 business / 282 sync+async bodies；source 身份集 4051/observation 3019 不变。新增 scenario `ACC-CN-VOD-RESOURCE-DISCOVERY-001`（SP142，`docs/coordination/SP142/admission.json`）：`bounded_resource_probe_frozen_not_executed`、`authorized_readonly=true`、`live_ready=false`、`max_requests=7`/`max_operations=2`，父 required `ACC-CANDIDATE-CN-5342-D0-LIVE-001` 仍未完成——probe 只覆盖 sync `spaces`+`vod.files` 辅助资源发现，不替代 async 或 1.0 任何方法 live。G0 范围责任/优先级已批准（SP030），但这不是全来源语义完成；G1 已允许云四入口（SP122）与 VOD remux/任务四入口（SP169）；全 scope `all_semantics_dispositioned`/`required_acceptance_complete`/`product_complete` 仍为 `false`。5 条国内 `device_voice` 仅为 review 候选、无实现或 G1/live 授权；七个官方 npm 版本 archive 完整性与包身份已核定，但 11 repo 内容/34 binary/许可证兼容与集成验收仍未完成。

main 已含 **PR #16 合并头 `254ea73acafc5dd2f81c90c4a867fa0fb56a2d26`**（既有云文件 cohort `b251b11` 已并入）；该 PR 的 CI run `44235604644` 与 main CI run `44236090511` 的 Python 3.12–3.15 四 job 均通过——此事实来自 root 已核 SP165，**仅对应已合并的云文件 code，不代表当前 `fce298b` VOD remux code 的 CI**。本离线验收快照记录时，VOD remux 实现 commit `fce298b34911eac89e6c5813b203c5b267e80095` 为本地提交，**未 push/未建 PR/无本批新 CI**；后续状态请读对应 PR/CI 真实记录，不预写未来 PR 号，也不能把 PR16/旧 CI 继承为本批通过。`fce298b` 是 SDK 代码 pin，不要求该 code commit 本身单独有 CI——未来 CI 验证的是当次 PR 或 main HEAD，且其中 SDK 哈希与此 code 一致。

当前批次（本离线验收快照记录时实现 commit `fce298b34911eac89e6c5813b203c5b267e80095` 为本地提交，未 push/未建 PR；后续状态以对应 PR/CI 真实记录为准）：四个国内入口 `vod.create_remux_task`/`vod.task`/`vod.tasks`/`vod.recording_urls` 已实现（`src/ezviz_unified/vod.py`）。**四条 OFFLINE required case（`ACC-CANDIDATE-CN-3971/3972/4941/3973-D0-OFFLINE-001`）已 source-pinned `recorded_evidence` + `execution_outcome=passed`；四条 LIVE required 未执行/未授权或资源未齐**。正式验收证据：[checks.json](2026-11-18-vod-remux/checks.json)。本机观察：全量 pytest 6023 + 35 subtests（46.29 s）、lint/format、mypy 45、wire 212 contracts/1728 executions 全过、诊断 141 business/sync+async body 282、定向 wire 246/behaviour 304/integration 507、构建/产物/Twine/干净 wheel+sdist 安装均过；sdist 3925 passed 11.75s。计数为已执行测试单元/已登记契约，**非覆盖率或 API 总数**；安装依赖走网络但 `real_api_used=false`。`0.1.0a1` 是开发构建、未冻结最终发行、未上传 PyPI。国内只读资源发现窗口（SP166）实际**超时关闭**、无报告、`execution_outcome=not_verified`；实际 HTTP 请求数 `null`，不可声称 0 或账号无资源，不继承为 remux 实测，公开记录 [live/2026-11-18-vod-resource-discovery/attempt.json](live/2026-11-18-vod-resource-discovery/attempt.json)；凭据从未进入本任务。不能宣称 Goal 完成、官方全部覆盖或实测完成。`product_complete=false`。

本批验收登记联动说明：上述 SP188 全量 pytest（6023 + 35 subtests）与 SP183 集成批（507）是 `fce298b` SDK 代码/测试字节上的历史通过记录。离线状态升级后原 harness 测试硬要求 `not_verified` 状态，已另单修正为带证据状态守卫；post-acceptance 定向执行结果与新测试 sha256 见 `checks.json` 的 `post_acceptance_validation`。修正后的新测试不属于 `fce298b` 代码字节，不能冒充其计数；后续定向检查 SP206 已通过：300 项测试及 lint；新测试 SHA 与实际回执见 `post_acceptance_validation`，不改写此前全量执行的版本或计数。SDK 源码与入包 SDK tests 未变、未重建。

**历史批次（云文件，commit `b251b11`，已随 PR #16 合并入 main `254ea73`）**：四个入口 `cloud_recording_v1.unfreeze_file`/`file_info`/`download_urls`、`vod.files` 已实现；四条 OFFLINE required（`5341`/`1375`/`1377`/`5342`）`recorded_evidence`+`passed`，四条 LIVE required 仍 `awaiting_*`。以下计数属于该旧 code 的当时观察，与上面 `fce298b` 的计数分开、不混用：全量 pytest 5448 + 35 subtests（45.39 s）、mypy 45、wire 208 contracts/1696 executions、诊断 137 入口/sync+async body 274、cloud-wire 190、Vod 145、云 behaviour+表单 312，sdist 3371 passed 10.39s；证据 [checks.json](2026-11-18-cloud-files/checks.json)（说明见 [README](2026-11-18-cloud-files/README.md)），计划 [2026-11-18-cloud-files-readonly-probe](../plans/2026-11-18-cloud-files-readonly-probe.md) 与 [2026-11-18-cloud-recording-v1-vod](../plans/2026-11-18-cloud-recording-v1-vod.md)。

## r16 三业务域实施准入（历史 G1 准入快照）

新增 G1（LEGACY-MESSAGE-001 消息订阅域，docs 983/984，facade message_subscriptions，2 SDK op + 1 integration）与公开 SP213 G1 及案例已登记 r16；367 slots/402 cases 为非覆盖计数。本轮 8 SDK op + 3 integration 属新增准入，983/984 复用原 ID。actual registry 212（145/67）、诊断 141/282/387；target 220（153/67）/149/298/403 仅为目标值而非支持事实。11 项新 LIVE required 的具体资源与次数未冻结，消息创建/消费未获授权；既有国内只读授权延用但不覆盖消息提交副作用。全部 product/发布未完成；r15 旧 VOD 证据仅作历史保留，不构成本域证据。

## r17 三业务域离线验收（当前段，实现 commit `dd105464bb68de358b555d94aff81c9689bc83b7`）

scope 推进至 `2026-11-19-r17-capture-messaging-hubs-offline-acceptance`。三 cohort（`CLOUD-CAPTURE-001`/`LEGACY-MESSAGE-001`/`HUB-DISCOVERY-001`）的 **11 条 required OFFLINE（8 SDK 操作 + 3 仓库示例）`recorded_evidence`+`passed`**；对应 **11 条 required LIVE 全未执行**（`awaiting_*`/资源与授权未齐），**本批未发真实云/设备/消息请求**。正式证据：[checks.json](2026-11-19-capture-messaging-hubs/checks.json)。

- 覆盖入口：云抽帧 `cloud_capture.create_interval_task`/`create_timing_task`/`task`，消息 `message_subscriptions.create_consumer`/`consume`，HUB `hubs.list_subdevices`/`subdevice_info`/`channels`；示例 `examples/cloud_capture.py`/`message_subscriptions.py`/`hub_discovery.py`。
- registry 现为 **220**（国内 153 / 海外 67），诊断 149 business / 298 sync+async bodies；本批新增 8 行 binding 均 `offline_status=passed`/`live_status=not_verified`。367 capability 槽位、402 cases、1792 传输检查计数为槽位/案例口径，非 API 总数或覆盖率。
- 本机观察（该 code commit 上）：全量 pytest **7220 + 35 subtests**（62.34 s）、lint/format/mypy、wire 220 contracts/1792 executions、构建/Twine/干净 wheel+sdist 安装均过；sdist **4827 tests**（18.70 s）。`0.1.0a1` 为开发构建、未冻结最终发行、未上传 PyPI；安装依赖走网络但 `real_api_used=false`。
- 限制沿用 checks.json `limitations`：真实抽帧任务、真实消息投递与提交、HUB 与实际子设备的对应关系尚待有界联调；账号套餐、设备型号覆盖、收费、媒体可用性与 PyPI 发布均未由本批离线结果验证。后续国内 async 为复用既有 SDK 的只读验收，不是本批新增实现。
- PR/main 状态请读仓库按 commit `dd105464bb68de358b555d94aff81c9689bc83b7` 的对应 PR/CI 真实记录，不预报 CI 已通过。`product_complete`/`required_acceptance_complete`/`all_semantics_dispositioned` 仍 `false`；上述 OFFLINE 通过不等于 cohort 或产品完成。
