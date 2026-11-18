# r16 cloud_capture（国内抽帧任务）离线实施计划

## 0. 范围、基线与边界
- 本写入域仅 5 个文件：`src/ezviz_unified/cloud_capture.py`、`tests/test_sdk_cloud_capture.py`、`tests/test_sdk_cloud_capture_behaviour.py`、`examples/cloud_capture.py`、`tests/test_example_cloud_capture.py`。
- 公共 Client 挂载、包导出、errors、诊断 allowlist、contracts/evals 登记由独立集成写入方负责；本域不修改这些文件，测试亦不依赖其已落地（不引用 `client.cloud_capture`）。
- `baseline_commit=95aa8f8e0040e86a53dc87adc92089a8d79ebc85`；`admission_sha256=5f6f658193a55760468d1dbb4f058bd097776a653b5a0d92fd709510d48464a6`（r16-capture-messaging-hubs-admission）。
- 公共 G1 与 case 已登记：8 SDKop + 3 integration，983/984 为复用；本计划无另行待登记项。
- 延用既有国内只读授权；所有新 LIVE 具体资源/边界未冻结，创建/消费不在授权内：交付全部为离线合成 HTTP 实现，不发起真实调用、不创建或消费云资源。
- 显式不做：DTO/任务句柄、轮询/退避、业务重放、SDK 去重、隐式资源发现或创建、服务端默认注入、时区或当前时钟推断、严格 start<end、timing 排除 frame_model=2、双存储选择器互斥、channel 默认 1、header/channel 胁迫、向 1389 移植 jt808。
- 共享不变量落实：不改变既有操作响应/异常/顺序/重试；新作用域包裹区域与校验（含 pre-auth 错误）；无自动采集/上传/文件日志/全局 HTTPX 修改/后台业务请求；诊断名仅扩展精确 allowlist；None 表示全部可选项省略。

## 1. `src/ezviz_unified/cloud_capture.py`

### 1.1 类与构造
- 新增 `CloudCapture`（同步）与 `AsyncCloudCapture`（异步），facade 名 `cloud_capture`。
- 构造仅接收一个既有真实 Client（异步类为其异步等价物）并保存引用：不新建 Client、不读环境变量、无模块级可变状态；与既有国内外观同构，复用同一 Operation 执行管线。
- 导入复用既有依赖：`datetime`、`collections.abc.Sequence`、`typing.Literal`、既有 `ApiResponse` 与 Operation/诊断设施；无新增第三方依赖。

### 1.2 Operation 描述（`regions` 仅 `domestic`）
- `cn.cloud_capture.create_interval_task`：POST `/api/v3/open/cloud/video/frame/interval/start`；token_location=form；body_encoding=form_urlencoded；code_path=`meta.code`、message_path=`meta.message`、data_path=`data`、success_values=`[200]`；read_only=false。
- `cn.cloud_capture.create_timing_task`：POST `/api/v3/open/cloud/video/frame/timing/start`；其余策略同上。
- `cn.cloud_capture.task`：GET `/api/v3/open/cloud/task/{taskId}`；token_location=query；body_encoding=none；响应策略同上；read_only=true；沿用既有至多一次 10002 读恢复，无 HTTP/网络重试。

### 1.3 公共签名（逐字冻结；Async 仅加 `async` 前缀，参数与返回相同）
```python
def create_interval_task(device_serial: str, *, channel: int, rec_type: Literal["local", "cloud", "live"], start_time: datetime, end_time: datetime, frame_interval: int, project_id: str | None = None, result_space_id: int | None = None, frame_model: Literal[0, 1, 2] | None = None, dev_proto: Literal["", "gb28181", "jt808"] | None = None, stream_type: Literal["1", "2"] | None = None, validate_code: str | None = None) -> ApiResponse

def create_timing_task(device_serial: str, *, channel: int, rec_type: Literal["local", "cloud", "live"], timing_points: Sequence[datetime], project_id: str | None = None, result_space_id: int | None = None, frame_model: Literal[0, 1, 2] | None = None, dev_proto: Literal["", "gb28181"] | None = None, stream_type: Literal["1", "2"] | None = None, validate_code: str | None = None) -> ApiResponse

def task(task_id: str) -> ApiResponse
```

### 1.4 执行顺序
- 诊断作用域内 → 区域护栏 → 参数校验 → path/form 渲染 → auth/provider/factory → 传输 → 响应策略。
- 区域护栏先于一切参数校验与 auth/provider/factory：非 domestic 立即抛既有区域错误。

