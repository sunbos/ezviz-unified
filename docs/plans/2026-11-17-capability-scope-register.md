# SP021：完整范围登记与能力矩阵的最小数据设计

本稿是 Codex 架构决定与后续派单输入，不实现工具或 SDK，不代表矩阵已生成、接口已冻结或产品已完成。核对基线为原 checkout `641d6cc10010b11a49c65c9d6783f92eefda00d3`。只读取安全账本、审核候选、registry、验收结构及队列元数据；未读取或输出原始敏感样例，未执行测试、业务网络或重新采集。

结论：新增一份 `docs/capabilities/scope-register.json`，继续维护现有 `docs/validation/sdk-product-acceptance.json`，共两份工作数据。scope-register 同时容纳语义能力、完整来源/观察责任、related-material 版本审查和 cohort 准入；实际验收结果只存 acceptance。既有 registry、schema 2 source ledger、候选文件和 SP009 队列作为有 SHA 的权威输入，不复制第二套协议或结果。

完整范围登记和验收范围先于批量 SDK 实现。全范围仍必需的待审项必须进入登记并继续阻塞全 Goal 完成；已核定、独立且验收范围已冻结的单个 cohort 可以先实施。仅有4051条机械账本、203项现有登记或49条海外候选，不满足这个准入条件。

## 1. 当前事实与输入固定

| 输入 | 本次核对 SHA-256 | 用途与边界 |
|---|---|---|
| `contracts/reviewed-transport-contracts.json` | `fd1afc667ea3c5b96e02dea82ddf46591b7c947f07129a881b96bb120f44bfd2` | 203项既有operation的协议权威来源 |
| `contracts/sdk-bindings.json` | `a8453bcf9fecc73784d0911f414a9c905ecadce87ddb51f5e5eefbf86c02aed7` | 入口/符号、历史offline/live记录；不是本轮新实测 |
| 安全source ledger：`data/reports/sdk-product-matrix-public-v2-2026-11-17.json` | `d0e6bae17322a3aa379e75918327f85bcdcfb9ce53ee53371604047a6cad9540` | schema2、reviewed_metadata_only；4051来源、3019观察；不能当语义能力矩阵 |
| `docs/capabilities/overseas-review-candidates.json` | `3bb1b8c8008a24833ab7c93cb705100670d004fd7d93ca9b92c57636c693c3e2` | 49条审查候选：45个未有既定operation的候选，加4个已有operation来源映射 |
| `docs/validation/sdk-product-acceptance.json` | `7d477d6344196900a9f8fe4aa19fc737f349912224a18eb808e1cfa68e28daeb` | 10条历史实例/场景模板混合记录；scope仍provisional，不能自动当全部能力已绑定 |
| 私有 `SP009-source-queues.json` | `ef2f5d0feee12efc9457e12605a22f191e091262da44342aba68e4f22385a0e6` | 国内2229来源的八组唯一执行归属；本身不代表语义处置完成 |

source ledger的五个source set为portal 2281、国内静态参考73、海外静态参考219、旧账号指南1、assets1477，合计4051；portal观察3019（1529声明、1490示例）。这只描述已声明输入范围。缺页可能隐藏引用，linked SDK binary及官方仓库发行版本尚需审查，不能改称全厂商生态闭包。

已有203项全部进入语义登记；海外49条中的四条单独登记关系而不再生成能力：

- 469 §1.2 → `contract:global.token.get`，关系 `additional_source`。
- 469 §1.10 → `contract:global.devices.bind`，关系 `additional_source`。
- 534 §1.2 → `contract:global.playback.invalidate`，关系 `source_alignment`。
- 2868 d0 → `contract:global.security.update_password`，关系 `source_alignment`。

其余45条保留原candidate ID、proposal状态及未解决字段。因此初始能力登记有203个已有operation槽位和45个候选槽位；248不是API总量，也不是已核定的完整能力分母。后续语义审查可以发现新能力或拆分/合并候选，但须保留原候选、来源和变更关系，不能靠删除行缩小范围。

