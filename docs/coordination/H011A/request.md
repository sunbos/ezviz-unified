# H011A：补丁提案准备器

devin/swe-2、effort=max、零工具。返回JSON代码块files，且仅包含harness/patch_proposal.py、tests/test_harness_patch_proposal.py、docs/research/2026-11-15-patch-proposals.md。实现与测试由你生成，不能执行或声称通过；不修改SDK。

API：`prepare_proposal(repo_root: Path, proposal: object, allowed_paths: Sequence[str], *, input_sha256: str, max_bytes: int = 2097152) -> dict`。proposal严格字段schema_version（严格int1）、input_sha256、edits列表。每edit严格path/base_sha256/old/new，后三个文本字段：base_sha256为64位小写hex；old非空、new可空但不能与old相同。空edits合法表示无候选改动；不接受score/passed/approved/权限等额外字段。

allowed_paths是操作者单独提供的非空唯一字符串序列，不从proposal推导；路径必须为规范相对src/ezviz_unified/*.py（允许包内子目录），拒绝绝对路径、..、反斜杠和目录/外部symlink。每个被修改文件须在allowlist中，实际常规文件的当前SHA256必须等于提案base_sha256；同一文件多edit的base_sha必须一致。input_sha256须与操作者参数相同，不能信模型自报授权。

按原始文件定位每个old，必须精确出现一次（包含重叠出现的检查）；同一文件各替换范围不得重叠。基于原文从后向前应用范围，不能把前一项生成的新文本作为后一项的旧文本。新文件须保持UTF8和末尾换行，ast.parse通过；不执行新代码。使用difflib.unified_diff生成标准相对a/path、b/path diff，不自造patch解析器。读取结束后复查相关源文件哈希，不输出混合版本。

返回schema_version=1,evidence='proposal_shape_hash_and_syntax_only',input_sha256,files列表（path/base_sha256/new_sha256/new_text/diff）、has_changes(bool)、publication_authorized=false,patch_applied=false,real_api_used=false,limitations。它仅校验形式/来源/语法，不证明逻辑或安全，不自动写回/测试/发布。明确允许范围由调用方控制，不能接受模型的自授权字段。

CLI --repo/--proposal/重复--allow/--input-sha256/--max-bytes；proposal从文件限量读取(limit+1)，拒绝重复JSON键、非有限值、坏UTF8。最终报告的实际UTF8 stdout字节（含换行）也≤max_bytes，按同一序列化核对，不截断。错误固定stderr `patch-proposal: validation failed\n`、exit2；有效含无改动exit0。自定义ProposalError(ValueError)，异常链不保留输入文本/内部异常。不要访问模型服务或SDK真实API。

测试真实临时文件：正确精确替换和diff、新SHA、原文件不变；input/base hash不匹配；未允许路径/越界/目录/外部symlink在读取前拒绝；old不存在/多次/重叠出现、替换范围重叠、不同base hash；新语法错误/无末尾换行；额外字段和bool版本；空提案；中途源变化；真实CLI输出预算与固定错误不泄漏PRIVATE。不要通过扫描sys.modules猜接口。
