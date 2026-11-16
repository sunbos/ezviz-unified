# DG006 全范围验收映射

状态：accepted。H01–H06在两份新文件的定向运行中合计62 passed；真实tool_result与受测源码边界见acceptance.md。

| 原需求 | 唯一目标文件 | 候选中的证明场景 |
| --- | --- | --- |
| H01 | test_sdk_diagnostic_hardening.py | Ezviz/plain/ValueError/TypeError恶意子类，provider与capture两条路线；__class__与字段访问计数；str/int子类hash/eq/str/repr；Operation.name私有受控边界；原异常identity及recording_errors=0 |
| H02 | test_sdk_diagnostic_hardening.py | _Scope.__init__、_operation_name、_append、push后抛错、pop抛错；真实请求成功与body RuntimeError/CancelledError/KeyboardInterrupt/SystemExit；counter及恢复后root |
| H03 | test_sdk_diagnostic_hardening.py | active capture内两task交错；跨recorder；to_thread继承；多线程并发序号；修改事件dict后的快照独立；有界失败路径 |
| H04 | test_sdk_diagnostic_behaviour.py | sync/async factory AuthenticationError不进入重试；首次10002 failed；第二次业务及provider失败；完整get/invalidate/http次序与次数 |
| H05 | test_sdk_diagnostic_behaviour.py | enabled/disabled请求method/path/body/headers、完整事件外部调用序列与返回字段对照；provider和transport异常identity、函数traceback链、已有cause/context/suppress_context |
| H06 | test_sdk_diagnostic_behaviour.py | attempt内真实task取消对象identity；实际transport.aclose门控；释放前无cleanup成功、释放后exactly once；close waiter cancelled与cleanup succeeded分开 |

DG009最新边界：观测器内部普通Exception fail-open；新到达的控制BaseException恢复ContextVar后传播。H02注入的_Fault继承Exception，控制异常来自业务body，未要求吞掉内部控制异常，故任务语义保持。
