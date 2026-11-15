# OTAP与账户托管 Implementation Plan

**Goal:** 完成官方归档中四项国内OTAP与五项B1/B2托管接口的同步/异步封装，并登记来源、绑定、独立传输评测与明确限制。

**Architecture:** 复用现有Client、Operation和ResponsePolicy，增加client.otap与client.delegation两组门面。官方JSON请求体与跨账户授权保持原协议，不复制传输/认证层。

**Tech Stack:** Python3.12、HTTPX、标准库JSON、pytest/MockTransport、现有registry/wire_eval。

## OTAP固定接口（仅domestic）

公共类Otap/AsyncOtap。均以device_serial为首参数，公共keyword-only字符串为resource_category、local_index、domain_identifier。
- get_property(...,property_identifier)：GET /api/v3/device/otap/prop，header accessToken及deviceSerial/resourceCategory/localIndex/domainIdentifier/propIdentifier，read_only=True。
- set_property(device_serial,payload,*,...,property_identifier)：同路径PUT，裸JSON对象，Content-Type application/json，read_only=False。
- invoke_action(device_serial,payload,*,...,action_identifier)：PUT /api/v3/device/otap/action，相同headers中actionIdentifier替代propIdentifier，read_only=False。
- latest_property(...,property_identifier)：GET /api/service/device/otap/prop/latest，header同get_property，read_only=True。

Operation名分别otap.get_property/set_property/invoke_action/latest_property。所有服务策略meta.code整数200、meta.message、data；action另设置可选device_code_path=meta.moreInfo.deviceMeta.code、device_message_path=meta.moreInfo.deviceMeta.errorMsg、device_success_values=("0x00000000",)、require_device_code=False，记录该成功码取自示例、其它拼写/缺失语义未完全说明。保留raw更多信息，不从1513的msgDetail文本推断错误。

参数字符串非空白、不规范大小写或自动转设备序列号。HTTP头字段额外按本地HTTPX传输限制拒绝非ASCII、CR/LF/NUL；这不是厂商语义限制。仅latest约束resource_category/domain_identifier/property_identifier长度<255，local_index为0或非零开头的≤10位ASCII十进制字符串；旧v3不套用这些长度/数字限制。

payload为调用方给定Mapping[str,object] JSON对象；非字符串嵌套键、不可序列化、NaN/Infinity、循环与非法UTF8均在认证前固定ValueError；通过标准JSON序列化/反序列化准备独立副本，不修改调用者对象，不包装Body、不推断timeZone字段。此对象形状是本SDK选择，不证明上游其它JSON根类型非法。保留已有Client根accessToken保留字段约束。空对象允许；设备属性报备schema由平台判断。

1307015说明同步操作上限16秒；调用方需要完整等待时可显式设置Client(timeout>16)，门面不修改默认超时、不轮询、不自动重放写操作。latest的desired/reported及时间/空值原样保留，不推导在线或二者相等。

## Delegation固定接口（仅domestic）

公共类Delegation/AsyncDelegation，Operation名delegation.apply_code/authorize_account/list_resources/authorize_device/relieve_device。均POST、header accessToken、form_urlencoded、meta.code整数200/meta.message/data；仅list_resources为read_only。
- apply_code(owner_app_key)：B2客户端调用/api/v3/lapp/trust/sign/apply，form appKey为B1对方key。
- authorize_account(authorization_code)：B1调用.../userAuthorize，form sign。
- list_resources(authorization_code,*,device_serial=None,authorization_type=None,page_start=None,page_size=None)：.../resource/list，deviceSerial可选header；form sign/authorizeType/pageStart/pageSize，仅非None发出。
- authorize_device(authorization_code,device_serial,*,state=None,local_index=None)：.../resource/authorize；header deviceSerial/localIndex，form sign/state，state可选。
- relieve_device(recipient_app_key,device_serial,state,*,local_index=None)：.../resource/relieve；header deviceSerial/localIndex，form appKey=B2对方key及必填state。

必填str非空白、可选str非None时同规则，保留原值；header额外按HTTPX ASCII/控制字符限制校验。不猜测state/authorizeType枚举，不默认state=1，不解析sign格式或更换客户端主体。通道授权/解绑时调用方必须提供local_index，不能由SDK猜其意图。平台需要付费账号和后台开通，SDK不激活服务。

分页仅严格int非bool，page_size不超过文档最大20；下界未声明，不以SDK假设补成官方限制。None省略以保留平台默认pageStart0/pageSize10，不自动分页。Boolean data原样保留（含False），不把meta200解释为授权一定True。返回sign作为敏感数据处理；在errors._SENSITIVE_KEYS加入sign，测试请求中原值保留而日志/异常隐藏，不能拆解析#示例格式。

## 并行分工

- [x] S022A：Claude生成otap.py与test_sdk_otap.py，不改Client或根导出。
- [x] S022B：Claude生成delegation.py、test_sdk_delegation.py以及sign脱敏补丁，不改Client或根导出。
- [x] H014：整体JSON对象的契约/评测表示，保留既有177条全量回归。
- [x] S022C：统一挂载两门面、导出、来源/契约/绑定/cases与README；同一集成文件只由该任务生成候选。
- [ ] Codex核对源码证据和候选边界，运行离线、类型、格式、发行检查及CI后集成。账号授权与属性写入不做真实调用；已有国内inspection只读结果另记。

## 本地验收

全库3025项测试及11项子测试通过；新门面117项、整体JSON工具71项、联调工具19项均通过。186项契约的1520次离线执行与当前源码/输入哈希一致；38文件mypy、131文件Ruff/格式检查、wheel/sdist及干净安装通过。新九项接口均未进行真实调用；国内inspection只读样本另见真实联调索引。远端验收按PR checks记录。