## 2. 为什么只用两份工作数据

| 方案 | 判断 |
|---|---|
| 在机械ledger生成器中加入所有人工语义、材料许可和验收结果 | 不采用。重生成容易覆盖人工决定，且会混淆provisional观察与真实能力，并扩大已审公开投影边界 |
| 新增scope-register作为语义与责任覆盖层，复用现有acceptance保存结果 | 采用。只需有限数据整理与引用核对；不改采集、HTTP提取、传输、audit/eval机制 |
| 另建能力库、来源库、材料库、验收库和调度框架 | 不采用。当前工作不需要多库同步、通用DSL、迁移引擎或自动准入服务 |

实际新增数据文件仅一份；related-material条目嵌入scope-register，不另起第三套结果文件。现有候选JSON保持审查输入角色，其旧offline/live字段不成为新事实的第二写入点。可按队列准备小候选diff，再由单一集成方更新scope-register；不为并行协作建立新框架。

## 3. `scope-register.json`：最小布局

顶层只需 `schema_version`、`scope_revision`、`baseline_commit`、`inputs`、`scope_state`、`queues`、`capabilities`、`source_relations`、`source_reviews`、`observation_reviews`、`material_reviews`、`cohorts`、`summary`。

`inputs`固定registry、候选、安全ledger、队列输入和acceptance版本的路径/安全artifact标识、schema及SHA。相邻数据文件使用稳定entity ID交叉引用；只有scope-register保存acceptance文件的当前SHA，acceptance不反向固定整个scope-register SHA，避免循环hash。冻结的case和cohort固定各自输入子集与scope_revision。输入变化时只使受影响关系/证据进入待复核，不删除原记录或自动重采。

`scope_state`至少区分四件事：

- `accountability_registered`：所有声明范围内来源、观察、已知能力及材料版本审查责任均已登记。
- `scope_and_priority_ratified`：Codex已核对全范围、当前Goal排除项与优先顺序；须有审查记录。
- `all_semantics_dispositioned`：所有必需来源/观察/材料语义已明确处置。待审或源阻塞存在时为false。
- `required_acceptance_complete`：具体必需case达到自身判据且证据适用。不能由前三项推导。

这四个状态不能合成一个complete。初始设计及候选导入不得把 `all_semantics_dispositioned`、`required_acceptance_complete` 或 `product_complete` 设为true。

### 3.1 能力记录

| 字段 | 最小内容／权威来源 |
|---|---|
| `capability_id` | 已有项为`contract:<operation_id>`；新增项暂保留原candidate ID。ID中的candidate是来源谱系，不是自动准入状态；状态由review字段决定 |
| `origin_record_ref` | registry或候选的input_ref＋entity_id；不复制整份request/response schema |
| `scenario`、`family`、`nature` | 审核过的业务场景、产品族、server_api/authorization_flow/message_protocol/integration等；不从path自动推断 |
| `requirement` | `required`、`pending_scope_review`、`phase_2_authorized`。pending_scope_review仍按必需未关闭项阻塞Goal；没有默认optional |
| `source_refs`、`observation_refs` | 仅引用安全ledger ID和content pin；人工新增callback/symbol/scenario观察也必须有来源SHA及数字section/element指针 |
| `region_scope`、`origin_policy_ref` | 地区/服务范围与明确origin策略；未知为null并列入unknown_fields，不通过地区名、同path或返回目录推导域名 |
| `account_permission_scope`、`device_prerequisites` | 账号身份类型、权限、白名单、型号/能力条件；只记录类别，不记录真实账号/设备值；未声明不等于不限 |
| `protocol_ref`、`sdk_choice_refs` | 已审核契约或候选的协议字段指针；官方事实与SDK设计选择分开。请求根类型、参数位置、响应层、读写/重试尚未冻结时不得伪装成已审核契约 |
| `sdk_entry` | 已有binding/public_entry/symbols的引用；新增entry/signature引用保持proposal/frozen状态。operation_id与runtime diagnostic name不能混为一项 |
| `implementation` | unknown/missing/partial/implemented/integration_required/not_applicable，加code/binding引用。AST可达只记ast_verified证据，不替代运行验收 |
| `review`、`disposition` | candidate/needs_review/ratified/revalidation_required及明确处置。implement仅表示拟实施方向，不表示已实施；source_alignment放在来源关系，不伪装第二能力 |
| `unknown_fields`、`gaps` | 受控问题类别、责任队列、阻塞阶段、证据定位和解除条件；来源未知、SDK缺项、资源不足、授权待定、证据不足分别记录 |
| `acceptance_case_refs` | 各层关联具体case ID；结果由acceptance唯一保存，矩阵不另存一套最新passed/live值 |
| `completion_criteria` | 具体来源/协议处置、实现或官方集成交付、文档入口、必需case判据、残留问题处理条件；不是“数量达到N” |
| `owner_queue`、`cohort_id`、`depends_on` | 唯一执行责任及明确依赖；多来源/多能力关系不产生多个写入owner |

