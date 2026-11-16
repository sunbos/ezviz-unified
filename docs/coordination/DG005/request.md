# DG005

输入版本：原SDK b2b6dca + 冻结设计 bceeee4；运行时接口以计划为准。模型 devin/swe-2、effort=max。

新增tools/live/verify_diagnostics.py和tests/test_live_diagnostics.py；tools/live/README.md只追加说明。这是显式一次本机只读验收工具，不是SDK自动服务。复用Client、DiagnosticRecorder、inspect_device固定五步；复用已有verify_devices.py中回环表单/公开结果模式，但以独立窄工具实现，不改旧工具。verify(config)只接 domestic app_key/app_secret，使用Client(auth_transport=guard,transport_factory=guard_factory,diagnostic_recorder=recorder,timeout=10)。同时guard认证与业务，唯一origin=https://open.ys7.com；唯一method/path为认证POST /api/lapp/token/get、设备列表POST /api/lapp/device/list、信息POST /api/lapp/device/info、通道POST /api/lapp/device/camera/list、状态POST /api/lapp/device/status/get、能力POST /api/lapp/device/capacity、版本POST /api/lapp/device/version/info。共享总HTTP预算7，每路由至多1；list严格pageStart0/pageSize1。通过list响应在内存选第一台已有授权普通设备后绑定serial；后续所有deviceSerial必须相同，状态/能力channelNo=1，不增加查询。超过预算/路径/错误配置以固定错误停止，不能实际发送；SDK本身重试不修改，只是本验收边界拒绝超限。只调用一次list再inspect_device，不取媒体/存储/托管/写操作。报告必须使用recorder.report，不使用DeviceInspection.public_summary作为新安全报告（其code允许任意数字不满足白名单）。单独receipt只含schema_version,scope固定bounded_domestic_diagnostic_v1, outcome passed/failed/not_verified, logical_calls整数<=6,http_requests整数<=7, read_only=true；空设备/缺serial not_verified。工具return {receipt,diagnostics}，不输出凭据/原始对象/自由错误。即使异常也finalize close，fatal只用固定status；不假称实测。CLI --serve --output明确请求写文件；stdin模式输入JSON输出安全JSON，错误固定stderr不traceback，stdout没有其它数据；不读取环境。回环serve随机path/端口，Origin、Host校验、no-store、Content-Length<=8192、密码字段；只消费一次有效POST(先原子标记再执行)，永远固定domestic模式不接受hidden覆盖，返回页面只显示严格安全报告；服务可保持显示但拒绝重复POST。不自动读浏览器或凭据。测试MockTransport/纯unit验证路径/预算/设备绑定/7次成功/空列表/失败只输出枚举/CLI无敏感异常/表单复用限制，命令pytest tests/test_live_diagnostics.py；不访问真实账号。输出尽量分文件、约300–400行工具，测试150–250行。

唯一可写候选文件：
- `tools/live/verify_diagnostics.py`
- `tests/test_live_diagnostics.py`
- `tools/live/README.md`

禁止读凭据/网络/自动化真实设备，禁止修改其他文件。候选不声称执行。验收：冻结计划对应案例、mypy/Ruff及指定pytest真实tool_result。

补充唯一可写文件 `.github/workflows/ci.yml`：仅将上述两个新Python文件加入两条现有Ruff检查命令。
