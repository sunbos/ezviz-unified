# HTTP接口候选清单 Implementation Plan

**Goal:** 从主文档归档提取可追溯的HTTP声明与示例，形成后续接口审核清单；不把候选数量作为API总数或SDK覆盖率。

**Architecture:** 纯HTML解析器独立于登记表；归档适配器先验证全部原文、再解析、最后关联已有契约。静态SDK参考库/附件不混入本期主库分母。

## 解析器固定接口

`tools.documents.http_candidates.extract_http_candidates(html: str) -> dict`，无文件/网络/SDK/登记表访问，不执行HTML脚本。
返回schema_version=1、declarations、examples、warnings。declaration字段：ordinal（零基文档序）、status（declared_pair/address_only/ambiguous）、method（大写或None）、raw_method（str或None）、raw_address（str或None）、address_kind（absolute/root_relative/template/invalid）、host（str或None，模板原值保留）、path（str或None）、query和fragment（str）、section_title（str）、section_index（int或None）、evidence（role/element_index/text列表）、reasons（固定原因码列表）。examples使用相同字段，status=example_only，另有related_declaration_ordinals列表与agrees_with_declaration（bool或None）。warnings为code/element_index字典列表。

支持已观察三种声明：中文请求地址/请求方式标签及后续值；接口信息两列表中的请求方法/请求路径；海外h3操作域内Request Address/Request Method/Request Type嵌套li-p。标签忽略空白/nbsp、尾冒号、大小写；地址同义词含请求URL/Request URL/Request Path，方法含HTTP Method。编号只用于识别元章节，不修改URL。

DOM element_index沿用parse_html：在任何改动前按所有元素文档序编号。忽略script/style。非元信息标题开启新操作域；请求/响应/参数/示例/返回码/接口信息等元标题为子节或边界。

禁止第k地址配第k方法。地址只能采用同域、下一地址/操作标题/参数或示例边界之前的明确方法；缺方法address_only，多方法或多地址取值冲突ambiguous，不选首项。方法先于地址仅在一个连续声明单元内各恰一条且无竞争项/边界时可配对；否则保留ambiguous或orphan_method警告。孤立方法不得推断地址。不同操作域同路由保留各自出现，重复DOM嵌套标签不得重复计数。

方法白名单GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS/TRACE/CONNECT；多方法或未知值保留raw并标ambiguous。绝对http(s)、根路径、{areaDomain}/api等模板分别保存，不补host、不据host推region、不解码/改大小写/去尾斜杠。query/fragment保留；fragment、userinfo、空或歧义地址不得成为可关联declared_pair。路径模板保留并给出templated_path原因，不展开。

curl -X/--request（无显式方法时method=None）及严格METHOD target HTTP/1.x行只产examples，不能升级为声明。同域示例与声明不一致须可见，485的list声明/get示例不得被覆盖。普通链接/图片/下载、响应JSON中的URL、Java@RequestMapping、dev:/cam:资源串不产声明。

## 归档固定接口

`tools.documents.endpoint_inventory.build_inventory(archive: Path, *, contracts_path: Path | None = None) -> dict`。复用derive._verify_sources，补足身份/路径检查，不修改旧工具。先验证全部原文。坏JSON/hash/身份、重复身份、越界路径、未知状态固定InventoryError(ValueError)，不得输出部分成功结果。正常不可用状态保留；结束前复查catalog、所有使用原文及可选contracts字节hash，拒绝输入漂移；不声称原子快照或OS隔离。

输出schema_version=1、evidence=offline_http_declarations_only、summary、documents、unrecovered_reviewed（operation_id/reason列表）、input_sha256（原始catalog/可选contracts字节hash）、source_files_sha256（归档相对路径hash）、limitations。documents按(region,数字doc_id)排序，含region/doc_id/title/source_url/content_sha256/category_paths、status=scanned/source_unavailable及declarations/examples/warnings。每条declaration附reviewed_operation_ids与same_route_operation_ids：前者仅status=declared_pair且region+doc_id+source_url+contenthash+method+path完全一致；后者仅同region/method/path，不能替代来源恢复。路径模板或歧义项不得通过登记表补出路由。

summary固定：documents、archived_documents、source_unavailable_documents、documents_with_declarations、declarations、paired_declarations、unresolved_declarations、examples、matched_reviewed_contracts、unrecovered_reviewed_contracts。合法旧契约在新快照中缺源、URL/hash变化时记录unrecovered原因，而不是阻止扫描；坏契约结构/重复operation_id拒绝。无契约输入时匹配计数为0。

CLI：python -m tools.documents.endpoint_inventory --input ARCHIVE [--contracts FILE] [--summary-only]。UTF8 JSON stdout含换行，成功exit0；失败仅固定stderr endpoint-inventory: validation failed及换行、exit2。摘要仅含schema/evidence/summary/unrecovered_reviewed/input hashes/limitations，不输出正文或示例URL。同输入输出逐字一致，无时间戳或随机值。大清单保存在data/reports，不提交全文。

## 分工与验收

- I003A：仅http_candidates.py与docs/research/2026-11-15-endpoint-inventory.md。
- I003B：仅endpoint_inventory.py与tools/documents/README.md，不改解析器。
- I003C：独立测试，仅tests/test_document_http_candidates.py与tests/test_document_endpoint_inventory.py，不接收实现。
- Codex核对跨域错配、来源链接与全条目账目，执行真实样本和全主库扫描。候选提取不证明端点存在/完整语义/真实支持，零候选不代表无接口。
- 11个真实样本预期21条声明（20条已审、1条未审图片查询），须按原文与登记复核而非信模型；主库2269条/2221正文/48不可用条目闭合。已审186条有未恢复时具体列出，不回填候选伪造召回。