实现、来源审核、offline、live_read、live_write、physical_effect、integration、packaging必须各自可追踪。能力记录按层提供case refs即可；可读状态视图从这些引用及registry推导，不维护第二套结果。

unknown的表达必须直接：`value=null`＋`unknown_fields`＋待核定理由。`missing`只用于已形成具体交付对象但确无相应实现的情况；未经审核的source/observation不获得“缺失API”或“SDK缺失”结论。

### 3.2 来源与观察责任

`source_reviews`为安全ledger的每个source保留一条紧凑覆盖记录：`source_ref`、`content_sha256`、`owner_queue`、`requirement=required_review`、`review_state`、`resolution_refs`、`review_record_ref`、`unknowns/blockers`。目录归属引用ledger，不复制原始title/URL/下载文件名。`review_state`可为pending/in_review/reviewed/blocked_source/revalidation_required。

`observation_reviews`为ledger每个observation保留一条：`observation_ref`、`source_ref`、`review_state`、`capability_refs`或`material_review_refs`、明确处置记录。owner继承source且必须可解析。声明与示例始终分开，parser相关示例只是线索；同路由不构成语义等价。

关键规则：

- source和observation两层都闭包登记；某页一项已实现不能使其余观察消失或自动完成。
- source整体reviewed需要对该版正文的场景范围负责；仅抽样、仅匹配一个声明、仅读标题不能当整页已审核。
- 零HTTP、仅示例、缺正文、SDK参考、附件全部保留。新发现的callback/原生symbol/组合场景以数字指针建立人工观察，再形成候选能力；不要求HTTP parser才能承认它存在。
- 非能力资料可经审核关联到related-material或记为有证据的资料处置；这只完成该source的审查责任，不产生“实现了一个API”。
- source缺正文可记blocked_source；它仍为必需未关闭审查项。不能用null pin、phase_2、not_applicable或“可选”默认清除。
- schema2的SDK/asset序数ID只在其manifest SHA内稳定；manifest换版时须重新映射，不按相同index、URL hash或猜测同名迁移。
- source mapping的四条已有operation关系各自保留source/alignment问题；人工审核来源例外不伪造机械exact恢复成功。

### 3.3 全量责任队列

| 队列 | 当前来源身份数 | 责任 |
|---|---:|---|
| SP009第一批A/B/C | 326 / 165 / 283 | 平台与云接入；云存储；AI/生态/蓝海 |
| SP009第二批A/B/C | 315 / 385 / 294 | 三组组件族；1273不在这些执行集合中 |
| SP009第三批A/B | 340 / 121 | SDK/音视频；正文与入口补充 |
| 海外全门户队列 | 52 | 现有候选覆盖的16篇仍需保留同页其他观察；不能只登记49候选而漏其余来源 |
| related-material队列 | 1770 | 静态参考293＋assets1477；全部先有审查责任，再按实际复用对象分组 |
| related-material版本补充审查 | 不按API计数 | 已有来源指向的官方SDK binary、官方仓库发行版及许可/兼容性；列为独立必需审查任务，不宣称当前4051已覆盖这些版本事实 |

