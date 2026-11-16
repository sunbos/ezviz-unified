# DG005S：离线表单测试与显式文件导出

固定devin/swe-2 effort=max，零工具候选。唯一文件 tests/test_live_diagnostics.py、tools/live/verify_diagnostics.py、tools/live/README.md。输出简短JSON围栏 {"edits":[{"path":path,"old":unique_old,"new":replacement}],"notes":[]}；不全文件输出，不执行/声称通过，不改conftest/pytest策略。

真实pytest结果：66 passed，2个serve tests因 tests/conftest.py 默认禁网 RuntimeError("Network access is disabled in offline tests")失败，恰为test_serve_consumes_single_post_and_ignores_posted_overrides和test_serve_rejects_oversized_post_before_verify。不是守卫功能失败，不允许解禁socket、加live标记或跳过。

修订测试路线：不使用真实HTTPServer线程/本地socket连接。monkeypatch verify_diagnostics.HTTPServer 为轻量fake，只捕获构造传入的Handler类并提供server_port固定整数；调用真实_make_server得到真实闭包路由/Origin/Host逻辑。对每次请求构造Handler实例（object.__new__，不触发BaseHTTPRequestHandler网络constructor），设置path/headers、rfile=io.BytesIO(body)、wfile=io.BytesIO，替换send_response/send_header/end_headers为捕获函数，然后直接调用do_GET/do_POST。保留现有全部断言：密码字段、无region可改模式、single post消费、第二次409、wrong origin403、wrong host404、输出JSON、超限400且verify未调用。新增断言响应Cache-Control no-store和CSP；request体用urlencode生成仅合成值。可以把新helper集中约50–80行，不另造HTTP框架。移除不需要的threading/_stop，不运行server线程。其余66个通过的测试不改。

另一个小的明确默认边界：main中--output不要默认目录。参数default None；--serve时必须显式--output，否则parser.error固定消息；stdin模式仍只返回JSON不写文件。这使本工具也不会隐式选择文件路径。README加一句serve必须显式--output且保存的是报告，不是自动日志。追加1个纯CLI测试，没有--output时不调用serve且退出2，无文件创建；其他CLI行为不变。主线程真实验收将显式传--output。
