# S019C：统一存储原文复核

固定devin/swe-2、effort=max。只读本文件和input.json。禁止写文件/命令/联网/配置/凭据。输出单个JSON围栏，不输出中间版本。每页核对说明、参数表、curl和响应，不从接口名字猜token位置，不宣称实测。

输出{"documents":[{"doc_id":str,"method":str,"path":str,"token_location":"header"或"query","body_encoding":"unknown"或"form_urlencoded","read_only":bool,"code_path":"meta.code","success_values":[200],"message_path":"meta.message","data_path":"data","parameters":[{"name":str,"location":str,"type":str,"required":bool}],"limits":[str],"uncertainties":[str]}]}。

保留原始类型文字（如List与残留反斜杠），不要把文档枚举示例推断为全集。特别核对：空间expireDays -1/1..5475与旧云录制0永久不同；修改空间省略字段按FAQ保持不变；文件搜索的选择器优先级及CSV编码证据强弱；bizTypeList表为String且没有多值实例；fileChildType示例06不在参数摘录；批量文件过期数量上限由平台配置，不能套下载的50；downloadurls的token在query、expiry秒与返回expireTime毫秒不同。错误码400等未明确HTTP/业务层的歧义只记录，不擅自转换。不要复制示例中的真实式文件ID、设备ID、token或下载签名。
