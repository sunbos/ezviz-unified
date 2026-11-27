# 文档维护 harness

## 构建维护候选输入

`python -m harness.candidate_input --repo . --before data/official-docs/BEFORE --after data/official-docs/AFTER --task domestic:5230`会先运行完整review基线，再重新核验并打包所选任务的前后HTML、相关契约与直接SDK模块；执行前须替换为已准备好的快照目录。额外上下文通过`--extra-module`显式指定，不能读取清单外的配置文件；超出实际UTF-8输出预算时失败。它只生成输入材料，后续模型调用、补丁生成及独立验收仍是分开的步骤。具体参数、目录稳定要求和限制见[使用说明](../docs/research/2026-11-15-maintenance-candidate-inputs.md)。

## 维护模型语义评测

`python -m harness.maintenance_eval --cases evals/maintenance/cases.json --expectations evals/maintenance/expectations.json --answers <answers.json>` 对独立会话产出的候选判断评分。它比较字段、JSON类型、证据行和不确定项，漏答保持总分母；不调用模型、不执行补丁、不授予发布权限。数据协议及范围见[公开合成基准](../evals/maintenance/README.md)，第一次真实模型调用的[8案例结果](../docs/validation/maintenance/2026-11-15-swe2-semantic/README.md)只证明该次语义提取表现。

## 一次生成维护审查任务包

```sh
python -m harness.review --repo . \
  --before data/official-docs/BASELINE \
  --after data/official-docs/CURRENT > review-packet.json
```

两个目录均须包含采集、派生后的门户快照。before必须与当前审核契约的来源URL和正文hash一致；after可以有新增、移除或变化，但不能先改登记来掩盖基线失配。工具按顺序验证原始归档与派生索引、审计登记/AST、规划变化任务，再运行完整的合成SDK契约基线。登记、案例、SDK源码或快照索引在过程中发生可检测变化时，报告作废。

决策为`no_document_review`（未发现本轮文档差异）、`metadata_only`（标题/导航变化，需单独评估生命周期等信息）、`review_required`（来源/正文/可用性变化）或`baseline_failed`（SDK基线失败/覆盖不足）。前三项退出0只表示报告生成成功，baseline_failed退出1，输入/完整性/期限/版本错误退出2。标题和导航不自动产生协议补丁；完整前后导航保留在报告中。

每页任务保留全部直接绑定及选择参数、相关来源问题；认证等共享核心会提示扩大回归范围，不冒充完整调用图。源页面缺失不自动删除SDK，新增文章也不直接判为新API。任务包不生成候选、不修改文件、不抓网、不调用真实业务API或发布。SDK基线仍执行在既有子进程防护中，它不是OS安全沙箱。

本命令目前只比较门户catalog，未比较静态SDK参考库或附件内容，也不运行完整pytest。归档一致性不证明上游无缺失或语义正确，基线通过不证明未来候选正确；报告始终标明`publication_authorized=false`。快照目录应保持不变，后续读取原文仍须重验记录的hash。

下列独立工具仍可用于逐阶段排查。

当前实现门户目录差异检测、直接绑定的影响定位、静态登记校验与离线传输评测，不调用模型、不修改SDK、不自动发布。正式输入先用 `tools/documents/verify.py` 验证原始归档，再运行：

```sh
python -m harness.docdiff \
  --before data/official-docs/OLD/catalog.json \
  --after data/official-docs/NEW/catalog.json \
  --output reports/docdiff.json
```

按region/doc_id比对正文hash、可用状态、来源URL、标题和导航。只因抓取时间或原始响应包装变化不会产生正文变更事件。报告中的 `needs_review` 表示新增、删除、正文、状态或来源需要协议审查；标题和导航变化仍记录，但不据此要求代码改动。该值不是自动发布许可，也不是语义兼容性证明。

