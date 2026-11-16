# 静态 SDK 参考归一化桥接

`tools/documents/normalize_sdk_reference.py` 把 `reference_collectors/` 采集到的
静态 SDK 参考库（`manifest.json` + 原始 HTML，位于独立 region 根目录）离线归一化
为现有 `derive.py` / `verify.py` 支持的快照子树。它只补采集管线的桥接缺口，不改写
`parse_html`、`derive`、`verify` 或 `collect_assets`。

## 输入与输出

- 输入：一次**完整**采集的 region 目录，含 `manifest.json` 及其引用的原始 HTML。
  完整性以 manifest 顶层 `traversal_finished` 显式声明为准：只有取值恰好为
  JSON `true` 才视为遍历收尾；`false`、缺失、`null` 或 `1` 等真值非布尔一律
  整体拒绝（全部已登记条目为终态不能替代该声明，collector 中断时会留下
  条目全终态但 `traversal_finished=false` 的快照）。`entries[].status` 只允许
  `ok` 或采集器已知的终态失败
  （`http_error`、`redirect_outside_or_alias`、`not_html`、`missing_html_title`、
  `network_error`）；`queued`/`running`/未知状态视为未完成输入，整体拒绝。
  `local_path` 必须是 canonical POSIX 相对拼写（无 `.`/`..`/空段/重复分隔符，
  逐字符等于其 `PurePosixPath` 规范化结果），并在输入根内唯一、不越界、
  不与保留输出路径或其它条目（含符号链接别名）冲突。
- 输出：全新路径（任何已占用路径——包括悬空符号链接——即拒绝；检查落在
  未 resolve 的原始输出参数上，resolve 后再复核一次），含原字节
  `manifest.json`、按原相对路径保留的原始 HTML、`catalog.json`、
  `pages/<region>/<doc_id>.source.json` wrapper、`README.md`、
  `quality/summary.json`。
- 发布机制：所有校验先全量完成，再在输出父目录下建临时暂存目录写入全部产物，
  最后 `mkdir` 独占新输出目录并逐项 `move` 填入；`mkdir` 对已占用路径原子拒绝，
  异常时清理本次自建输出，不留半成品。**填充过程不是 OS 级原子快照**——并发
  观察者可能看到部分填充的目录，消费者应以 `quality/summary.json` 存在且可解析
  为完成标志。源目录不被修改，也不会覆盖既有用户数据。

## 三步命令

```sh
# 1) 归一化（离线）；--output 必须是尚未占用的全新路径
python -m tools.documents.normalize_sdk_reference \
    --input /path/to/sdk-corpus-domestic \
    --output /path/to/new-main-snapshot/sdk-reference/domestic \
    --region domestic

# 2) 派生
python -m tools.documents.derive \
    --input /path/to/new-main-snapshot/sdk-reference/domestic

# 3) 验证
python -m tools.documents.verify \
    --input /path/to/new-main-snapshot/sdk-reference/domestic
```

海外侧把 `--region` 换成 `overseas`、输出指向 `sdk-reference/overseas`。

## 再运行 collect_assets

`collect_assets` 固定在主快照根下读取 `sdk-reference/<region>/manifest.json`
与 `references.jsonl`。归一化 + derive 之后，`sdk-reference/<region>/` 下已具备
这两个文件，把 `--root` 指到主快照根即可让归档 HTML 被识别为
`already_archived_document`：

```sh
python -m tools.documents.collect_assets --root /path/to/new-main-snapshot
```

## 边界与限制

- 输入只接受**新采集**的静态 manifest；不拼接、不增量刷新旧证据。
- `traversal_finished` 缺失或非 `true` 的 manifest 视为未完成采集并整体拒绝；
  `entries` 为空但 `traversal_finished=true` 视为“已遍历、范围内无页面”的
  合法输入，按现有 scope 正常产出空 catalog。
- 归一化失败不修改输入；输出目录已存在即拒绝，旧证据不被覆写。
- 原始 HTML 只作字节保存与传递，不执行、不渲染、不修复。
- catalog 行声明 `source_kind='static_sdk_reference'`、
  `doc_id_kind='local_url_hash_identity'`；doc_id 由公开 URL 的 SHA-256 前 16 位
  派生并做碰撞检查，仅作归档键，不是匿名化。
- 失败条目以 `status='source_unavailable'` 保留在 catalog，不生成 wrapper，
  以便后续补采核对缺页。
- UTF-8 严格解码失败的原始字节原样保留；wrapper 内派生文本用 U+FFFD 替换并
  记录 `encoding_warning`，`content_sha256` 覆盖的是重编码后的派生文本。
- `quality/summary.json` 只记录离线归一化与原字节核验：
  `derived_verified=false`、`semantic_contract_accuracy_verified=false`、
  `live_api_verified=false`。归一化通过不代表接口语义正确或真实 API 已实测。
- 非 SDK 的静态 book/指南可复用本桥接保持 `static_sdk_reference` 管线兼容，
  但不得据此当 REST 资源处理；API 语义派生归 derive/verify 之后的独立步骤。
