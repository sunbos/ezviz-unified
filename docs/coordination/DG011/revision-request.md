# DG011R：报告scope契约必须使用真实固定值

固定devin/swe-2 effort=max，零工具候选，仅文本处理，不调用工具/自测。唯一修改是尚未应用的DG011候选helper中的scope段落，返回单个Python围栏完整_assert_diagnostic_report函数即可；其余DG011候选内容保持，移动已经完成且字节哈希一致。

上次输入未给完整report契约，候选猜测scope为mapping并在notes要求调整。现明确：DiagnosticRecorder.report顶层只有schema_version=1、scope="local_sdk_diagnostics"（固定str！）、max_events=256、dropped_events=0、recording_errors=0、events=list。没有scope.modes/regions/entrypoints。events每条含kind/entrypoint/operation/mode/region/outcome等。不要猜别的字段。

请将helper改为assert report["scope"] == "local_sdk_diagnostics"，去掉scope mapping三项读取。mode/region/entrypoint继续在两个实际facade事件上精确断言（各一个succeeded），保持report/to_json相等、zero counters、四个合成敏感值不在payload，不输出报告。import json由原候选添加。下面给当前候选helper完整文本，不需要其它文件。
