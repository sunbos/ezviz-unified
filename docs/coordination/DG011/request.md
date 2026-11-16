# DG011：保持SDK发行包独立并验证诊断导出

固定devin/swe-2 effort=max，纯文本零工具候选。完整所需文本在下方，不调用工具/执行/声称自测。唯一范围：将tests/test_sdk_diagnostic_coverage.py移动为tests/test_harness_diagnostic_coverage.py；修改tools/release/smoke_installed.py。输出JSON围栏 {"moves":[{"from":"tests/test_sdk_diagnostic_coverage.py","to":"tests/test_harness_diagnostic_coverage.py"}],"edits":[{"path":"tools/release/smoke_installed.py","old":unique_old,"new":replacement}],"notes":[]}。不改覆盖测试任何内容，不改MANIFEST/check_artifacts/pytest/CI，也不跳过任何断言。

架构审查：MANIFEST明确prune docs/harness/evals且sdist只带tests/test_sdk*.py；覆盖文件依赖harness.wire_eval、docs/diagnostics-v1-coverage.json、contracts/evals。它属于仓库契约覆盖审计，应该归test_harness前缀，保留132×2全部真实方法体调用与精确registry断言。完整repo pytest和CI已包含test_harness*.py，因此覆盖数量/断言不变；SDK源码包继续只携带独立SDK单测，不把harness或官方资料塞进运行时产品。移动是分类修正，不能作为省略安装验证的手段。

干净安装smoke只有71行：复用原有sync/async两次mock调用（devices.list与recordings.local），分别创建sdk.DiagnosticRecorder()传给各自Client(diagnostic_recorder=recorder)。关闭后验证公开report/to_json：schema_version=1,scope正确, recording_errors/dropped_events为0；两个固定入口各有succeeded终态且mode与region=overseas正确；to_json可json.loads且等于report；不含SMOKE_KEY/SMOKE_SECRET/SMOKE_TOKEN/DEVICE。仅输出原有成功marker字符串，不打印报告/请求/凭据，不添加业务调用。不修改原有源目录隔离断言和请求报文字段断言。可用约15行共享_assert_diagnostic_report(helper)避免重复。

后续由受限Claude执行build、check_artifacts、twine与verify_installations，真实工具结果才算完成。其它测试与源文件不动。
