# R005 上游 pyEzvizApi 复用评估（修订版，pinned v2.4.0）

## 结论
本期只交付**固定版本集成指南**：零新增依赖、零适配代码。指南让调用方自持已认证的上游 `EzvizClient`，直接调用 `poll_devices()`，原样获得类型化快照、逐设备新鲜度与原始 unknown 语义。可选适配器整体暂缓，须先具备跨源盘点 schema 与独立测试，再单独立项评估。

## 评估范围与证据
- 固定点：v2.4.0，commit `8fb943e`。观察时上游 `main` 与该 commit 一致；上游文档关于 main 的通用免责不构成“存在未发布差异”的证据。
- Wheel 证据范围：仅**只读下载**并比对打包文件与 pinned Git tree——167 个文件全部一致、0 不一致、0 缺失。**未安装、未导入、未执行、无 live 账号测试**，不做任何运行态声明。
- 本次为单版本审计，不足以支撑版本区间建议；指南只精确钉版（`pyezvizapi==2.4.0`），不写 `>=2.4,<3`。

## 已验证事实（修正后）
- 同步 requests 客户端；异步应用须由调用方在 executor 中调用，`close_session()` 仅清理 HTTP 会话。
- `poll_devices(DevicePollOptions(timeout))` 为**总预算**（锁等待+HTTP+认证重试+可选摄像头告警读取），返回不可变 `DeviceSnapshot`：Camera/Detector/Light/Plug 类型化状态 + `DeviceHealth{present,current,last_success}`。
- 严格 unknown 语义：online 仅认码 1/2，battery 限 0–100，`capabilities.has()` 缺失返回 None，detector `door_open/water_leak` 保持 None。
- 认证：消费者 App 账号+密码+交互式 MFA（`EzvizAuthVerificationCode`），sessionId/refreshSessionId 轮转，`token['api_url']` 路由；channel-99 场景 `on_token_updated` 须先持久化再返回（[python-api](https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/docs/python-api.md)、[device_inventory](https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/docs/device_inventory.md)）。
- 区域：默认 `apiieu.ezvizlife.com`，region 仅换 host 非迁移工具；**中国大陆账号支持未验证**。

## 增量（仅相对本 SDK 现状，不声称排他）
1. detector/light/plug 类型化状态（电量、原始诊断码、App 验证的开关选择规则）——本 SDK 当前无对应，但**未证明**官方渠道均不提供；
2. 逐设备数据新鲜度记录与严格 unknown 语义——`devices.list` 无逐设备新鲜度模型；
3. App 侧升级状态校正与统一消息流——与开放平台 `alarms.list` 字段级重叠待核；
4. 设备配置类：**本 SDK 已有官方绑定/解绑管理接口（`devices.bind`/`devices.unbind`；`DeviceManagement.bind(device_serial, verification_code)`，HTTP `/api/lapp/device/add`），无 `devices.add` Python 方法、无 setup 方法**，撤回“开放平台没有设备配置”的表述，但除绑定/解绑外不声称更多 provisioning 能力；上游 `add_device`/WiFi 列表/网络搜索/Hik activate 是否构成净增量未核实，不预断。

## 异步指导（指南不写包装代码）
- asyncio 取消不终止在途上游请求；裸 `asyncio.Lock` 在等待方被取消时会释放，可能放行重叠调用，**不足以实现 singleflight**。
- 指南只规定义务：调用方自持 executor、使用有界总预算、显式保证不重叠（singleflight 由调用方自行实现）；不提供 async 封装代码。

## 明确不做
- 不再导出上游异常为本项目稳定别名；不新增 MFA/password provider/session manager 等纯改名包装。
- 不纳入 push/MQTT/channel-99、streaming/media、统一消息流（额外收益未证）。
- 不做开发者 appKey/appSecret 与消费者身份之间的隐式回退；两授权主体保持分离。
- 不 vendoring、不套壳、不声称任何 live 验证。

## 未知项
PyPI 分发面与 tag 的一致性仅限本次 wheel 比对结论；统一消息 vs `alarms.list` 字段重叠未定；开放平台设备列表是否已含 detector/light/plug 行未定；上游 provisioning 与本 SDK `devices.bind`/`devices.unbind` 绑定管理的能力差集未定；sessionId 生命周期与并发登录限制未知；仅盘点场景下 paho-mqtt/crypto 导入副作用未验；App 端限流/反滥用策略未知；中国区支持未验证。

比对口径：167个文件指 wheel 内 `pyezvizapi/` 包目录内容，不含构建生成的 `dist-info`；版本、Python要求与依赖声明另由 METADATA 读取。
