# DG006 交付与验收

状态：accepted（本子任务H01–H06全部交付）。未结束整体Goal，未提交git，未修改src、其它tests或产品文档。

交付：

- tests/test_sdk_diagnostic_hardening.py：H01–H03；隐私投影、内部普通Exception单故障隔离、上下文并发。
- tests/test_sdk_diagnostic_behaviour.py：H04–H06；重试边界、启用/关闭行为对照、异常身份与链、取消与真实资源清理。

完整验收映射见acceptance-map.md；交付文件及验收记录写入时观察到的核心源码SHA256见acceptance-manifest.json；SDK哈希不是单独捕获的测试启动快照，测试期间源码稳定由主线程明确保持。

执行方固定Claude Code via Magpie，devin/swe-2，effort=max。7轮候选/修订全部核验observed_models=[devin/swe-2]、tools=[]、tool_calls=0；Codex仅派单、审查、机械提取/替换与结果验收，未直接编写测试。原DG006拒绝候选未应用，其自测与工具声明不构成证据。

真实执行结果：

- `.venv/bin/python -m ruff format tests/test_sdk_diagnostic_hardening.py tests/test_sdk_diagnostic_behaviour.py`：`2 files reformatted`。
- `.venv/bin/python -m ruff check tests/test_sdk_diagnostic_hardening.py tests/test_sdk_diagnostic_behaviour.py`：`All checks passed!`。
- `.venv/bin/python -m pytest -q tests/test_sdk_diagnostic_hardening.py tests/test_sdk_diagnostic_behaviour.py`：`62 passed in 0.48s`。

三条命令均由claude_test_runner逐条执行；model匹配、仅一个Bash调用、命令精确匹配、单一关联tool_result、is_error=false。证据分别位于.claude/coordination/DG006-{format,lint,tests}-execution.json。没有以模型最终自述替代tool_result。

边界：本次pytest使用DG007后的稳定SDK源码，发生在主线程DG009控制异常传播修订应用之前。H02注入_Fault继承普通Exception，控制异常来自body；不要求吞掉记录器内部控制BaseException。后续DG009及整库集成验证由主线程统一完成。

本轮未发现新的SDK实现缺陷；静态审查发现的候选测试问题已全部经Claude候选修订，未删除或放宽H01–H06断言。
