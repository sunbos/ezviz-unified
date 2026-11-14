# H009：维护语义评测器与受控案例

固定devin/swe-2、effort=max，零工具候选生成；只接收本任务和计划。输出JSON对象files，包含且仅包含 `harness/maintenance_eval.py`、`tests/test_harness_maintenance_eval.py`、`evals/maintenance/cases.json`、`evals/maintenance/expectations.json`、`evals/maintenance/README.md` 五个文件。JSON文件的值也是完整文本字符串。不要运行声明或引入依赖。实现保持简单，避免通用Agent框架、网络和自动补丁。

## 数据协议

cases顶层严格为schema_version=1,cases非空列表。每case严格字段：case_id（M001等）、synthetic=true、region（domestic/overseas）、operation_id、question、fields（允许分析的字段名列表）、before_status/after_status（archived/source_missing）、before/after（行对象列表，每行严格id,text两个非空字符串字段，行ID在各侧唯一）。archived必须有行；source_missing必须空行。以计划8行表生成简短中文材料，b1/a1提供明确协议证据，其他行分别b2/a2等。源文本均为合成，没有真实密钥或设备。M008 question明确询问是否可把国内400上限用于海外；只应证据不足。

expectations顶层严格schema_version=1,expectations非空列表。每条严格case_id,decision,changes,uncertainties。decision枚举no_contract_change/contract_change/insufficient_evidence。changes每项严格field,before,after,before_evidence,after_evidence：field必须在case.fields中且单case不重复；before/after仅null/string/严格int/bool（不接受float），且类型+值不能完全相同；证据是对应侧存在的行ID非空列表，不得重复。uncertainties为source_missing/source_conflict/region_unverified的不重复列表。no_contract_change要求两数组为空；contract_change要求changes非空且uncertainties为空；insufficient_evidence要求changes为空且uncertainties非空。oracle与case身份集合完全一致。oracle正文与计划预期一致，M001/M003/M004只1个change，各用b1/a1，其他changes为空。

answers顶层严格schema_version=1,answers列表（允许空，以便评分漏答）。每条结构同expectation但case_id必须已知，不得重复。允许漏答，逐case记missing_answer并失败；不允许额外ID或字段、publish/score/passed等自报字段。列表、scalar、schema_version等需严格类型，避免True==1、200==200.0误判。

## API与CLI

纯函数 `evaluate(cases:object, expectations:object, answers:object)->dict`，不修改输入；错误输入抛自定义MaintenanceEvaluationError(ValueError)，固定错误文字不附内容。比较decision、完整变化(field/type+value/证据集合)、uncertainties集合；忽略行顺序/变化顺序/uncertainties顺序，不能忽略缺失/多余证据。每case报告case_id,passed,problems；problems固定集合missing_answer/decision_mismatch/changes_mismatch/uncertainties_mismatch。有效但不符oracle的数据是评分失败，不是解析异常。

结果严格记录schema_version=1,evidence='synthetic_maintenance_semantic_evaluation_only',passed,summary(total_cases,answered_cases,passed_cases,failed_cases),results，publication_authorized=false,patch_applied=false,real_api_used=false。不回显源段落、候选原文、预期答案或自由文字。报告不声称实际模型身份；模型运行凭据由主导方另记。

CLI：`python -m harness.maintenance_eval --cases ... --expectations ... --answers ...` 三项必填，只读取文件，stdout报告并附inputs_sha256（cases/expectations/answers原文件bytes的SHA256）。用同一份读取bytes解析与计算hash，文件上限2MiB（limit+1读取后拒绝）；严格JSON读取拒绝重复key、NaN/Infinity和非UTF8。输入错误固定stderr，不回显文件内容或异常链。exit0=全部通过，1=有效候选未通过，2=输入/读文件错误。不写文件、不执行候选、不导入SDK。

## 测试与说明

测试应覆盖：正确答案通过但不授权发布；缺答保持总分母；少报/误报变化；类型敏感（200 vs字符串200、True vs1）；一侧引用不存在行、重复证据；多余ID/重复case/多余字段；无变化却带变化；未知uncertainty；不修改输入；输入重复JSON key/非finite/UTF8/超限；CLI的0/1/2及sha与实际bytes一致；错误输出不回显植入PRIVATE_MARKER。使用独立小fixture或已知8案例，不从候选报告反推预期；可以用oracle生成一个正确控制答案以测试评分器，但必须另有独立错误突变。测试所有异常按真实接口构造，禁止__new__绕过构造或吞TypeError来适配未知签名。

README解释：公开合成基准，不是隐藏留出或真实官网变化；本轮只测语义提取，不测补丁正确性，运行模型前不能把expectations文件传入。模型身份不能由answers自报证明；计分器只评文件，实际运行另记。默认pytest仅测试评分器、不调用LLM。给出CLI复现示例；所有报告始终不授予发布权限。
