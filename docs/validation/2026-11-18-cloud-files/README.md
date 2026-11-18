# 云录制 1.0 文件与 VOD 元数据离线验收（CLOUD-FILE-001）

本目录记录 commit `b251b11389405075ba30838cf9b2a594e1477ca6` 上**离线一致性 + 本机合成验收**的控制器证据 [checks.json](checks.json)。证据层级 `evidence_scope`：**SDK offline conformance and local synthetic acceptance; no real cloud/device calls**——本批**未发任何真实云或设备请求**，`real_api_used=false`。

## 四个入口（实现状态）

| 入口 | 方法 | 源文件 | wire 语义 |
| --- | --- | --- | --- |
| `cloud_recording_v1.unfreeze_file` | POST 表单，token 在 body | `src/ezviz_unified/cloud_recording_v1.py` | 写操作、不重放 |
| `cloud_recording_v1.file_info` | GET query，token 在 query | 同上 | 只读 |
| `cloud_recording_v1.download_urls` | GET，token 在 Header | 同上 | 只读 |
| `vod.files` | GET，token 在 Header | `src/ezviz_unified/vod.py` | 只读 |

实现细节与 SDK 选择（非官方事实）见 [研究报告](../../research/2026-11-18-cloud-recording-v1-and-vod.md)；验收用例绑定见 [sdk-product-acceptance.json](../sdk-product-acceptance.json) 的 `CLOUD-FILE-001` 八条 case 与 scenario `ACC-CN-VOD-RESOURCE-DISCOVERY-001`。

## 执行模型

`requested=devin/swe-2`、`effort=max`、`observed=devin/swe-2`。

## 检查命令与结果

全部 `result=passed`、`actual_tool_result_is_error=false`。`local_execution_artifact_id` 是本机执行回执标识，**非 fresh clone 需要的文件**；`artifact_sha256` pin 对应输出。

| 检查 | 命令 | 计数 |
| --- | --- | --- |
| 全量回归 | `python -m pytest -q --tb=short --maxfail=5` | 5448 tests + 35 subtests，45.39 s |
| 新增 lint | `ruff check`（新模块 + 新测试 + probe） | 通过 |
| 类型 | `mypy src/ezviz_unified` | 45 源文件 clean |
| 契约评测 | `python -m harness.contract_eval --repo . --require-all` | 208 contracts / 1696 executions / 0 failed |
| 诊断覆盖 | `python -m pytest tests/test_harness_diagnostic_coverage.py` | 278 tests（137 入口 sync/async body 274；278 指该文件 pytest 用例数） |
| cloud wire 单测 | `python -m pytest tests/test_sdk_cloud_recording_v1.py` | 190 tests |
| VOD 单测 | `python -m pytest tests/test_sdk_vod.py` | 145 tests |
| 云 behaviour + 表单 | `python -m pytest tests/test_sdk_cloud_recording_v1_behaviour.py tests/test_live_cloud_files_form.py` | 312 tests（历史 targeted 基线，早于后续 probe 测试文件的新增/修订；并非早于 probe 全部测试加入） |
| 构建 | `python -m build` | 通过 |
| 产物检查 | `tools/release/check_artifacts.py` | 通过 |
| Twine | `twine check dist/*` | 通过 |
| 干净安装 | `tools/release/verify_installations.py --installer pip` | wheel + sdist 干净安装通过 |

上表 pytest 行统一用 `python -m pytest` 入口书写，均可从 repo 根目录运行；`checks.json` 中记录的实际命令带 `.venv/bin/python -m` 前缀，二者等价（venv 内 `python` 即该解释器）。

**计数边界**：上述数是已执行测试单元/已登记契约数，**不是官方能力覆盖率或 API 总数**，不把它们加总成 coverage 百分比（见 `coverage_boundaries`）。

## 构建与安装（`0.1.0a1`，开发构建非最终发行）

- `build_artifacts`：wheel `ezviz_unified-0.1.0a1-py3-none-any.whl`（51 成员）、sdist `ezviz_unified-0.1.0a1.tar.gz`（121 成员），各带 SHA256。
- `clean_install`：installer=pip、Python 3.12.13、`offline=false`（安装依赖走网络）、`real_api_used=false`；wheel/sdist 干净安装均通过，`sdist` 源码包测试 **3371 passed in 10.39s**，smoke 为已安装 SDK 的 sync/async mock（不触真实 API）。
- **本批 0.1.0a1 只是开发构建，未冻结最终产品发行版本，未上传 PyPI**；产物哈希仅对当前 commit 有效，重建须重新核验。

## 真实 TCP 本机合成 callback（不冒充 SDK 真实云业务）

`local_probe_safety`：`real_local_tcp_get_post=true` 指 `tests/test_live_cloud_files_deadlines.py::test_loopback_acceptance_get_and_post` 在本机 loopback 用真实 TCP 跑通 GET+POST；`verify_callback` 是**合成 allowlisted 报告、无云调用**。它验证的是探测器/表单的本机回环路径与隐私 sentinel/输入清理，**不是** SDK 对真实云业务的调用证据。`synthetic_only=true`。

## 时限与隐私（probe 边界）

`time_and_route_limits`：`intake 600s` / 单连接 `absolute 10s` / probe `120s` 软检查点；**真实 live launcher 必须由验收控制器另施加 900 s 外层墙钟上限**（属验收控制器，非 SDK 隐式后台任务）。`response_limit`：保留 payload ≤ 1 MiB，**非**实际网络读取字节数。`privacy_sentinel_and_input_cleanup=true`；`known_field_types` 为固定 13 字段 type-only 白名单。清理为 `finally` 引用清除，**不宣称安全内存擦除**。

## 保留的失败回归（不作废）

- **测试夹具**：修正了不存在的 API/fixture 名、错误认证 envelope、错误 expected retry/close、缺实际 transport observer、错误 slow-drip 与 HTML 解码；对照冻结 SDK 契约修正，旧测试函数/场景保留并强化断言。
- **probe 实现真实 bug**：早期 oversize request-line 错误在初始化前引用 `requestline`；已改为在 early errors 前初始化 handler state，现有测试要求 HTTP 414/固定文本/不回显（回执 `SP156-CoreDeadlineUnit-execution.json`）。

## 验收结论边界

- 四条 **OFFLINE** required case（`5341`/`1375`/`1377`/`5342`）均 `recorded_evidence` + `execution_outcome=passed`，source-pinned 至 `b251b11`。
- 四条 **LIVE** required case 仍 `awaiting_resource_confirmation`/`awaiting_authorization`，**未执行**、无真实云证据。
- scenario `ACC-CN-VOD-RESOURCE-DISCOVERY-001` 仅 `offline_verifier_safety_and_sdk_regressions=met`（离线安全判据），其余 `pending`；真实执行需等当前提交 CI + 用户本地输入 + 900 s 外层控制器。
- cohort `admission_state=implemented_offline_acceptance_passed_live_pending`；全 scope `all_semantics_dispositioned`/`required_acceptance_complete`/`product_complete` 均 `false`。**不能写 Goal 完成、官方全部覆盖或实测完成**。
- `not_completed`：真实国内云探测、`cloud_recording_v1` live、海外设备 live、平台消息 live、完整产品 release、PyPI 上传均未完成。
