# 官方能力矩阵生成器（harness.product_matrix）

仓库工具，非 SDK 功能。把"全量来源闭包登记 → 语义审查形成能力"路线中的**来源登记与归属汇总**落成离线报告：portal 门户目录、静态 SDK 参考 manifest、附件 manifest.jsonl 三类快照，与已审核的 `contracts/reviewed-transport-contracts.json` / `sdk-bindings.json` 注册表做精确关联。

证据层级固定为 `source_and_registry_matrix_only`：仅记录来源快照与注册表结构，不抓取 URL、不调用设备、不导入或执行 SDK 模块、不做实测，也不宣称产品完成或 API 总数。

## 公开投影边界（schema 2，`publication_profile=reviewed_metadata_only`）

**所有输出（返回值、stdout、`--output` 文件、`--summary-only`）都是 schema 2 的逐类型显式白名单投影。** 输入 manifest/decisions 仍是 schema 1（`input_manifest_schema_version=1`），报告 `schema_version=2`；不存在 raw 公开输出模式。

- 内部证据记录仍保留原始 URL/path/host/section_title/asset_id/discovered_from/reference locator/文件名，用于六字段精确 join、来源 pin 检查与索引；这些字段**永远不进入报告**，也不用 hash 冒充匿名化。
- 公开字典**逐字段显式构造**（不复制内部 dict 再 pop）：内部新增字段永远不会自动出现在公开结果里。
- 唯一公开定位符是**构造出来的**：portal 来源的 `canonical_document_url` 由已核定 region（domestic/overseas）+ ASCII 数字 doc_id 生成固定门户链接；reviewed_contract 能力额外保留已审核 registry 的常量 `path` 及同样构造的 `canonical_document_url`。除此之外不发布任何原始 locator。非法门户 identity（非核定 region 或非 ASCII 数字 doc_id）是**硬失败**，错误不回显原值。
- 三类 source 的 `source_id` 分别是 `snapshot:region:doc_id`、`snapshot:sdk_reference:<index>`、`snapshot:asset:<index>`；index 为输入记录零起始序号（SDK 用 manifest.entries 数组下标，asset 用非空 JSONL record 序号），在投影/去重前确定。派生 observation/capability/unresolved ID 全部从这些公开 ID 构造；schema 1 的 URL/asset_id token ID 无任何迁移别名。
- `evidence_refs` 只由已知 manifest 文件名与序号构造（`snapshot/catalog.json#record/N`、`snapshot/manifest.json#entries/N`、`snapshot/manifest.jsonl#record/N`），不嵌入原 URL、asset_id 或下载文件名。
- status/kind/disposition/method/reason 全部经固定枚举过滤：未知 status → `unknown`，非九种标准 HTTP method → `null`，未知 reason → `unknown_reason` + `reason_count`；summary 从公开枚举重统计，原始字符串不能成为字典键。
- decision 的 reason/evidence_refs 自由文本**不复制**；source 只带 `decision_reference`（decisions 输入 SHA256、record_index、reason_present、evidence_ref_count）作为可追溯指针。decision 的 capability_id 仅当其已存在于公开 capability 集合时公开，否则只标 `has_external_capability_ref`。
- inventory warning 细节折叠为固定 `inventory_warning` reason + 数字 `element_index` 指针，不携带原 warning 字符串。

## 运行

```sh
python -m harness.product_matrix \
  --repo . --manifest matrix-manifest.json \
  [--decisions decisions.json] [--output matrix.json] [--summary-only]
```

- `build_matrix(repo_root, manifest_path, decisions_path=None) -> dict` 是冻结公共接口，返回 schema 2 公开投影。
- 默认输出到 stdout；`--output` 显式写 JSON。**`--output` 绝不覆写任何已存在路径**：任何既存文件/目录/符号链接（含指向输入的硬链接、悬空链接、已有报告、注册表契约、SDK 源码、manifest/inventory）一律拒绝；只允许全新路径，且不得位于任何 snapshot root 内。发布采用同目录临时文件 + 不可覆盖 `os.link`（EEXIST 拒绝），检查后竞争新建的目标也不会被覆盖。
- 校验失败/输入非法时 stderr 固定 `product_matrix: <reason>`，exit 2；不回显原始数据。

## manifest（schema_version=1，输入不变）

```json
{
  "schema_version": 1,
  "snapshots": [
    {
      "id": "2026-11-17-main",
      "kind": "portal",
      "root": "/abs/path/to/data/official-docs/2026-11-17-main",
      "manifest_sha256": "…",
      "inventory": "/abs/path/to/endpoint-inventory.json",
      "inventory_sha256": "…",
      "region": "domestic"
    },
    {
      "id": "2026-11-17-sdk-ref-domestic",
      "kind": "sdk_reference",
      "root": "/abs/path/to/sdk-reference/domestic",
      "manifest_sha256": "…"
    },
    {
      "id": "2026-11-17-assets",
      "kind": "assets",
      "root": "/abs/path/to/assets",
      "manifest_sha256": "…"
    }
  ]
}
```