工具只比较catalog声明的hash，不访问清单中的URL，不解析正文指令。畸形输入和重复身份会失败；报告不能覆盖输入清单，写入使用随机临时文件和原子替换。既有2269项门户清单自比较已验证无差异。

随后运行 `python -m harness.impact --diff reports/docdiff.json --bindings contracts/sdk-bindings.json --issues contracts/source-issues.json --output reports/impact.json`，定位直接关联的SDK入口，并列出未映射文档和已有原文问题。它重新计算needs_review，不信任输入中的批准标志；不加载源码、不覆盖输入、不会输出发布许可，也不分析全部间接依赖。

`contracts/sdk-bindings.json`记录地区契约与实现符号的关系；mixin实现另标public_entry，共享枚举入口记录argument_constants。不能只凭方法名推断具体操作。离线通过、真实成功及未验证分别记录。`contracts/source-issues.json`保留来源问题与SDK处理方式。静态SDK参考站点仍未接入；新快照采集、候选变更、维护模型评测与审核发布继续推进，不宣称已经实现无人维护。


## 登记与静态代码校验

```sh
python -m harness.audit --repo . --check-summary
```

校验operation与绑定集合、地区/文档身份、source hash格式和同源一致性、参数与token位置、官方契约覆盖数量——参数`required`可为显式`null`表示官方必填性未标明，但必须同时给出非空`required_uncertainty`，缺失required键或无解释`null`仍拒绝，`accessToken`依旧必须`required=true`；再通过AST检查实际类方法、mixin继承、嵌套client入口和常量参数名。C3仅对本地类解析；明确构造赋值以外的动态或条件属性不作为可达证明，不执行或导入SDK。

已修复早期缺失的来源哈希和read_only标记，并规范form token位置。报告记录三份官方登记输入和实际解析源码的哈希。live_evidence只检查文件引用存在，实测内容和HTTP语义仍需独立验收；工具不会将空列表、失败、未验证合并为成功，也不是发布许可。

[coverage.json](../contracts/coverage.json) 由登记生成。变更登记后，在仓库根目录运行以下命令。审计成功后再替换摘要，生成失败时保留原文件。

```sh
(
  set -e
  summary=$(mktemp contracts/.coverage.XXXXXXXX)
  trap 'rm -f "$summary"' EXIT
  python -m harness.audit --repo . --summary-only > "$summary"
  mv "$summary" contracts/coverage.json
  python -m harness.audit --repo . --check-summary
)
```

该校验不证明catalog hash与原始网页相符、不验证每个参数的官方含义、不判断模型质量或真实设备能力；来源归档仍需前述verify.py，协议和行为仍需专项测试与实测。当前通过的是固定结构/静态回归，不是模型自我改进的eval成绩。

## 离线传输评测

运行 `python -m harness.contract_eval --repo . --require-all`，按[独立案例](../evals/contracts/README.md)调用同步/异步SDK，在MockTransport上检查报文、响应与故障处理。父进程校验场景完整性和源码哈希；异常输出不回显，Python网络调用被阻断。`token_location=none`的公开业务操作评测要求零认证请求、恰好一次业务调用、`10002`按原错误抛出不重放；认证端点本身的既有评测不受该分支影响。此检查不证明真实设备能力或模型质量，不是运行不可信代码的OS沙箱。

SDK和维护检查均不依赖旧utils或旧迁移清单。旧对照冻结为研究资料，不作为覆盖率分母、CI输入或发布要求。

## 官方目录关联

`python -m harness.source_inventory --catalog <snapshot>/catalog.json --repo .`逐页核对当前契约引用的地区、文档ID、URL和正文hash，并保留未关联与源缺项。`--summary-only`输出分类统计及输入hash。报告不读取raw_file、不访问URL、不把文档数当接口覆盖率；一个已有契约的页面仍可能包含其他未审核端点。多分类统计不可加总，原文完整性继续由采集verify.py检查。示例结果和复现步骤见[范围对照](../docs/research/2026-11-14-official-scope.md)。
