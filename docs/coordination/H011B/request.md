# H011B：独立分页修复场景与Oracle

devin/swe-2、effort=max、零工具。不接收模型补丁。输出JSON代码块files，仅包含evals/maintenance/repair_cases/page_size_limit/case.json、oracle.py、README.md（键用完整路径）。不要执行、改SDK或声称通过。

case.json明确schema_version1、case_id=R001（中性ID）、synthetic=true、目标cn.storage.spaces、allowed_paths只有src/ezviz_unified/storage_models.py、原规则最大50/模拟新规则最大20、未改变默认分页与其它接口；包含原文来源https://open.ys7.com/help/5230及给定归档hash。模型输入说明用于“已确认新契约到代码补丁”阶段，不能宣称真实官网更新。不要把oracle代码传给待测模型。

oracle.py由可信协调方在副本中运行，提供：
1. `validate_scope(before_source: str, after_source: str) -> None`：AST比较只允许顶层函数_spaces_query中upper-bound整数50变20和对应ValueError文本1..50变1..20，恰好各一处；其它AST结构完全相同（忽略行列位置），该范围之外的修改必须拒绝。不要用全局字符串replace接受连带修改。不执行源码。
2. `adapt_regression_test(source_text: str) -> str`：针对附带tests/test_sdk_storage.py内test_documented_inclusive_bounds_and_no_extra_batch_limit的spaces调用，将其page_size=50正例改为20，且仅这一处；其它50（文件ID列表、下载限制、StorageFileQuery等）不变。返回新文本，不写文件；唯一性/预期原文不符要失败。此调整属于操作者预先固定的新契约测试，不由候选生成。保留已有覆盖，不能删除失败测试。
3. pytest可发现的独立测试（文件名oracle.py时将以显式路径调用）：真实Client/AsyncClient + MockTransport，spaces(page_size=20)成功且query为20；21与50在认证/业务请求前ValueError；None默认不额外注入pageSize；1仍成功；StorageFileQuery(file_node_ids=["EVAL_NODE"],page_size=50)仍接受，51拒绝；download_urls的50个ID仍接受。所有数据合成，clock/TokenManager/Client构造签名取附带源码/测试，不绕过构造或使用真实API。

oracle函数任何失败不能被candidate自报passed覆盖。不能修改SDK、原始测试或注册表，只由协调方把返回的适配文本用于隔离副本。README解释这个受控案例只验证一个参数修改与回归，当前没有源登记晋升、真实服务或自动发布；AST范围检查限制第一例，未来复杂修复需另定独立验收。
