# DG001

输入基线 b2b6dca；冻结设计与覆盖清单随输入。模型 devin/swe-2，effort=max。

实现有界DiagnosticRecorder和两个内部hook，严格按冻结接口。只输出JSON代码围栏 {"files":{"src/ezviz_unified/diagnostic_report.py":"完整文本","src/ezviz_unified/_diagnostic_registry.py":"完整文本"},"notes":[]}。静态registry完全对应覆盖JSON，运行时不得读文件。隐私不能只过滤键；拒绝或归类带副作用/恶意子类，不调用未知str/repr。recorder线程安全，ContextVar栈不串recorder/线程/async task。内部记录失败不能替换任何SDK异常；不要捕获调用方异常后返回成功；recording_errors可见。显式capture里未知异常归application，SDK的ValueError/TypeError仅call/inspection校验阶段归validation，authentication/attempt中不可谎称输入校验。保持实现聚焦，不加配置项。

唯一候选文件：
- `src/ezviz_unified/diagnostic_report.py`
- `src/ezviz_unified/_diagnostic_registry.py`

验收命令由后续受限Bash会话执行：`.venv/bin/python -m pytest -q tests/test_sdk_diagnostic_report.py tests/test_sdk_diagnostic_coverage.py`、`.venv/bin/python -m mypy src/ezviz_unified`、Ruff。验收条件以冻结计划 A01–A12 相应项为准。禁止修改其他文件、联网、读取配置或凭据。