### 1.5 `create_interval_task` 校验
- `device_serial`：非空 UTF-8 str，原样发送不改写；`channel`：int 且非 bool，不设符号界、无默认 1。
- `rec_type`：严格 str ∈ {local, cloud, live}；`frame_model`：None 或 int 非 bool ∈ {0,1,2}；`stream_type`：None 或严格 str ∈ {"1","2"}；None 省略服务端默认。
- `project_id` 与 `result_space_id` 至少一个非 None；允许同时提供并原样同发，保留文档 resultSpaceId 优先语义，不丢弃或推导任一选择器。
- `project_id`：ASCII 字母/数字/下划线、长 1..32（ASCII 解释为 SDK 选择）；`result_space_id`：int 非 bool 且 ≥1。
- `validate_code`：None 省略，否则非空 UTF-8 str；加密前置由调用方保证，SDK 不检测。
- `start_time`/`end_time`：datetime；naive 按 Python utcoffset 语义处理、整秒；渲染 `yyyyMMddHHmmss` 四位年；不做时区或当前时钟推断。
- `end_time < start_time` 由 SDK 拒绝；相等透明放行（不声称服务端接受）；不施加 10 秒/48 小时限制。
- `frame_interval`：int 非 bool 且 ≥1，原样发送，无单位换算（模式→单位映射属保留未知）。
- `dev_proto`：None 或 Literal["", "gb28181", "jt808"]；显式空串是允许值而非服务端默认。

### 1.6 `create_timing_task` 校验
- 共有项 `device_serial`、`channel`、`rec_type`、`frame_model`、`stream_type`、`validate_code` 规则同 1.5；`channel` 必填（“可填1”措辞不解除必填）。
- `frame_model` 显式接受 2，依本端点正式表；计划与源码注释记录 1388/5324 相反表述，要求独立 LIVE 证据，不回退 0/1。
- `timing_points`：非字符串 Sequence；在 auth/await 之前快照为 tuple；非空；逐元素 datetime 校验；逗号连接；保序保重复；不设数量/窗口/间隔界。
- `project_id`：仅非空 UTF-8 str（不继承 32 长度/ASCII 约束）；`result_space_id`：仅 int 非 bool（0/负值原样放行，不声称服务端接受）；两者至少一个非 None，同发规则同 1.5。
- `dev_proto`：None 或 Literal["", "gb28181"]；不接受 jt808。

### 1.7 `task` 校验与渲染
- `task_id`：ASCII 字母/数字/下划线、长 1..32（按正式表解释，ASCII 为 SDK 选择；不按冲突散文收紧为恰 32）。
- 使用既有 Operation `path_params`/`render_path`；token 走 query；无 body、不注入 Content-Type；不拼 URL、不复用 VOD task 响应语义。

### 1.8 字段渲染与返回契约
- form/query 字段名按各源正式表原样（1388 表 element 12/40/153/172；1389 表 16/100/120；1370 表 10/37/66/153），snake_case 形参一一映射；accessToken 由既有传输按 token_location 注入；所有 None 一律不发送。
- 返回精确 `ApiResponse`：data/raw/未知字段与底层异常身份原样保留；无 DTO/转换/轮询/任务句柄/响应字段强制。
- 写操作对 10002/HTTP/API/超时/断连/取消不做业务重放；既有 token 失效语义保留，结果可为未知。

## 2. `tests/test_sdk_cloud_capture.py`（wire/校验）
- 直接实例化 `CloudCapture(real Client)`/`AsyncCloudCapture`；不依赖 `client.cloud_capture`；不新增全局 fixture；不使用 `Client(transport=)`；传输截取沿用既有国内测试对执行层的既有打点方式。
- wire 断言逐操作覆盖：HTTP 方法/路径/token 位置/编码；form 字段名集合与取值；`yyyyMMddHHmmss` 渲染与四位年；`timing_points` 逗号连接保序保重；全部 None 不出现；双选择器同发；`dev_proto=""` 显式发送；`task` 的 path 渲染、query token、无 body。
- 校验断言逐条覆盖 1.5/1.6/1.7：bool 拒绝、边界值、类型错误、`timing_points` 快照时机（先物化后 auth）、字符串 Sequence 拒绝、jt808 拒绝、1389 的 project_id/result_space_id 宽松度、双选择器全 None 拒绝、end<start 拒绝与相等放行。
- 区域护栏断言：非 domestic 时即使参数非法也先抛区域错误（仅断言模块自身护栏，不依赖集成挂载）。

