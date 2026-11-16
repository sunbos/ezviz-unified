# SP003：官方能力矩阵生成器（仓库工具，非SDK功能）

输入版本81b86b4 + docs/plans/2026-11-17-sdk-product-completion.md（本轮冻结设计）。固定devin/swe-2 effort=max，在当前受控worktree直接实现并自测。唯一可写：harness/product_matrix.py、tests/test_harness_product_matrix.py、docs/product-matrix.md。不得写src/contracts/evals/其它tests/用户CLAUDE.md。默认断网，不能调用设备、读取凭据或工作区外文件。提供的真实schemas仅为公开字段，不读取真实data目录；先用合成fixtures验证，Codex审查后另跑真实输入。

公共接口冻结：build_matrix(repo_root: Path, manifest_path: Path, decisions_path: Path|None=None)->dict。CLI python -m harness.product_matrix --repo PATH --manifest PATH [--decisions PATH] [--output PATH] [--summary-only]。默认stdout，--output显式写JSON，不覆写任何输入文件；固定错误stderr/exit2。只做离线文件读取/归属汇总，无SDK业务执行或网络。复用harness.audit.audit_repository对已登记实现做AST/registry审计（不要重复实现审计器）；报告不能称实机或全产品完成。

manifest schema_version=1, snapshots=[{id,kind,root,manifest_sha256,inventory? ,inventory_sha256?,region?}]。kind为portal/sdk_reference/assets。root是调用方明确给出的资料根目录（测试放tmp_path，不猜cwd）；portal的manifest固定catalog.json，sdk_reference固定manifest.json，assets固定manifest.jsonl。分别验证manifest原字节SHA256；如果有HTTP inventory则路径与SHA都必须同时存在、验证原字节。读取路径限制在各明确root或显式inventory文件，不能遍历任意文件，归档local_path/raw_file必须位于根内。JSON严格拒绝重复key/NaN；错误不回显原始数据。不得修改任何输入。

输出至少：schema_version=1,evidence="source_and_registry_matrix_only", product_complete=false, denominator_defined=false, input_hashes, source_sets, sources, observations, capabilities, unresolved, summary。
- sources：每条portal catalog、每条static manifest.entries、每条assets manifest.jsonl都保留唯一source_id=(snapshot.id +稳定region/doc_id或URL)，包含公共来源身份、status、hash、category_paths、kind、disposition、operation_ids、evidence_refs。不可丢缺正文/缺文件条目；不存在正文可为blocked_source，存在但未审查默认needs_review。不能仅因标题、无HTTP声明或URL重复判定已实现/非API。
- observations：每条HTTP declaration/example分别保留source_id、kind、ordinal、section/element定位、status、method、path（原样模板）、host。不复制raw curl、参数值、证据自由文本、query/fragment值。source_id只用公共文档身份，不用设备/用户hash；字段白名单投影。same_route_operation_ids只保留hint，不能标supported。
- 精确关联contract须region/doc_id/source_url/content_sha256/method/path六项全等；绝不把same_route当精确支持。允许同页多个观察与contract，不让已实现的一项吞掉同页未知项。
- capabilities：现有每个reviewed contract保留唯一contract:<operation_id>，绑定module/symbols/public_entry从已审核登记，implementation_status=ast_verified；offline_status/live_status严格按binding原记录标记为recorded_evidence，不升级为本轮实测。即便当前声明未恢复也保留现有实现并标source review问题。
- 未登记观察不能自动变成API实现：作为provisional capability / review item，状态needs_review或blocked_source，linked_op_ids空。文档无声明、callback/SDK参考/附件仍需处置，不静默丢弃。不要将provisional数量称API总数/覆盖率。
- decisions schema1, decisions=[{source_id,content_sha256,disposition,reason,evidence_refs,capability_id?}]。仅明确接受的处置集合 implement/reuse_official_component/not_an_api/duplicate/deprecated/phase_2/needs_review/blocked_source；content pin必须匹配source（缺正文source允许null但要记录），reason和evidence_refs非空，重复/未知source/过期pin都拒绝。不据decision宣称测试/代码已完成。默认不存在decisions也能产出全部待审矩阵。
- summary 分开source_count/observation_count/reviewed_contract_count/unresolved counts和各kind/status/disposition计数；不输出百分比、不把source数当API数，不将blocked计入通过。保留known reviewed contract的来源不匹配/缺观察例外。

真实输入schema：
portal catalog为list，row含region,doc_id,source_url,title,status(archived/source_missing...),content_sha256,raw_file,raw_file_sha256,memberships(含category_path),missing_evidence。
HTTP inventory为dict documents=list，doc含region,doc_id,source_url,content_sha256,category_paths,status,declarations,examples,warnings；declaration含ordinal,status,method,raw_address,address_kind,host,path,query,fragment,section_title,section_index,evidence(list，element_index/text/role),reasons,reviewed_operation_ids,same_route_operation_ids；只投影上面许可信息，不信任其reviewed IDs，重新与六字段核对。
static manifest为dict entries=list，entry含url,status,local_path,sha256,discovered_from；kind应登记为sdk_reference待审/官方复用候选，不能自动判断服务端REST。
assets manifest.jsonl entry含asset_id/url/status/file/sha256/category/references（具体可参考tools/documents/collect_assets.py）；缺失/排除也保留，不下载或执行文件。
registry实际字段名是source_content_sha256（不是content_sha256），source_doc_id（不是doc_id）；其余请Read当前worktree的contracts/harness既有源码，不猜名字。代码审计只证明AST符号可达；保持证据分层。

测试：先写有意义失败案例再实现；使用tmp_path构造输入及最小合法repo或mock audit_repository的明确结果，不依赖本地data，默认断网。覆盖源记录全保留、重复归属不增分母、same_route不误认精确匹配、缺正文/无声明/SDK/assets仍待审、敏感code/query不输出、错误SHA/路径越界/duplicate JSON/过期decision拒绝、已有实现未恢复仍保留、CLI output不覆写inputs。不要镜像实现后自证，不降低断言。

授权命令仅spec所列，按需执行有依据的修订回归，不执行全仓Ruff。输出精简真实diff摘要、执行结果及未解决项。不要修改工作区信任/配置。若源码或依赖不清楚可Read指定公开代码；禁止新增不必要通用框架或SDK业务代码。
