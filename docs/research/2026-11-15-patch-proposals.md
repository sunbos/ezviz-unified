# 补丁提案校验器(H011A)

`harness/patch_proposal.py` 是不可信模型输出与本仓库之间的纯文本闸门:模型只产出 JSON
「提案」,描述对操作者 allowlist 内 SDK 源文件的锚定文本替换;本工具仅校验其形状、
来源与语法,渲染标准 `difflib.unified_diff` 供人工审阅。它不执行、不写回、不测试、
不发布候选代码,不调用模型,不访问网络或真实 EZVIZ API。

## 信任模型

| 输入 | 提供方 | 处理 |
| --- | --- | --- |
| `proposal` | 模型 | 不可信数据,严格 schema,无自授权字段 |
| `allowed_paths` | 操作者 | 权威范围,绝不从提案推导 |
| `input_sha256` | 操作者 | 绑定已核验的候选输入 |
| `repo_root` | 操作者 | 只读源树,校验期间须保持稳定 |
| `max_bytes` | 操作者 | 提案文件与输出报告的硬字节上限 |

范围只能收缩:edit 的 path 须逐字命中 allowlist;`score`、`passed`、`approved`、
`permissions`、`publication_authorized` 等字段均为 schema 违规——模型不能自授权
更大范围、更换输入哈希或自报通过。

## 提案 schema(恰三个键)

```json
{"schema_version": 1, "input_sha256": "<64 位小写 hex,等于操作者值>",
 "edits": [{"path": "src/ezviz_unified/<...>.py", "base_sha256": "<64 位小写 hex>",
            "old": "<非空,恰好出现一次>", "new": "<替换文本,可为空>"}]}
```

- `path`:规范相对 `src/ezviz_unified/*.py`(允许子目录);拒绝绝对路径、`~`、`.`/`..`/空段、反斜杠与非 `.py`。
- `base_sha256`:须等于文件当前真实 SHA-256;同一文件多个 edit 钉同一值。
- `old` 在原文中恰好出现一次(重叠出现也计入);`new` 可为空但不得等于 `old`,两者须可 UTF-8 编码。
- 空 `edits` 合法,表示无候选改动(`has_changes: false`)。

## 校验流程

任一失败抛出 `ProposalError`(`ValueError` 子类),消息不含提案文本,异常链为空。

1. 操作者输入:`input_sha256` 为 64 位小写 hex;`max_bytes` 严格正 int;`allowed_paths` 非空且唯一。
2. 提案形状:键集合精确;`schema_version` 严格为 int 1;内嵌 `input_sha256` 等于操作者值;`edits` 为 list。
3. 文件系统闸门(读取任何字节之前):仓库根与 `src/ezviz_unified/` 各解析一次,**包根须仍位于已解析仓库根内**——`src` 或包根为指向外部的符号链接时直接拒绝;每个 allowlist 路径须解析为锚点内常规文件,目录、缺失与越锚链接一律拒绝。
4. 锚定与应用:目标文件读一次、校验哈希、UTF-8 解码;`old` 在原文恰定位一次;范围两两不重叠;从后向前在原文上应用,任何 edit 不能锚定到其他 edit 生成的文本。
5. 结果检查:新文本保持 UTF-8、末尾换行并通过 `ast.parse`——只解析,不执行。
6. 漂移复查:结束后重读重哈希所有被编辑文件,中途变化则整份失败,不输出混合版本。

## 输出上限与报告

报告按 CLI 实际序列化核对:`json.dumps(..., ensure_ascii=False, indent=2)` 加末尾换行的真实
UTF-8 stdout 字节数必须 ≤ `max_bytes`,不截断;提案文件按 `max_bytes + 1` 探测限量读取,
严格 JSON 解析拒绝重复键、非有限值与坏 UTF-8。

报告字段:`schema_version=1`、`evidence="proposal_shape_hash_and_syntax_only"`、`input_sha256`、
按路径排序的 `files`(path/base_sha256/new_sha256/new_text/diff)、`has_changes`、
`publication_authorized=false`、`patch_applied=false`、`real_api_used=false`、`limitations`。

CLI:`python -m harness.patch_proposal --repo . --proposal proposal.json --allow <sdk路径,可重复、必需>`
加 `--input-sha256 <hex> [--max-bytes 2097152]`。有效提案(含零改动)exit 0;任何失败 exit 2,
stderr 固定一行 `patch-proposal: validation failed`,不泄漏提案内容。

## 边界:不是沙箱

绿色报告仅证明:形状合规、绑定操作者输入哈希、范围限于操作者 allowlist、忠实锚定于读取与
复查时刻的文件内容、产物为语法合法的 Python 与标准 diff。它**不**证明逻辑正确、完整或安全,
也**不是沙箱**:无操作系统级隔离、只解析不执行;哈希复查只能发现漂移并整体失败,不提供
原子性保证,校验期间仓库必须保持稳定。正确性判断属于 H011B 验收规则与 H012 受控探测。

## 测试

`tests/test_harness_patch_proposal.py` 用真实临时文件覆盖:精确替换/diff/新哈希与仓库不可变;
allowlist 越界、目录、文件级外部链接及 `src`/包根外部链接(读取前拒绝,含读探针断言);合法
别名仓库根与仓库内链接仍可用;`old` 零/多/重叠出现;范围重叠;base 不一致;语法错误与缺末尾
换行;额外字段与非 int 版本;空提案;中途漂移;CLI 字节预算、严格 JSON、深层嵌套递归固定错误;
固定 stderr 不泄漏内容。测试未在本交付中执行。
