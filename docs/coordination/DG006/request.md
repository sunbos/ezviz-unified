# DG006：诊断隐私与行为一致性关键验收测试

输入：b2b6dca基线+已应用DG001S/DG002R/DG003S。初轮388 tests+mypy已由Claude真实Bash通过，不需要重复这些测试。本任务唯一新增 tests/test_sdk_diagnostic_hardening.py。固定Claude Code via Magpie devin/swe-2 effort=max；零工具纯候选，输出单个Python围栏完整该测试文件（尽量250–350行参数化），不修改实现/已有测试，不执行/不声称通过。

基于下面实际实现编写可运行pytest，不猜内部名字。每项保护真实要求，不写实现镜像。全部断网MockTransport、合成sentinel，不读环境。不能复用tests模块导入（非包）；可用精简本文件fixture。

必须覆盖：
H01 恶意异常子类（EzvizError及普通Exception）覆写__class__、code/http_status/reason property，计数。直接通过provider抛出及capture应用：报告无sentinel/类名，getter调用0，recording_errors=0，原异常identity保留。str/int子类覆盖hash/eq/str/repr作为真实SDK异常的字段值，先构造正常异常再赋字段避开SDK原构造；投影不得调用覆写，unknown归other。Operation.name同类值在内部 _diagnostic_scope受控对象边界上测试（SDK业务自身会读name，不能把原有读取算成诊断泄漏）。
H02 内部单点故障，patch实际_scope创建(_Scope.__init__)、_operation_name投影、DiagnosticRecorder._append、_push_scope先调用原方法再raise、_pop_scope先raise触发fallback。每种场景用捕获scope或真实client请求，验证业务成功返回/原异常身份（参数化正常RuntimeError、CancelledError、KeyboardInterrupt/SystemExit几个代表），recording_errors>0，无吞异常。退出后移除patch，下一root scope parent None。只验证单故障，不人为破坏计数器锁或制造不可恢复的多故障链。用有界join/事件避免测试永远挂起。
H03 同一capture中create_task两子任务交错（事件gate），兄弟scope不互为parent；A.capture里B.client父级不能指向A；asyncio.to_thread继承capture按对应recorder正确关联；线程同时写入序号唯一/快照独立。
H04 同步/异步factory抛AuthenticationError，原异常identity、无invalidate、无retry、无attempt(transport_setup failed可见)，保护旧try边界。第二次业务attempt失败与第二次provider失败：只一次retry，失败分类真实，实际请求数2或1。
H05 同步/异步启用/关闭recorder运行相同MockTransport脚本（先10002后成功），比较get_token/invalidate/HTTP的完整序列、method/path/body/headers，以及返回字段和请求次数；另provider/transport异常traceback函数链（忽略测试runner函数与lineno）和__cause__/__context__不能被诊断改变，异常object保持。
H06 async attempt中CancelledError保持identity和终态；aclose waiter取消时实际transport.aclose受gate保护，释放前无cleanup成功、释放后实际close exactly once、cleanup成功且close cancelled。

测试文件不需要覆盖所有排列，选代表组合验证上述根因。只增加独立测试，不对既有断言妥协。不得把创建但未await的task错误当capture可见；明确边界不要求SDK捕获外部任务。