前十个队列的来源身份联合集应等于4051，唯一owner无交集。国内部分严格复用SP009的2229身份集合与content pin，1273保留全部目录关系但owner只为第一批A。版本补充任务是额外审查义务，不虚构source或API行来凑数量。

执行可保持2–3个Claude会话并行，各写独立候选diff；由Codex核定跨域协议和cohort，一位集成写入者更新scope-register。队列职责不是接口支持、scope排除或验收通过标记。

### 3.4 related-material版本审查

`material_reviews`只需 `material_id`、`source_refs`、`owner_queue`、`requirement`、`upstream_component`、`selected_version_or_commit`、`artifact_sha256`、`license_evidence_ref`、`compatibility_scope`、`reuse_mode`、`capability_refs`、`acceptance_case_refs`、`review_state`、`unknowns`。

- 先区分reference-only图片/指南、可复用代码、运行依赖、再分发制品、SDK/媒体组件。未审查之前不自动把附件判为无关，也不为每张图片制造一个运行测试case。
- 实际采用的组件必须固定版本或commit和可核验制品，核对对应版本许可、平台/地区/型号/令牌边界；归档正文SHA不是SDK发行版本，项目自身MIT许可证不是上游许可证明。
- reuse_mode为direct_use/thin_adapter/independent_implementation/pending，并记录具体成本/能力理由。官方资料不完整不禁止自主实现，但事实与本地设计选择分开。
- 原生媒体/RTC引擎优先复用不等于集成已完成；材料审查、集成计划、真实集成必须有分别可追踪的case。
- 所有结果仍在acceptance；材料记录不重复维护offline/live的passed值。版本改变时相关集成证据需核对适用性，不能继承旧版成功。

## 4. 继续使用 `sdk-product-acceptance.json`

维持现有case_id、release_gates和历史结果。必要时在同一文件为case增加 `template_case_id`、明确的 `capability_ids`/`operation_ids`、`scope_revision`、`freeze_state`、证据输入SHA以及 `criterion_assessment`；不另造验收数据库。

`capability → case_instance → template_case`是多对多关系。实例必须绑定确切operation/能力、region/cluster、账号权限类别、设备前提、surface、风险、前置条件、验收判据、执行边界和恢复/授权状态。不同能力可以共享一次实际执行，但只能引用该执行真正覆盖的那部分语义和surface。

现有10项不是十个已冻结的万能模板：

- CN同步、异步、CLI各自保持；不能把同步成功复制成异步/CLI成功。
- 已有海外时区目录、开发者认证/空列表实例保留；新区域目录、OAuth、UUID查询分别建立具体实例，不继承它们的结果。
- `ACC-DOMAIN-OFFLINE-PER-NEW-001`和`ACC-WRITE-OPERATIONS-001`是政策占位；最终实例不能只写per domain、all writes或前缀选择器。
- 消息case仍须实际平台回调、ACK、重复投递/故障路径等证据；helper向量不替代真实接入。
- `ACC-MEDIA-OFFICIAL-INTEGRATION-001`家族分别承接材料/方案审查与固定版本的真实集成；SDK薄适配关联DOMAIN-OFFLINE，有实质写效果再关联WRITE。

`result`、`authorization_status`、`resource_state`不合并。recorded_evidence只说明引用了记录；完成判定还要核对判据、surface、地区、资源类别和代码/产物SHA是否适用。criterion_assessment记录pending/met/not_met或有明确适用性理由的not_applicable，不能用证据文件存在自动设met。

