# SP054 证据补足与引用式审查试点（冻结设计）

- 基线（原冻结时输入，历史）：`main` `aba158bd5ff836284e248a75d62b5b0a302a38af`
- 投影器（原冻结时输入，历史）：`docs/coordination/SP037/project_semantic_review.py`
  SHA-256 `2abc577399906d9c8d85c651cdfb6f87b334ae4369131303354fc5b9506e3926`
- 状态：设计冻结；不实现 controller / importer / SDK；不调整范围
- 发布注记（文档对齐，非重跑）：本文是设计冻结时的历史文档。当前发布投影器同路径
  SHA-256 为 `0d3795d2382978e870ab5624c7448429449f81d0c012f55f66700867046b484d`；
  本补充工具 `supplement_evidence.py` 当前 SHA-256 为
  `e44ca8028e9acd4e87484ed8f705fd2159603b2f7f9da4ab94d243d1a140b202`。
  原冻结时的旧 SHA 保留为冻结时输入的历史证据，不代表发布后又重跑。9-source pilot
  与 43-batch 扩展已各自按冻结输入验收（见 §9），不据此声称语义审查、SDK 能力或
  协议冻结已完成。
- 范围义务：完整 Goal 范围、326-source 首批、4051-source 总库均不变；SP037 原 18 个 shard 保留且不被覆盖

## 1. 背景与结论

SP053 已证明两类既有缺陷：

1. 派生结构（tables/code_blocks）漏掉普通 `p`/`span` 中的已知小节标签，模型无法区分“返回数据”与“请求参数”等上下文；
2. 让模型自由重写 SHA、数字 anchor（element_index / doc_id）或官方事实时产生转写错误。

方案比较结论（已冻结）：

- 继续补强提示词 → 仍会重述缺证事实，不解决转写错误；
- 重建通用文档/Agent 框架 → 超出当前需要；
- **采用**：在现有解析器上做固定标签的证据补充（`supplement_evidence.py`），并把审查输出改为只引用事实的 `fact_ref` 协议 v2。

本变更属已授权的架构与验收改进：不新增 SDK 业务能力、不改变 SDK 运行时。

## 2. 分工与文件归属（单写入者）

- **A（实现方）** 唯一写：
  - `docs/coordination/SP037/project_semantic_review.py`（仅 `HEADERS["name"]` 增加别名 `字段名`，及对应合成自检）；
  - `docs/coordination/SP054/supplement_evidence.py`（薄 CLI，建议 ≤250 行）。
- **B（本文件）** 唯一写：
  - `docs/coordination/SP054/design.md`；
  - `docs/coordination/SP054/review-output-contract-v2.json`（从本设计整理，无实现）。
- Codex 持有私有 plan、命令清单与验收；同一文件单写入者；真实 pilot 只能由后续明确的单命令派单运行。

## 3. A 方 CLI 与安全输入（摘要）

`supplement_evidence.py`：

- `--self-check`：运行合成隐私/边界案例（见 §6），不触碰真实输入；
- `--repo WORKTREE --plan PLAN --out <固定相对路径>`：只生成显式补充文件；`--out` 仅允许固定相对输出**文件**（显式单文件路径，非目录），已存在则拒绝；
- 禁止：运行 SP037 全量投影或 SDK 测试、联网、读 env/原始配置、遍历归档中未钉住的文件、执行任何外部内容。

输入安全：

- 精确 9 个 source 及各自 raw / structure / content pin 由 pilot plan 冻结；
- 运行前校验：plan SHA、documents 索引、每个 source 的 raw 与 structure SHA；
- 归档路径必须是归档根内相对路径，拒绝绝对路径、`..`、空段与 symlink 逃逸；
- 只读取对应归档文件，不做目录遍历；
- 复用 `BeautifulSoup`/`html.parser`、`tools.documents.http_candidates._element_label_key` / `_ALL_LABELS` 及原 `element_index` 约定；
- 复用 SP037 `_table` 与原 `reviewed_vocabulary`，不复制、不放宽字段与值白名单；
- `HEADERS["name"]` 新增固定别名 `字段名`；未知 field/type/required/location 仍为 `null` 并产生 anchored pending；
- 不导出：字段说明文本、code 文本、HTML/prose、任意 URL/路径值、样例值、异常链。

## 4. 补充输出文件（supplement packet）

固定顶层键，除此之外不得有其他键：

```
schema_version, input_pins, sources, facts, pending, receipt
```

