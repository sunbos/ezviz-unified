# DG003

输入基线 b2b6dca；冻结设计与覆盖清单随输入。模型 devin/swe-2，effort=max。

独立编写两文件测试，未接实现候选。只输出JSON围栏 {"files":{"tests/test_sdk_diagnostic_report.py":"完整文本","tests/test_sdk_diagnostic_coverage.py":"完整文本"},"notes":[]}。以冻结接口测试A01–A09，尽量参数化sync/async。覆盖测试必须真实调用132×2方法体，可复用已有201个合成wire cases与harness.wire_eval的解码helpers建立DTO/region并将client.request替换为返回ApiResponse的无网络函数；逐项assert该entry started/succeeded，不仅检查源码装饰器。另外针对facade前校验、request前校验、unknown component、constructor/参数绑定capture/未await边界、恶意异常字符串属性、known/unknown operations、不输出任意数值码、重试实际序列、返回/异常identity、cancel、cleanup、跨task/线程parent、截断和快照。不要依赖尚未冻结的私有方法名或绕过约定接口；不要把全部ValueError都断言validation(phase也重要)。pytest默认断网，MockTransport验证真实序列。输出长度控制，每文件约200–350行可用参数化。

唯一候选文件：
- `tests/test_sdk_diagnostic_report.py`
- `tests/test_sdk_diagnostic_coverage.py`

验收命令由后续受限Bash会话执行：`.venv/bin/python -m pytest -q tests/test_sdk_diagnostic_report.py tests/test_sdk_diagnostic_coverage.py`、`.venv/bin/python -m mypy src/ezviz_unified`、Ruff。验收条件以冻结计划 A01–A12 相应项为准。禁止修改其他文件、联网、读取配置或凭据。
