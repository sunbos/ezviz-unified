# R001 — 受控修复演练:`cn.storage.spaces` 的 `pageSize` 上限 50 → 20(合成)

本目录存放一个**合成、由协调方控制**的修复案例,用于演练“已确认新契约 → 候选补丁 → 独立验收”链路;改动被刻意限制在最小范围。通过验收**不代表**源登记晋升、真实官网变更或发布许可,也不证明任意修复可以被自动接受。

## 案例内容

- 目标:国内(domestic)`cn.storage.spaces`(`GET /api/service/open/storage/engine/space/listById`),参数 `page_size`(线上字段 `pageSize`)。
- 模拟新契约:`page_size` 由 `1..50` 收紧为 `1..20`,边界与对应 `ValueError` 文案同步更新。
- 不变项:`page_size` 仍为可选——缺省不注入 `pageSize`(默认分页不变);`StorageFileQuery.page_size` 仍为 `1..50`;`download_urls` 的 1..50 个 ID 上限不变;其余所有行为一致。
- 候选仅允许修改 `src/ezviz_unified/storage_models.py`。

## 来源与诚实声明

- 文档来源:<https://open.ys7.com/help/5230>,2026-11-14 归档 `doc5230`,sha256 `15c63a8d00d55432b946b18f36b62034800d80e17041507c1b2a570c8290edf3`。
- 该归档描述**当前已发布**规则(上限 50);上限 20 是**本演练的合成设定**,不表示 open.ys7.com 发生了真实变更。

## 角色分工

- **候选模型**:获得新契约说明与当前 `storage_models.py`,返回限制在 `allowed_paths` 内的补丁。候选永远看不到 `oracle.py`、`tests/test_harness_repair_oracle.py`、适配后的测试文本与任何验收结果;其自报“通过”不能覆盖 oracle 失败。
- **协调方**(可信,在隔离副本中):
  1. `validate_scope(before_source, after_source)`——解析原始源码并在其 AST 上施加唯一允许的改写(`_spaces_query` 内 `1 <= size <= 50` 整数上界 `50 → 20`,以及同函数内 `ValueError` 实参 `"page_size must be within 1..50" → "...1..20"`,各恰好一次),再与候选 AST 比较 `ast.dump(include_attributes=False)`。其他任何差异一律拒绝:包括 `50 → 50.0` / `(0, 1) → (False, True)` 这类**值相等但类型不同**的连带改动(即已修复的根因缺陷)、`kind`/节点类型变化、docstring、`StorageFileQuery.__post_init__` 中相同的边界、`_DOWNLOAD_MAX_IDS`、新增 import/辅助函数、目标锚点缺失或不唯一、以及未做任何改动的候选。源码只解析、不执行;不做全局字符串替换。
  2. `adapt_regression_test(source_text)`——协调方预先固定的新契约测试调整:仅把 `test_documented_inclusive_bounds_and_no_extra_batch_limit` 中 `spaces` 调用的 `"page_size": 50` 字面量改为 `20`;按 **UTF-8 字节偏移**定位(AST 列号是字节偏移而非字符下标,已修复此前缺陷),其余文本逐字节保留,锚点缺失或不唯一即失败。返回文本只作用于隔离副本;原始测试与登记不变,不删除任何既有覆盖。
  3. `python -m pytest evals/maintenance/repair_cases/page_size_limit/oracle.py`——以与 `tests/test_sdk_storage.py` 完全相同的构造参数(`region`、`clock`、`auth_transport`、`transport_factory`)在 `httpx.MockTransport` 上驱动真实 `Client`/`AsyncClient`;全部合成数据,不接触真实 API。SDK 采用**惰性导入**,取运行环境 `sys.path` 所解析的 `ezviz_unified`(隔离副本);没有路径回退,绝不静默导入原始仓库。
- **可信辅助回归**:`tests/test_harness_repair_oracle.py` 属默认测试套件,覆盖上述静态检查与适配器自身(含 50→50.0 值相等绕过与 UTF-8 偏移两类已修复缺陷);不向候选展示。

## 运行时覆盖

`page_size=20` 被接受并出现在线上参数中;`0`、`21`、`50` 在认证/业务请求之前抛 `ValueError`;省略 `page_size` 不注入 `pageSize`;`page_size=1` 仍可用;`StorageFileQuery` 仍接受 `page_size=50`、拒绝 `51`;`download_urls` 仍接受 50 个 ID、拒绝 51 个。

## 案例边界

AST 范围检查之所以可行,是因为本例改动仅为单条边界字面量及其文案。涉及多文件、多字面量或行为变化的修复不能复用它——每个后续案例都需要自己的范围规则与验收 oracle。本案例通过不授予任何发布许可,也不调用真实服务。