- `schema_version`：常量 `1`（补充包自身版本，区别于审查输出 v2）。
- `input_pins`：实际实现只含三项：`plan_sha256`、`archive_documents_sha256`、`reviewed_vocabulary_sha256`；代码 pins 实际位于 `receipt.code_sha256`，raw/content/structure pins 位于 `sources` 各项。用途是完整性核对，不是匿名化。
- `sources`：9 个 source 的完整身份与 pins（`source_id, region, doc_id, raw_file, raw_file_sha256, content_sha256, structure_file, structure_sha256`），只允许机器复制，不允许模型改写。
- `facts`：只允许两种类型，见 §4.1 / §4.2；每条 fact 实际含 `kind` 鉴别字段。
- `pending`：anchored pending，见 §4.3。
- `receipt`：计数与标志，见 §4.4。

### 4.1 `label_context` fact

字段（且仅有）：

```
fact_id, kind: "label_context", source_id, element_index, element_kind,
preceding_label_key, label_element_index, context_only: true
```

- `label_context` 目标仅为该 source pinned `structure.tables` 各项与 pinned `structure.code_blocks` 各项；`element_kind` 固定枚举 `table|code_block`，无其他目标类型。
- 生成前核对每个 pinned `element_index` 处的原始 DOM tag 与其结构种类一致（`table`→`table`；`code_block`→`pre`/`code`）；tag 不匹配或 pinned 索引不解析为存活 DOM 元素，均按 `pinned target invalid` **fail closed 整体拒绝**，不是只记 pending 继续。pinned 结构之外的额外目标类型或 fact 在真实 pilot/模型派发前即拒绝。
- pinned 目标若落在 opaque 祖先（`table`/`pre`/`code`/`script`/`style`）之内：仍按 pinned structure 产出该 fact，但其 `preceding_label_key`/`label_element_index` 为 `null`，并另记 `target_inside_opaque` anchored pending；该目标内部的 label 不作为外部上下文。
- `element_index` 与 `label_element_index` 沿用原 parser 编号（`script`/`style`/`br` 清理之前分配的序号），不得重排或新建编号。
- 普通 label carrier 节点只提供 `label_element_index` 锚点，绝不成为额外 fact。
- `preceding_label_key` 只能取自固定 `_ALL_LABELS`（即 `_ADDRESS_LABELS | _WEAK_ADDRESS_LABELS | _METHOD_LABELS | _META_LABELS` 的并集，比较时忽略空白、尾冒号与大小写）；表示在**同一标题边界之后**最近的已知标签。
- 无命中保持 `null`，不猜测角色。
- 任何未识别的 `h1..h6` 都重置上下文；其他已识别标签也更新上下文，不跳过中间的 Header/Body 等 label。
- `table`/`pre`/`code`/`script`/`style` 内部的标签不计作外部上下文；这些节点自身也不得作为 label carrier。
- 上下文标签只证明文档分段位置，**不能**证明：完整响应 schema、成功码、版本分支、真实效果或跨接口 flow。

### 4.2 `field_table` fact

字段（且仅有）：

```
fact_id, kind: "field_table", source_id, projection
```

- `projection` 直接复用 SP037 `_table` 的白名单结果（含 `role: "unknown"`、行级 `name/type/required/location`），不扩展列、不放宽值。
- 行内 `null` 需区分两种情形：单元格存在但其值不在已审词表/规则内时，该字段保留 `null` 且产生对应 row/column anchored pending（如 `unreviewed_field_name`/`unreviewed_type`/`unreviewed_required`/`unreviewed_location`）；该列未在表头识别或单元格为空/缺失时，字段同样为 `null`，但不保证都已有对应 pending。不能把所有 `null` 都解释为已有 pending 覆盖。

### 4.3 `fact_id` 与 pending

- `fact_id` 由 `source_id + kind + 局部锚`（`e<index>`/`t<index>`）确定性生成；同一 fact 只出现一次。
- 凡未知字段/布局保留数字锚点 pending（`element_index` 等）；表/块级 pending 可定位时保留数字锚；无法定位时保留 `source_id` + 固定 reason + `null`，不猜造索引，与下一行可空 `element_index` 一致。
- source 级 `prose_not_projected` pending 以 `source_id` 锚定、无 `element_index`（该 source 一条）；code 内容不复制，按 pinned `code_blocks` 每块一条 `code_content_not_projected` pending（同样以 `source_id` + 可空 `element_index` 锚定）。

### 4.4 receipt 与预算

