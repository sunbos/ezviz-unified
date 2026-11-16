# DG009：保留观测器执行期间的进程控制语义

固定Claude Code via Magpie devin/swe-2 effort=max。纯文本零工具候选，提供完整目标源码；禁止调用任何工具，不执行/不声称测试通过。唯一 src/ezviz_unified/diagnostic_report.py、新增tests/test_sdk_diagnostic_control.py、docs/diagnostics.md。输出JSON围栏 {"files":{"tests/test_sdk_diagnostic_control.py":"完整测试"},"edits":[{"path":path,"old":unique_old,"new":replacement}],"notes":[]}，只精简修改，不重写整个模块。

Codex与独立审查确认必须修：内部fail-open目前except BaseException。真实Ctrl-C若在记录器_enter/_append/Lock.acquire期间到达，会被吞掉，随后SDK业务体继续执行，可能发写请求。这违反用户异常/取消语义不变；不依赖恶意篡改私有对象即可发生。

明确边界：
- 记录器自身普通Exception才隔离并尽力计数，不覆盖SDK业务返回值或正在传播的业务异常。
- 进程控制BaseException（KeyboardInterrupt、SystemExit、CancelledError及其它非Exception的BaseException）必须原样传播，不算成内部故障后继续业务。
- 不机械替换所有except：__enter__过程中可能已push ContextVar，包含emit和错误计数时也可能收到控制异常。必要时catch BaseException仅做恢复parent/token/entered状态后bare raise；普通Exception仍fail-open。新控制异常不能在恢复/计数内再次被except BaseException吞掉。
- __exit__仍通过参数记录业务体取消/中断，普通记录错误保留原业务异常，返回False。退出记录或pop/恢复期间收到新的进程控制，恢复上下文后让新控制按Python语义传播；不要为了保全报告而忽略停止指令。
- _read_field/_operation_name/_recorder_of/_region_scope_of/capture/_diagnostic_scope/_diagnostic_retry/_note_recording_error同样不能吞控制异常，只有普通Exception可隔离。未知异常实例不要isinstance，必要时用真实type+可信issubclass判断，避免__class__ getter问题复发。
- 控制异常可能使报告缺事件/终态，即使recording_errors为0也不证明完整，这是明确限制，文档补充。不新增字段、网络、后台任务、全局signal handler或HTTPX设置。

新测试约120–200行、同步/异步关键代表即可，提供实际ClientAPI避免猜测：
C1 普通RuntimeError故障仍fail-open，业务返回与原业务异常identity不变。
C2 scope构造、enter写入、push已改变context后失败、pop失败、exit写入这些内部位置收到KeyboardInterrupt/SystemExit/CancelledError时不被吞：入口中断业务体不得执行；出口中断可覆盖已结束body但必须传播原控制object；下一root parent None（嵌套scope则恢复原parent）。patch实际给出的内部方法；只单点故障，不制造无法恢复多故障组合。
C3 可加1个仅sync的真实signal.raise_signal(SIGINT)测试，在受控_append中触发并pytest.raises KeyboardInterrupt，确保请求handler没被调用；不要改signal handler/启动Timer，不测试asyncio.run SIGINT转换细节。其余控制以直接抛原object确定性模拟已到达的控制异常。
C4 body自身的取消/中断仍记录并原样传播，无额外请求或任务。测试真实Client(anonymous=True,region=RegionConfig.china(),transport_factory=MockTransport)只计数不实际联网；Async同形。不传不存在的transport=。

主线程先应用新增测试观察red，再应用源修订。现有已通过测试不能删改；H硬化测试由另一会话处理普通RuntimeError故障，两者语义独立。
