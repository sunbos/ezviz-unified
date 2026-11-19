# 能力登记与验收数据说明

本目录存放产品能力的权威登记数据 [scope-register.json](scope-register.json)，与 [../validation/sdk-product-acceptance.json](../validation/sdk-product-acceptance.json) 配合使用：前者登记每个 `capability_id` 的字段与 `acceptance_case_refs`，后者登记逐条验收结论。定位证据时先按 `capability_id` 查 scope，再沿 `acceptance_case_refs` 找到具体用例与记录。

## 字段口径

- `implementation.status`：实现情况核定结果；`unknown` 表示尚未核定实现情况，`missing` 才是已核定缺失。
- `review`：语义审核状态，取 `candidate`/`needs_review`/`ratified` 等值。
- `result`：各验收 case 的状态与证据，取 `recorded_evidence`/`awaiting_design`/`pending_execution` 等值；并非每项都已有已跑证据。
- `freeze_state`：合同/案例是否冻结；自拟案例草案不算冻结。
- `requirement`：取值为 `required` 时表示本轮必须完成范围。

## 2026-11-18 本批变更

- 101 个国内候选槽位纳入场景说明：5 个复用既有 voice 候选 ID（这些 voice 尚未实现、未准入 G1），96 个为新增 `implementation.status=unknown` 槽位；对应案例均为 `awaiting_design`，尚无已跑证据。
- 109 条既有合同追加 `scenario`/`family` 与已跑 wire 证据引用；904 个合成 cells 仅对应测试范围，本批未新跑 SDK。
- 44 条海外候选补描述、案例状态 `awaiting_design`；公共国家/地区目录沿用原候选 ID，已有离线验收和有界 SDK 实测记录。

## 2026-11-18 云文件 cohort 更新（scope `2026-11-18-r13`）

- 新增 cohort `CLOUD-FILE-001`：`admission_state=implemented_offline_acceptance_passed_live_pending`，`verified_code_commit=b251b11`，`admission_ref` SP122，`acceptance_evidence_ref` `docs/validation/2026-11-18-cloud-files/checks.json`。capability_refs 为四个完整 ID `candidate:domestic:5341:d0`/`candidate:domestic:1375:d0`/`candidate:domestic:1377:d0`/`candidate:domestic:5342:d0`（其它文档中的 `candidate:domestic:5341/1375/1377/5342:d0` 只是这四个 ID 的列举短写，不是一个真实 capability_id）。这是本批 4 个国内云文件入口的 G1 准入（`cn.cloud_recording_v1.unfreeze_file`/`file_info`/`download_urls`、`cn.vod.files`），**不扩展**到其它待审候选。
- 四条 **OFFLINE** required case `recorded_evidence`+`passed`；四条 **LIVE** required 仍 `awaiting_*` 未执行。scenario case `ACC-CN-VOD-RESOURCE-DISCOVERY-001`（`2026-11-18-r12` 登记）自身 `result=pending_execution`——`pending_execution` 是 case `result` 枚举值。**`local_execution_state=passed` 与 `live_execution_state=not_executed_pending_current_ci_and_local_credentials` 是 cohort `CLOUD-FILE-001` 的字段，不是该 scenario case 的字段**，不要写成 scenario 的属性。
- 全 scope `all_semantics_dispositioned`/`required_acceptance_complete`/`product_complete` 仍 `false`；上述 OFFLINE 通过不是全 cohort 通过或产品完成。

## 2026-11-18 国内 VOD remux 与任务 API cohort 更新（scope `2026-11-18-r15`，历史离线验收快照）

- 新增 cohort `VOD-REMUX-001`，`code_commit=fce298b34911eac89e6c5813b203c5b267e80095`，`offline_evidence_ref` `docs/validation/2026-11-18-vod-remux/checks.json`（`verified_code_commit` 是 OFFLINE case 字段，不是 cohort 字段）。本批为四个国内 VOD 入口：`cn.vod.create_remux_task`（`vod.create_remux_task`）、`cn.vod.task`（`vod.task`）、`cn.vod.tasks`（`vod.tasks`）、`cn.vod.recording_urls`（`vod.recording_urls`），对应 capability `candidate:domestic:3971/3972/4941/3973:d0`，准入记录 `docs/coordination/SP169/admission.json`。**不扩展**到其它待审候选。
- 四条 **OFFLINE** required case `recorded_evidence`+`passed`（`ACC-CANDIDATE-CN-3971-D0-OFFLINE-001`/`ACC-CANDIDATE-CN-3972-D0-OFFLINE-001`/`ACC-CANDIDATE-CN-4941-D0-OFFLINE-001`/`ACC-CANDIDATE-CN-3973-D0-OFFLINE-001`）；对应四条 **LIVE** required 仍未执行/未授权或资源未齐——**本批未发真实云/设备请求**。
- scope 当前为 `2026-11-18-r15-vod-remux-offline-acceptance`：358 capability 槽位、382 cases、212 registry（国内 145 / 海外 67）、107 条已登记 source issues；诊断 141 business / 282 sync+async bodies；source 身份集 4051 / observation 3019 不变。槽位/计数非官方 API 总数或覆盖率。
- 国内只读资源发现窗口（SP166）实际**超时关闭**，无报告，`execution_outcome=not_verified`；实际 HTTP 请求数 `null`（不可声称 0 或账号无资源），不继承为 remux 实测。公开记录 [../validation/live/2026-11-18-vod-resource-discovery/attempt.json](../validation/live/2026-11-18-vod-resource-discovery/attempt.json)；凭据从未进入本任务。
- 全 scope `all_semantics_dispositioned`/`required_acceptance_complete`/`product_complete` 仍 `false`；上述 OFFLINE 通过不等于 cohort 或产品完成。

