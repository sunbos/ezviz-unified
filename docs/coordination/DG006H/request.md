# DG006H 独立候选任务

状态：accepted。候选来源、定向验证见submission.md与../DG006/acceptance.md。

唯一文件：`tests/test_sdk_diagnostic_hardening.py`。

范围仅原DG006 H01–H03：恶意异常/标量getter与精确投影，内部单点故障隔离，ContextVar跨task/recorder/thread关联和快照。必须完整满足下面原任务的H01-H03。H04-H06由另一个文件承担，不在本文件重复。目标约300–420行，以有意义的参数化减少重复。H01要核验getter/hash/eq/str/repr调用计数为零，不只依靠抛错。真实SDK错误构造后再赋恶意字段，避免把SDK原构造调用算成诊断读取。未知ValueError/TypeError子类也必须unknown/application。H02逐个patch真实私有接缝，一次只破坏一处；patch移除后新root parent None；保留同一异常对象，统计recording_errors。不要patch业务创建之前导致测试fixture本身失败；必要时先造好client。H03必须在同一active capture中创建两个交错任务，而非两个空context任务；两个不同recorder、to_thread、线程同时写入及事件dict快照独立都覆盖。

输入：b2b6dca基线+当前已落盘DG001/DG002；源码快照SHA256见input-manifest.json。DG007已修复未知ValueError/TypeError子类精确分类与RegionConfig精确类型投影；测试应遵守冻结语义，不迎合旧缺陷。

候选命令：`python3 .claude/coordination/tool_free_task_runner.py DG006H`。
验收命令（由受限Claude runner执行）：`.venv/bin/python -m pytest -q tests/test_sdk_diagnostic_hardening.py`。
禁止修改SDK、其它tests、运行工具或声称测试已通过。