- `receipt` 记录：`source_count`、`fact_count`、`pending_count`、`total_nodes`（全局累计节点数，非每 source 分列）、`code_sha256`（三个代码文件 pins 实际位置）、`full_source_review=false`、`protocol_freeze=false`、`raw_examples_exported=false`。
- 预算含 pending：累计 `total_nodes >4096` 或 `>9` source 直接 fail closed；不截断后宣称 complete。
- 输出最大 `512 KiB`，显式单文件；stdout 只输出计数与固定状态；错误用固定 reason code，不回显原文/路径/异常链。

## 5. 引用式审查输出 v2（本任务冻结的协议）

核心原则：**模型只引用事实，不重述事实。**

- 控制器注入：输入版本、完整 source/obs 身份与 hash、bindings、历史验证证据；模型**不**复制它们。
- 控制器另注入 `fact_index`：把旧 packet 已有的 source/obs/field/shape/route 条目与本次补充 fact 统一分配唯一 `fact_ref`。
- 模型仅返回四个决策数组，见 `review-output-contract-v2.json`：
  - `source_decisions`：`source_ref`、`classification_candidates`、`classification_fact_refs`、`open_request_ids`；`classification_candidates` 取值限定为 `server_api`/`message`/`client_component`/`tutorial`/`duplicate`/`deprecated`/`missing`/`uncertain` 八种分类（分别对应服务端 API、消息协议、客户端组件、教程/示例、重复项、废弃项、资料缺失、语义未知）；无事实支撑的类别**必须**保持 `uncertain`，且仅附 `open_request_ids` 不构成任何肯定分类的支撑——需要澄清时保留有界问题，但 open request 不是证据；`duplicate`/`deprecated`/`missing` 另须各自具备相应可核查事实（重复对象、废弃依据、缺失材料）方可断言。
  - `observation_decisions`：`observation_ref`、`assessment`、`scenario_refs`、`open_request_ids`；模型不得新写 HTTP method/path/参数/JSON shape/字段计数/验证状态。
  - `scenario_candidates`：`local_id`、`source_refs`、`observation_refs`、`scenario_proposal`、`relationship{kind,target_binding_ref}`、`official_fact_refs`、`design_choices`、`open_request_ids`、`closure_requirements`。
    - `official_fact_refs` 只能精确选取已提供的 `fact_ref`，不得含自由文本“事实”；其他文字仅是设计提案或待证问题，不伪装官方事实。
    - `relationship.kind=existing` 时 `target_binding_ref` 必须匹配已提供的 `binding_refs`，不得自建“approved operation ID”。
  - `evidence_requests`：`id`、`source_refs`、`fact_refs`、`requested_safe_fact`、`impact`、`basis`；必须有界，不索取原文或具体取值。
- 未知 path 或未核定 required 的协议保持 `protocol_freeze_blocker`；未授权**不**自动等于协议阻塞。
- 不新增：验证层、summary、计数、whole-source 完成字段。
- 控制器注入的是 `source_ref`/`observation_ref`/`fact_ref`/`binding_ref`；`scenario_candidate.local_id` 与 `evidence_request.id` 是**模型本地 ID**，由本次输出定义，只需在本次输出内唯一且被本输出的引用解析，不属注入身份、不与任何输入 ID 集合做守恒校验。
- 控制器负责校验：所有注入类 ref 存在且类型匹配；`source_decisions` 对每个注入 `source_ref` 恰出现一次（不缺、不重、不增），`observation_decisions` 对每个注入 `observation_ref` 恰出现一次；`local_id` 与 `evidence_request.id` 在输出内唯一；全部 `scenario_refs`/`source_refs`/`observation_refs`/`open_request_ids` 解析到已注入身份或本次输出定义的本地 ID；观察引用归属其申报 source、证据引用支撑相关 source 或显式提供的跨 source 关系、`kind=existing` 的 `target_binding_ref` 匹配已提供的绑定上下文而非仅某个存在的 ID；输入 pins 一致、派生计数一致；**原始模型回答不是事实库**。
- v2 未实现通用 controller/importer：协议先冻结，9-source 试点已完成并有界验收（SP059），43-batch 扩展导出亦已按各自冻结输入验收（SP071，见 §9）；不能只因格式/计数通过就算语义审查完成。

## 6. A 方合成验收（--self-check 必须覆盖）

