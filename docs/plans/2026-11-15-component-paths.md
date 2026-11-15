# 组件路径、JSON值与已审能力目录

## 目标与依据

为国内设备组件仓建立可复用的路径传输与有来源约束的调用入口，避免逐型号复制方法。此次重新获取774、775、812、847、868、998、1134、1447、1512、1513、1514、3873共12页，正文哈希均与归档一致。

路径式`/api/v3/otap/{prop,action}/{deviceSerial}/...`与现有头部式`/api/v3/device/otap/{prop,action}`没有已确认的等价/替代声明，保持两套独立契约。共享的是传输、鉴权、编码和响应检查，不据此推断型号能力、白名单状态或物理效果。

离线探针使用Python 3.12.13、HTTPX 0.28.1：预编码路径保持原始转义，`url.path`是解码视图；`quote('..', safe='')`仍为点段且会被URL解析器归一化。真实SDK的`json=None`产生空请求体，`0`、`False`、空字符串/数组/对象均能正确编码。模拟业务错误回显路径标识时，当前错误str/repr仍含该标识；模拟连接错误未回显。上述不是生产API泄露或设备能力测试。

## 实现决定

### 路径参数

- `Operation.path`支持完整路径段形式的`{name}`，名字仅ASCII标识符；拒绝不平衡/嵌套花括号和嵌入式表达式。模板静态部分拒绝导航点段。既有固定路径验证保持。
- `Client.request`与`AsyncClient.request`增加仅关键字参数`path_params: Mapping[str, str] | None = None`。所给键必须与模板占位符精确对应；缺项、多余键、非字符串键值、空白值、不可UTF-8编码值、控制字符、单独`.`/`..`及原始`/`或`\`拒绝，均在认证前完成。
- 参数是未编码的单个路径段。使用标准UTF-8百分号编码一次，保留大小写和非空白原文；原始`%`作为数据编码，例如`a%2Fb`变为`a%252Fb`。不预先unquote，不将编码视图与解码视图混用。
- 原始路径参数及编码形式加入本次请求的脱敏上下文；公开与认证请求均适用。实际传输仍通过现有路径/区域校验，重试使用同一份已验证路径与参数快照。
- 模板保留在Operation元数据中，设备值只进入每次请求的已渲染路径；绑定与源码审计不记录真实设备标识。

### 显式JSON null

- 新增公开常量`JSON_NULL`，代表根级JSON字面量`null`；`json=None`保留原有省略语义。嵌套JSON中的null仍用普通None。
- header/query/none token位置允许显式JSON_NULL；form/json token位置拒绝，因为不能向根级null注入受管token。与data同用仍拒绝。
- 传输层将标记发送为`b'null'`，缺省添加JSON Content-Type而不覆盖调用方明确设置的类型。普通JSON值与现有消息处理不变，不新增通用原始body绕过入口。

### 已审组件目录

`Client.components`/`AsyncClient.components`提供`call(operation_id, device_serial, *, payload=未提供标记)`，以及不联网的`describe(operation_id)`、`list_documented()`。目录描述官方声明，不是设备能力检测；不自动查询型号、遍历设备或尝试替代路径。

初期可调用目录：

| operation_id | 文档 | 方法/用途 | 请求体 |
| --- | --- | --- | --- |
| cn.components.face_voice_video.get | 774 | GET，Video/1语音提示查询 | 无 |
| cn.components.face_voice_video.set | 775 | PUT，同一路径设置 | 对象，enabled为可省略Boolean |
| cn.components.face_voice_lock.get | 812 | GET，DoorLock/0变体查询 | 无 |
| cn.components.temperature.get | 1134 | GET，温度原值查询 | 无 |
| cn.components.smoke_countdown.get | 1447 | GET，烟感倒计时查询 | 无 |
| cn.components.gas_countdown.query | 998 | PUT action，正文声明为查询 | 显式null |
| cn.components.chime_power.set | 868 | PUT，电量字段设置 | 非bool整数0..100 |

所有路径除deviceSerial外保持对应文档的固定字面段，不互换Video/1、DoorLock/0、GasSensor/0、SmokeSensor/0等资源。body操作要求调用方显式给payload，None仅为null操作的合法值；无body操作拒绝额外payload。775只接受声明的enabled字段及Boolean类型，保留其可省略性；无字段的空对象效果未验证。868不解释为改变物理电池容量，不变换数值或单位。

目录保留847格式化与3873异常模板的不可调用元数据/原因：前者缺少明确选盘规则且为破坏性动作，后者的双花括号和引号索引含义未确认。它们不计入已实现契约；call在认证前拒绝。真实业务调用均不在本批验收范围。

目录信息至少包括ID、来源文档/URL、原路径、方法、声明型号、白名单要求（未声明为未知）、读写依据、SDK可调用性与限制。不要从文档列举型号推断其他型号不支持，也不把可调用性当实机验证。采用现有可选deviceMeta检查：有该层时检查示例/明确说明的`0x00000000`；缺层不等于物理执行成功，并保留证据强弱差异。

### 契约与评测

- 参数位置增加path；每个模板占位符都须有对应的必填path参数，不能使用受管accessToken占位符。
- 独立案例增加`path_fields`，与原有fields/json_fields/json_body分开，避免同名字段跨位置混淆。期望路径由源契约模板和案例值独立构建，不调用SDK路径函数，也不取实际请求反推期望。
- 比较实际`url.raw_path`的路径部分，避免把编码斜杠与真实分隔符混为一谈。保留固定路径原有评测。
- whole JSON请求体支持object/array/string/integer/number/boolean/null；明确区分案例缺少json_body键与键值为null。描述符required可为bool或带required_uncertainty的显式null，accessToken规则不放宽；每个whole-body案例必须明确提供期望json_body，不据此证明源接口的可省略性。
- 保留认证接口、无token公开操作、命名JSON字段与现有whole-object评测。增加错误路径值、重放、路径标识回显、JSON根类型及null/无body混淆的独立反例。

## 分工与验收

核心路径/JSON标记、组件目录、harness分别指定唯一文件写入方；公共导出与client挂载由核心任务统一完成。独立测试按固定接口编写，集成前不运行未审查网页派生代码。生成代码不接收凭据，设备调用默认使用MockTransport。

先实现核心与评测，再接目录源契约；不得把通用传输可构造任意URL计成全量接口覆盖。必要验收包括旧196项契约回归、新目录逐项源码与报文核对、同步/异步、无效输入零认证请求、匿名路径处理、点段与转义、错误脱敏、body null与省略的区分、打包和四版本CI。
