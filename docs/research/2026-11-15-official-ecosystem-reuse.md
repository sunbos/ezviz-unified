# 官方生态对照与复用边界

核查日期：2026-11-15。用户要求优先复用 Ezviz-OpenBiz，避免重复建设。本记录用于调整投入顺序，不宣称全生态唯一，也不把接口数量作为产品价值。

## 核查范围与事实

已读取 [Ezviz-OpenBiz](https://github.com/Ezviz-OpenBiz) 的 41 个公开仓库清单，并重点查看以下仓库的 README、目录或入口源码。没有安装或运行这些上游包，未检查所有发行二进制；仓库内容与官网产品能力不能直接画等号。

| 官方项目 | 本次核实的能力 | 本项目处理方式 |
| --- | --- | --- |
| [EZUIKit-JavaScript-npm](https://github.com/Ezviz-OpenBiz/EZUIKit-JavaScript-npm) | 直播、回放、对讲、截图、录制；README 已支持海外 `env.domain` 配置 | 播放和媒体引擎优先由官方组件承担；Python 提供必要的服务端调用与集成说明 |
| [官方接入 skills](https://github.com/Ezviz-OpenBiz/EZUIKit-JavaScript-npm/tree/master/skills) | JS/Native 接入资料；README 明确供 Copilot、Cursor、Kiro 使用 | 链接、复用现有接入资料；新增 skill 必须说明具体任务增量及评测方式 |
| [Node.js SDK](https://github.com/Ezviz-OpenBiz/ezviz-openplatform-sdk-nodejs) | 当前 `main.js` 仅导出 Auth；完整目录与 README 对应五类 Token 生成器 | 签发协议以官方资料为准；先评估现有包或服务接入，不另造认证协议 |
| [Java SDK 仓库](https://github.com/Ezviz-OpenBiz/ezviz-openplatform-sdk) | 当前公开源码类为空，README 指向官网 1874 下载页 | 不能据仓库名称推断全量 REST 支持，也不能据空类断言官网 Java 产品没有能力 |
| [OpenCodeSnippets](https://github.com/Ezviz-OpenBiz/OpenCodeSnippets) | 宠物、烟火、台球、抽烟、安全帽等 AI 场景示例 | 优先引用或适配示例，不重复开发同类示范项目作为卖点 |

Node 目录树为 `84b629afb6657c97b04c379bb5ed49d7ac90515e`，Java 目录树为 `447e3ade7cbf9ef04f3f68831732d7e071423533`。官网 [1874](https://open.ys7.com/help/1874)、[1872](https://open.ys7.com/help/1872) 的 2026-11-14 本地归档用于补充核对：服务端 Java 提供通话 Token；通话令牌文档已有更新与替代说明。因此不能直接把 Node README 的历史 Token 类型作为当前所有产品的接入建议。

平台 `AppKey → accessToken` 获取与面向终端的权限 Token 签发是不同机制；取流令牌、RTC 令牌和平台令牌不能混用。播放器域名清单也不能直接替代服务端认证路由契约。

## 投入决策

保留 Python 原生同步/异步服务端 SDK，增量集中于地区、账户权限和响应语义的明确处理，以及可复现的故障定位和集成体验。仅“换成 Python”“支持海外”“带 skills”都不足以证明差异化；已查仓库中未发现同定位 Python SDK，不代表整个生态不存在。

已有 HTTP 封装作为底座保留，继续区分离线契约验证、真实只读和真实写验收。既不按方法数继续堆叠，也不因尚未实测而删除已审核实现。完整资料目录的目标保留，新增实现按真实场景、可复用程度和验收条件排序。

维护 harness 保持独立：来源变化、契约影响和回归证据服务于降低维护成本。当前已有审查任务包、经哈希核验的候选输入包与一次隔离副本内的单补丁受控演练，尚未完成生产补丁应用、规模化模型 eval 或自动发布；不以“自进化”替代可用性验收。

选择顺序是：直接使用成熟组件；在边界处做薄适配；确有缺口才独立实现。为直接调用 Java/Node 包而给所有 Python 用户强制引入 JVM/Node，会增加部署与故障成本，应按具体场景评估。公开可见不等于各包许可相同，代码复用按对应版本许可保留归属。

## 后续任务准入

每个新增业务域或 skill 先记录：

1. 用户要完成的具体任务，以及官方和现有生态已经提供的能力。
2. 可直接复用的组件、版本与链接；无法复用的具体原因。
3. 本项目新增价值、地区/权限边界和验收证据。

优先把现有设备查询与诊断做成可复现的使用闭环，再扩展新域。播放器、编解码、RTC 引擎及通用 Agent 框架不进入当前自研范围。模型、skills、RSI 只有在明确减少任务失败或维护工作时才推进。

## 独立审查

已由 Claude Code 经 Magpie、`devin/swe-2`、effort=max 对脱敏事实摘要提出反对意见，Codex 对照源文件复核。采纳“不凭名称推断能力、不强加其他语言运行时、控制 harness 投入”；不采纳“删除未实测契约”和“已证明 Python 生态空缺”。原始候选与模型核验记录留在本地 `.claude/coordination/R001-overlap-review.*`；该审查不代替上游源码或实际运行验证。
