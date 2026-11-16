# DG006H 候选只读审查

状态：needs_revision。已核验 devin/swe-2、effort=max、tools=[]、tool_calls=0；完整单文件候选650行，AST可解析。尚未应用到tests、尚未执行测试。

必须修订：

- H01明确要求__class__访问计数为零。当前_HostileMixin特意不统计__class__，注释称except匹配会读取，无法证明该隐私边界。应统计并断言零；测试provider自身用真实type检查异常，避免测试fixture触碰伪装属性。
- 所有已创建Client/AsyncClient须在finally关闭，并避免关闭事件混入针对特定业务事件数量的断言；先保留独立快照再清理或明确过滤close/cleanup。
- H03并发线程使用有界join但非daemon；若被测锁回归死锁，残留非daemon线程仍可能阻止进程退出。使用daemon线程，并保留存活失败断言。

其余H01-H03主体覆盖具备：字段类型投影、Operation.name受控边界、五个单故障接缝、原异常身份、fault counter、恢复后root、同capture兄弟task、跨recorder/to_thread、并发序号及事件dict独立快照。
