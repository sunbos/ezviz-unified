# SP065 对账规则与 9-source 工作量样例

- 基线：`main@7c4d954c0619152af7df2baa1072d98fce214786`
- 角色：有限对账规则 + 固定输入样例。仅描述"旧技术投影障碍何时被新结构事实替代"，不改写旧包/补充包/input/matrix，不关闭任何共享状态。
- 决策范围：**仅允许在 pin 与锚点完全一致时的结构 layout 替代（L1）**；其余一律保留。
- 历史注记：本文数字与指针来自当时冻结的 9-source 输入及私有 `SP062` model-input/旧 shards（见 §8）；它们是历史已执行工作量，不是当前全项目状态，也不提供公开可复跑输入。

## 1. 身份与匹配规则

1. 先核三处 pin：补充输出独立验收 SHA（SP054 pilot packet，见 §8）、旧 packet SHA、controller manifest（`SP062-controller-manifest.json`）的 source_pins；关联一律用完整 `source_id`，不凭 doc_id/标题跨 region/snapshot 匹配。
2. 同 source 要求 `content_sha256` 与 `structure_sha256` 逐值相等，补充 raw pin 等于 manifest source_pin；内容变更或结构版本不同则不自动对齐 element/row/column，保持 unresolved。
3. 表 key = `(source_id, content_sha256, structure_sha256, element_index)`；pending key 再加 `(reason, row, column)`。缺 row/column 记 `null` 占位，不做行列通配；source 级 prose 义务的 element 也为 `null`，不得与任一表/块合并。
4. 同一 pending key 重复只合并为一个工作项，保留全部 old/new provenance refs；同 key 带不同计数等 metadata 时保留差异待审，不择一覆盖。本批出现 12 组新旧同 key 重复，按此去重。

## 2. 自动规则

- **L1（唯一允许的结构替代）**：旧 reason 恰为 `table_layout_unsupported`，且新输出在同一表 key 有唯一 `field_table` 事实，补充独立验收已确认 target 原 DOM 匹配。附加条件：同锚点不得再出现 `table_layout_unsupported` 或 target/index/pin 错误；预算/截断/读取失败标记不算有效补足；`field_table` 限用原有字段/类型/位置/required 白名单，row 编号来自同一结构。
  - 动作：该旧表级障碍标 `superseded_by_field_projection`，记 `new_fact_ref` 与所有残留行/列 pending；仅表示该次字段投影现在能产出。
  - 不蕴含：整表字段已核定、属 request/response 正式契约、必选性/缺省值、schema/错误码/成功条件完整、source 或 capability 关闭。
- **L2**：旧 `table_layout_unsupported` 仅有 `label_context`、或同锚点无 `field_table`、或新仍为 `table_layout_unsupported` → 保持 open；label/context_only 不能替代字段投影。
- **L3**：原表已成功投影、补充又给同锚点 `field_table` → 只记新增 provenance，不算新能力或问题关闭；现有 row 级 pending 按精确 key 并集保留。
- **L4**：新表出现 `unreviewed_field_name/type/required/location`、`table_cell_merged_or_missing` 或任何行列 pending → 保持 open，以新事实 ref + 数字 row/column 指回；不得用旧整表原因掩盖细粒度义务；`null` 不等于必填/可选/缺项，实际影响留协议审查。

## 3. 本规则集永不自动关闭

- `json_keys_withheld`：同名字段、可识别返回标签、已有 contract 都不能证明每个被隐藏 JSON key 及层级/适用情形；计数保留在原层级，不相加成唯一字段总数。
- `non_json_example` / `non_strict_json_example`：label_context 未重新解析或核定代码内容，不因位置已知而关闭。
- `code_content_not_projected`：安全出口未提供代码 body/schema/值；label 仅补位置。
- `prose_not_projected`：每 source 保留；零 HTTP、已有 contract、全部目标有 context 或旧 pending 为空都不能关闭全文/场景审查。
- row 级 privacy/schema pending：本批仅授权 L1 结构替代；字段名/类型/required/location 的语义闭环需另批证据，不能凭同表位置、同名或 source 目录关闭。
- route/origin/permission/selector 与语义请求：表可读或 label 可定位与这些协议判断无自动蕴含；未给权限可为限制，不自动成为所有 wrapper 的阻塞。
- context_only 证据：不能升级为 role/schema 批准；不能由返回标签推成功码、完整响应、版本关系、跨接口 flow 或设备效果。

## 4. 固定输入的工作量数字（仅守恒检查）

