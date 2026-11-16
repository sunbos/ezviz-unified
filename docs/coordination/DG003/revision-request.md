# DG003S：独立测试审查修订

固定 devin/swe-2 effort=max，零工具，仅候选，不执行/不宣称通过。唯一文件 tests/test_sdk_diagnostic_report.py 与 tests/test_sdk_diagnostic_coverage.py。仅输出JSON代码围栏 {"edits":[{"path":file,"old":unique_old,"new":replacement}],"notes":[]}，不要输出全文件。

修订确定性测试错误与已批准schema变化，不能削弱断言：
1. devices.get("bad serial!!") 在既有SDK合法，前置校验测试改纯空白字符串。2. test_thread_parent_isolation有两次逻辑request但默认provider仅一个effect，给该test专用provider两枚_token，不改全局provider行为。3. 匿名海外api.dg003.test不在允许域，改 https://isgpopen.ezviz.com 仍MockTransport。
4. _FAMILIES加入api；分类参数化加入ApiError->api。_KINDS加入transport_setup。_assert_well_formed维持字段白名单。
5. code_category: None -> none, 10002/10031真正str/int映射，其余值other；http_status None->none，真正int100..599分桶，其余other。修改错误fixture期望，不能把other放宽成none或other。
6. 覆盖registry从subset变完全相等，ENTRIES唯一性132。删除多路径import fallback，直接from harness import wire_eval。
7. 每个132×2业务覆盖精确断言一个started+一个succeeded且同span、请求恰好一次且实际方法返回与stub ApiResponse相同identity；当前代码存放result并保存单个expected response即可，不改运行真实方法策略。
8. test_snapshot_detached_and_truncation增加修改snapshot.events[0]字段后原report不变，保护事件dict拷贝。
9. 重试首次失败增加authentication/invalid_token类别断言，保留原先首次failed与先后顺序断言。

本次仅这些精简修订，其余隐私/故障注入/对照测试下一独立小任务补齐，不在本次扩写1000行。输入为当前完整两个文件，设计变化以上述精确约定为准。
