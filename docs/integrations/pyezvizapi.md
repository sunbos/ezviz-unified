# pyezvizapi 集成指南（可选 · 钉版 2.4.0）

> 目的：让调用方在**不改动 ezviz-unified** 的前提下，自行评估消费者 App 侧盘点数据。
> 本仓库**没有** `ezviz-unified[app]` extra、适配模块或开发者↔消费者身份的自动回退。

## 1. 两个授权主体，保持分离

| | ezviz-unified `Client` | 上游 `EzvizClient`（第三方 pyezvizapi） |
|---|---|---|
| 渠道 | 萤石开放平台官方 API | 消费者 App 接口（非开放平台） |
| 凭证 | 开发者 appKey/appSecret | App 账号 + 密码（按需交互式 MFA），或完整已保存令牌 |
| 会话 | HTTPX（本 SDK 唯一依赖） | requests 同步客户端 |
| 归属 | 本仓库 | 调用方自建并持有 |

不做开发者凭证到消费者身份的隐式回退；两源相同标识符**不自动**视为同一实体（见 §8）。

## 2. 凭证、MFA、令牌存储：上游与调用方的责任

- App 账号、密码、MFA 交互、sessionId/refreshSessionId 轮转及 `on_token_updated` 持久化（须先落盘再返回）均由调用方按上游文档实现；本仓库不实现、不代存、不转发消费者凭证。
- 上游认证与令牌复用（钉版）：[python-api.md](https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/docs/python-api.md)。仅作链接，不复制其口令/CLI 示例。

## 3. 隔离环境精确安装

- 运行环境要求：Python 3.12+。

```bash
python -m venv .venv-appdata
.venv-appdata/bin/pip install 'pyezvizapi==2.4.0'
```

- 仅审计过 2.4.0 单版本：钉死 `==2.4.0`，不写 `>=2.4,<3` 之类区间。
- 依赖成本以该 wheel 的 METADATA 为准（直接依赖含 requests、paho-mqtt、pycryptodome 等）；仅盘点路径未必触发全部导入，导入副作用未验证。
- 本 SDK 的标准依赖仍只有 HTTPX；本指南不改变其依赖面。

## 4. 最小同步用法（借入已认证客户端）

```python
# DevicePollOptions 定义于 pinned 上游源码 pyezvizapi/devices/snapshot.py：
# https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/pyezvizapi/devices/snapshot.py
from pyezvizapi.devices.snapshot import DevicePollOptions

def fetch_inventory_snapshot(client):
    """借用调用方已认证的 EzvizClient 做一次盘点，返回 DeviceSnapshot。

    不登录、不持久化令牌、不调用 close_session()；本调用可能发起网络请求。
    """
    options = DevicePollOptions(
        timeout=20, include_camera_alarms=False, max_devices=100
    )
    return client.poll_devices(options)
```

调用方按约定消费快照：

```python
snapshot = fetch_inventory_snapshot(client)
cameras = snapshot.select(kind="camera")   # 纯内存视图，不再发请求
health = snapshot.health                    # serial -> DeviceHealth
# 只取所需字段；不打印序列号、设备名、令牌或原始 payload。
```

语义要点：

- `max_devices` 是设备数预算：**超出即抛错拒绝**，不截断、不静默丢设备。
- `timeout` 是一次盘点的**总预算**（锁等待 + HTTP + 认证重试 + 可选摄像头告警读取），不是单次 HTTP 超时，也**不等于**一次请求。
- 未知即 `None`：`online`、`battery_level`、detector `door_open`/`water_leak` 缺失或不可识别时返回 `None`，不猜值。`capabilities.has(name)`：属性被观测到时返回 `True`，缺失时返回 `None`——存在性与取值（及设备权限）相互独立，不会把“存在但值未知”折叠成 `None`。
- `DeviceHealth.present`/`current`/`last_success` 表示快照**新鲜度**，不是健康评分。
- 不声称任何字段“仅 App 侧可得”；与开放平台字段级重叠尚未核实。
- 会话归调用方所有：`close_session()` 由所有者负责，借用的函数不得关闭客户端；操作可能联网。

## 5. 异步：只规定义务，不提供封装

- 上游为同步 requests 客户端：asyncio 应用须在**自有 executor** 中调用盘点与清理。
- `asyncio` 取消不会终止在途上游请求；裸 `asyncio.Lock` 在等待方被取消时会被放行，**不构成 singleflight**——不重叠调用由调用方自行保证。
- 本文不提供 async 包装器，也不承诺取消语义。

## 6. 明确不做

push/MQTT 与统一消息流、streaming/media、设备控制、设备配网/添加均不在范围；上游 provisioning（HTTP /api/lapp/device/add）与本 SDK 官方绑定方法 `client.devices.bind()` 的差集未核实；本 SDK 无 `devices.add`/setup 方法。

## 7. 区域与账号

- 默认 host 为 `apiieu.ezvizlife.com`（欧盟）；`region` 仅替换 host，**不是**自动路由或数据迁移建议。
- 中国大陆 App 账号支持**未验证**。

## 8. 证据与边界

- 只读下载 `pyezvizapi==2.4.0` wheel：167 个打包文件与 pinned Git tree（`8fb943e`）全部一致，0 不一致 / 0 缺失。**未安装、未导入、未执行、无 live 账号测试**；PyPI 与 tag 的一致性仅限本次比对。
- 上文 Python 片段为示意，**未执行**；本仓库仅做语法级检查。
- 跨源盘点 schema/适配器尚未实现（已列入 roadmap）：即使两源标识符相同，也不要自动合并设备身份。

## 9. 来源（主源，钉版 commit `8fb943e`）

- 上游仓库：<https://github.com/RenierM26/pyEzvizApi/tree/8fb943e363f2052e17523b234de9ac55377c4131>
- 认证与令牌：[python-api.md](https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/docs/python-api.md)
- 盘点语义：[device_inventory.md](https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/docs/device_inventory.md)
- `DevicePollOptions` 定义：[pyezvizapi/devices/snapshot.py](https://github.com/RenierM26/pyEzvizApi/blob/8fb943e363f2052e17523b234de9ac55377c4131/pyezvizapi/devices/snapshot.py)
- 本 SDK 官方客户端用法：[../getting-started.md](../getting-started.md)
比对口径：167个文件指 wheel 内 `pyezvizapi/` 包目录内容，不含构建生成的 `dist-info`；版本、Python要求与依赖声明另由 METADATA 读取。
