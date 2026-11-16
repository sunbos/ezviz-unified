# DG001S：简化分类入口与阶段枚举接入

Claude Code via Magpie，devin/swe-2 effort=max，零工具候选。唯一文件 src/ezviz_unified/diagnostic_report.py；仅输出JSON围栏 {"edits":[{"path":"src/ezviz_unified/diagnostic_report.py","old":"唯一完整片段","new":"替换"}],"notes":[]}，不重新输出整个文件，不执行/自测声明。

现已应用DG001R候选，审查确认已修正常规属性读取白名单、未知码other、api类别、外层had_child和严格类型。但同类恶意getter风险在isinstance路径仍存在：未知异常可覆写__class__属性，isinstance(exc, KnownType)对不匹配类型会访问它。连续复核后路线调整：_classify从type(exc)开始，全部根据真实类型身份或可信issubclass(type(exc),固定内建异常基类)判断，绝不对未知实例使用isinstance，未知SDK/ValueError子类不读取属性，不能执行自定义metaclass __eq__/__hash__。普通具体SDK类型用is逐项判断，取消/中断可使用对真实type的issubclass以保持子类语义。

新增批准kind="transport_setup"加入_KINDS；它与authentication/attempt均不归validation，不新增public字段或配置。这个scope在Client原factory/origin/注入处，attempt仅transport.request。

Scope enter恢复：如果_push_scope已改变context然后异常，当前__enter__ except直接return留下假parent。失败路径在不覆盖SDK错误前提下恢复_enter之前的_parent_scope（根为None），记错误；不让下一次call parent指向未开始scope。_pop_scope失败现有restore保留。不要新增持久异常对象、任务或无界缓存。

文档不要称mutable _Scope对象immutable；说明ContextVar每上下文的当前scope引用，parent only had_child单向标记，无共享可变list/dict栈即可。余下逻辑保持，禁止再扩大重构。验收后续加入恶意__class__/property计数为0及enter/exit故障测试。
