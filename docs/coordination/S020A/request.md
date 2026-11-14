# S020A：云录制计划SDK候选

根据附带计划、原文与源码，生成实现和pytest候选。只返回一个JSON代码块：`{"files":{...},"edits":[{"path":"...","old":"唯一原文","new":"替换内容"}],"notes":[]}`。files仅新增`src/ezviz_unified/cloud_recording_plans.py`和`tests/test_sdk_cloud_recording_plans.py`；edits只允许cloud_recording.py、__init__.py、errors.py和harness/wire_eval.py。不要重发未改变的完整源码，不改登记/契约/CI。不运行工具、不声称测试已通过。

使用CloudRecordingPlans/AsyncCloudRecordingPlans无实例slot的mixin，把现有CloudRecording/AsyncCloudRecording继承接入，保留现有构造和查询。新模块不得反向import cloud_recording以免循环依赖。同步/异步复用参数构造，调用已有_client.request；请求数据不能存为共享可变状态。公开类和DTO有类型标注，满足现有strict mypy；DTO仅导出CloudRecordingDevice，单一字段校验错误不得包含输入值。

源参数、method/path/envelope遵从计划与原文；业务operation name使用cloud_recording.<公开方法名>。methods keyword参数与计划一致；devices接受Sequence[CloudRecordingDevice]，拒绝str/bytes/非DTO项，不修改调用者列表，构造wire时复制。一次性创建/更新按1–1000检查，即时计划只校验序列和元素类型，不复用源未声明的1–1000数量限制。DTO两必填str非空；validate_code非None时须非空str，可选不发送，不推断设备是否加密。

创建/更新名称regex仅ASCII英数、CJK中文、下划线和中划线，长度1–32；即时名称仅非空str。上述名称/时间/数量边界之外不猜业务权限。所有显式Boolean参数严格bool或None，form bool发送小写true/false；JSON保持Boolean。ID严格int拒绝bool，不猜未声明的正值范围。stream_type显式时只1/2；hours为正整数，不设24h硬上限。None省略；create specified_end_time=None按平台默认需要end_time，但不主动发送True；update无论该flag如何都要求end_time。输入有结束时间时检查不早于开始；日期不接受aware或microseconds。

Header token位置由Operation声明，JSON/form payload里不要手工注入accessToken。前四接口meta整数200、即时顶层字符串200。所有写操作read_only=False；海外在认证前拒绝。不要自动重试、恢复、轮询、改设备密码或下载媒体。

根__init__.py导出DTO，wire_eval.py现有DTO白名单加入CloudRecordingDevice。errors.py敏感字段加入planName（若尚不存在），复用现有嵌套收集与脱敏；不要另造日志系统。cloud_recording.py现有只读总述更新为查询+显式计划写，保留其余原文。

pytest覆盖五方法sync/async实际HTTP位置与payload、None省略、False保留、JSON与form编码、成功码层级/类型、10002写不重放、海外和无效输入在认证前拒绝、结束时间规则、1/1000边界、即时未声明上限不误限、设备列表不变及repr/异常中的planName/deviceSerial/validateCode脱敏。只用合成值，不抄原文真实样例。用现有SDK真实构造签名；不绕过构造或删断言来适配猜测。
