# 公共国家/地区目录：接口与验收冻结

当前状态：Codex 已通过 [SP030 准入记录](../coordination/SP030/admission.json)批准 G0 范围责任及本 cohort 的 G1 实施准入。下文保留准入前的冻结合同；真实 SDK 验收仍须等待实现审查与离线验收，尚未执行或通过。

输入基线641d6cc10010b11a49c65c9d6783f92eefda00d3；本文件是Codex接口设计。接口与两条具体验收case（`ACC-OS-PUBLIC-AREAS-OFFLINE-001`、`ACC-OS-PUBLIC-AREAS-SDK-001`，`scope_revision=2026-11-17-r1`）已冻结为实施合同并落入 `docs/validation/sdk-product-acceptance.json`；但 `freeze_state=frozen_for_implementation` **只冻结条件，不批准实施**。本cohort仍须通过完整scope-register的G0范围核定与G1单cohort准入审核后才能派实现；全范围登记、单cohort实施准入、live执行、发布是四件不同的事，此处只完成接口/案例冻结。全Goal其它必须项不因此缩减。

## 官方证据与已有能力

- overseas:469，content SHA fa599256d11f6097dc4dd429b1b1bc2f93f3431007b327ca2216f03633add4e8，e1459固定地址open.ezvizlife.com/api/lapp/area/list，e1463列GET/POST，e1468展示GET，e1476列字段id:int、name:string、region:string、telephoneCode:int。
- 2026-11-16 UTC23:24:13的SP016仅一次无凭据GET，HTTP200/code字符串200/data数组240项，四字段类型一致。记录docs/validation/live/2026-11-17-public-area-directory/raw-http.json；这是HTTP协议观察，不是SDK验收，240不作为固定通过数量。
- Client现有token_location=none路径要求显式api_base_url，不调用token provider、不附token、只发一个请求；复用该路径与现有settings.list_timezones风格。

## 固定公共接口

- Settings.list_areas(self) -> ApiResponse；AsyncSettings.list_areas(self) -> ApiResponse，async版本需要await。
- runtime Operation.name为settings.list_areas，registry operation_id为global.settings.list_areas，public_entry/diagnostic entrypoint为settings.list_areas。
- GET /api/lapp/area/list，overseas-only、read_only=True、token_location=none，无query、body或业务header。不调用POST、不加token/自动认证、不重试或追随来源域名。
- 沿用Client显式api_base_url及transport_factory，SDK不覆盖调用方配置。文档与真实SDK验收必须显式使用https://open.ezvizlife.com；其它入口及调用方代理未由本条来源证明。
- 复用现有ApiResponse DTO与code/msg/data policy（成功值字符串200）。不转换data、不将id/region映射成RegionConfig或集群地址、不制造apiDomain/authDomain字段。返回字段表说明为国家/地区目录，不与设备时区目录混用。
- 新方法使用已有_diagnostic_scope，sync/async各自固定mode；默认记录关闭，无额外业务请求、全局HTTPX改动或后台任务。

## 版本与source issue

registry body_encoding沿用现有GET的unknown枚举，同时约束明确本GET没有body/query，而不是编码未核定后随意发送。明确源列双方法、SDK选择GET；不能为提升来源匹配率伪造declared_pair。source issue记录POST认证/编码未核定与GET选择证据；保留既有两个单侧反引号例外。新登记可能增加parser未恢复数，必须解释，不能削弱source gate。

## 必须离线判据

1. sync/async方法均发准确GET/path，空query/empty body，不含token/Authorization；即使Client持有refreshable provider也不能调用它。正常ApiResponse/data保留额外字段和空数组，不硬编码240。
2. 国内region、未配置public api_base_url和closed client在发请求前失败，沿用现有异常语义；token-free请求无认证重试，业务错误/HTTP错误/格式错误与公共目录基类一致。
3. SDK返回值与异常语义不因diagnostic on/off改变；每mode覆盖成功、前置验证、业务失败、传输失败、取消/关闭的相关路径，复用既有机制而不重建测试框架。
4. registry/bindings/eval fixture/source issue及coverage summary同步；diag entrypoint与两种operation name白名单、coverage manifest、仓库覆盖审计同步。
5. fixture使用完全合成国家数据与占位配置，不复制240项响应或真实样例；sdk tests不导入harness/docs/data，仓库审计测试单列。

## SDK真实验收场景（待接口实现后执行；result=pending_execution）

固定文档origin，anonymous Client与AsyncClient各调用一次，合计最多2HTTP，无凭据、body、query、redirect、retry，每个client正确close/aclose，响应1MiB上限通过验收transport边界明确控制；不改变SDK全局设置。公开回执只保存源/代码hash、mode、请求数、HTTP/业务成功状态、data类型、数量和字段类型聚合，不保存原始响应/URL参数值。两次都满足policy与列表结构，至少一个元素含四个已知类型字段；不把数据条数固定成240。若失败即如实记录，不自动追加探测。该case全部criterion为pending，不表示已授权执行。

这是公共目录SDK行为验收，不证明海外账号设备、集群路由或硬件。海外无绑定设备和消息服务未知的独立必需场景仍为阻塞。

## 派单边界草案（未准入）

以下仅为准入后的责任范围预案，不表示本cohort已获实施准入；`admission`由scope-register的G0/G1审核决定，不由本文件自封。

单一实现方负责settings.py、诊断registry/覆盖清单、contracts三份登记/summary、evals/contracts/cases.json、新SDK与harness测试、使用文档及示例入口。公共文件不得与其它cohort并行写入；后续独立批次可先做各自领域源码/安全事实，再由唯一集成方更新共享文件。具体唯一文件清单/命令以准入后的任务单冻结。
