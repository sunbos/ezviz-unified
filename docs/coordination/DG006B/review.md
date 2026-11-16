# DG006B 候选只读审查

状态：needs_revision。已核验devin/swe-2、effort=max、tools=[]、tool_calls=0；完整单文件候选760行，AST可解析。尚未写tests或执行。

主体满足H04：同步异步factory AuthenticationError位于retry边界外，第二次attempt/第二次provider失败，首次10002 failed与重试顺序。H05比较完整get_token/factory/http/invalidate序列、wire method/path/body/headers与返回字段；traceback函数链未忽略diagnostic帧。H06覆盖transport收到的取消对象身份、真实aclose门控、exactly once与close/cleanup事件。

必须修订：

- H05目前cause/context断言仅None，无法证明已有非空链未被清除；普通transport场景只验证新转换出的TransportError类型，未验证原异常对象穿过transport边界的identity。增加/调整合成异常含既有cause/context/suppress_context，通过provider和MockTransport handler抛出，sync/async enabled/disabled对照，断言每次is原对象、cause/context身份及函数链不变。保留现有HTTPX网络错误转换测试，不把预期转换当identity错误。
- H06失败路径必须finally取消/有界等待遗留request或waiter，释放close gate并有界等待client.aclose，防止等待事件超时或断言失败后留下挂起任务/资源。不要用清理异常掩盖原测试断言，也不弱化业务事件顺序断言。
