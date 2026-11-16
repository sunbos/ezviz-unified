# DG004R：示例输出与最新schema对齐

固定devin/swe-2 effort=max，零工具候选。输入为DG004尚未落盘的候选与批准设计修订。唯一范围仍DG004文件，输出精简JSON围栏 {"edits":[{"path":path,"old":unique_old,"new":replacement}],"notes":[]}；这些edits应用到DG004候选文本，随后由Codex落盘。不要再输出原README链接修改，保留原候选。

1. examples不能把_common.print_failure的任意业务码/错误字段写stderr作为诊断输出；只复用load_config，用固定无异常内容的stderr提示（例如 'SDK diagnostics example failed; inspect the JSON report.'）。stdout在成功失败都保持唯一JSON报告。
2. SDK与recorder.capture必须保持原异常语义。示例main目前未知Exception仍传播可保留；CLI边界新增私有run_cli()（sync调用main，async asyncio.run(main)），捕获Exception只输出固定stderr并返回1，KeyboardInterrupt/CancelledError返回130固定提示，不输出异常message/traceback。if __name__==main只sys.exit(run_cli())。已知ConfigError/EzvizError在main用固定提示返回1。main测试未知错误identity传播保留，再新增run_cli测试unknown error私密sentinel不在stdout/stderr、返回1。不吞SDK异常，CLI明确非0退出。
3. tests/test_examples_diagnostics.py _EVENT_KINDS加入transport_setup。已知失败/缺配置stderr断言改为固定消息，不再期待_common动态code/error_type。补CLI未知异常测试，仍保留main未知异常传播测试。
4. docs/diagnostics.md kind增transport_setup；transport_setup仅factory/origin/注入、attempt仅transport.request，started不证明HTTP发出，auth成功可能缓存。ValueError/TypeError只有未进入子scope的call/inspection才归validation，普通provider/factory传播外层不得重标。隐私明确只对确切SDK异常读取属性，未知子类不执行getter，真内建标量白名单。inspection返回类型是DeviceInspection（当前写错InspectionResult）；逐项诊断看子call终态，不推荐把public_summary()并入本严格报告。capture不捕获不等待/脱离作用域任务。
5. '离线测试覆盖...'当前未执行不能写已覆盖/通过；写'验收案例见冻结设计，最终执行证据见后续验收记录'，不伪造实机/CI结果。
6. 说明CLI失败固定stderr非0，stdout可重定向为单一JSON；同形同步/异步。
