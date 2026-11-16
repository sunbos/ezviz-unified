# 来源账本公开投影 schema 2

## 原因与范围

schema 1 的来源 URL 查询值投影通过了检查，但实际语料的部分业务示例 path 仍包含样例设备标识及不透明任务/模型段。该报告未上传，已标记 not-for-sharing；原始报告与归档保留。不能继续把逐个 URL 模式替换当作公开安全边界。

本修订只处理来源账本；SDK 诊断 v1 和 src/ezviz_unified 不变。build_matrix(repo_root, manifest_path, decisions_path=None) 接口保持；输入manifest/decisions schema仍1，返回/CLI/stdout/--output/--summary-only报告统一为schema2，publication_profile=reviewed_metadata_only。不存在raw公开输出模式。

## 信任与投影边界

- 输入仍是经核验的官方归档及本地审核registry。导航归属属于已核验目录元数据，不是原文标题、正文或业务样例；仅这一类category_paths可保留。
- 内部原始URL/path/host仍用于六字段关联、来源pin检查和索引；公开投影不能改变内部匹配、处置、未知/阻塞状态或记录数量。
- 最终按source/observation/capability类型新建显式白名单字典；禁止复制内部dict再pop，不让内部新字段自动出现在公开结果。
- 公开原始定位信息只允许由已核定region与数字doc_id构造的门户查询链接，以及reviewed registry中的常量API path。不是通用匿名化器；来源hash只用于完整性绑定。
- 未审查来源的raw locator、path、host、section_title、discovered_from、asset_id、下载文件名、reference_file和reference URL均不公开。不把它们改成hash或清洗版path。
- 门户identity限定domestic/overseas及ASCII数字doc_id；manifest snapshot.id是调用方指定的公开版本标签，不得携带账号/设备信息。非法门户identity硬失败，不回显值。

## 标识与证据定位

portal source_id保持snapshot:region:doc_id。sdk_reference和asset分别使用snapshot:sdk_reference:<index>、snapshot:asset:<index>，index为输入记录的零起始序号，在投影/去重前确定。SDK使用manifest.entries数组index；asset使用非空JSONL record index，不声称是物理行号。

source evidence_refs只由已知manifest文件名与index/portal identity构造，例如snapshot/manifest.json#entries/0或snapshot/manifest.jsonl#record/0。不能嵌入原URL、asset_id或下载文件名。全部derived/linked/unresolved/capability IDs使用这些公开ID，不能保留旧URL/hash ID别名。稳定性限定于manifest SHA锁定的快照；旧schema1非portal decisions不能猜测迁移。

## 公开字段白名单

顶层保留schema_version、input_manifest_schema_version、evidence、publication_profile、product_complete=false、denominator_defined=false、input_hashes、source_sets、sources、observations、capabilities、unresolved、registry_audit、summary、disclaimer、limitations。嵌套审计对象仅保留现有已核定的计数/静态标识/hash，不整体透传未来audit对象。

source公共字段：source_id、snapshot_id、record_index、kind、region、doc_id、canonical_document_url、status、content_sha256、category_paths、disposition、operation_ids、observation_ordinals、evidence_refs、unresolved。canonical_document_url仅portal可由identity构造，其余为null。kind、disposition、status使用已知生成器枚举，未知status归unknown；不能把原始状态字符串放入summary键。

SDK source仅额外保留discovery_reference_count，不复制发现链接/标签。asset仅额外保留asset_category（image/document/unknown）、reference_count和references；每条reference只含reference_index、reference_type（link/image/embedded_resource/unknown）、由manifest record/reference index构造的evidence_ref。引用计数不因省略locator减少。

observation公共字段：observation_id、source_id、kind、ordinal、status、method、section_index、element_indices、has_query、has_fragment、reason_codes、reason_count、same_route_hints、related_declaration_ordinals、agrees_with_declaration、exact_operation_ids、inventory_content_sha256。无path、host、section_title、inventory_url、raw值或它们的hash。method仅九种标准HTTP方法或null；same_route_hints/exact IDs仅审核registry中存在的ID。状态已知declared_pair/ambiguous/address_only/example_only及生成器已定义状态，未知归unknown。当前源reason码为address_backtick/conflicting_methods/invalid_address/missing_method/multiple_methods/multiple_urls/no_explicit_method/templated_host/templated_path/unknown_method/unparsed_curl；其他reason归unknown_reason并保留reason_count，不回显原值。warning细节仅用固定inventory_warning标记及数字指针。

capability按kind分别投影：保留现有静态assessment状态、operation_id、region/source_doc_id/source_content_sha256、method、module/symbols/public_entry、offline/live证据层级、source_review状态/受控问题码、linked IDs、related_example_ids、disposition、provisional。reviewed_contract可额外保留来自审核registry的常量path及由region/doc_id构造的canonical_document_url；provisional/unobserved不含path或任何原locator。不得从observations或same-route回填path。所有动态derived IDs从新的公开source/observation ID生成。

unresolved只发布已知kind、可解析的公开entity/operation ID和受控reason码。inventory_warning后不拼接原始warning字符串。未知理由归unclassified_review_issue，条目数保持；summary从公开枚举重新统计，不能让原值通过字典键泄漏。

显式decisions的reason/evidence_refs仍必须非空并保留在原decisions文件，内容合法不等于已审查。schema2默认不复制其自由文本/locator；source上仅给decision_reference（输入decisions SHA、record_index、reason_present、evidence_ref_count），disposition仍反映实际decision。capability引用仅当已存在于公开capability集合时公开；其他仅has_external_capability_ref标记。这样能定位审查理由，不能把源码或URL自动改写成理由。

## 其他行为保持

所有来源和观察保留，缺文件继续标阻塞，已存在文件hash错误仍拒绝；不覆写任何输入/已存在输出，继续使用不可覆盖的发布方式。重复key/NaN/非法观察/重复ordinal等输入拒绝不放宽。CLI参数错误也使用固定消息/exit2，不回显传入值；process-control语义不变。

## 验收

- 全部API返回、stdout、file和summary由同一个公开投影产生；每种实体字段集合有独立schema断言。
- 合成canary覆盖path、host、section title、URL各部分、asset_id、discovered/reference及派生ID/evidence_ref/summary键，检查完整序列化结果。不得使用真实样例标识；不得用hash作为替代。
- 已审核registry path保留，unknown path不公开；六字段比较仍使用内部原值，不能因构造canonical链接而嫁接身份。
- 同一真实输入仍有4051 source、3019 observation、203 reviewed contract、201来源关联及2已知例外；所有graph引用有效，全部未审/阻塞记录保留，不以删除行脱敏。
- 真实检查只输出布尔/计数。旧schema1本体不改，新增schema2文件并标记旧报告不可分享；来源完整性通过不等于语义或SDK全产品完成。
- 输入SDK资料/附件版本、当前已登记契约和源码均不变。没有新失败/改动，不重采或重跑已通过的无关检查。
