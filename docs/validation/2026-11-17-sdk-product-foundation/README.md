# 来源基础批次验收（2026-11-17）

汇总 SDK 产品阶段第一批「全量来源闭包登记」的验收事实：门户快照、静态 SDK 参考、旧账号指南与声明附件四个采集批次，以及来源账本的 schema 2 公开投影。证据分层固定为 `sdk_product_source_foundation_only`：**来源登记与完整性验收不构成语义审查、SDK 支持声明或产品完成**。机器可读明细见同目录 [checks.json](checks.json)。

- 基线：main `81b86b462825739db8f059a418d5de235db88c87`；范围冻结 `004a795`（[SDK 产品阶段计划](../../plans/2026-11-17-sdk-product-completion.md)、[验收结构](../sdk-product-acceptance.md)）。
- `src/` SDK 源码自诊断 v1 合入以来未变更（`sdk_sources_changed=false`）；`product_complete=false`、`semantic_capability_matrix_complete=false`。
- 执行：Claude Code 经 Magpie，`devin/swe-2`、effort=max；验收为 Codex 对真实工具结果的独立复核，执行方未接触凭据。

## 来源批次与状态

五个**声明** source set（仅声明范围，不是全厂商生态闭包）：

| source set | kind | manifest SHA-256 |
| --- | --- | --- |
| `portal-2026-11-17` | portal | `83bfc37a3dc45e4f126f6c92d5423ca42b60dff5377634486589c11fcd22c130`（inventory `4e6567579e0a5d34c813f4ff22785508469cb585b7265e0a26259e3498d0fe3f`） |
| `sdk-cn-2026-11-17` | sdk_reference | `08000fce33a8749a383a4e12b8f0770ea87bb6b250ceb9e677bfbb474e5d7a36` |
| `sdk-global-2026-11-17` | sdk_reference | `3674fd9c1dce1d48c164a7f74b8553c85bd1aca499fd815f95013892d9210901` |
| `account-guide-2026-11-17` | sdk_reference | `cf24414df447d7bb6b22cc21f9c351db7b854ea6f98363e9e8fd237e220f5769` |
| `assets-2026-11-17` | assets | `7f0ffd5203651bad1b449898818945c54da1d46933ee7ddee6407b8b76b50e72` |

- **门户**（`data/official-docs/2026-11-17-sdk-product-main`）：2281 条记录、2233 条正文、**48 条缺项**；相对 11-16 快照新增 12 条来源（3 条平台 BLE 资料 + 9 条视频巡检指南），既有正文 SHA 未变，derive/verify 成功。
- **静态 SDK 参考**：国内 manifest 73 条/成功 72，海外 219 条/成功 217；国内 1 条编码告警按原字节保留（UTF-8 strict 失败时 U+FFFD 派生文本 + 原字节留存，见[归一化说明](../../sdk-reference-normalization.md)）。静态/指南失效合计 **4 条**：`CN/pc/group__ddns.html`、`EN/pc/group__ddns.html`、`EN/android/com/videogo/openapi/EZOpenSDK.html`（旧快照既有），加本轮发现的旧账号指南 `doc/zh/book/index/user.html`（跳转到 `/doc/`、无正文）——保留为来源阻塞，不静默剔除。
- **附件**：1477 条记录、2157 条引用；1460 个响应文件 + 3 条已归档文档引用经独立核验，1438 条资源 URL 对应内容可读（`ok` + `source_mime_mismatch`），并非「全部附件派生正文已提取」。状态原样保留：`source_invalid_document` 22、`source_mime_mismatch` 31、`excluded_non_document_media` 12、`excluded_placeholder` 1、`excluded_url_policy` 1，未归零、未改写。采集为一次授权调用，Claude Bash 于 600s 后转入后台；原任务日志以 exit 0 结束，最终 manifest 与被引用字节另行独立核验，未启动第二份采集。
- **账本总量**：4051 条 source 记录（portal 2281 / sdk_reference 293 / asset 1477）、3019 条 observation（declaration 1529 / example 1490）；203 条既有契约全部保留，201 条精确恢复来源关联，2 条未恢复为已知的单侧反引号 parser 例外（`global.playback.invalidate`、`global.security.update_password`，见[计划](../../plans/2026-11-17-sdk-product-completion.md)第 41 行口径）。
- **计数纪律**：`counts_are_not_api_totals=true`——5339 条 provisional capability 与 7256 条 unresolved 是待审/阻塞队列规模，**不是**缺失接口数或缺陷数；来源/观察计数不是 API 总量、SDK 覆盖率或物理能力证明。

## schema 2 公开投影（关键设计修订）

设计全文见 [source-ledger 公开投影边界](../../plans/2026-11-17-source-ledger-publication-boundary.md) 与[矩阵说明](../../product-matrix.md)。

