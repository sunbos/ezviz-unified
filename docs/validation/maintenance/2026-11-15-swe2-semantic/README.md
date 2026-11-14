# 首次维护语义模型试跑

2026-11-15，由 Claude Code 经 Magpie 发起一次真实模型调用，请求 `devin/swe-2`、effort=max；接口报告的模型身份匹配，工具列表为空、工具调用数为0。未调用萤石业务接口、执行候选补丁或修改设备。

## 输入与结果

被测会话仅接收[固定任务提示](../../../../evals/maintenance/prompt.md)与[8个公开合成案例](../../../../evals/maintenance/cases.json)，拼接规则为提示全文 + `\n\n## 案例输入\n\n` + 案例文件全文。[预期答案](../../../../evals/maintenance/expectations.json)未传入该会话。实现评分器的会话和受测会话分开，均无会话持久化或工作区工具访问。

- [模型原始JSON答案](answers.json)：保留实际最终输出，不为提高得分改写。
- [确定性评分](report.json)：8项回答、8项通过、0项失败；参数值、类型和证据行均匹配。
- [运行记录](run.json)：模型/工具观察、输入输出与评分器哈希。用量为接口报告值，不是账单证明。

```sh
.venv/bin/python -m harness.maintenance_eval \
  --cases evals/maintenance/cases.json \
  --expectations evals/maintenance/expectations.json \
  --answers docs/validation/maintenance/2026-11-15-swe2-semantic/answers.json
```

## 限制

这是一次批量调用在8个简单、公开合成案例上的结果，不是隐藏留出集、重复采样统计或真实官网漂移测试。实现者模型也参与过数据文件生成，因此不能把输入隔离说成严格盲测。主导方独立制定并核对场景和预期值，评分器测试另有漏答、错误值、类型、证据和输入格式的反例。

本轮测量的是协议变化判断与证据提取，未测量生成补丁的正确性、修复后SDK行为、真实设备兼容性或自动发布能力。报告始终 `publication_authorized=false`、`patch_applied=false`、`real_api_used=false`。
