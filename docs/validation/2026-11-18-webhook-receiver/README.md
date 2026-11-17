# Webhook Receiver 验收说明（2026-11-18）

## 验收对象

- 代码 commit：`11cd8af`（feat: add bounded signed webhook receiver example），最后代码/测试文件 SHA 逐一与该 commit 匹配。
- 设计文档：[webhook-receiver.md](../../webhook-receiver.md)、[plans/2026-11-18-webhook-receiver.md](../../plans/2026-11-18-webhook-receiver.md)；用例登记见 [sdk-product-acceptance.json](../sdk-product-acceptance.json)。
- 权威结果由控制器写入同目录 [checks.json](checks.json)，本文仅为说明，不替代其判定。

## 本地验证结果

- 全量 pytest：4705 passed + 35 subtests（42.14s）；Ruff 9 个文件通过。
- TCP loopback 实测链路：sender → HTTP → SDK 验签/解析 → 有界 inbox → JSON ACK，覆盖重复、冲突、时窗、容量、截止、未知异常传播与隐私检查；全部为合成数据，零真实平台/设备业务。
- 真实 CLI 子进程：`--help` 退出 0；非 TTY 拒绝退出 1；PTY 合成 secret 输入前 ECHO 已关闭且捕获中无 secret；`run` 0.2s、1 连接上限实际 0 连接、正常 deadline 退出 0。

## 用例状态

- ACC-WEBHOOK-CORE-001、ACC-WEBHOOK-HTTP-001、ACC-WEBHOOK-CLI-001：本地层可记录通过。
- 平台用例（CN/OS）及原 ACC-PLATFORM-MESSAGE-001：仍为 `awaiting_resource_confirmation`，未授权配置变更。海外无绑定设备、消息服务/回调路径未知；相关配置可能停止 App 提醒且关闭后不自动恢复，须另行授权并附恢复方案。

## 边界与声明

- signed-only 为本地设计，OS `t` 单位不做推断；inbox 非持久，不提供跨重启 exactly-once，清理引用不等于安全擦除；helpers 自动诊断排除边界保持不变。
- CLI 真实 TTY 仅证明本机交互路径，不冒充平台回调。
- 本例不改变 SDK src/依赖/HTTPX/请求重试行为，不等于全 SDK 产品或 PyPI 发布已完成。
- 失败历史已保留并补入真实回归，不得以最初 4703 green 替代对最终实际代码的验收。
- 当前 PR/commit 的 CI 与构建安装结果由后续 Git 验收填写，本文不预写"已合并/已发布"。
