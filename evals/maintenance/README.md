# 维护语义评测（公开合成基准）

`evals/maintenance/` 是一个**公开**的合成基准，用于评测维护模型能否从“维护前 / 维护后”两份行级文档中提取有证据的协议变更、识别不影响协议的编辑，并在来源缺失、冲突或地区不一致时保留不确定性。

## 边界与声明

- 所有材料均为合成文本，不包含真实密钥、真实设备或官网真实变更；本数据集不是隐藏留出集，8 个案例通过不证明模型总体质量。
- 本轮只评测**语义提取**能力，不评测补丁正确性，不授予任何发布权限；所有评分报告始终 `publication_authorized=false`、`patch_applied=false`、`real_api_used=false`。
- 模型身份不能由 answers 文件自报证明；评分器只评文件本身，请求模型 / effort 与实际运行由主导方另行记录。
- answers 中的额外字段或 `passed` / `score` / `publish` 等自报字段会被评分器直接拒绝。

## 文件

| 文件 | 用途 |
| --- | --- |
| `evals/maintenance/cases.json` | 被测模型可见的案例材料（schema_version=1）。 |
| `evals/maintenance/prompt.md` | 被测会话使用的固定任务和输出协议。 |
| `evals/maintenance/expectations.json` | 评分 oracle；在模型作答前**不得**传入被测会话。 |
| `harness/maintenance_eval.py` | 确定性评分器与只读 CLI。 |
| `tests/test_harness_maintenance_eval.py` | 评分器测试；默认 `pytest` 只测评分器，不调用任何 LLM。 |

## 判定协议

每条答案仅含 `case_id, decision, changes, uncertainties` 四个字段：

- `decision` ∈ `no_contract_change` / `contract_change` / `insufficient_evidence`。
- `changes[]` 每项含 `field, before, after, before_evidence, after_evidence`：`field` 必须出现在对应 case 的 `fields` 中且在单 case 内不重复；`before`/`after` 仅允许 null / string / 整数 / bool（float 被拒），且两侧类型+值不能完全相同；证据为对应侧存在的行 ID 非空不重复列表。
- `uncertainties` 为 `source_missing` / `source_conflict` / `region_unverified` 的不重复列表。
- `no_contract_change` 要求 changes 与 uncertainties 均为空；`contract_change` 要求 changes 非空且 uncertainties 为空；`insufficient_evidence` 要求 changes 为空且 uncertainties 非空。

评分忽略行顺序、changes 顺序与 uncertainties 顺序，但不忽略缺失或多余证据；漏答记 `missing_answer` 并保持总分母不变。有效但不符 oracle 的答案记为评分失败，畸形输入直接拒绝。

## 运行

被测模型在零工具会话中只接收 `cases.json` 与上述输出格式，产出 `answers.json`，随后评分：

```bash
python -m harness.maintenance_eval --cases evals/maintenance/cases.json --expectations evals/maintenance/expectations.json --answers path/to/answers.json
```

退出码：0 = 全部通过；1 = 答案有效但未通过；2 = 输入或读取错误。stdout 报告附带 `inputs_sha256`（三个输入文件原始字节的 SHA256），不回显源段落、候选原文或预期答案。

首次真实模型运行及输入隔离边界见[2026-11-15记录](../../docs/validation/maintenance/2026-11-15-swe2-semantic/README.md)。实现者参与过数据生成，不能把新会话输入不含oracle等同于严格盲测。
