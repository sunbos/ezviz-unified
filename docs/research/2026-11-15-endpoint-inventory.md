# 端点清单工具说明（2026-11-15）

## 用途

`tools.documents.endpoint_inventory` 从官方文档归档中生成 HTTP 端点候选清单，帮助贡献者把文档中出现的端点与已审核传输契约对齐，找出待进一步审核、仅见于示例或存在歧义的候选。当前已审核契约 186 份；来源主库共 2269 条记录、2221 份正文、48 条不可用。工具仅处理主文档归档，不包含另行保存的静态 SDK 参考库。

## 使用命令

```bash
python -m tools.documents.endpoint_inventory \
  --input data/official-docs/2026-11-14 \
  --contracts contracts/reviewed-transport-contracts.json
```

可加 `--summary-only` 仅输出汇总。完整输出建议重定向到已被忽略的 `data/reports/` 目录，例如：

```bash
python -m tools.documents.endpoint_inventory \
  --input data/official-docs/2026-11-14 \
  --contracts contracts/reviewed-transport-contracts.json \
  > data/reports/endpoint-inventory.json
```

校验失败时，stderr 固定输出 `endpoint-inventory: validation failed`，退出码为 2。

## 来源匹配规则

- 输出区分三类条目：文档中明确**声明**的端点、curl/HTTP **示例**、以及**不明确候选**。示例不会被提升为声明，缺失的路径不做推断。
- 与已有审核契约的精确匹配要求 `region`、`doc_id`、`source_url`、`content_sha256`、`method`、`path` 全部相等；任一字段不同即视为未匹配。`same_route_operation_ids` 仅表示同路由线索，不构成匹配依据。
- 每条候选保留声明原地址、证据 DOM 序号与来源标识；存在歧义的条目原样保留，不做自动修补。

## 完整性与安全边界

- 输入在解析前全部校验 hash、身份与路径；任务结束前复查输入 hash，发现漂移即拒绝输出。该流程不声称提供原子快照。
- 解析器不访问网络，不执行 HTML 或 curl 内容；适配器不调用真实 API，全部结果来自静态文档。

## 限制

- 来源不完整（48 条不可用）且解析器仅支持部分文档格式，因此候选数不等于 API 总数；某文档零候选不代表其中没有 API，已关联契约也不代表该端点已经现场验证。

本次归档扫描的可核查统计见[离线验收摘要](../validation/2026-11-15-endpoint-inventory.json)。
