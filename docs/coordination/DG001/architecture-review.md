# Codex 架构审查补充（候选到达前）

初始派单后独立只读审查发现两项需要收紧的定义，已更新冻结计划；交付候选仍必须逐项核对，不将规划视为实现证据。

1. call/inspection 仅在没有进入子作用域时将 ValueError/TypeError 归 validation。provider/factory/HTTPX 异常在 authentication/attempt 及传播到外层时不能重标为 validation。应用 capture 归 application。
2. 132×2 覆盖必须确实进入方法体；Python 参数绑定失败不计为已覆盖。
3. 对未知异常子类不读取 code/reason/http_status 自定义属性；值白名单先检验 type 恰为内建类型，不调用未知 hash/eq/str/repr。
4. ContextVar 栈不可变，父级按 recorder 身份隔离；异步兄弟任务、跨 recorder 及 to_thread 不串线。
5. 内部故障隔离涵盖 scope 创建/进入/投影/退出/上下文恢复，不吞调用方异常、不重入死锁，不将记录器故障误报业务成功。
6. authentication 成功可能是缓存命中；attempt started 不证明已发 HTTP；capture 不捕获未等待后台任务；close waiter 取消不等于 cleanup 完成。

基线证据：b2b6dca 的全量离线测试由指定模型受限 Bash 执行，真实 tool_result 为 3808 passed, 11 subtests passed in 31.75s。main SHA 对应四个 Python CI check 均 completed/success。新诊断实现尚未验收。

7. ApiError 必须有独立 api 类别，不能与未知异常混为一类；authentication/permission/device 子类各自独立。该字段已补入schema枚举。
