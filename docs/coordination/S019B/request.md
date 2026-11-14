# S019B：统一存储同步/异步入口

固定devin/swe-2、effort=max。只读本文件、input.json、../S019A/request.md及src/ezviz_unified/{cloud_recording,client,models,operations}.py。禁止写文件/命令/联网/配置/凭据。输出完整storage.py单文件Python围栏，不输出中间版本或宣称测试通过。

Storage/AsyncStorage持有_client，Codex接到Client.storage与exports。导入storage_models约定的StorageFileQuery和私有helper（以S019A/request为准），不要重复实现其校验。TYPE_CHECKING引用Client/AsyncClient，方法精确注解通过mypy strict。每次返回ApiResponse，不改data/raw；无自动翻页/恢复文件/媒体下载/轮询/预操作。

所有Operation为domestic，名称storage.<方法>，ResponsePolicy(meta.code/meta.message/data, success_values=(200,))。方法执行时先拒绝非国内，再建参数/调用client.request，禁止认证或请求后才拒绝。

公开方法（sync/async相同签名）：
- create_space(name:str,*,biz_type:str,expire_days:int|None=None,storage_type:int|None=None,flow_limit:int|None=None,permission:int|None=None)：5236，POST /api/service/open/storage/engine/space，Header token，form。name必须用_name保证不被None省略；_space_fields生成字段再加bizType=_biz_type。read_only=False。
- update_space(space_id:int,*,name:str|None=None,expire_days:int|None=None,storage_type:int|None=None,flow_limit:int|None=None,permission:int|None=None)：5237，PUT同路径/Header/form，None省略，至少一个显式改动（SDK本地策略）；spaceId int，read_only=False。
- delete_space(space_id:int)：5238，DELETE同路径/Header/form，仅spaceId，read_only=False。文档说明连同文件不可恢复，SDK不自动清理/停止前置资源。
- spaces(*,start_time:datetime,end_time:datetime,last_space_id:int|None=None,page_size:int|None=None,biz_type:str|None=None,sort_rule:str|None=None,sort_field:str|None=None)：5230，GET /api/service/open/storage/engine/space/listById，Header token/query，read_only=True，_spaces_query。
- set_file_retention(file_node_ids:Sequence[str],expire_days:int)：5232，POST /api/service/open/storage/engine/file/batchExpire，Header token/form，read_only=False；_node_ids无硬上限、_retention，字段fileNodeIds/expireDays。
- search_files(query:StorageFileQuery)：5233，POST /api/service/open/storage/engine/file/listById，Header token/form，read_only=True；明确isinstance query，否则ValueError，不接受任意to_form对象；DTO.to_form()。
- download_urls(file_node_ids:Sequence[str],*,expire_seconds:int|None=None)：5234，GET /api/service/open/storage/engine/file/downloadurls，Query token/query，read_only=True，_download_query。

所有form显式Content-Type application/x-www-form-urlencoded，GET不额外加Content-Type。写操作即便源标幂等也不自动重放；read_only标志按上述确定。5341/5342跨产品及旧1.0解冻不属于本模块，不猜fileNodeId与旧fileId映射。

避免过度抽象；纯参数helper与稳定Operation常量即可，不添加模型调用、动态eval、未知kwargs透传或新HTTP客户端。
