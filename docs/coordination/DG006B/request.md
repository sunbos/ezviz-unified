# DG006B 独立候选任务

状态：accepted。候选来源、定向验证见submission.md与../DG006/acceptance.md。

唯一文件：`tests/test_sdk_diagnostic_behaviour.py`。

范围仅原DG006 H04–H06：factory AuthenticationError重试边界；第二次业务/第二次provider失败；同步异步enabled/disabled同脚本完整序列与wire数据对照；响应/异常身份与traceback/cause/context；attempt cancellation；aclose waiter取消与真实transport.close门控。必须完整满足下面原任务H04-H06。H01-H03由另一个文件承担，不在本文件重复。目标约250–400行。对照时必须比较get_token/invalidate/HTTP完整顺序和method/path/body/headers，不能只比较计数。provider每次client.request会get_token，不要意外用尽脚本。factory抛AuthenticationError不得invalidate/retry/attempt。实际transport.aclose必须gate保护且exactly once；释放前无cleanup succeeded，释放后真正close完成且close cancelled；不要把client.is_closed当资源完成证据。异常traceback比较函数名链时仅忽略本测试的驱动差异，不忽略diagnostic实现帧以掩盖重新raise。

输入：b2b6dca基线+当前已落盘DG001/DG002；源码快照SHA256见input-manifest.json。DG007已修复未知ValueError/TypeError子类精确分类与RegionConfig精确类型投影；测试应遵守冻结语义，不迎合旧缺陷。

候选命令：`python3 .claude/coordination/tool_free_task_runner.py DG006B`。
验收命令（由受限Claude runner执行）：`.venv/bin/python -m pytest -q tests/test_sdk_diagnostic_behaviour.py`。
禁止修改SDK、其它tests、运行工具或声称测试已通过。