每个能力所需的offline/live_read/live_write/physical_effect/integration等层须在准入前明确适用性。未知适用性先记required_pending_design并阻塞关闭，不能默认optional；某层不适用需给理由和审核记录，例如匿名国家目录没有物理设备效果。既有产品CLI必需项不能因某个新API不新增CLI入口而消失。

bounds未冻结、资源不足、授权未取得可阻止真实执行，同时保留该必需case和判据；不能倒推成SDK功能不在范围。候选JSON的acceptance_criteria只是设计输入，后续状态和证据只写acceptance实例。

## 5. 全范围仍待审时，单cohort能否先实现

可以有条件准入；这不是本设计授权扩大实现范围，更不是将未完成项算完成。

依据是当前冻结计划：第15行“矩阵覆盖与优先顺序核定前不批量增加业务代码”；第45行“每批先固定……独立wire案例、诊断更新和验收场景，再派实现”；第49行规定逐域优先顺序。它要求全范围和批次设计先行，没有写成“所有来源的全部语义已关闭后，才允许实现第一个已核定cohort”。

更严格的全语义关闭要求明确存在于现有 `REL-PRODUCT-SCOPE-RATIFIED` **发布门槛**：no source row left unreviewed；`REL-PRODUCT-REQUIRED-COMPLETE`又要求全部required验收完成。不能把这两个门槛降成工程CI绿，也不能把单cohort准入当它们已满足。

本次提供的用户要求是“完整能力矩阵/验收范围先于批量SDK实现”。为避免用机械登记偷换“完整”，必须先满足全局G0，再判断单cohort G1：

**G0，全局范围与验收范围先登记并核定**

1. 全部4051 source、3019原有observation、203既有operation、45新增候选、四条来源关系及相关材料版本审查义务均有可追踪责任；每个产品族都在总表中。
2. 所有待审、源阻塞、资源/权限未知都继续是required review或pending_scope_review；只允许用户已指定的App adapter、选择性遥测、独立模型评测、RSI四类第二轮延期，不把官方AI服务误挪到模型评测延期。
3. 全局验收政策、已有历史实例、尚待实例化的必需场景、发布门槛和优先顺序有明确审查记录。scope-register不得自称all_semantics_dispositioned。
4. 不满足G0时，只有来源/设计/验收准备工作可继续，不能凭4051行账本或本设计稿直接宣称业务cohort已准入。

**G1，单cohort实施准入**

1. capability_ids是封闭的具体集合；每项均有已核定语义、地区/origin/身份权限、接口与请求/响应/重试/影响合同。影响本cohort安全或正确性的源冲突已解决，或有明确审核的SDK设计选择与独立验收；不能把unknown简单删掉。
2. 同页剩余观察和其他未审业务仍有独立责任条目，不能被cohort的implement处置吞掉。公共认证/传输变更可能影响的依赖也必须先核定；不以局部实现绕过跨域问题。
3. 具体离线wire/error/边界/诊断及sync/async验收case、文档入口与后续真实验收适用性先冻结；不只指向wildcard。live资源、次数和授权若仍待确认，可阻止live执行，而不自动阻止已冻结的纯离线SDK实现。
4. cohort具备唯一写入范围、依赖、负责人、独立验收条件和可回退集成范围；未批准proposal签名不能直接当已冻结接口。
5. `cohorts[]`记录admission_state及审查证据。实施准入、live执行准入、发布许可是三个不同决定；本轮只能在相应条件满足时推进对应阶段。

若控制器掌握用户更早明确原话“必须先完成全语义分类，才允许任何业务实现”，该更严指令优先，应把它的消息/原文依据记录在G0并要求all_semantics_dispositioned=true。当前已检查文件不足以宣称用户给过这一更严禁令，也不能用本稿覆盖真实存在的更严指令。不为此凭空增加重复确认流程。

## 6. 首个小cohort建议：公开区域目录

控制器已给出的边界可用于准备准入，不构成SDK已实现声明：