- `kind` ∈ `portal | sdk_reference | assets`；`root` 为调用方明确给出的**绝对目录**；`manifest_sha256` 为 manifest 原字节 SHA256。
- 固定 manifest 文件名：portal=`catalog.json`，sdk_reference=`manifest.json`，assets=`manifest.jsonl`。
- `inventory`/`inventory_sha256` 仅 portal 可声明，必须同时出现，校验原字节 SHA256。
- 归档 `raw_file`、`local_path`、资产 `file` 必须解析在各自 `root` 内；绝对路径与越界直接拒绝，根内**缺失**文件保持 `blocked_source`，但文件存在而 hash 不符（被篡改）对三类快照一律是固定错误拒绝，不降级。
- 所有 JSON 严格解析：重复 key、`NaN`/`Infinity`、非法 UTF-8 一律拒绝；assets 逐行同样严格。

## decisions（schema_version=1，可选，输入不变）

```json
{
  "schema_version": 1,
  "decisions": [
    {
      "source_id": "2026-11-17-main:domestic:81",
      "content_sha256": "…",
      "disposition": "implement",
      "reason": "reviewed contract cn.token.get",
      "evidence_refs": ["contracts/reviewed-transport-contracts.json"],
      "capability_id": "contract:cn.token.get"
    }
  ]
}
```

- `disposition` ∈ `implement | reuse_official_component | not_an_api | duplicate | deprecated | phase_2 | needs_review | blocked_source`。
- `content_sha256` pin 必须等于对应 source 当前记录值；缺正文 source 的 pin 可为 `null`。
- `reason`、`evidence_refs` 必须非空；重复 `source_id`、未知 `source_id`、过期 pin、未知 disposition 一律拒绝。`source_id` 必须使用 schema 2 的公开 ID；schema 1 的 locator token ID 不解析、直接判为未知。
- decision 的 `reason`/`evidence_refs` 保留在原 decisions 文件中备查；报告只发布 `decision_reference` 指针（decisions SHA、record_index、reason_present、evidence_ref_count），不复制自由文本或 locator。
- decision 只改变 source 处置；不构成代码/测试/实测已完成证据，也不压掉同页未登记观察的 unresolved 计数。

## 输出结构（schema_version=2）

```
schema_version=2
input_manifest_schema_version=1
evidence="source_and_registry_matrix_only"
publication_profile="reviewed_metadata_only"
product_complete=false
denominator_defined=false
input_hashes          manifest + 各 snapshot manifest/inventory + decisions 字节 SHA256
source_sets           每快照 kind/region/hash/source_count/observation_count
sources[]             每条 portal 行 / static entry / assets 行一条白名单 source
observations[]        declaration/example 的字段白名单投影（无 path/host/title/locator/raw 值）
capabilities[]        contract:<operation_id>（已审契约）+ provisional:<obs_id> + review:<source_id>
unresolved[]          来源/处置/契约恢复/逐条未审观察问题清单（受控 reason 码）
registry_audit        复用 harness.audit 的固定 evidence/四个 registry 计数/两个 code 计数/三个输入 hash 显式投影
summary               source/observation/contract/unresolved 各计数与枚举分布
disclaimer/limitations 固定声明
```

### sources

- `source_id` 为公开序数身份（见上文边界）；`record_index` 为输入记录序号；`evidence_refs` 指向 manifest 序号位。
- `canonical_document_url` 仅 portal 来源非空（由 region+doc_id 构造）；sdk_reference/asset 恒为 `null`。
- `status` 经各 kind 固定枚举过滤，未知 → `unknown`；`category_paths` 仅保留 catalog 已核验目录归属——`inventory_only` 来源的 recorded 路径未经 catalog 确认，公开位恒为 `[]`（原值留在内部证据记录）。
- sdk_reference 仅额外保留 `discovery_reference_count`；asset 保留 `asset_category`（image/document/unknown）、`reference_count` 与 `references`（每条仅 `reference_index`/`reference_type`(link/image/embedded_resource/unknown)/manifest 序号 `evidence_ref`）。**引用计数不因省略 locator 而减少。**
- 缺正文 → `blocked_source`；有正文未审查 → `needs_review`；decision 可覆盖处置并附 `decision_reference`。
- inventory 中存在但 catalog 无对应行的文档，以 `status=inventory_only`、`blocked_source` 保留，标 `inventory_document_not_in_catalog`。