## 2026-11-18 捕获/消息/HUB 准入更新（scope `2026-11-18-r16`，历史 G1 准入快照）

- 本段为 SP213 公开 admission 后的**历史准入快照**（已由下方 r17 离线验收段接续）；上一节 r15 是国内 VOD remux 的**历史离线验收快照**，其通过证据保留但不扩展到本批。
- scope 推进至 `2026-11-18-r16-capture-messaging-hubs-admission`：**367 slots / 402 cases**；新增准入 **8 operations + 3 integrations**（983/984 复用原 ID）。上述 367/402 是**槽位/案例数**，不是官方 API 总量或覆盖率。
- 准入时点 registry 为 212（国内 145 / 海外 67），诊断 141/282/387；目标 220（153/67）/149/298/403 当时是冻结预期。这些计数在 r17 已变化，见下段。
- 新三 cohort 与全部 LIVE 均 `pending`/`awaiting_*`、未完成；G1 仅授权有界离线 SDK 与仓库示例实施，无真实云/设备调用、无发布结论。`product_complete`/`required_acceptance_complete`/`all_semantics_dispositioned` 仍 `false`。

## 2026-11-19 捕获/消息/HUB 离线验收更新（scope `2026-11-19-r17`，当前段）

- scope 推进至 `2026-11-19-r17-capture-messaging-hubs-offline-acceptance`，实现 commit `dd105464bb68de358b555d94aff81c9689bc83b7`；三 cohort（`CLOUD-CAPTURE-001`/`LEGACY-MESSAGE-001`/`HUB-DISCOVERY-001`）**11 条 required OFFLINE（8 SDK 操作 + 3 仓库示例）全部 `recorded_evidence`+`passed`**；对应 **11 条 required LIVE 全未执行**（`awaiting_*`/资源与授权未齐），本批未发真实云/设备/消息请求。正式证据 [../validation/2026-11-19-capture-messaging-hubs/checks.json](../validation/2026-11-19-capture-messaging-hubs/checks.json)。
- registry 现为 **220**（国内 153 / 海外 67），诊断 149 entries / 298 bodies；本批新增 8 行 binding 均 `offline_status=passed`/`live_status=not_verified`。367 capability 槽位、402 cases、1792 传输检查计数仍为槽位/案例口径，不是官方 API 总数或覆盖率。
- 本机观察（该 code commit 上）：全量 pytest **7220 + 35 subtests**、lint/format/mypy、wire 220 contracts/1792 executions、构建/Twine/干净 wheel+sdist 安装均过；sdist **4827 tests**。`0.1.0a1` 为开发构建、未上传 PyPI、非最终发行。
- PR/main 状态以仓库按 commit `dd105464bb68de358b555d94aff81c9689bc83b7` 的真实记录为准，不预报 CI 已通过。`product_complete`/`required_acceptance_complete`/`all_semantics_dispositioned` 仍 `false`；上述 OFFLINE 通过不等于 cohort 或产品完成。后续国内 async 为复用既有 SDK 的只读验收，非新实现批。

## 阅读注意

- 源数、矩阵行数、方法数均非覆盖率；填 `scenario` 不等于完成全语义处置。
- 本批 101+44 待审候选无新增 G1、无“通过”结论；但公共地区目录等沿用候选 ID 的条目已有验收记录，不能一概视为未验收。
- wire 证据仅证明已执行的离线验收层（合成协议执行），不能替代真实设备、真实消息、媒体与产品级验收。
- 官方消息接入与媒体组件本轮 `requirement=required`；仅四项第二轮工作延期。
- 关联：[原始产品计划](../plans/2026-11-17-sdk-product-completion.md)、[候选来源 scenario-input.json](../coordination/SP088/scenario-input.json)、[诊断 v1 实测记录](../validation/live/2026-11-16-diagnostics-v1/README.md)、[公共地区目录实测](../validation/live/2026-11-17-public-area-directory/sdk.json)。

## 官方 HLS 播放器的本地验收

2026-11-17 的归档清单记录了组件采集时的状态。随后在固定版本 `@ezuikit/player-hls@2.0.0` 上完成了本机 H.264 解码，并另行验证了 HEVC 8-bit TS 软件解码、Canvas 画面及 worker 释放。SDK 地址签发使用模拟传输，真实平台验收另列。见 [H.264 记录](../validation/2026-11-19-official-hls-preview/checks.json)与 [HEVC 记录](../validation/2026-11-19-official-hevc-preview/checks.json)。
