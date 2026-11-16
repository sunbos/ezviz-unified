# 官方 SDK 产品阶段：范围与验收设计

## 目标与基线

本轮完成核定官方服务端能力的 Python 接入、产品验收和受控发布。App adapter、选择性遥测、独立模型评测与受控 RSI 保留第二轮，不能因本轮完成结项。原有传输契约离线评测属于本轮必需验证，并非第二轮模型评测。

基线 `81b86b462825739db8f059a418d5de235db88c87`；原 checkout 为 `/Users/sunbo/Desktop/ezviz-unified`，实施 worktree 为 `.claude/worktrees/sdk-product`，分支 `codex/sdk-product`。原工作区未跟踪的 `CLAUDE.md` 保留，原 SHA 为 `2d2cf4fa0f9b3dce59aa1949516446a15fe73eabd214a24f821d8ac2ff0b236b`；不覆盖或擅自提交。其公开架构/命令指导连同 AGENTS.md 生效；本轮用户已明确授权新入口同步扩充诊断白名单，不因文件中的“先确认”重复索要同一授权。

诊断 v1、对应 4419 测试/11 子测试、国内单设备同步 6 逻辑调用/7 HTTP 与 main CI 已有源码绑定证据。相同源码的这些证据复用；仅在变更、失败或新验收要求出现时执行相应检查。

## 工作流选择

采用“全量来源闭包登记 → 语义审查形成能力 → 按业务域实现”的路线。直接把 HTTP 声明或 curl 转成 SDK 会混淆重复、产品/权限差异和缺失语义；只处理现有 203 契约又会漏掉未登记范围。因此先覆盖所有资料记录的处置，再形成可执行能力清单，不从数字推导完成率。

第一批仅刷新资料、建立矩阵和验收范围；矩阵覆盖与优先顺序核定前不批量增加业务代码。复用现有 collector、derive、verify、endpoint_inventory、source_inventory、契约 audit/eval、诊断 recorder 与 inspection；不重新建设采集器、传输层或错误分类。

## 资料版本与闭包

- 主门户新快照：`data/official-docs/2026-11-17-sdk-product-main`；保留 `2026-11-14` 和 `2026-11-16-main`。
- 静态参考初次刷新：`data/official-docs/2026-11-17-sdk-product-reference/{domestic,overseas}`。先沿用已核对种子；新门户完成后必须比对新入口并补齐发现范围，不能凭旧种子宣称闭包。
- 附件另行获取与校验；SDK 二进制/安装包只按用途登记，未经具体需要与审查不运行它们。HTML/JavaScript 始终按资料处理。
- 每批先有 README/quality summary，再消费正文。来源 URL、身份、响应/正文/派生 SHA、目录归属、采集时间、错误与缺项保留。
- 没有官方原子快照时记录采集窗口、目录首尾一致性和发现范围限制。缺页可能藏有引用，不能写“零遗漏”。

## 能力矩阵固定结构 v1

矩阵独立于运行 SDK，使用三层实体；详细观察保存在本地，发布的矩阵与摘要不复制样例凭据或设备标识。

| 实体 | 必需信息 |
| --- | --- |
| source | source_set/version、region/platform、doc_id 或静态符号身份、URL、所有目录归属、状态、hash、采集窗口、原文/派生证据定位 |
| observation | source_id/content hash、declaration/example/callback/symbol 等观察类型、ordinal、section/element 指针、method/原样 path、解析状态与问题 |
| capability | 稳定 capability_id、业务场景/产品族、关联来源/观察/契约/问题、地区/权限/设备前提、协议语义、影响等级、处置、实现及各层验收状态 |

能力的 `disposition` 独立于实现状态：`implement`、`reuse_official_component`、`not_an_api`、`duplicate`、`deprecated`、`phase_2`、`needs_review`、`blocked_source`。非实现处置必须有证据与理由；`needs_review`/`blocked_source` 不算完成，也不能偷偷变成可选。

`implementation_status` 使用 missing/partial/implemented/not_applicable；来源审核、offline、live_read、live_write、physical_effect 各有独立状态与 evidence refs。保留 unknown/not_verified，不将未知变成失败或通过。已存在代码但官方来源变化时，应同时保留 implemented 和 source_review_required，不自动删除或认定继续正确。

去重规则：门户按 region+doc_id 去重来源，目录归属多值；route 分组使用 region+origin/service/version+method+原样 path，但只用于审查线索。只有请求/响应、产品、权限和副作用语义经审查等价才合并 capability。path-style 与 header-style OTAP 无等价证据时继续分开。Native SDK 用 platform/version/namespace/class/symbol/signature，不套用 REST 路由键。

