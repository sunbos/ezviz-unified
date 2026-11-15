# 国内组件路径实现派单

基线`085bee1a81f7a90d5c852c3832c919a180498ddb`，分支`codex/component-paths`。方案见[设计](../../plans/2026-11-15-component-paths.md)。

执行均为Claude Code / Magpie `devin/swe-2`、effort=max。无凭据或设备请求；候选生成后独立执行离线测试。Codex负责方案、来源/差异审查和验收。

## P003A

唯一可写文件：

- `src/ezviz_unified/operations.py`
- `src/ezviz_unified/client.py`
- `src/ezviz_unified/transport.py`
- `src/ezviz_unified/__init__.py`
- `src/ezviz_unified/_request_values.py`
- `src/ezviz_unified/_path.py`
- `tests/test_sdk_path_parameters.py`
- `tests/test_sdk_json_null.py`

## P003B

唯一可写文件：

- `harness/registry.py`
- `harness/wire_eval.py`
- `tests/test_harness_path_parameters.py`
- `tests/test_harness_json_roots.py`

## P003C

唯一可写文件：

- `src/ezviz_unified/components.py`
- `tests/test_sdk_components.py`


既有196契约须保留，新增契约由后续独立登记任务完成。本批产生候选不代表验收或发布。
