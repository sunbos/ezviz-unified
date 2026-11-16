# DG008：CLI异常最后边界与文档精度

固定devin/swe-2 effort=max，零工具候选。唯一可写 examples/diagnostics_sync.py、examples/diagnostics_async.py、tests/test_examples_diagnostics.py、docs/diagnostics.md。输出单个JSON围栏 {"edits":[{"path":path,"old":unique_old,"new":replacement}],"notes":[]}；不全文件输出，不改SDK/其它tests，不执行或声称测试通过。

当前28条边界+示例tests与mypy通过，但Codex只读审查发现剩余CLI最后边界与文档承诺不符：
1. sync run_cli漏asyncio.CancelledError；两种run_cli都漏SystemExit('私密文本')和未知BaseException，会打印原文/traceback。只修CLI最外层：KeyboardInterrupt/asyncio.CancelledError固定提示+130；其它BaseException固定_FAILURE_MESSAGE+1。main仍原样传播未知异常，capture/SDK原异常语义不动；CLI明确非0退出不是把业务异常吞成成功。sync按需import asyncio。
2. 测试sync/async run_cli参数化上述四种异常，mock main让其抛特定原异常；期待固定退出码和固定stderr，sentinel文本/Traceback不出现；保持现有main未知RuntimeError传播测试。不要删已通过断言。
3. docs第76行capture内未await/脱离的任务不产事件过强。准确区分：从未调度或等待的coroutine方法体不执行；capture不收集脱离其退出链的应用异常；任务内启用了recorder的SDK调用仍可产生事件。第83行'到await时'改'被等待或调度运行时'。
4. ID表述精确：只有sequence随事件递增；span_id分配时递增但started/终态复用；parent_span_id引用父ID或null，可重复，不按事件顺序递增。冻结计划已同样修正。
5. 明确RegionConfig未知子类region=unknown，不额外读getter；未知ValueError/TypeError子类归unknown/application，只有确切内建类型在未进入子scope的call/inspection才validation。

不要扩功能/加参数/日志；保持stdout仅报告JSON，SDK默认关闭与本地安全约束不变。