| 项 | 值 |
| --- | --- |
| sources | 9 |
| 补充 field_table / label_context | 16 / 62 |
| 旧 pending / 补充 pending | 55 / 134 |
| 旧 layout 记录 / 被 L1 替代 / 仍 open | 17 / 8 / 9 |
| 替代表上的新 row 级 pending | 76（`unreviewed_field_name` 73 + `unreviewed_type` 3） |
| 新旧同 key 重复 | 12 |
| 结构替代前并集 / 替代后保留 | 177 / **169 工作项** |

保留 reason 计数：`unreviewed_field_name` 76、`table_layout_unsupported` 9、`non_json_example` 28、`code_content_not_projected` 37、`prose_not_projected` 9、`json_keys_withheld` 7、`unreviewed_type` 3。

**解释边界**：这只是当时冻结 9 个 source 的投影审查工作量（历史已执行统计），不是 API 数、能力数、覆盖率、schema 完整率、协议已冻结或当前全项目状态；不同 reason 可指同一代码块的不同义务，不能当独立业务问题数。被替代的 8 张表仍有 76 条 row 级 pending，另有 3 条原 request 侧 name pending 照旧保留；`label_context`、JSON shape、contract 引用均不能自动关闭全文、隐藏 key、代码或未审语义义务。第三方许可、集成、真实设备/海外/消息资源与 PyPI 门槛继续由完整 Goal 验收矩阵管理。169 等历史数不随后续人工语义裁定（如对个别 source 的人工全文审查）回改；人工裁定与本自动规则分层。

## 5. 9-source 样例（安全锚点）

| source_ref | source_id | old_source_ref | 旧layout element | 新field_table element | L1替代 | 仍open layout | 替代表row pending |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | portal-2026-11-17:domestic:19 | shard-007.json#/source_facts/0 | — | — | — | — | 0 |
| S02 | portal-2026-11-17:domestic:663 | shard-007.json#/source_facts/24 | 54, 67, 101 | 21, 54 | 54 | 67, 101 | 1 |
| S03 | portal-2026-11-17:domestic:671 | shard-007.json#/source_facts/31 | 62, 134 | 14, 62 | 62 | 134 | 11 |
| S04 | portal-2026-11-17:domestic:678 | shard-000.json#/source_facts/2 | 56, 224 | 17, 56 | 56 | 224 | 38 |
| S05 | portal-2026-11-17:domestic:708 | shard-001.json#/source_facts/0 | 50, 82 | 14, 50 | 50 | 82 | 0 |
| S06 | portal-2026-11-17:domestic:730 | shard-001.json#/source_facts/18 | 51, 87 | 14, 51 | 51 | 87 | 3 |
| S07 | portal-2026-11-17:domestic:822 | shard-008.json#/source_facts/13 | 50, 78 | 14, 50 | 50 | 78 | 2 |
| S08 | portal-2026-11-17:domestic:1422 | shard-005.json#/source_facts/14 | 90, 206 | 14, 90 | 90 | 206 | 18 |
| S09 | portal-2026-11-17:domestic:1512 | shard-002.json#/source_facts/5 | 68, 96 | 14, 68 | 68 | 96 | 3 |

替代条目示例（仅 ref 与数字锚点）：S02 element 54 → `new_fact_ref=F0022`（`/facts/9`），row pending `/pending/1` row=1,col=0 `unreviewed_field_name`；S04 element 56 → `F0058`，row 4–40 共 38 条（含 row 9 col 1 一条 `unreviewed_type`）。`Fxxxx`、`/facts/N`、`/pending/N` 与 shard 指针均可在本机私有 `SP062-model-input`、SP054 pilot 输出与旧 shards/data 中解析；这些输入未公开，需另行取得，本文不提供可点击的私有/失效链接冒充公开入口。每 source 的 `content_sha256`/`structure_sha256` 以输入 pin 清单为准，引用时整值照抄、不得缩写后比对。9 个 source 的 `prose_not_projected` 全部保留。

## 6. 引用与记录方式

- 记来源：完整 `source_id` + `content_sha256` + `structure_sha256` + `element_index`；旧侧 provenance 用 `shard-XXX.json#/pending_inputs/N`，新侧用 `Fxxxx`/`/facts/N` 与 `/pending/N`。
- HTML/structure pin + element/row/column 必须同值匹配后才谈替代；任一项不等、无法唯一匹配或 pin 变化 → 留 open。
- 每条 superseded 决策需同时具备：唯一旧 `pending_ref`、新 `field_table` `fact_ref`、相同 source/content/structure/element；不存在只凭 label 的关闭。

