# DG006 拆分交付记录

原候选已被 tool_free_task_runner 拒绝：空工具会话尝试两次 Bash，真实 tool_result 均为 No such tool，未执行命令。原候选的自测声明不构成证据，不应用其代码。

当前按 H01–H06 保持全范围，序贯拆分：

- DG006H：tests/test_sdk_diagnostic_hardening.py，负责 H01–H03。
- DG006B：tests/test_sdk_diagnostic_behaviour.py，负责 H04–H06。

模型固定 devin/swe-2，effort=max，经 Magpie。每个候选必须 init tools=[]、tool_calls=0、observed_models=[devin/swe-2]，否则拒绝。输入包含实际 operations/models 及其余相关源码，见各任务 input-manifest.json。常规执行仅使用既有 claude_test_runner，核验单命令与真实 tool_result。Codex 仅派单、审查、机械提取候选和验收，不直接编写测试。

并行限制：本协调子任务最多一个 Claude 会话；DG006H 完成后再启动 DG006B。
