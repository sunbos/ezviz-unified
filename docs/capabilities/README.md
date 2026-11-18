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

## 阅读注意

- 源数、矩阵行数、方法数均非覆盖率；填 `scenario` 不等于完成全语义处置。
- 本批 101+44 待审候选无新增 G1、无“通过”结论；但公共地区目录等沿用候选 ID 的条目已有验收记录，不能一概视为未验收。
- wire 证据仅证明已执行的离线验收层（合成协议执行），不能替代真实设备、真实消息、媒体与产品级验收。
- 官方消息接入与媒体组件本轮 `requirement=required`；仅四项第二轮工作延期。
- 关联：[原始产品计划](../plans/2026-11-17-sdk-product-completion.md)、[候选来源 scenario-input.json](../coordination/SP088/scenario-input.json)、[诊断 v1 实测记录](../validation/live/2026-11-16-diagnostics-v1/README.md)、[公共地区目录实测](../validation/live/2026-11-17-public-area-directory/sdk.json)。