## 7. 边界与未覆盖

- 输入侧事实：`SP062-model-input` 的 9 份 `open_projection_limits` 仍逐值保留旧+补充 pending；本文是派生视图设计，未改 input/packet/matrix。
- 扩至 326 source 时仅对实际有同 pin 有效补充的 source 应用 L1，其余 source/observation 与 pending 原样保留；对应 budget preflight 已按冻结输入执行并验收（SP067，输出 `d5cb5e…`，见 §8），但仅表示 326-source 预算预检完成，不代表业务语义或 SDK 验收；SP068 43 片导出亦已按冻结输入验收（SP071，manifest `50cc7338…`）。
- 其他类别（语义、route/permission、代码内容、隐藏 key、prose）需各自明确后续证据，不在本规则集内；`source_class` 各分类须对应各自事实，不能用 source/方法数量充当全覆盖。
- 本轮未执行：未读原始归档正文、未用网络、未跑 SDK/测试、未写通用 RSI/遥测/Agent 框架；本文件仅是协调对账文档交付，不代表项目唯一或当前交付物。

## 8. 输入/验收索引与复跑边界（发布注记）

- 定位：`preflight.py` 及同批 5 个脚本是**固定输入的一次性协调审计源码**，随文档归档发布；真实档案、私有 plan、原始 source/旧 shards/SP062 model-input 仍在本地 ignored `.claude`/`data`，不随 SDK 发布。fresh clone **不承诺可直接复跑**；不要复制带绝对路径的私有 plan 或 raw 样例进仓库。
- 本文工作量数字的输入角色（冻结时）：SP054 pilot 补充输出（9 sources、78 facts、134 pending 的安全投影，packet `e2128775…`，SP059 验收）；SP037 原 18 个旧 shard（较早执行的不可覆盖证据）；`SP062-controller-manifest.json`（本机私有 controller manifest，含 source_pins 引用）与 `SP062-model-input`（本机私有 model 输入，含 fact_index/`open_projection_limits`/output_contract 引用）。当前 `review-output-contract-v2.json`（SHA-256 `f73e09a305df4de4a985f3ee792cdb844460cc8149917a4fd567a48ee3946b7c`）与 `SP062-model-input.output_contract` 逐值语义相等，其 `status=frozen_design_not_implemented` 为当时说明。
- 代码 pins（当前发布 SHA-256）：`SP037/project_semantic_review.py` `0d3795d2382978e870ab5624c7448429449f81d0c012f55f66700867046b484d`；`SP054/supplement_evidence.py` `e44ca8028e9acd4e87484ed8f705fd2159603b2f7f9da4ab94d243d1a140b202`；`SP065/preflight.py` `f0eaecdffab89d3fa1b5210d45adb1a9cc654df3bc151f1d3cf4f1aeaef07f48`；`SP066/archive_roots.py` `2a3feaa2b966392a96344909473ab7006b591acd47eab35189cfdbce05dcb597`；`SP068/export_batches.py` `1de7628e4be6fd5c0d338ee6a33fe88784ea9e7ec694669c28aba121999d12e9`。旧输出 SHA 保留为历史证据，不冒充重跑。
- 已验收产物 SHA（历史证据，各自冻结输入下验收）：SP065 preflight `d5cb5e…`（SP067，326-source 预算预检）；SP066 manifest `7aec4f…`（SP067，11 root files/54090 bytes blob 身份归档）；SP068 43 片 manifest `50cc7338…`（SP071，326source/392obs、3736facts/4831pending，9 pilot 多重集等价）。均不外推为语义完成、完整协议、全 repo/许可证/集成通过或 Goal 完成。
- frozen plan SHA-256（plan 本体在本地，多数含本机绝对路径，不随库发布）：SP037 `5d6ab799d905e90d188f82645c309a9e761d7a8b3ba6ae1bbdabab2af36f3f99`；SP054 `19b602d88981ff589b7315e1955d04ee70401f58089b072e753b40dfbf9bc255`；SP065 `a2ddc4dfceca53f9aad425f1bb75a3e0ac367f9200f919cb5651cb5a1612b554`；SP066 `028ff7abed0cf340d412024e57e74cf7025aaacba578012f40c9720e3382ff15`；SP068 `e5174ee8c0e7c94135d0a9a459e7a6be26674cd66547b1e876f87ab7306a2427`。若将来要求通用可复跑，需独立审查公开输入/路径绑定版本。
