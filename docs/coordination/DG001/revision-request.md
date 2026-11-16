# DG001R：首轮关键审查修订

固定devin/swe-2 effort=max，零工具候选；原基线b2b6dca + 现有DG001候选 + 随附更新设计。唯一文件src/ezviz_unified/diagnostic_report.py；registry已解析，禁止重复输出。只输出单个Python代码围栏完整文件，约350–500行，不输出JSON，不执行或声称测试通过。

首轮候选不满足以下验收，修订这些根因，不删减功能：
1. 缺少ApiError独立api类别；加入确切ApiError分类，已更新设计枚举。
2. _read_field对EzvizError子类执行getattr；这会触发用户property副作用。仅type(exc)恰为可信SDK错误类型时读取基础属性；未知子类仅unknown/application，无getter/str/repr/eq/hash；Operation同样仅type(operation) is Operation，否则other。recorder只支持type严格DiagnosticRecorder。
3. code未知值当前返回none；schema要求None为none，未知值other，已知10002/10031映射。http_status未知非None也other。先type严格内建再比较。
4. 外层call把provider/factory ValueError重标validation。每scope跟踪是否进入子scope（无需异常对象/身份），call/inspection仅无子scope时把ValueError/TypeError映射validation；有子scope则unknown；application为application；authentication/attempt为unknown。child失败被抓住后再出现新异常不能沿用旧分类；本策略无异常传播缓存。保留ContextVar按recorder隔离、不可变栈/不共享可变list/dict栈，嵌套task不串parent。
5. _Scope继承AbstractContextManager需[None]以符合mypy strict；__exit__始终False用Literal[False]。所有严格类型检查完整。
6. 内部失败隔离现在只覆盖部分enter/exit和append。_diagnostic_scope/_diagnostic_retry构造及投影、capture scope创建也不能把诊断错误覆盖SDK调用；恢复parent要有明确fallback，不留下伪parent。不要在yield外宽泛捕获调用方异常或return成功。不新增callback、自定义sink、事件注入。
7. 保留固定15字段、无raw引用、无I/O、无新task；快照独立、容量及线程安全。不要添加测试/其他文件，测试方独立。

验收由后续受限Bash执行pytest/mypy；只交付候选。