1. 已知 request/return 标签只输出固定标签与数字锚点；
2. 未知新 heading 重置上下文；
3. `code`/`table`/`script` 内伪造的返回标签不改变外部上下文；
4. 同一 section 两个 code 块只记录 context，不臆造 success/error/version；
5. `字段名` 表头进入现有白名单投影，非白名单参数/类型仍省略并 pending；
6. 嵌入的凭据/serial/mediaURL 哨兵不出现在输出 JSON 或 stdout/stderr；
7. 变更 raw/structure pin、路径逃逸、重复 source、预算超限均拒绝且不写产物；
8. 禁止为了通过而删除失败案例、吞异常或扩大值白名单。

## 7. 真实 pilot 接受条件

- 独立检查输出：仅含固定键/值，所有 source pins 与 fact 引用准确；
- 9-source 守恒；新增 `field_table` 记录能指回原表；
- 已定位的对应关系（仅核对 context，不自动升格为完整协议）：
  - 678：`返回数据` e48 → e49
  - 1422：`返回数据` e81 → e82/e86
  - 730：`返回数据` e46 → e47
  - 671：`返回数据` e57 → e58
- 旧包 SHA 未变；不要求重复已通过 SDK/main CI；
- 未覆盖项继续保留：源正文原文、未知 key 语义、literal route 审核、设备/消息/发布门槛。

## 8. 未覆盖项（本设计不解决）

- 通用 controller / fact_index 生成器 / importer 实现；
- source 正文、prose 语义、code 内容的进一步投影；
- literal route 审核、真实设备/消息/发布门槛；
- 超出已验收固定输入的进一步运行或通用化（历史：9-source 之外的扩展曾待 pilot 验收后另行派单；SP068/SP071 已按各自冻结输入有界完成 43-batch 导出，见 §9）。

## 9. 输入/验收索引与复跑边界（发布注记）

- 定位：`supplement_evidence.py` 及同批 5 个脚本是**固定输入的一次性协调审计源码**，随文档归档发布；真实档案、私有 plan、原始 source 数据仍在本地 ignored `.claude`/`data`，不随 SDK 发布。fresh clone **不承诺可直接复跑**。
- 五入口均拒绝非固定 plan SHA； frozen plan SHA-256 如下（plan 本体仍在本地，多数含本机绝对路径，故不随库发布；**不要**复制带绝对路径的私有 plan 或 raw 样例进仓库）：
  - `SP037-full-semantic-review-plan.json` → `5d6ab799d905e90d188f82645c309a9e761d7a8b3ba6ae1bbdabab2af36f3f99`（guard `project_semantic_review.py:20`）
  - `SP054-pilot-plan.json` → `19b602d88981ff589b7315e1955d04ee70401f58089b072e753b40dfbf9bc255`（guard `supplement_evidence.py:28`）
  - `SP065-full-preflight-plan.json` → `a2ddc4dfceca53f9aad425f1bb75a3e0ac367f9200f919cb5651cb5a1612b554`（guard `preflight.py:34`）
  - `SP066-archive-plan.json` → `028ff7abed0cf340d412024e57e74cf7025aaacba578012f40c9720e3382ff15`（guard `archive_roots.py:27`；无本机路径，仅 11 项公开 Git blob 元数据，但发布前仍需独立白名单复核）
  - `SP068-batch-plan.json` → `e5174ee8c0e7c94135d0a9a459e7a6be26674cd66547b1e876f87ab7306a2427`（guard `export_batches.py:33`）
- 依赖角色（缺这些不等于代码失败）：SP037 需 routing/source-input-pins/词表/ledger/registry/voice/验收标准及归档 documents/structure；SP054 需 pilot plan/词表/固定归档 raw+structure；SP065 需同 326 sources、18 旧包与 preflight 输入；SP066 发最多 11 次 GitHub GET（非离线）；SP068 需 master manifest、SP065 plan/result、18 旧包、pilot 与归档。
- 已验收产物 SHA（历史证据，各自冻结输入下验收，非发布后重跑）：SP054 pilot packet `e2128775…`（SP059）；SP065 preflight `d5cb5e…`（SP067）；SP066 manifest `7aec4f…`（SP067，11 root files/54090 bytes）；SP068 43 片 manifest `50cc7338…`（SP071，326source/392obs、3736facts/4831pending，9 pilot 多重集等价）。这些验收是**有界结果**：9-source/78facts/134pending 的安全投影、326-source 预算预检、11 blob 身份归档、43 片等价导出；不外推为完整协议、语义完成、全 repo/许可证/集成通过或 Goal 完成。
- 若将来要求通用可复跑，需独立审查公开输入/路径绑定版本；不在本次重写工具或级联改 pins。
