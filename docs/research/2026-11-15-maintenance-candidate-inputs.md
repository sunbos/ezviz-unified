# H010 — 已核验的维护候选输入数据包

`harness/candidate_input.py` 为显式选定的维护评审任务构建只读模型输入数据包。它仅是重新打包步骤：完整运行一次 `harness.review.run_review` 基线，重新核验其触及的全部哈希钉定输入，并在严格的序列化尺寸上限内输出归档文档正文、全部相关受评审契约、任务源问题与直接绑定的 SDK 模块。它不是模型后端、补丁执行器或代理框架。

## 流程

1. 参数校验：`task_ids` 必须为非空且唯一的非空字符串序列（裸 `str` 拒绝）；`extra_modules` 为唯一字符串序列；`max_bytes` 为严格正 `int`（`bool`、浮点与字符串均拒绝）。
2. 先调用 `run_review` 完成归档与SDK基线验证，再读取候选所需正文与源码；仅接受 `evidence=offline_maintenance_review_packet`、`decision=review_required`、conformance `passed`/`coverage_complete` 严格为 `True`、无 `guard_failure`、四项权限标志严格 `False` 的基线结果。
3. 读取时重读快照 `catalog.json`/`derivation-summary.json`、三份注册表（reviewed-transport-contracts、sdk-bindings、source-issues）、评估用例与 SDK 源码清单，并与 `review.inputs_sha256`、`review.source_files_sha256` 比对；构建末尾对全部输入再次重读并复查哈希，任何漂移即失败。重查只证明读取时刻一致，不宣称消除所有竞态，因此快照与仓库目录在运行期间必须保持稳定。
4. 选中任务整体深拷贝；文档按 `(region, doc_id)` 绑定 before/after 两侧来源，新增或移除文档允许一侧目录条目缺失并输出 `null`；两侧都没有目录条目则失败。`source_missing` 保留状态并输出 `html_content: null`，不伪造正文；归档行重读 `raw_file`（拒绝绝对路径、`..`、反斜杠与外部符号链接），`raw_file_sha256` 与 `content_sha256` 均须匹配；`synthetic_fixture` 条目如实标记。HTML 仅作数据，绝不执行。
5. 契约：凡被选中任务操作绑定的受评审契约，全部各收入一次；缺项、重复契约或重复任务ID均失败。
6. SDK 上下文：去重后的 `operations[].module` 集合加显式 `extra_modules`；路径须逐字存在于已核验源码清单、位于 `src/ezviz_unified/` 下且以 `.py` 结尾。输入源代码仅作上下文，不构成依赖闭包；需要更多上下文的调用方必须显式请求额外模块并通过同样的清单校验，不能假设已包含整个依赖闭包。
7. 尺寸：以 `ensure_ascii=False` 序列化后的实际 UTF-8 stdout 字节数（含末尾换行）不得超过 `max_bytes`（默认 2 MiB），超限即失败而非截断证据。

## 输出

`schema_version: 1`，`evidence` 为 `verified_candidate_input_only`，字段含 tasks、documents、contracts、source_files、source_manifest、review_inputs_sha256、baseline_conformance、四项权限标志 `false` 与 limitations。

## 非目标与限制

不调用任何模型，不生成或执行候选代码，不应用补丁，不访问网络或真实 API，不授权任何发布。边界检查不构成操作系统沙箱。哈希仅见证打包时刻的输入，后续使用须重新核验。

## CLI

在源码仓库中运行。先将BEFORE/AFTER替换为已采集的快照目录，任务ID须来自本次review；多个任务或额外模块可重复提供对应选项。

```bash
python -m harness.candidate_input --repo . \
  --before data/official-docs/BEFORE --after data/official-docs/AFTER \
  --task domestic:5230 \
  --extra-module src/ezviz_unified/storage_models.py \
  --max-bytes 2097152
```

完整数据包 JSON 打印至 stdout，退出码 0；任何失败仅向 stderr 打印 `candidate-input: validation failed` 并以退出码 2 结束，不泄露文件内容、不可信路径或异常细节。