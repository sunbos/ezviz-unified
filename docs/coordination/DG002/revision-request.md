# DG002R：修复attempt诊断语义

固定devin/swe-2 effort=max，零工具。唯一候选仍为原DG002文件集合；本次只输出JSON围栏 {"edits":[{"path":".claude/coordination/DG002-apply.py","old":"完整旧函数原文或唯一常量","new":"替换文本"}],"notes":[]}。该脚本只是Codex本地应用载体，不提交为SDK文件。禁止输出其它修改/执行/自测声明。

原机械转换尚未应用；首轮审查发现 _execute_edits 生成的 attempt scope 包围原AuthenticationError handler，导致首次10002被处理成retry时scope记录succeeded，实际应failed。不要改变原SDK异常边界或重试。

重新实现_execute_edits：
- 同步和异步原来的provider获取/await/validate_token仍在authentication scope。
- 原 transport=self._transport_for(token)、bound=_inject(...) 两语句单独在kind="transport_setup" scope里，位置保持在原try之前；这个新kind是批准枚举，attempt可0。
- 在原try.body里用 kind="attempt",attempt=2 if retried else 1 包住原 transport.request return。handler仍在外侧，先前factory的AuthenticationError不进入handler。
- 原handler顺序不变，只在retried=True后插retry事件（与原try同级语义），第一次attempt必须failed然后scheduled然后第二次attempt。
- public anonymous分支将origin与factory前置语句包transport_setup，return transport.request包attempt=1，无authentication。
- 更新EXPECTED_WITH_COUNTS client.py，从原11调整到实际所需15；计算并说明新数。其它转换函数/覆盖列表不改。
- 可返回整个替换函数+常量精确edits；只有ast/json/pathlib，不执行网络/命令。
