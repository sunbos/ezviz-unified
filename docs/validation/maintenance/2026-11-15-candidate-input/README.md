# 候选输入到模型判断的联通演练

本轮使用本地2026-11-14官方归档作为before，H008已标明synthetic的副本作为after。协调方在模型调用前逐字确认：5230页面仅增加指定的尾部HTML注释，接口正文未变；没有声称官网发生真实更新。

`harness.candidate_input`重新执行完整review基线并核验来源，输出52298字节，保留`domestic:5230`、`cn.storage.spaces`和两个SDK上下文模块（storage.py、storage_models.py）。全部177项契约完成1448次离线执行。完整输入包含官方原文，保留在本地，不提交到仓库；[输入receipt](input-receipt.json)记录哈希、范围和基线。

独立零工具会话请求devin/swe-2、effort=max，输入为固定维护提示、该数据包和[案例](cases.json)，未传入[预期答案](expectations.json)。[原始回答](answers.json)为no_contract_change、无变更建议；[评分](evaluation.json)与预期一致，[模型记录](model-run.json)保留接口报告的身份和用量。未产生或应用补丁，未调用萤石业务API，未授予发布许可。

## 证据限制

这是流程联通演练，不是模型能力基准：只有一个合成无变化案例，case_id还含COMMENT_ONLY提示，不能据此比较模型能力或声称会正确修复真实协议变化。后续补丁级评测需要中性案例ID、实际参数变化对照和独立回归。

案例里的正文行只引用本地输入包及对应hash，单独的cases.json不包含完整原文。重现模型输入需恢复对应快照与数据包；评分器只检查结构化答案是否符合已固定预期，不自行证明网页语义。
