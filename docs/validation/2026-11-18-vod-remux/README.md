# VOD 转封装 / 任务 / 录像地址 — 离线验收（VOD-REMUX-001）

> 范围：仅 `client.vod` / `AsyncClient.vod` 四个国内入口（`create_remux_task` doc 3971、`task` doc 3972、`tasks` doc 4941、`recording_urls` doc 3973）的 **SDK 离线一致性验收**。无真实云/设备调用。权威结构化记录为同目录 [`checks.json`](./checks.json)；本文是导航说明，不是覆盖率、不是产品完成、不是发布证明。

## 代码与模型 pin

| 项 | 值 |
|---|---|
| `code_commit` | `fce298b34911eac89e6c5813b203c5b267e80095`（本离线验收快照记录时为本地 commit，未 push、无 PR、无本 commit 的 CI；后续状态以对应 PR/CI 真实记录为准） |
| 执行模型 | requested `devin/swe-2`、effort `max`；observed `devin/swe-2` |
| scope revision | `2026-11-18-r15-vod-remux-offline-acceptance`（G1 准入冻结时为 r14，累计 358 slots / 382 cases） |
| registry | 212 contracts（domestic 145 / overseas 67）；诊断 `business_entrypoints` 141、方法体 282；`operation_names` 387 |
| 来源 | 4051 sources / 3019 observations 未变；107 条已登记 source issues（102 `unresolved_source_semantics`、2 `documented_prerequisite`、1 `observed_account_specific_routing_difference`、1 `local_choice`、1 `supplemented_by_public_probe`） |

计数均为已登记项，不是官方能力总量或覆盖率。

## 验证命令与结果

全部为离线（合成凭据 / wire 录制 / 本地构建），结果见 `checks.json` 中各 `local_execution_artifact_id`：

| 命令 | 结果 | 计数 |
|---|---|---|
| `pytest -q --tb=short --maxfail=5`（全量） | passed | 6023 tests + 35 subtests，46.29s（SP188-FullPytest） |
| `pytest tests/test_sdk_vod_remux.py` | passed | 246 wire tests（SP177-RemuxWireUnit） |
| `pytest tests/test_sdk_vod_remux_behaviour.py` | passed | 304 tests（SP187-Behaviour） |
| `pytest` 集成批（harness vod remux / diagnostic / sdk vod / transport） | passed | 507 tests（SP183-IntegrationTests） |
| `ruff check` | passed | SP187-Lint |
| `ruff format` | 先于全量回归完成 | SP188-Format |
| `mypy src/ezviz_unified` | passed | 45 source files（SP179-Mypy） |
| `harness.audit --summary-only` | passed | 212 contracts（SP179-Audit） |
| `harness.contract_eval --require-all` | passed | 212 contracts / 1728 executions / 0 failed（SP179-WireEval） |
| `python -m build` + `check_artifacts` + `twine check dist/*` + `verify_installations --installer pip` | 全部 passed | SP191-Build / Artifacts / Twine / CleanInstall |
| sdist 源内测试（clean install 后） | passed | 3925 tests，11.75s |
| 构建产物 | `ezviz_unified-0.1.0a1`（wheel 51 成员 / sdist 123 成员），`metadata_and_contents_only` 校验 | 开发版本 0.1.0a1，**非最终冻结、未上传 PyPI** |

注意：测试与契约计数不是覆盖率百分比；`actual_tool_result_is_error` 均为 false。

## 复测说明

SP191 打包链路在本轮重跑，因为此前构建中 README 权限说明文本发生变化，影响 sdist/wheel 内容；`checks.json` 记录的 artifact sha256 为最新构建产物。

## 隐私与边界

- 真实凭据从未进入本任务；全部离线用合成凭据。
- `taskId` 与 `resultSpaceId` 已并入既有脱敏键集合；签发的地址 URL 仅在 `data`/`raw`，不进入自动日志或诊断导出；任意服务端错误文本中的未知 URL/未发送标识不保证全部脱敏。
- 保留失败（`retained_failures`）：`task_type` 曾被 float 数值相等接受（已加整数校验）；一批测试期望超出既有脱敏/header 契约且录制夹具漏 recorder（已按冻结契约修正）。历史失败保留于本地回执 SP175 / SP181 / SP184。
- 无任务完成、`fileNodeId`/`taskId` 映射、媒体可用性、真实物理效果或 live 权限被确立；`recording_urls` 的 `read_only=False` 是 SDK 不自动重放的策略，非官方写分类；零时长 `recording_urls` 是本地透明传递，服务端接受性未知。

## 真实验收与发布状态（未完成）

- 4 条 required OFFLINE case（3971 / 3972 / 4941 / 3973 各一条）已 `recorded_evidence` + `passed`。
- 4 条 required LIVE case **未执行**：创建写（3971）与地址签发（3973）等待授权，查询类（3972 / 4941）等待现成且获授权的任务/资源确认；不继承 SP166 或任何其它域的历史/离线成功。
- SP166 国内只读资源发现窗口实际超时关闭、无报告：`execution_outcome=not_verified`，`actual_http_requests=null`（HTTP 数未知，不能写 0，不能推断账号无资源）；公开记录见 `docs/validation/live/2026-11-18-vod-resource-discovery/attempt.json`。
- `product_complete` / `required_acceptance_complete` / `all_semantics_dispositioned` 均为 **false**；境外设备 live、平台消息 live、产品发布、PyPI 上传均未完成。本批 4 入口的离线通过**不**等于 cohort 或产品整体完成。

## 相关链接

- 结构化证据：[`checks.json`](./checks.json)（含 source/tested-file sha256、build artifact sha256、registry metadata sha256）
- 实施计划：[`docs/plans/2026-11-18-vod-remux.md`](../../plans/2026-11-18-vod-remux.md)
- 使用说明：[`docs/research/2026-11-18-vod-remux.md`](../../research/2026-11-18-vod-remux.md)
- G1 准入：`docs/coordination/SP169/admission.json`（基线 main `254ea73`，PR #16 已合并；该合并 CI 事实为历史记录，不代表本 commit 的 CI——`fce298b` 是 SDK 代码 pin，本 commit 本身不要求单独 CI；未来 CI 验证的是当次 PR 或 main HEAD 且其中 SDK 代码哈希与此 `code_commit` 一致）
- 验收登记：`docs/validation/sdk-product-acceptance.json`（`ACC-CANDIDATE-CN-{3971,3972,4941,3973}-D0-{OFFLINE,LIVE}-001`）
- 验收登记联动：本批 OFFLINE 通过引用的执行计数是历史批次在同一 SDK 代码/测试字节（`fce298b`）上的通过记录——SP188 全量 pytest 6023 + 35 subtests、SP183 集成批 507。离线状态升级后原 harness 测试曾硬要求 `not_verified` 状态，现另单修正为带证据状态守卫（post-acceptance 定向执行结果与新测试 sha256 见 `checks.json` 的 `post_acceptance_validation`）；修正后的新测试不属于 `fce298b` 代码，不能冒充其计数。后续定向检查 SP206 已通过：300 项测试及 lint；新测试 SHA 与实际回执见 `post_acceptance_validation`，不改写此前全量执行的版本或计数。SDK 源码与入包 SDK tests 字节未变、未重建。
