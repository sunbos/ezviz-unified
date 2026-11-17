# SP026：公共国家/地区目录 SDK

输入：main `641d6cc10010b11a49c65c9d6783f92eefda00d3`，G0/G1见[SP030](../SP030/admission.json)。Claude Code经Magpie固定`devin/swe-2`、effort=max；候选写入与命令执行分会话，Codex关键审查与验收。

唯一可写文件：

- `src/ezviz_unified/settings.py`
- `src/ezviz_unified/_diagnostic_registry.py`
- `contracts/reviewed-transport-contracts.json`
- `contracts/sdk-bindings.json`
- `contracts/source-issues.json`
- `contracts/coverage.json`
- `evals/contracts/cases.json`
- `docs/diagnostics-v1-coverage.json`
- `tests/test_sdk_public_areas.py`
- `tests/test_harness_public_areas.py`
- `tests/test_examples_public_areas.py`
- `README.md`
- `docs/getting-started.md`
- `docs/research/2026-11-17-public-areas.md`
- `examples/public_areas.py`
- `tests/test_harness_diagnostic_coverage.py`
- `docs/diagnostics.md`

接口、来源、场景及判据：[冻结合同](../../plans/2026-11-17-public-area-directory.md)与`ACC-OS-PUBLIC-AREAS-OFFLINE-001`/`ACC-OS-PUBLIC-AREAS-SDK-001`。实现会话无Bash和网络，不修改验收结果、不发设备请求。

后续命令：指定新入口/仓库/示例测试及诊断覆盖审计；harness.audit --check-summary、harness.contract_eval --require-all、mypy与相关Ruff；代码审查通过后全回归和构建/安装/CI。所有命令由精确授权的执行会话记录真实tool_result；SDK真实验收由Codex按最多2次匿名GET控制。

验收：同步/异步准确GET、无query/body/token/provider/重试，保留ApiResponse和异常语义，前置失败与取消/关闭进入既有诊断机制；新增registry/binding/eval/source issue/诊断白名单与133入口覆盖同步，合成fixture、sdist独立。示例和文档明确国家目录不是集群路由。实测、硬件及发布未通过时保留pending。

输入SHA：

```json
{
  "docs/capabilities/scope-register.json": "1f44904c6f826deacdc282f0deb8769dbf19e8ed280874dab7e9db75c1374536",
  "docs/coordination/SP030/admission.json": "3bdcdab71d2c677a317ae6624d65f18103165796a001f53ee0921b7aa86dd06f",
  "docs/plans/2026-11-17-public-area-directory.md": "da8d3c9151051cb57c4f245060143caa24747ac4275fc8bba0a1b29b754f9d42",
  "docs/validation/sdk-product-acceptance.json": "7d79fd82a6bc33167853cac57e4776743e9bf75bb04d1929002b0e8ff6237090",
  "src/ezviz_unified/settings.py": "0a640edeac8c5bd17faf95c041bc8e8a750f30b513f4b69cb09112eaae886527",
  "src/ezviz_unified/_diagnostic_registry.py": "49a15a36a1cedb318f1288723373a1607e25a8b860f2235940e1d904cea17b96",
  "contracts/reviewed-transport-contracts.json": "fd1afc667ea3c5b96e02dea82ddf46591b7c947f07129a881b96bb120f44bfd2",
  "contracts/sdk-bindings.json": "a8453bcf9fecc73784d0911f414a9c905ecadce87ddb51f5e5eefbf86c02aed7",
  "contracts/source-issues.json": "1231b604d49a83ecee986a86a74d3bd43d7aa58d3890893ae84f654bfc94a5d7"
}
```