- 原 **schema 1** 报告仅处理 URL query 值，但实际语料的部分业务示例 **path** 仍含样例设备标识与不透明任务/模型段。该报告**未上传**，保持私有并标记 `not-for-sharing`（SHA `f8e104c2df72ad9e96aaa70b5b00cd3443a0eb69fee5819beda5e737340352f7`，原报告与归档保留）；逐 URL 模式替换不再被视为公开安全边界。
- **schema 2**（`publication_profile=reviewed_metadata_only`）：对 source/observation/capability/unresolved 逐类型**显式新建白名单字典**（不复制内部 dict 再 pop），内部新增字段永远不会自动出现在公开结果；未审查的 path/host/section_title 与 asset/discovery locators 一律不输出，也不用 hash 伪装匿名。
- 非 portal 来源以 manifest 记录序号作公开 ID（`snapshot:sdk_reference:<index>`、`snapshot:asset:<index>`），不用 URL hash 冒充身份；`evidence_refs` 仅由已知 manifest 文件名 + 序号构造。
- 唯一公开的原始定位形式：**已审核 registry 常量 path**（仅 reviewed_contract，203 槽位）与由核定 region + ASCII 数字 doc_id **构造**的 `canonical_document_url`（2484 槽位）；非法门户 identity 硬失败且不回显原值。
- **真实复核而非删行脱敏**：全部 record index/hash/graph refs 保留（`all_record_indices_and_hashes_preserved`、`graph_references_valid`）。完整投影检查是**逐类型显式白名单字段 + 图引用核验**；另对全部观察做局部条件检查匹配出 **17 条含 literal 的记录**（非随机采样），确认其中 4 个不同 literal 原值、3 个 query 原值及全部 1477 个旧 asset ID 均未出现在输出中——任何原值不得输出。这 4 个值的单独比较不能代表全面匿名化，白名单字段集与图关系才是完整投影边界。来源 hash 只用于完整性绑定，不是匿名化手段。
- schema 2 与私有 schema 1 在同一输入下 summary 完全一致（`same_summary_as_private_schema1`），输入 hash 未变（`input_hashes_unchanged`）。

## 本地验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest（SP012-tests，最终 raw-locator 绑定变更前，旧导出结构） | 4488 tests + 35 subtests 通过（42.17s；SDK 代码未变） |
| 全量 pytest（SP019-tests，schema 2 之后） | 4499 passed, 35 subtests passed in 42.46s |
| schema 2 定向 `tests/test_harness_product_matrix.py`（SP003P2FinalT） | 44 通过（1.42s） |
| Ruff `harness/product_matrix.py` + 对应测试（SP003P2FinalL） | 通过 |
| 归一化 `tests/test_document_sdk_reference.py`（SP005T2） | 22 tests + 24 subtests |
| 文档输出 `tests/test_document_inventory_output.py` + endpoint inventory（SP006T） | 74 通过 |
| SP012 lint / format（当时 CI 显式范围，172 文件） | 通过 |

- schema 2 之后的全库回归已由 **SP019-tests** 完成：**4499 passed, 35 subtests passed in 42.46s**（`post_schema2_full_suite`）。上方 4488 为旧导出结构下的历史记录，计数差异来自两次快照间的测试新增，不作横向对比；本记录不编造 CI、PR 合并或发布结论。
- 工具行为正常：`--output` 对任何已存在路径（输入、旧报告、快照根内路径、悬空链接）一律拒绝，不会覆盖输入或既有产物。
- 失败迭代不逐条留流水账；关键修订（query 掩码 → 逐类型白名单投影）与未解决范围分别见上、下节。

## 产物位置与复现

完整 source ledger（约 12 MB）位于**原 checkout** 的 `data/reports/sdk-product-matrix-public-v2-2026-11-17.json`（report SHA `d0e6bae17322a3aa379e75918327f85bcdcfb9ce53ee53371604047a6cad9540`）。`data/` 属本地忽略：该文件**未随本批 PR 公开**，也不进入 SDK 包；`harness/product_matrix.py` 定位见 checks.json `implementation_file_sha256`（SHA `6d7aefafa82f05b1ddacd145c4c5532bc982283c728b090b4d24129cb10dcb06`）。本验证记录范围**不包含** PR 合并、main CI 或发布证明——这些须以对应准确 commit 的外部 checks 另行核对；此处只给代码路径与 SHA、不造指向不存在文件的链接，也不链接不安全的 schema 1。SDK 业务补齐、语义审查、真机验收与 PyPI 发布仍未完成。

checks.json `local_artifacts` 中的相对路径在有本地归档时有效：输入 manifest `data/reports/sdk-product-source-manifest-2026-11-17.json`（SHA `fd96ba8ad291f6a1954968a879c64e5d29d0c5c49c09f84c31c7c095bad6479f`）、schema 2 账本与独立验收 JSON、seed revalidation 记录。有本地归档时可用**新输出名**复现（`--output` 拒绝一切已存在路径，必须换新文件名）：

```sh
python -m harness.product_matrix \
  --repo . \
  --manifest data/reports/sdk-product-source-manifest-2026-11-17.json \
  --output data/reports/sdk-product-matrix-public-v2-<新标签>.json
```

## 未解决边界

- 本批仅完成来源闭包登记与机械账本验收；**未**完成全量能力语义审查、SDK 业务补齐、真机验收或 PyPI 发布。4051 条记录不得称为全生态闭包；linked SDK binaries 与官方 repo 发行版仍需组件级版本核定。
- 海外 16 篇经审文档产出 49 条能力候选（implement 40 / duplicate 2 / source_alignment 2 / needs_review 5），SDK 入口名仍属 proposal 未冻结，见 [overseas-review-candidates](../../capabilities/overseas-review-candidates.md)；该 16 篇不是海外总量。
- 国内 2229 条来源已按八个互斥**业务来源审查队列**全登记（队列规模 326 / 165 / 283 / 315 / 385 / 294 / 340 / 121，合计 2229）。这是八个待审队列的入口分桶，不是 8 个 disposition 标签，也不表示语义处置已完成；逐项语义审查仍待进行，`needs_review`/`blocked_source` 不算完成。
- 资源与授权缺口保留：海外账号经用户确认**无绑定设备**；平台消息服务与可用回调入口**未知待查**；PyPI 主体/版本/上传授权**未定**。
- 第二轮 deferred 完整保留：App adapter、选择性遥测、独立模型评测、受控 RSI。缺 source/device/权限/服务的条目按原样保留在账本与验收 JSON 中，不静默归零。
