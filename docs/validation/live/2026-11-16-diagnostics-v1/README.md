# 本地诊断 v1 国内实机只读验收

- 时间：北京时间 2026-11-16 21:24:44，由 Codex 控制的本机验收流程完成；本记录只汇总其结果，Claude Code 未接触凭据、未执行实测。
- 范围：已授权国内账号；设备列表首页 `page_size=1` 取首台普通设备；通道 1；单次同步运行。
- 调用：认证 1 次 + 设备列表 1 次 + `inspect_device` 固定五项（get/channels/status/capabilities/version），共 6 个逻辑入口、7 次 HTTP，全部 SDK 响应策略判定成功。
- 报告：64 个事件，`dropped_events=0`、`recording_errors=0`；逐字段与值白名单检查通过。
- 一致性：运行所用 SDK 源码与 verifier 哈希同预检快照一致，源码 commit `99613eaf26203329600316daf2424f42cd34ac0e`。
- 凭据纪律：凭据只进入本机进程与官方认证路径，未进入 Claude 输入、日志或 fixture；临时服务已确认停止。

## 边界

- 仅覆盖本次同步路径下该国内设备与通道的只读调用链；不证明异步、海外真实 API、全部型号/字段或硬件健康。
- 示例 CLI 未在本次联调中运行，verifier 结果不计为 CLI 联网实测；本结果不构成遥测或 RSI 交付。
- PR CI、合并与 main 验证由 Codex 另行核对，本目录不构成合并或 Goal 完成声明。

## 证据与关联

- [验收总记录](../../2026-11-16-diagnostics-v1.md)
- [诊断说明与未覆盖范围](../../../diagnostics.md)
- 安全报告：[domestic-diagnostic.json](domestic-diagnostic.json)
- 执行证据：[execution.json](execution.json)
