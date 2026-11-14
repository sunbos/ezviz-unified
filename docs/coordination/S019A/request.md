# S019A：统一存储参数与查询模型

固定devin/swe-2、effort=max。只读本文件、input.json及src/ezviz_unified/{recordings,ram_models}.py。禁止写文件/命令/联网/配置/凭据。输出完整storage_models.py单文件Python围栏，不输出中间版本，不宣称测试通过。Codex审核应用。

实现以下纯函数，供storage.py导入；通过mypy strict，不导入Client/HTTPX，不执行IO：
- _text(name:str,value:object)->str：非空白str，原样返回，不strip。
- _integer(name:str,value:object)->int：int且非bool；不推断Long ID上下限。
- _name(value:object)->str：1..32字符，仅ASCII英文字母/数字/下划线与CJK汉字（可用unicodedata.name的CJK UNIFIED IDEOGRAPH-/CJK COMPATIBILITY IDEOGRAPH-前缀）；不允许中划线、空白、emoji或希腊字母。
- _biz_type(value:object)->str：vod/record/cloudCapture/capture。
- _retention(value:object)->int：-1或1..5475，拒绝0与bool。
- _node_ids(values:Sequence[str],*,limit:int|None=None)->str：非空Sequence但非str/bytes，每项非空白str且不含逗号；保留顺序/重复，不设置未声明的全局数量上限；返回逗号连接。
- _space_fields(*,name:str|None=None,expire_days:int|None=None,storage_type:int|None=None,flow_limit:int|None=None,permission:int|None=None)->dict[str,str|int]：None省略；name用_name、expire用_retention；storage_type/permission只1/2，flow_limit仅int不猜范围。映射spaceName/expireDays/storageType/flowLimit/permission。
- _spaces_query(*,start_time:datetime,end_time:datetime,last_space_id:int|None=None,page_size:int|None=None,biz_type:str|None=None,sort_rule:str|None=None,sort_field:str|None=None)->dict[str,str|int]：时间naive整秒、start<=end、间隔<=30days，发送yyyy-MM-dd HH:mm:ss（年份四位）。可选全None省略；page1..50，biz单一枚举映射bizTypeList（不猜多类型分隔符），sort_rule asc/desc，sort_field createTime/updateTime/totalSize，lastSpaceId整数。
- _download_query(file_node_ids:Sequence[str],*,expire_seconds:int|None=None)->dict[str,str|int]：fileNodeIds 1..50，expireSeconds如给定须int30..604800，None省略。

StorageFileQuery为frozen dataclass（所有字段keyword-only）：file_node_ids:Sequence[str]|None、space_ids:Sequence[int]|None、tag_id:str|None、start_time/end_time:datetime|None、file_type:int|None、file_child_type:str|None、page_size:int|None、last_id:str|None、sort_rule:int|None、source_type:int|None，默认均None。标识字段repr=False。__post_init__复制Sequence为tuple，拒绝str/bytes等；to_form()->dict[str,str|int]生成全新字典。

至少一种选择器：file_node_ids（非空，无文档硬上限）>tag_id（非空白）>space_ids（1..5整数，并必须有start/end）。允许同时传多种选择器，原样发送，按服务端优先级处理；不偷偷合并/丢弃字段。所有显式值都验证类型，时间为naive整秒，二者同传时start<=end；tag模式可单独省略任一时间，不猜配对；file模式时间仍可传但源说明无效；搜索不套用空间列表的30天上限。

space_ids用CSV发送（正文未举多空间实例，Codex会记录为约定推导）；file_type为0..4整数，wire为String；file_child_type仅非空白字符串，允许示例外的06，不把摘录枚举当全集；page_size1..50；last_id允许空字符串或非空白str；sort_rule仅0/1int；source_type任意int。None省略，不注入服务端默认值；fileNodeIds为唯一正式字段名，不提供folderNodes别名。ValueError不得含用户值。