203 个已有契约全部进入矩阵，不以其作为官方总量；当前两个声明恢复例外（global.playback.invalidate、global.security.update_password）须保留并说明来源证据。旧 186 契约 cohort 不复用为当前缺口。每条 catalog、静态参考及附件记录须有可追溯归属、待审或阻塞处置；examples-only 与无声明页面不能直接丢弃。

## API 批次准入

每批先固定公共接口、地区/权限、operation/response policy、参数位置与根类型、读写语义、独立 wire 案例、诊断更新和验收场景，再派实现。新能力联动 contracts、bindings、source-issues、evals、源代码、SDK 单测、诊断 registry/覆盖文件/仓库覆盖审计、示例和使用说明。

官方事实、SDK 设计选择、交叉核对与实测证据分别记录。材料缺失允许自主设计，不允许伪造来源。Native/媒体/RTC 能力优先以官方组件和可验证接入方案交付；不得以“不想实现”排除服务端 API，也不得靠重写播放器/引擎堆完成数。

优先顺序由矩阵确定：先解除已登记来源例外和公共基础缺口，再处理有明确契约/场景的未覆盖接口；国内组件按产品族分批，海外重复来源与真正新增分开，消息接入单列。后续账户/托管、云存储/云录制、AI、云接入等不得在总表中丢失。

## 产品验收矩阵

维度包括 capability、地区/具体集群、账号身份/权限、sync/async、SDK/CLI/安装包、代表设备或能力前提、风险等级、所需证据层级、代码/产物 hash、授权范围、恢复办法、结果和阻塞。

复用仍匹配源码的既有国内只读、新加坡认证/空列表、公开时区 sync/async 证据；空列表不证明海外设备可用。优先补国内 async 和示例 CLI，再按矩阵安排海外设备/业务资源与具体权限。每项 required 验收必须先定场景和判据，不能临近结束再降低门槛，也不无依据要求未声明的全部厂商型号实测。

真实消息验收需要已开通服务、可达回调入口、独立 secret、目标类型及触发消息的授权；检验原始正文/头的验签、实际 ACK、重复投递、时限和错误处理。现有纯函数 helpers 与合成向量不能替代平台真实回调。未确定的时间单位、超时及密文语义保留问题。

既有国内只读授权延用。真实写入、绑定解绑、权限、费用、删除、升级、格式化按具体设备/主体、参数、次数/预算和恢复路径授权；此前不执行。凭据只在本机授权通道使用，不进入模型输入、日志或 fixture。公开证据不含设备身份/原始敏感数据。

## 发行与结束条件

范围和实际兼容性确定后提出具体版本，不预先假定 0.1.0a1 已发布。核实 PyPI 项目归属、版本可用性、发布身份与方式；准备可审查的不可变产物后才进入已授权上传。

必需门槛：范围矩阵必须项完成；相关来源语义问题处置；同步/异步与错误/边界回归、mypy/Ruff、audit/eval、规定实测；wheel/sdist metadata/layout、Twine、干净安装与 source tests；PR 与 main 当前 SHA 的 CI；上传后核对索引、下载 hash 与实际安装。SDK runtime 不含 harness/资料/LLM；repo 元数据审计不混入独立 SDK sdist 单测。

完成只代表本轮官方 SDK 产品阶段。设备、权限、回调、来源或发布条件不足时维护具体阻塞并继续独立工作；尚有必须项未完成/未核定时不标 Goal complete。交付全量矩阵、验证分层、发布入口、限制和第二轮完整剩余清单。

## 分工与执行纪律

Codex 负责本设计、派单、关键协议判断、审查和最终验收；实现、修订、文档与常规测试交给 Claude Code 经 Magpie，固定 devin/swe-2、effort=max。每单指定输入版本、唯一文件、允许命令、判据；2–3 个独立会话并行，同文件单写入者。未经实测的候选不自称通过。两轮同类失败先核对任务定义、权限与实现路线；不以跳过、删测试、吞异常或篡改期望获得通过。

需要工作区工具的代码执行限定在上述 worktree，凭据目录不可见。若信任/权限拒绝，保留拒绝证据，修正派单或采用完整脱敏文本候选，不修改用户全局配置或扩大权限绕过。命令尽量单条；测试大输出先规定摘要方式，不让执行方临时追加未授权命令。
