# H008B：组合离线维护审查

固定devin/swe-2、effort=max，纯文本零工具模式。允许内容通过stdin提供：本任务、H008A/request.md、harness/{audit,contract_eval,source_inventory}.py、tools/documents/verify.py。禁止读取工作区/设置、调用工具或命令。输出完整harness/review.py单文件Python围栏，不输出中间版本、不声称通过测试。Codex提供独立测试与最终审查。

实现ReviewError(ValueError)和run_review(repo_root:Path,before_dir:Path,after_dir:Path,*,cases_path:Path=Path('evals/contracts/cases.json'),timeout:float=90.0)->dict。这是门户快照→审查任务包，仅执行现有已审核SDK基线的离线eval，不生成候选、不修改SDK/快照、不抓网/发业务请求/发布。

复用：from tools.documents.verify import verify as verify_snapshot；harness.audit.audit_repository/_load；source_inventory.inventory；review_plan.plan_review；contract_eval.run_evaluation/_source_snapshot。不要重写这些算法，不调用verify.py的CLI（CLI会写报告）。所有CLI错误只打印固定短消息，不输出异常文本、请求/响应或原始文档。

执行顺序：
1. resolve各root，读取before/after的catalog.json与derivation-summary.json原始字节并hash。文件须在对应snapshot root内（拒绝越界/逃逸symlink），JSON必须有效。
2. 两个snapshot均调用verify_snapshot；passed必须严格True，input_catalog_sha256和derivation_summary_sha256必须等于刚读取的字节hash，否则ReviewError。只保留scope/documents/archived_bodies/missing_body_classifications/encoding_warning_count等摘要，不输出error.detail。错误/异常不调用SDK eval。
3. _source_snapshot(repo)固定SDK源码；audit_repository(repo)，用_load加载contracts、bindings、issues，三份hash须与audit一致。inventory(before_catalog,contracts)确认当前登记对应before的地区/来源URL/内容hash；不能据新快照悄悄改登记。baseline不匹配固定ReviewError。
4. plan_review生成数据任务包；run_evaluation(repo,cases_path,timeout=timeout)跑真实SDK MockTransport基线。它执行SDK有进程/网络防护，但不是OS安全沙箱。无论文档变化与否都必须评测完整基线；不得提供跳过eval的参数。
5. eval输入contracts/bindings哈希必须与步骤3一致、source_files_sha256与步骤3固定的源码集完全一致；重新_load三份登记hash、两快照catalog和derivation-summary hash、_source_snapshot源码，任一变化拒绝，不发布混合版本报告。

输出schema_version1,evidence='offline_maintenance_review_packet',decision：eval.passed或coverage_complete不是True则baseline_failed；否则plan.needs_review为True则review_required，余下no_document_review。保留snapshot_integrity{before,after}、baseline_source_links（inventory.summary）、registry_audit（audit.registry/audit.code摘要）、conformance（仅evidence/passed/coverage_complete/summary/guard_failure及failed_operations的唯一ID数组，不复制1272+行结果）、review_plan（完整）、inputs_sha256{before_catalog,before_derivation,after_catalog,after_derivation,contracts,bindings,issues,cases}、source_files_sha256、publication_authorized=False、candidate_generated=False、patch_applied=False、real_api_used=False、limitations。

limitations：未比较静态SDK/附件；完整性不证明官网无缺失/语义正确；基线通过不证明未来候选正确，不是模型eval分数或发布许可；不运行完整pytest；快照目录应保持不变，后续读取文档须按记录hash重验。

CLI python -m harness.review --repo . --before SNAPSHOT_DIR --after SNAPSHOT_DIR [--cases ...] [--timeout ...]，stdout JSON（不提供--output，不写文件）。参数缺失走argparse。正常报告退出：baseline_failed=1，其余=0（review_required也不是失败）；输入、校验、期限、版本不一致等错误退出2，stderr固定'harness-review: source, registry or evaluation validation failed\n'。不得出现safe_to_publish=True。大约300行以内，明确小helper。
