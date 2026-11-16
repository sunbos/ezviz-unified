# DG006H 候选已应用

状态：submitted，等待Claude单命令定向测试；未声称通过。

来源链：DG006H完整候选 → DG006HR 11个精确替换 → DG006HR2单函数清理替换。三轮均devin/swe-2、effort=max、tools=[]、tool_calls=0，已核验对应metadata。Codex只审查和机械应用，无手写测试实现。

HR2替换按它收到的完整test_h03_concurrent_threads_unique_sequences函数范围应用，函数内old唯一匹配；不影响其它finally。最终AST可解析，应用文件tests/test_sdk_diagnostic_hardening.py。

H01–H03范围保留：恶意字段与__class__零访问、五类内部单故障与body控制异常、上下文恢复、跨task/recorder/to_thread、并发线程与独立快照。DG009内部控制异常传播变更不改变本文件_Fault(Exception)语义。

最终候选SHA256：cb631422d7d41f55126ec117aef4fd1403f57193da26b2e674a62871a3646b9a
