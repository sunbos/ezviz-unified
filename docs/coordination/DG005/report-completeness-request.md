# DG005T：实机验收必须证明诊断事件完整

固定devin/swe-2 effort=max，零工具候选。唯一tools/live/verify_diagnostics.py、tests/test_live_diagnostics.py；输出简短JSON {"edits":[{"path":path,"old":unique_old,"new":replacement}],"notes":[]}。只增加小的完整性判断和参数化回归，不改请求、guard、server或SDK，不执行/声称通过。

已审查通过正常7路由与字段白名单，现有逻辑却会在 report={} 或 {recording_errors:0,dropped_events:0,events:[]} 时仍判passed。这会漏掉诊断埋点未执行的实机验收失败，不满足可信输入。保持返回报告/失败诊断现有行为，仅收紧passed门槛：
- report为dict，schema_version是确切int且等于1，scope是确切str且local_sdk_diagnostics；recording_errors与dropped_events存在且是确切int0，bool不接受；events为list且每项为dict。
- 从kind==call的终态事件中，只按entrypoint匹配_LOGICAL_STEPS（六个固定facade），要求每个入口有且只有一个succeeded终态，没有重复或failed/cancelled/interrupted；不能按operation匹配client.request，不clamp计数。
- 此有界成功流程预算total必须恰为7（认证1+业务6），否则降failed。仍保留empty/missing serial not_verified，真实SDK步骤失败failed，不把缺报告场景当成功。
- 只为这条本机profile检查诊断是否执行，不做通用远程报告解析框架。不要修改SDK采集器/回报schema或新增endpoint，不把任意模型结论当证明。

追加参数化mock回归：空dict、空events、缺counter、bool counter、错误schema或scope、非list/含非dict events、漏一个step、duplicate终态、failed终态都必须failed；原完整report=passed。用已给的_recording_handler/_run_verify等现有fixtures与monkeypatch recorder.report，仅合成数据。新增case不得用放宽已有guard/禁网策略通过。构造新report尽量从original(self)的真实报告变更，保留字段不凭空伪造成功。

主线程先应用测试edits观察red，再应用工具edits观察green；给测试命名 test_incomplete_diagnostic_report_prevents_pass（参数化）便于定向执行。生产helper建议不超过35行，复用_LOGICAL_STEPS/_TERMINAL_OUTCOMES和_count_logical_calls。