### observations

- 每条 HTTP declaration/example 一条：`observation_id/source_id/kind/ordinal/status/method/section_index/element_indices/has_query/has_fragment/reason_codes/reason_count/same_route_hints/related_declaration_ordinals/agrees_with_declaration/exact_operation_ids/inventory_content_sha256`。
- **无** path、host、section_title、inventory_url、raw 值、query/fragment 文本或它们的 hash。
- `method` 仅九种标准 HTTP 方法或 `null`；`status` 归 declared_pair/ambiguous/address_only/example_only，未知 → `unknown`；`reason_codes` 只含已知 parser reason 码，其余归 `unknown_reason` 并保留 `reason_count`。
- `same_route_hints`/`exact_operation_ids` 仅含已审核 registry 中存在的 operation_id；hint 绝不标 supported。exact 由本工具按六字段重算。
- example 的 `related_declaration_ordinals`/`agrees_with_declaration` 只是 parser 线索，不能提供 exact_operation_ids 或免审地位。
- 精确 join 要求观察自身 inventory pin 与 catalog 声明身份一致且归档正文可用；`inventory_identity_mismatch` 或 `inventory_only` 来源的观察不能嫁接 catalog 身份获得 exact 匹配。
- 必需字段畸形或重复 (kind,ordinal) 观察是固定错误硬失败，不静默漏行；白名单之外的未知附加字段安全忽略，不进任何输出、不参与 join。

### capabilities

- 出口处每种 capability 按 `capability_kind` 固定字段白名单**新建字典**（不是排序内部列表）：内部新增字段永远不会出现在公开结果。
- **reviewed_contract**：`contract:<operation_id>`；保留 `path`（审核 registry 常量）与构造的 `canonical_document_url`；`implementation_status=ast_verified`；`offline_status`/`live_status` 原样保留 binding 记录并标 `recorded_evidence`；`linked_observation_ids` 仅含六字段精确恢复的 declaration；`related_example_ids` 单独列出 parser 标记一致的 example，仅作审查线索。
- **provisional_observation**：每条未匹配观察一个 `provisional:<obs_id>`，`implementation_status=missing`，`operation_id=null`；**不含 path 或任何 locator**（不从 observation/same-route 回填）。
- **unobserved_source**：有正文但零观察的来源保留为 `review:<source_id>`，`implementation_status=not_applicable`；同样不含 path/locator。
- 未识别的 capability_kind 一律回落到无 locator 的 provisional 字段集。
- provisional 数量不是 API 总数/覆盖率。

### unresolved

`kind` ∈ `reviewed_contract_source | source | disposition | observation`；实体引用只用公开 source_id/capability_id/operation_id/observation_id；`reason` 为受控码集（`source_body_unavailable`、`snapshot_body_unverified`、`inventory_identity_mismatch`、`inventory_document_not_in_catalog`、`asset_body_unavailable_or_excluded`、`official_reuse_candidate_pending_review`、`inventory_warning`、`needs_review`、`blocked_source`、`contract_source_document_absent`、`contract_source_identity_mismatch`、`contract_declaration_not_recovered`、`binding_record_missing`），未知 → `unclassified_review_issue`；`inventory_warning` 条目附数字 `element_index` 指针。条目数保持，不以删除行脱敏。

### summary

分计 `source_count`/`observation_count`/`reviewed_contract_count`/`provisional_capability_count`/`unresolved_count`/`unresolved_reviewed_contracts`，及 `sources_by_kind`/`sources_by_status`/`sources_by_disposition`/`observations_by_kind`（全部由公开枚举字段重统计）。无百分比，`counts_are_not_api_totals=true`。

## 复用与边界

- 复用 `harness.audit.audit_repository` 做已登记实现的 AST/registry 审计（不重写审计器）；`registry_audit` 只显式投影已核定字段：`evidence` 固定 `registry_and_static_ast_only`、`registry_summary` 仅 `contracts`/`bindings`/`source_documents`/`source_issues` 四个整型计数、`code_summary` 仅 `bindings_checked`/`symbols_checked` 两个整型计数、`inputs_sha256` 仅 `contracts/reviewed-transport-contracts.json`、`contracts/sdk-bindings.json`、`contracts/source-issues.json` 三个已核对 hash；审计对象的任何未知子键一律不进入报告，不从输入动态扩白名单；`inputs_sha256` 与 contracts 文件二次核对，防审计后漂移。
- 只读：不修改任何输入文件；`--output` 拒绝一切已存在路径与快照根内路径，发布用不可覆盖 `os.link`。
- 报告不称实机、不称全产品完成；`product_complete=false`、`denominator_defined=false` 固定。
