# 云录制计划管理与消息资料并行批次

## 目标与边界

补齐现有云录制查询的显式计划创建、更新、停止和删除调用，便于调用方完成录制生命周期。复用Client、区域认证、传输和响应策略；不开发播放器、消息服务器或通用任务框架。仅离线开发，不实际创建录制、删除资源或消耗用户套餐。

SDK以2026-11-14归档的国内文档2045、2046、2049、2050、2062为依据。资料中不同响应层级、默认值和可选字段必须保留；不推定海外、托管或子账户可用，也不代调用方自动串联停止与删除。

## 并行分工

1. S020A：Claude生成`cloud_recording_plans.py`、测试和必要集成候选；拥有SDK相关候选，不修改契约登记。
2. S020B：独立Claude会话从相同原文生成5项契约、绑定、wire案例与源问题候选，不接收实现代码。
3. R003：Claude核对消息服务与云录制回调原文，指出接口、生命周期、验签/解密与ACK证据缺项；不生成网络服务或执行消息配置。

所有任务固定devin/swe-2、effort=max、零工具输入模式。Codex固定公共接口与边界，交叉核对实现和独立源契约，统一应用共享登记并执行验收。

## 公共接口约定

`CloudRecordingDevice(device_serial: str, local_index: str, validate_code: str | None = None)`为冻结DTO，设备序列号与验证码不进入repr。`client.cloud_recording`与异步同名入口新增：

- `create_one_off_plan(*, plan_name, space_id, start_time, devices, end_time=None, template_id=None, stream_type=None, specified_end_time=None, auto_delete=None)`。
- `update_one_off_plan(plan_id, *, plan_name, space_id, start_time, end_time, devices, template_id=None, stream_type=None, specified_end_time=None)`。
- `stop_one_off_plan(plan_id, *, only_stop_preview=None)`。
- `delete_one_off_plan(plan_id)`。
- `create_instant_plan(*, plan_name, space_id, hours, devices, template_id=None, stream_type=None)`。

Long为int并拒绝bool；可选None全部省略。一次性创建/更新按原文限制名称1–32、中英数下划线中划线、设备通道1–1000；即时接口不套用原文未明确规定的名称长度或通道数量上限。hours需正整数（SDK的有效录制时长校验），不把“一般≤24h”当无条件硬上限。

时间使用整秒naive datetime，发送yyyyMMddHHmmss，不猜时区；结束不得早于开始。创建仅在specified_end_time明确False时允许省略end_time；更新按参数表仍要求end_time。保留该差异及其证据局限，不擅自处理加密设备或自动补验证码。

三项JSON写（创建、更新、即时）；停止与DELETE按原curl用form。全部Header token、read_only=False。前四项严格整数meta.code=200，即时计划严格顶层字符串code="200"。不重放写操作、不轮询状态、不自动申请权限或下载媒体。

## 验收

本批5项接口已完成离线集成：新增191项测试、整体2245项与11个子测试、177项契约的1448次合成执行通过。真实录制未验收。具体修订与证据见[S020验收](../coordination/S020A/review.md)；消息资料成果见[独立审查](../research/2026-11-15-message-ingestion.md)。

- 逐页核对source hash、方法、路径、参数位置、嵌套设备字段、响应码类型和默认值。
- 先验收独立测试与预期报文，再运行同步/异步wire评测、无效输入前置拒绝、海外拒绝、写操作不重放和标识脱敏。
- 核查SDK/契约/绑定/案例数量一致；原有查询保持通过。通过离线检查不改写为真实录制验收。
- 消息资料单独交付源差异清单，未确认的签名算法、时间单位、重试次数和传输位置不补造实现。

独立核对确认2062没有validateCode字段证据，即时计划方法会在认证前拒绝非None的validate_code；一次性创建/更新继续按已声明字段支持。该限制属于当前SDK审核范围，不断言服务端永远不支持。
