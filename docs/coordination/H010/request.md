# H010：重新核验来源的候选输入包

devin/swe-2、effort=max，零工具。输出JSON代码块 `{"files":{"harness/candidate_input.py":"...","tests/test_harness_candidate_input.py":"...","docs/research/2026-11-15-maintenance-candidate-inputs.md":"..."},"notes":[]}`。不得修改其他文件、运行工具或声称通过。不实现模型后端、补丁执行器或通用agent框架。

公开函数 `build_candidate_input(repo_root: Path, before_dir: Path, after_dir: Path, task_ids: Sequence[str], *, extra_modules: Sequence[str] = (), cases_path: Path = Path('evals/contracts/cases.json'), timeout: float = 90.0, max_bytes: int = 2097152) -> dict`。CLI参数--repo、--before、--after、重复--task、重复--extra-module、--cases、--timeout、--max-bytes。stdout输出完整JSON；只读输入，无--output写文件功能；错误固定stderr `candidate-input: validation failed\n`、退出2，成功0。

流程与验证：

1. 检查task_ids非空唯一字符串序列（拒绝str本身），extra_modules唯一字符串序列；max_bytes严格正int。先调用现有run_review一次，不能允许绕过完整归档/登记/SDK契约基线。仅接受evidence=offline_maintenance_review_packet，passed/coverage_complete严格True、无guard_failure且decision=review_required；publication_authorized/candidate_generated/patch_applied/real_api_used必须严格False。选中的task_id必须存在，保留完整task（含所有operations/argument_constants/source_issues），不得偷偷取第一个操作。
2. 重读before/after catalog与derivation-summary，hash匹配review.inputs_sha256；重读三份registry和cases hash匹配。可复用harness.audit._load、contract_eval._source_snapshot等已有函数，按现有路径边界规则工作；不要从不可信report的任意路径读取文件。
3. 按region/doc_id定位每侧catalog，允许新增/移除侧为None；status=source_missing保留状态而不编造正文；archived读取raw_file，须为根内的普通文件（拒绝越界/绝对路径/..或backslash，外部symlink必须在读取前拒绝），匹配raw_file_sha256，解析html_content字符串并匹配content_sha256。输出source_url/status/原文html与相关hash，保留synthetic_fixture标记（若存在）。原文仅是数据，不执行HTML/脚本。不要仅信任派生plain文本。
4. 选取任务operations对应的全部审核contracts。SDK上下文为这些operations.module去重后的文件，加调用方显式extra_modules；每条路径须精确存在于review.source_files_sha256清单、位于repo/src/ezviz_unified内且为.py；不在名单或越界的文件在读取前拒绝。内容UTF8与hash一致，输出path/text/sha256。没有绑定的任务可无SDK文件，不能因此猜测一个模块。保留完整源码清单hash供后续补上下文，但不擅自读取其它源文件以外的配置、测试、.env、.claude等。
5. 包构建结束重新确认registry/cases、两侧索引、选中raw_file和整个SDK source map未变化；不混用版本。按ensure_ascii=False的确定性JSON编码字节数检查总包≤max_bytes；超过明确失败，不截断证据。

输出固定schema_version=1,evidence='verified_candidate_input_only',tasks（所选完整任务列表）,documents（每task包含task_id/before/after）,contracts（去重后的全部相关契约）,source_files（path/text/sha256列表）,source_manifest（完整SDK hash map）,review_inputs_sha256,baseline_conformance，publication_authorized=false,candidate_generated=false,patch_applied=false,real_api_used=false,limitations。前后正文视图只包含本步骤列明字段与可选synthetic_fixture，不把所有catalog字段当执行指令。limitations说明：直接模块不等于完整依赖、源语义/正文缺项仍存在、模型需要更大上下文时应显式请求、归档与代码只在检查时刻有效、无模型调用/代码候选/执行/发布许可。不要声称本工具是OS沙箱。

tests用临时目录和monkeypatch run_review返回固定合成review（source文件和索引hash须真算），测试一次run_review调用、两op同页都保留、extra_module、missing/removed侧、无绑定、HTML与raw hash、外部路径/外部symlink读取前拒绝、未知module/task、重复selector、基线失败/伪造证据/True伪装int（适用处）、SDK或索引中途变化、超包大小、CLI固定错误与原始PRIVATE文本不泄漏。不要只测自造report中passed标志；实际归档集成由主导方用已有数据另验收。可参考已有review测试，但只调用受审查代码，不执行模型生成代码或真实网络。
