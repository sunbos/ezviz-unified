# 云录制2.0计划管理（写接口）

在既有10个查询入口之上，本批按2026-11-14官方归档为国内`client.cloud_recording`（异步同名）新增5个计划生命周期写接口。复用同一Client、区域认证、传输与响应策略，仅国内，不推定海外、托管或子账户可用；不开发播放器、任务服务器或通用任务框架。写操作不重放、不轮询、不自动串联停止与删除，也不修改设备权限或密码。

| 方法 | 官方文档 | 请求 | 成功判定 |
| --- | --- | --- | --- |
| `create_one_off_plan` | [2045](https://open.ys7.com/help/2045) | POST JSON | 整数`meta.code=200` |
| `update_one_off_plan` | [2050](https://open.ys7.com/help/2050) | PUT JSON | 整数`meta.code=200` |
| `stop_one_off_plan` | [2046](https://open.ys7.com/help/2046) | POST表单 | 整数`meta.code=200` |
| `delete_one_off_plan` | [2049](https://open.ys7.com/help/2049) | DELETE表单 | 整数`meta.code=200` |
| `create_instant_plan` | [2062](https://open.ys7.com/help/2062) | POST JSON | 顶层字符串`code="200"` |

token一律在Header，全部要求read_only=False。前四个meta接口的原文错误表只列400/404/406/412/500等语义码，2062即时计划另列10001/10002/60020/60030等body业务码，均不等同于HTTP status；SDK按响应体code判定成功，两层互不映射。data/raw原样保留planId、oneOffPlanId等字段命名差异，不归一；2046的data可缺失，未返回时为None，`only_stop_preview`非None时按小写`"true"`/`"false"`写入表单。

## DTO与校验

`CloudRecordingDevice(device_serial, local_index, validate_code=None)`为冻结DTO，序列号与验证码不进入repr。Long型ID用int并拒绝bool；可选参数为None一律省略，显式False（如`specified_end_time=False`、`auto_delete=False`）仍发送。一次性计划按原文限制plan_name为1–32位中文/英文/数字/下划线/中划线，devices为1–1000条。即时接口不套用这两项上限：2062未声明名称长度与通道数量，hours仅由SDK本地校验为正整数，“一般≤24h”不当作无条件硬上限。2062全文未提及validateCode，即时接口对非None的validate_code前置拒绝——这是缺少来源支持的保守处理，并非断言平台禁止。SDK不擅自处理加密设备，也不自动补验证码。

## 时间规则

`start_time`/`end_time`取无时区整秒naive datetime，按`yyyyMMddHHmmss`原样发送；来源未声明时区，SDK不猜测也不转换，结束不得早于开始。创建仅在`specified_end_time`显式为False时允许省略end_time；更新按2050参数表仍要求end_time，该差异属未决源语义，保留不抹平。

## 边界与验收状态

这些显式写调用可能创建录制、使用套餐或存储资源。示例只用占位数据，显式调用才发送，不含secret、不打印真实设备响应、不内置真实区域账号或设备。本批仅通过离线检查，未经真实录制验收，不承诺套餐余量、设备在线或海外可用。

```python
from datetime import datetime

from ezviz_unified import CloudRecordingDevice

# client 为已配置区域和凭据的实例，首次请求时才认证，不要求预先完成认证
devices = [CloudRecordingDevice(device_serial="EXAMPLE_SERIAL", local_index="1")]
result = client.cloud_recording.create_one_off_plan(
    plan_name="example_plan",
    space_id=100000,
    start_time=datetime(2026, 11, 20, 8, 0, 0),
    end_time=datetime(2026, 11, 20, 12, 0, 0),
    devices=devices,
)
# result.data 保留服务端原始字段；此处不打印真实设备响应
```
