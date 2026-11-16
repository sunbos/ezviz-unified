# endpoint-inventory `--output` 文件输出说明（2026-11-17）

## 用途

`tools.documents.endpoint_inventory` 现在支持把完整报告直接写入一个全新文件，替代只能依赖 shell 重定向的旧用法。该接口面向无法执行重定向的受限执行器。

## 使用命令

```bash
python -m tools.documents.endpoint_inventory \
  --input /path/to/frozen-snapshot \
  --contracts contracts/reviewed-transport-contracts.json \
  --output /path/to/new-inventory.json
```

可加 `--summary-only`：有 `--output` 时文件只保存摘要块（`schema_version`/`evidence`/`summary`/`unrecovered_reviewed`/`input_sha256`/`limitations`）；无该 flag 时保存完整报告。不带 `--output` 时 stdout 行为与旧版完全一致。

## 成功回执

`--output` 成功后 stdout 不再打印报告全文，只输出一行固定小回执：

```json
{"schema_version": 1, "evidence": "offline_http_declarations_only", "report_sha256": "<已写入字节的SHA-256>", "summary": {...}}
```

`summary` 为摘要选择前的真实汇总计数。回执不包含报告正文、示例 URL 或输出路径值；校验 `report_sha256` 与写入文件字节一致即可确认落盘内容未被改动。

## 安全边界

- **从不覆盖**：目标路径已存在即失败——无论是普通文件、目录、符号链接、悬空符号链接还是硬链接。报告先写入同目录临时文件，再以硬链接发布；并发出现同名目标只会被拒绝，不会被覆盖。
- **不写输入**：输出路径不得位于输入归档根目录内，即使是新文件也拒绝；归档、catalog 与 contracts 输入不会因本 flag 被修改。
- **失败行为**：任何输入校验失败都不会产生报告文件；写盘失败清理本次临时文件但不删除任何既有内容。CLI 错误仍为固定 `endpoint-inventory: validation failed` + 退出码 2。
- 缺失的父目录会被创建。

## 数据性质不变

文件中的报告仍只是**离线 HTML 观察**：候选计数不是 API 总量或 SDK 覆盖率，已关联契约不代表现场支持。该输出不能替代对静态 SDK 参考、消息声明与附件材料的单独审查，零候选文档不代表其中没有接口。
