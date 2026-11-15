# 国内单设备只读检查验收

观察时间：2026-11-14 23:12:31 UTC（北京时间2026-11-15 07:12:31）。使用用户已授权的国内账号，经本机回环表单调用真实SDK。账号凭据只在本次进程中使用，未提供给Claude；公开记录见[domestic.json](domestic.json)。

读取第一页（page_size=1）返回1条设备记录，随后对该条设备以通道1调用 `inspect_device`。信息、通道、状态、能力、版本五项逻辑调用均满足SDK响应策略。它们不是固定数量的HTTP请求，认证及SDK既有重试可能增加请求数。

本次为同步、国内、单设备、单通道样本，验证范围为 `bounded_readonly_inspection_policy_check`；不做全字段结构检查或健康判定，也不证明其他型号、异步路径、海外设备或全部能力可用。检查入口返回策略成功与设备物理功能完成是不同证据。未执行存储、媒体、配置写入、属性设置、命令调用或托管授权。

运行方式：

```sh
.venv/bin/python tools/live/verify_devices.py --serve --inspection --output docs/validation/live/2026-11-15-domestic-inspection
```

浏览器导航等待曾超时；随后只读取结果页，确认同一次请求已经完成，未再次提交。临时服务已停止，浏览器脚本环境已重置。CLI示例模块未在本次联调中运行，不能据此宣称示例CLI已完成联网验收。
