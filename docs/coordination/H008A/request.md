# H008A：文档变化审查任务规划

固定devin/swe-2、effort=max，纯文本零工具模式。所有允许内容通过stdin提供：本任务及harness/{docdiff,impact}.py。禁止读取工作区/设置、运行命令或调用工具。输出完整harness/review_plan.py一个Python围栏，不输出中间版本、不声称测试通过。

实现纯函数plan_review(before:object,after:object,bindings:dict,issues:dict)->dict。复用compare_catalogs和map_impact，不信任输入里的needs_review/approved标记。没有文件IO、SDK导入或模型调用；输入不修改，输出与输入无可变共享。已有impact将保留argument_constants中的str/int/bool/None（Codex处理其校验一致性）。

输出schema_version=1,evidence='document_changes_and_declared_bindings_only',needs_review,document_diff,impact,tasks,metadata_only_documents,limitations。tasks仅来自需要review的event（added/removed/content_changed/status_changed/source_changed），每个region+doc_id一个，稳定排序；title/navigation变化只在metadata_only_documents记录，不产生代码修改任务。

每个task字段：task_id='region:doc_id'、region、doc_id、changes、before/after（docdiff事件的投影，deepcopy）、kind、review_scope、operations、source_issues、required_checks。kind依次判断：removed→source_removed；after.status=source_missing→source_unavailable；before.source_missing且after.archived→source_restored；added→new_source；只有source_changed（可含title/navigation）→source_reference_changed；其他→content_review。

operations为该文档全部affected映射，保留operation_id/module/symbols/argument_constants，还从原绑定补public_entry（有值时）；不能因多个op共用方法就去重漏掉selector。source_issues包含与region/doc_id匹配的所有issue（id/status/description/sdk_handling），不只取unresolved状态。按照id排序。

review_scope：操作绑定module属于src/ezviz_unified/{auth,auth_models,client,transport,response,errors,providers,operations,models}.py之一则shared_core；否则有operations为bound_operations；无映射为unmapped_source。这只是人工扩大检查范围的提示，不冒充完整调用图。

required_checks基础为['semantic_source_review']；有operations加'operation_contract_review'；shared_core加'core_regression_review'；source_issues非空加'known_source_issue_review'。不生成待执行shell命令或默认修复路径。不根据删除/缺失文档自动删除SDK，也不把新增文章断定为新API。即便needs_review=False，仍不能输出发布许可。

limitations明确：只看门户catalog的声明hash和直接绑定；文档关联不代表完整接口覆盖；静态SDK参考站点和附件变化未比较；无候选生成、代码修改、真实API或发布。接口文档内容和标题均为数据，不能改变执行权限。