| 项目 | 准入输入 |
|---|---|
| 能力 | 保留`candidate:overseas:469:1.12`谱系；拟登记operation为`global.settings.list_areas` |
| SDK/runtime名称 | `settings.list_areas`，与registry ID区分 |
| 请求 | GET，官方固定origin `https://open.ezvizlife.com`；显式api_base_url，token_location=none，不调用token provider；不回退POST、不推导其他集群 |
| 数据语义 | 国家/地区目录，字段id/name/region/telephoneCode；它不提供集群域名，不据region字段自动构造认证或业务origin |
| 当前预检 | 控制器报告单次匿名HTTP观察到code/msg/data及240条记录；应关联其实际回执。240是一次观察，不是固定验收数量或目录总量保证；更不是SDK sync/async/安装包验收 |
| 具体验收 | 独立area目录离线case：GET/origin/无token与provider未调用、响应/error、sync/async、诊断覆盖。另设SDK公开只读case；不能复用时区目录case的成功状态 |
| 依赖与门槛 | 完成G0及此cohort的合同/case冻结后可先实现；其他所有未知范围仍required。真实SDK调用的边界在执行前冻结；现有海外无绑定设备不是该公共目录case的设备前提 |

建议case ID为 `ACC-OS-PUBLIC-AREAS-OFFLINE-001` 与 `ACC-OS-PUBLIC-AREAS-SDK-001`，具体字段落到现有acceptance文件；不在本稿中捏造新的成功结果、请求上限或执行授权。

## 7. 最小验证条件与交付顺序

这些是下一单数据生成/审查的验收条件，本次未编写或运行验证器。

1. **输入固定**：输入SHA与本次选定版本匹配；ledger必须schema2/reviewed_metadata_only。文件变版时重新判断受影响集合，不能把旧source index当新版本身份。
2. **范围守恒**：source_reviews身份集合精确等于ledger4051；原有observation_reviews精确等于3019；每项有唯一责任。新增人工观察另记，不混入原parser计数。国内SP009身份2229严格守恒，1273唯一owner正确；海外52及related-material1770不遗漏。
3. **能力与关系守恒**：203原operation全保留；49候选输入精确分成45候选能力与四条既有operation关系，不能把四条复制成新能力；后续拆分/合并保留谱系和审查依据。
4. **语义边界**：D/E/Z/M、same-route、parser-related-example、provisional/missing等机械字段不自动驱动能力处置。整页decision不能覆盖未审观察。所有unknown/blocked仍在必须项闭包中。
5. **验收引用**：每个已准入能力都有具体case，而非wildcard；层/地区/surface明确，历史证据匹配确切输入和执行范围。结果只维护一次；没有来源或回执的met、passed、live成功应拒收。
6. **材料边界**：拟采用组件有版本/commit、许可与兼容范围证据及实际集成case；仅资料的source有可追溯处置。材料登记或方案通过不能等价运行集成完成。
7. **cohort与发布分离**：有未关闭必需项时，允许满足G0/G1的局部实施，但all_semantics_dispositioned、required_acceptance_complete、product_complete及发布门槛不能被局部通过自动置真。
8. **公开边界**：只复用安全ledger ID与审核过的事实引用；不输出原始example path、host、section_title、下载locator、query/fragment或其hash替身。来源hash仅用于完整性；不复制原始正文或样例进scope-register。

最短落地顺序是：Claude按固定输入整理scope-register及具体area case → Codex核对范围守恒、当前cohort协议和验收 → 派单该cohort实现并独立验收，同时其他队列继续语义审查。无需先建设新的大型harness、调度框架或自动发布系统。全局未知项不是放弃项；直到全部必需语义处置、实现/集成、验收及受控发布条件满足，整体Goal仍未完成。

本设计的实施判断仅限已检查要求与文件；本次唯一写入为本私有设计稿，未生成上述两份工作数据、未修改registry/acceptance/SDK，未提交代码。
