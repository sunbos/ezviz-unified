# 显式只读联调

`verify_devices.py` 使用真实SDK，仅认证、读取一页设备（page_size=1），并查询第一台设备的信息、通道及状态。不执行PTZ、配置变更、删除、格式化或视频播放。

```sh
.venv/bin/python tools/live/verify_devices.py --serve --output docs/validation/live
```

命令输出一个临时 `127.0.0.1` URL，浏览器表单向本机进程提交已经授权使用的AppKey/Secret。仅监听回环地址，使用随机路径、同源检查与no-store；凭据不写文件或日志，不传给协作模型。完成后按Ctrl-C停止服务器。海外认证入口默认来自官方文档，也可按已核实的区域入口指定。

国内可显式增加 `--include-managed`，额外读取一页托管列表，仅记录结果数量，不将托管条目拿去自动调用其他设备接口。默认关闭此选项，避免把两个资源范围混为一谈。

`--include-diagnostics`可追加设备能力与版本查询；只有基础信息明确报告在线且声明本地存储能力时，才查询存储状态和容量（容量查询仅国内）。不自动升级、格式化或更改任何设置，不能与`--protocol-only`同时使用。业务传输受固定method/path及当前区域origin白名单约束。

本地临时页面可显示白名单中的状态与能力标记，供当前验收判断；写入文件和stdin模式输出都只保留公开投影：操作结果、错误码、区域origin和有界数量。在线状态、能力集、升级提示和存储细节不写入公开记录。完整响应、序列号、名称、IP和凭据始终不输出。

`--inspection`启用显式检查模式：读取一页设备（page_size=1）后，仅由SDK只读检查助手对第一台设备执行一次固定的五个逻辑调用（信息、通道、状态、能力、版本，固定通道1），不重复调用基础三项，也不查询存储、媒体或执行任何写操作。空账户或首台设备序列号缺失时仅记录not_verified，不断言账户设备数量。逐步结果取自助手的公开摘要，只保留操作名、passed/failed、脱敏error_type、纯数字code与整数http_status；助手级致命错误统一记入inspect_device阶段。此模式公开记录的validation_scope为bounded_readonly_inspection_policy_check，以区别于基础的请求与响应结构检查。与--include-managed、--protocol-only、--include-diagnostics互斥；serve表单通过隐藏inspection字段开启。该模式已于UTC 2026-11-14 23:12:31（Asia/Shanghai 2026-11-15 07:12:31）在国内账号完成一次真实执行：读取一页设备（page_size=1返回1条）后，对首台设备的固定五项只读逻辑调用均返回策略内成功（通道1，单次同步样本；记录见`docs/validation/live/2026-11-15-domestic-inspection/`，目录按Asia/Shanghai本地日期命名），未做全字段结构校验或健康推断。海外与其他场景仍未实测，其余覆盖仍依赖MockTransport测试。

`--protocol-only`只查询文档652的公开型号/版本示例，跳过设备列表和详情，与--include-managed互斥。它只能验证该次协议查询结果，不能证明真实硬件支持；HTTP失败保留为失败。

无浏览器时可以通过stdin传入JSON（region、app_key、app_secret，可选auth_base_url，可选inspection等模式键，组合规则与CLI一致）；避免把密钥写入shell历史、脚本或仓库。输出只含操作状态、数字错误码、HTTP状态和区域origin，不含完整响应、设备标识或密钥。

空账户只证明认证和设备列表可调用，其他设备方法标记not_verified。默认与诊断档案中的passed表示该次请求成功且data结构符合基本预期；--inspection档案的passed仅表示只读检查助手对该次逻辑调用返回策略内ok，属检查策略结果而非逐字段结构校验。两类结果均不证明全部设备型号、字段语义或海外集群均支持。默认pytest不会执行此工具的真实网络入口。

`verify_diagnostics.py`是独立的窄口径一次性诊断验收工具：固定国内区域，先用一次`devices.list`（严格pageStart=0、pageSize=1）取第一台已授权普通设备，再复用`inspect_device`对它执行固定五个只读逻辑调用（信息、通道、状态、能力、版本，通道1）。不查询存储、媒体、托管，不执行任何写操作。

```sh
.venv/bin/python tools/live/verify_diagnostics.py --serve --output docs/validation/live
```

认证与业务共用唯一origin（`https://open.ys7.com`）、固定7条method/path白名单（token获取+六个只读设备路径）和共享HTTP预算：总计至多7次、每路由至多1次；越界或超限在发送前以固定错误停止，SDK自身重试不变，但本验收边界不放行第二次请求。回环表单只有AppKey/Secret两个密码字段（无区域或隐藏选项，domestic不可覆盖），POST需通过Origin与Host校验、正文不超过8192字节，且只消费一次（先标记后执行，之后一律拒绝）；页面只回显安全JSON。stdin模式仅接受`{"app_key": ..., "app_secret": ...}`（可加固定值"domestic"的region键），stdout只输出安全JSON，错误只以固定文本写stderr，不读环境变量、不打开浏览器、不读取任何凭据文件。`--serve`不提供默认输出目录，必须显式指定`--output`；其中写入的`domestic-diagnostic.json`是本次验收报告，不是自动累积的日志。

