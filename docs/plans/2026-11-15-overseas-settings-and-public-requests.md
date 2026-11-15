# 海外设备设置与公开请求

## 目标与证据

从接口清单推进海外设备配置使用闭环：PIR区域、门铃类型、红外开关、时区与语言。国内物模型路径另行审核，不把与现有header版OTAP相似的路径视为等价，也不逐型号生成重复方法。

2026-11-15重新采集国内774、775、1512–1514及海外476、477、3722–3730共16份来源，正文哈希与归档一致。海外477的1.14–1.19、1.21–1.22、1.24–1.25为本批十项。声明与示例的路径冲突、错误参数、duration单位缺失、timeFormat必填标记缺失须保留。

对文档明确列出的新加坡公开地址进行三次无凭据POST：默认语言、表单ENGLISH、表单SIMPCN均返回code字符串200及105项时区；默认与英文数据相同，中英文105项名称不同。证据保存在被忽略的`data/research/2026-11-15-timezone-public-probe/`。这仅验证该区域的公开目录查询，不代表设备设置、其他区域或SDK真实调用已验收。

## 设计决定

沿用Client/AsyncClient及Settings/AsyncSettings，增加以下能力，不新增网络引擎或平行客户端体系。

1. Operation.token_location增加`none`。显式公开请求完全跳过token provider、token获取/刷新/失效处理，也不向任何字段注入token；10002按原响应错误抛出，不重试。
2. Client/AsyncClient增加`anonymous: bool = False`。默认仍要求成对应用凭据或provider；匿名模式必须无应用凭据、provider和auth_transport，且RegionConfig.api_base_url明确可用。匿名客户端调用需认证Operation时，在请求前给出固定ValueError。
3. 公开Operation即使由普通认证客户端调用，也只使用已配置api_base_url；缺少该地址时在调用provider/网络前给出固定ValueError。不得把auth_base_url当业务区域、不凭空创建token、不猜测地域。复用现有传输缓存、同步锁、异步关闭与取消语义。
4. 保留所有既有校验、日志脱敏、区域校验、参数拷贝及凭据生命周期规则。匿名模式不允许用户绕过现有保留accessToken/Host字段限制。

## Settings公共接口

全部新方法仅限overseas，返回ApiResponse，不重写设备字段。所有调用使用POST，除时区列表之外均为form token；成功code为字符串`200`。get/list声明read_only=True，其余False。

| 方法 | Python参数（serial为device_serial） | 请求路径与字段 |
| --- | --- | --- |
| get_pir_area | serial, *, channel=None | /api/lapp/device/pir/get；deviceSerial、可选channelNo |
| set_pir_area | serial, area, *, channel=None | /api/lapp/device/pir/set；额外area原字符串 |
| get_chime | serial, *, channel=None | /api/lapp/device/chime/get |
| set_chime | serial, chime_type, duration, *, channel=None | /api/lapp/device/chime/set；type、duration |
| get_infrared | serial, *, channel=None | /api/lapp/device/infrared/switch/get |
| set_infrared | serial, enabled, *, channel=None | /api/lapp/device/infrared/switch/set；enable整数0/1 |
| list_timezones | *, language=None | /api/lapp/timezone/list；无token，可选表单language |
| set_timezone | serial, timezone, *, time_format, daylight_saving=None | /api/lapp/device/timezone/set；timezone、timeFormat、可选daylightSaving字符串0/1 |
| get_language | serial | /api/lapp/device/language/get |
| set_language | serial, language | /api/lapp/device/language/set；language |

serial、area、timezone、设备language为非空UTF-8字符串，保持原大小写/内容。channel与duration仅校验非bool整数，不猜范围、默认通道或duration单位。chime_type限定非bool整数1/2/3；enabled与daylight_saving仅接收bool，后者允许None省略。time_format限定非bool整数0/1/2；SDK要求显式传入是本地设计，不能写成官方已确定的必填规则。list_timezones语言仅ENGLISH/SIMPCN，None交给服务端默认；设备language按能力字段选择，不复用这个枚举。

## 分工与验收

契约中`required`保留bool；遇到官方未标明必填性的字段，新增显式`null`加非空`required_uncertainty`解释，不把未知写成可选或必填。缺少required键、无解释的null和非bool/非null仍拒绝；accessToken依然必须required=true。timeFormat使用此记录方式，SDK显式参数要求另在约束中说明。契约评测仅将明确true视为已知必填，不对未知规则宣称通过；本批case显式发送timeFormat。

- Claude A：仅client.py与operations.py中的公开请求支持；不改Settings或旧测试。
- Claude B：仅settings.py十项方法；保持get_switch/set_switch行为。
- Claude C：独立新增匿名模式与设置测试，不读取A/B候选实现。
- 下一批并行补充契约/绑定/评测案例、公开请求评测语义与文档；共享登记文件由协调方按审核后的独立片段合并。
- Codex核查协议、区域和provider隔离；要求同步/异步、未授权区域、非法输入零认证/零业务请求、公开操作零provider调用与零token字段、错误不重放、关闭/取消、数据原样保留和既有回归通过。
- 公开请求评测必须期望零认证请求，不得调整测试以掩盖仍有认证请求或自动刷新。设备写入只做离线评测；没有海外设备，不声称设备动作可用。
