# DG004

输入版本：原SDK b2b6dca + 冻结设计 bceeee4；运行时接口以计划为准。模型 devin/swe-2、effort=max。

编写同步/异步最小示例和用户文档，源码唯一examples/diagnostics_sync.py、examples/diagnostics_async.py，新docs/diagnostics.md，新tests/test_examples_diagnostics.py；现有README.md、README.zh-CN.md、docs/getting-started.md只精确edits。复用examples/_common.py环境配置与现有示例习惯，只读一页或指定设备inspect_device，不加写请求，不复制凭据值。示例显式构造recorder并在finally导出to_json到stdout；不要默认写文件，读者重定向是显式动作；不打印ApiResponse或异常message。以capture覆盖构造及应用异常但不要吞掉它们；CLI可返回固定退出码与固定提示无stacktrace。async普通with capture跨await。测试import无副作用、main被mock实际调用、不凭据日志、没有自动写文件。docs中文说明字段白名单、truncation和recording_errors、attempt不是HTTP计数、无时钟/标识、sync/async时序边界、constructor与绑定错误需要capture、inspection正常返回非健康或全子调用成功、close waiter取消不等于cleanup完成、未覆盖清单、报告非防篡改/非遥测/非RSI。真实验收尚未完成，不写完成声明。英文README最小链接和一句scope即可。验收命令pytest tests/test_examples_diagnostics.py。

唯一可写候选文件：
- `docs/diagnostics.md`
- `examples/diagnostics_sync.py`
- `examples/diagnostics_async.py`
- `tests/test_examples_diagnostics.py`
- `README.md`
- `README.zh-CN.md`
- `docs/getting-started.md`

禁止读凭据/网络/自动化真实设备，禁止修改其他文件。候选不声称执行。验收：冻结计划对应案例、mypy/Ruff及指定pytest真实tool_result。
