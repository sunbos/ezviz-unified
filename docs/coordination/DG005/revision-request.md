# DG005R：联调守卫与真实计数修订

固定devin/swe-2 effort=max，纯文本零工具。原DG005尚未应用。唯一候选仍tools/live/verify_diagnostics.py、tests/test_live_diagnostics.py；README/CI原edits已保留不用重新生成。输出单个有效JSON围栏 {"files":{"tools/live/verify_diagnostics.py":"完整修订工具","tests/test_live_diagnostics.py":"完整修订测试"},"notes":[]}。保证files对象结束为}，上一轮外层多了]但单个文件字符串和edits完整已被独立解析，不要重复格式问题。不要执行/声称通过。

Codex关键审查证据：
1. 实际本地httpx.URL("https://open.ys7.com").port 是 None；当前_ORIGIN_PARTS要求443会拒绝所有正常请求。以(url.port or 443)规范化默认HTTPS端口，仍拒绝其它端口、非HTTPS、未知host/userinfo/query/fragment。factory同规则。新增有效默认端口和显式443通过、8443等拒绝测试。
2. 守卫只检查method/path和计数，没有落实派单的pageStart/pageSize、设备绑定和channel边界。新增共享budget中的选定设备绑定（只内存，不输出，禁止hash），verify拿到第一页首台serial后显式绑定。_GuardedTransport在admit之前检查application/x-www-form-urlencoded请求的解析字段：auth只允许appKey/appSecret各单值非空；list只accessToken/pageStart/pageSize且0/1；五个设备路由都必须同一个绑定deviceSerial，info/channel/version只有accessToken+deviceSerial，status/capacity还必须channelNo=1。未知/重复/缺失字段、未绑定、错误serial/channel、分页2或limit修改都拒绝且delegate零调用。parse_qs只用于本机内存，不记录值。合理body长度8192和UTF8异常固定拒绝。所有keys精确检查值只需必要协议边界，不把错误参数回显。
3. _count_logical_calls把facade与client.request按operation都算一遍，再min(6)掩盖重复计数。这会把auth失败的1次逻辑调用报成2。仅按kind=call且entrypoint在_LOGICAL_STEPS的终态计数；去掉operation fallback和计数min，固定流程真实最多6。http_requests也去min，budget已经限制7。新增auth失败logical_calls=1、列表空/缺serial=1、完整=6、同设备绑定错误拒绝测试。
4. 如果report为None或recording_errors/dropped_events非0，receipt不能passed；应failed且不虚构完整报告。业务失败依旧outcome failed，不把partial success当passed。
5. 补本工具main实际业务failed/not_verified的退出码非0；只有receipt.outcome passed返回0。参数/JSON/配置错误固定stderr无traceback，捕获CLI层异常时不要输出自由文本。serve用户Ctrl-C可正常关闭无traceback。不改SDK异常语义，不捕获改写SDK自身返回。
6. 保持原7路由/国内/一次表单提交/Origin+Host/no-store/最大8192凭据表单/固定region，原HTTP重试策略不改。form忽略hiddenregion是固定国内模式可保留。

测试现有真实完整流程在修复port后应该才能通过；新增越界字段是实际guard测试，不只证明正常SDK恰好发送合法参数。全部mock离线。未知异常文本不得落到任何公开receipt/diagnostics，不输出serial/token/原始body。
