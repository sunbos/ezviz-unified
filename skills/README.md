# 技能包索引

本目录收录面向 Agent 的技能包，复用本仓库的 SDK 与 harness 完成限定任务。

## 性质与边界

- 技能包是随源码仓库分发的说明性资产，不是 SDK 的运行依赖；SDK 与 harness 均不读取本目录。
- 执行这些仓库工作流需要可定位的源码 checkout；SDK 安装包不提供这一层。技能包文件本身也可单独下载获取。
- 本目录存在于磁盘上不保证已被宿主加载；是否自动发现、如何安装由宿主自身的发现/安装机制决定，本仓库不规定具体命令。
- 技能包是面向 Agent 的指南，描述任务步骤、约束与验收口径；实际的文件修改与调用由明确任务范围与验收约束决定（例如契约技能可在用户授权范围内修改 SDK/登记）。

## 技能列表

- [ezviz-device-inspection](ezviz-device-inspection/SKILL.md)：设备只读检查，盘点设备状态与能力，不产生写操作。
- [ezviz-contract-review](ezviz-contract-review/SKILL.md)：单契约审查，针对单个端点契约核对来源、实现与测试。
- [ezviz-doc-maintenance](ezviz-doc-maintenance/SKILL.md)：快照维护，维护地区文档快照及相关校验产物。

## 状态

- 三个技能包格式检查已完成。
- 2026-11-15 完成一轮验证：7 个合成计划案例经人工语义核对覆盖核心标准（人工审查，非自动执行通过率），并完成一次相同快照的 `harness.review` 命令抽查（判定 `no_document_review`，不代表真实变更修复）；详见[验证说明](../docs/validation/skills/2026-11-15/README.md)，评审材料与手动重跑方法见 [evals/skills](../evals/skills/README.md)。
- 模型评审未启用工具，计划不构成技能包可靠执行完整工作流的证据；技能包随仓库一同维护，发现问题按仓库流程反馈。