## 3. `tests/test_sdk_cloud_capture_behaviour.py`（错误/对等/隐私）
- 错误：`meta.code != 200` → 既有错误类型与身份；`10002` → 既有 token 失效语义；写操作以传输调用次数断言无业务重放；`task` 断言至多一次恢复；HTTP/超时/断连/取消不重放。
- 对等：sync/async 同参产生逐字段一致请求。
- 隐私：沿用既有请求脱敏并新增 `consumerId`；报告不含 raw message/header/body、取值、ID、偏移、URL 与异常细节；已知请求值（含短数字形 ID）脱敏且不改变业务码分类；未知错误保持既有通用分类，不声称任意错误文本被普遍擦除。
- 异常身份穿透：底层异常不被包装替换。

## 4. `examples/cloud_capture.py`
- 仅仓库示例、非 SDK 运行时依赖；import 与 `--help` 零环境/配置读取、零网络、零任务。
- CLI 显式 sync/async 模式；仅在执行时调用 `examples._common.load_config`；凭据/ID 不走 argv、不回显；无 .env 自动加载、无文件日志、无上传。
- 默认仅查询一个显式环境变量 `EZVIZ_CLOUD_TASK_ID` 对应的任务（按所选模式一次）；不默认创建任务。
- 以显式步骤函数演示 `create_interval_task`/`create_timing_task` → `task` → `storage.search_files` → `storage.download_urls`：敏感值仅存局部变量，每步显式调用；无自动轮询、无空间/录制创建、无媒体下载、不从 task 响应推断 fileNodeId（指南 tagId=taskId 属跨源证据）。
- storage 检索仅有限单页；明确不声称 spaceIds+tagId 为 AND（沿用既有选择器优先语义），要求调用方检查返回文件身份。
- 成功/失败输出固定字段、枚举值与计数：绝不输出 ID/URL/token/原始负载/环境值/任意错误文本或异常类名；CLI 错误以固定失败类别返回非零，不吐 argparse 原文或异常文本。

## 5. `tests/test_example_cloud_capture.py`
- import 无副作用断言（无网络、无环境读取、无任务）。
- MockTransport 走真实 SDK auth/业务传输路径；断言精确请求顺序与次数；CLI 错误非零退出、无吞并异常伪造成功。
- 离线演练：新 task 查询 + 既有 `storage.search_files`/`download_urls` wire（合成身份）；`taskStatus` 0/3 语义保留，不得出可播放图像结论；本地合成 HTTP 不声明平台/设备成功。
- 非法 CLI 参数/配置：非零退出、固定失败类别、不回显 argv 或环境值。

## 6. 源修正与保留未知（实施须遵守）
- 修正执行：1389 resultSpaceId 行存在（SP199 误滤整行，撤销缺字段结论）；1357 以官方 HTTP 样例定 form 编码、以响应键样例定 code/msg/data，与 E49 扁平业务表差异保留；983/984 主机 `https://open.ys7.com` 非缺失；成功标量 1388/1389 为 `meta.code`=int 200，983/984/1357 为 `code`=str "200"；以 element_index+字段名为稳定定位器，不假设统一 R 约定。
- 保留未知：frameInterval 模式-单位映射；时间时区与最大窗口；双选择器同发语义；费用/配额/部分结果生命周期；错误分层；frameModel=2 跨页矛盾；1389 未列 projectId 字符集/长度与 resultSpaceId 边界；timing 点数量/时区/服务端限制；1370 正式表与散文冲突、响应必填性与时间格式、fileNodeId 缺席、status 0/3 不证明媒体/硬件正确。

## 7. 验收命令（逐字全列；本计划未执行任何一条）
```bash
.venv/bin/python -m pytest -q --tb=short --maxfail=5 tests/test_sdk_cloud_capture.py tests/test_sdk_cloud_capture_behaviour.py tests/test_example_cloud_capture.py
.venv/bin/python -m pytest -q --tb=short --maxfail=5
.venv/bin/python -m mypy src/ezviz_unified
.venv/bin/python -m harness.audit --repo . --check-summary
.venv/bin/python -m harness.contract_eval --repo . --require-all
.venv/bin/python -m ruff check src/ezviz_unified/cloud_capture.py tests/test_sdk_cloud_capture.py tests/test_sdk_cloud_capture_behaviour.py examples/cloud_capture.py tests/test_example_cloud_capture.py
.venv/bin/python -m ruff format --check src/ezviz_unified/cloud_capture.py tests/test_sdk_cloud_capture.py tests/test_sdk_cloud_capture_behaviour.py examples/cloud_capture.py tests/test_example_cloud_capture.py
.venv/bin/python -m build
.venv/bin/python tools/release/check_artifacts.py
.venv/bin/python -m twine check dist/*
.venv/bin/python tools/release/verify_installations.py --installer pip
```
- 说明：以上命令均未执行，`tests_executed=false`；验收以未来精确 PR/main HEAD CI 为准，不继承旧 CI 结果。
