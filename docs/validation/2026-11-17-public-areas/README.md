# 公开国家/地区目录离线验收（2026-11-17）

汇总 `ACC-OS-PUBLIC-AREAS-OFFLINE-001` 的实际检查记录：公开国家/地区目录（`settings.list_areas` / `global.settings.list_areas`，cohort `PUBLIC-AREAS-001`，`scope_revision=2026-11-17-r1`）在 SDK code commit `58e7881a3e7aee4c3ffc431b5016b019cefb447c` 上的离线工程验证。机器可读明细见 [checks.json](checks.json)；配套 SDK 真实调用证据见 [live/sdk.json](../live/2026-11-17-public-area-directory/sdk.json)；SP036 决策记录见 [coordination/SP036/acceptance.json](../../coordination/SP036/acceptance.json)。

## 结果

- SDK/harness targeted 327 通过、示例 22 通过、全量 4580 + 35 subtests 通过；registered contracts 204 / wire executions 1664 全部通过；mypy 43 源文件、Ruff check 与 format 均通过（三个测试文件经 format 后确认）。
- `ezviz_unified-0.1.0a1` wheel/sdist 已构建并通过内容/元数据检查与 Twine 校验；干净环境 pip 安装通过，sdist 内 2744 测试通过，SDK smoke 走 mock——**安装依赖解析走网络，但 SDK smoke 与全部测试不触真实 API**。
- 诊断覆盖：133 条 business 入口，sync 与 async 合计 266 个方法体（各 133），为已登记/已冻结输入数量，不是官方能力总量。

## 初始失败 → 定向修订

`initial_targeted_result` 保留历史：首轮 targeted 311 passed / 3 failed。三处失败经定向修复——facade/request 诊断期望按实际签名修正、CLI argv 隔离、补充诊断失败等价性与有界 CLI 摘要——**未删除断言换取通过**，修订记录与各项执行 receipt SHA 见 checks.json `steps`。

## 边界

- 这是 code58 的**开发候选验证**，不是最终 SDK 产品 release，也未构成 PyPI 发布（`pypi_published=false`、`product_complete=false`、`candidate_only=true`）。
- `bounded.max_requests=0` 指真实云请求与业务效果；测试内有限 MockTransport 调用不冒充真实操作。
- wire 报告边界：require-all CLI 实际工具结果报告 204 contracts / 1664 passed cells（CLI 已校验其完整 worker 报告）；外部 Claude output-file 链接已过期，未另行保留逐格输出，不虚构该证据。
- 本批检查的 PR exact-head 与 main 验证待后续独立 Git 证据；不能据此宣称 PR 已合并或 main 通过。

## 证据

- [checks.json](checks.json)：逐步命令、执行 receipt SHA、两个产物的 SHA-256、干净安装结果与 43 个 SDK 源文件 pin。
- [live/sdk.json](../live/2026-11-17-public-area-directory/sdk.json)：独立 SDK 真实调用回执（2 HTTP/2 逻辑调用，固定 origin，identity 编码）。
- [live/README.md](../live/2026-11-17-public-area-directory/README.md)：SP016 raw HTTP 历史观察与新 SDK 实测证据的区分说明。