输出分两部分：`receipt`只有schema_version、固定scope `bounded_domestic_diagnostic_v1`、outcome（passed/failed/not_verified）、logical_calls（≤6）、http_requests（≤7）、read_only=true；`diagnostics`为`recorder.report()`的schema_version=1白名单快照，不含序列号、密钥、URL、异常消息等标识。空账户或首台设备缺序列号记not_verified；任何一步失败或边界拒绝记failed，不借`DeviceInspection.public_summary`充当安全报告（其code允许任意数字，不满足白名单），也不假称实测通过。即使中途异常也会close并产出报告。默认pytest不触发此工具的真实网络入口；`tests/test_live_diagnostics.py`只用MockTransport验证路径、预算、设备绑定、枚举输出与单POST约束。

`verify_diagnostics_async.py`是同一冻结范围（`docs/coordination/domestic-async-read/admission.json`，scope `domestic_async_device_inspection`）的异步版本：走`AsyncClient`与`inspect_device_async`真实异步路径（不复用同步verifier走线程池），同样固定国内`https://open.ys7.com`、固定7条method/path白名单、auth+业务共享至多7次HTTP且每路由至多1次、固定顺序、禁止redirect/代理/托管fallback/其他页或设备/媒体URL；业务侧仍是6个只读逻辑入口（`devices.list`严格pageStart=0/pageSize=1 → `devices.get`/`devices.channels`/`devices.status`/`diagnostics.capabilities`/`diagnostics.version`），单设备通道1，不查询存储、媒体、托管，不执行任何写操作与分页。注意状态与能力两个入口的入参字段名不同：`devices.status`用`channel=1`，`diagnostics.capabilities`用`channel=1`上线为表单`channelNo=1`。

```sh
# 方式一：只在终端显示结果（不落盘，推荐默认）
.venv/bin/python -m tools.live.verify_diagnostics_async --prompt

# 方式二：同时把报告写到一个尚不存在的新文件（独占创建，已存在或符号链接即拒绝）
.venv/bin/python -m tools.live.verify_diagnostics_async --prompt --output domestic-async-report.json
```

以上两种方式任选其一即可；不要为同一批凭据重复发起真实请求。运行只需三样：本仓库源码checkout并装好依赖的`.venv`、真实交互式TTY终端、已获国内只读授权的AppKey/AppSecret——沿用既有国内只读授权，执行前无需再申请，凭据由本人现场输入。账号内是否有可访问设备不影响运行：认证与首页列表都成功但首页为空时，全程恰好2次HTTP、不再发出任何后续请求，结果记not_verified（进程按预期非零退出）；拿到这个两步结果不要求账号里已有设备。只有首页上存在可访问的普通设备时，才会继续对它做五项只读检查，此时HTTP总计至多7次。无论走到哪一步，都不推断账号授权范围或硬件能力。

与同步版不同，本工具不提供serve/stdin模式，只支持`--prompt`交互：AppKey与AppSecret在用户自己的交互式TTY上分别以getpass隐藏输入（`GetPassWarning`视为错误），非TTY、回退可见输入、空值或缺值都在发起任何网络请求前直接中止；凭据不进入命令行、环境变量、文件、日志或模型对话。verify协程设90秒异步等待超时、单次HTTP 10秒；超时或取消仍走取消传播与实际`AsyncClient.aclose()`清理路径——90秒是网络等待上限，不是进程总时长的强制结束承诺。`--output`在网络开始之前以独占方式创建（mode 0600），只接受尚不存在的普通新文件（已存在或符号链接即拒绝）；verify一旦产出完整白名单回执——包括failed与not_verified——都照常写入并可安全保存，仅当回执未完整落盘（序列化拒绝、写/flush/close故障或运行中止）才清理半成品；清理前核对路径所指文件的设备号与 inode，仅在与本次创建的文件匹配时尝试移除；无法确认身份或文件权限预检失败时可能保留空文件。取消或失败的结果绝不允许报passed。输出报告只含白名单投影（outcome、http_requests≤7、logical_calls≤6、固定路由标签、diagnostics_intact、closed、boundary_rejected等），不含序列号、密钥、URL或异常原文；首页为空或首台设备缺序列号记not_verified，认证失效等场景不会触发第二次同路由请求（重试在delegate前拒绝并记为本次失败）。默认pytest不触发真实网络入口；`tests/test_live_diagnostics_async.py`用MockTransport覆盖六步异步调用、路由/预算/重复auth拒绝、取消传播与输出独占性。

当前状态：有界runner已实现，同步+异步边界的定向离线测试全部通过（MockTransport，无真实网络；逐项证据见[离线验收记录](../../docs/validation/2026-11-19-domestic-async-read/offline.json)）；真实`ACC-CN-ASYNC-READONLY-001`仍`pending_execution`——7次HTTP/6次业务/首页单设备通道1沿用既有国内只读授权范围、无需再授权，但尚缺新鲜凭据与真实回执，拿到真实回执前不得报passed；不记录任何真实设备/账号标识。
