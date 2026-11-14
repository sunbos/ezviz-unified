# S020B：独立云录制计划契约

不接收SDK候选实现。仅依据附带归档原文和已确定接口计划，输出一个JSON代码块，顶层为contracts/bindings/cases/issues/review_notes数组。五项操作的ID固定为cn.cloud_recording.create_one_off_plan、update_one_off_plan、stop_one_off_plan、delete_one_off_plan、create_instant_plan（后四同样带cn.cloud_recording前缀）。对应原文2045/2050/2046/2049/2062。

contracts遵循附带已有样例：region=domestic, source_doc_id/url/content_sha256来自已验证输入，方法/路径、token位置、body_encoding、success、parameters、read_only=false、constraints、uncertainties、transport_evidence。JSON编码名使用json；表单使用form_urlencoded。success的code_path/message_path/data_path/values按原文，禁止把数字200与字符串200统一。parameters仅列实际最外层Header和body字段；devIndexInfos[].deviceSerial/localIndex/validateCode等嵌套字段完整记录在constraints与证据中，不当成独立顶层wire字段。optional默认如已声明才填写，不推断FAQ里的“一般”是硬上限。

bindings模块固定src/ezviz_unified/cloud_recording_plans.py；symbols为CloudRecordingPlans.<方法>和AsyncCloudRecordingPlans.<方法>；public_entry=cloud_recording.<方法>。offline_status=pending，live_status=not_verified，不能自报已通过。

cases按附带传输评测格式给每项一个独立调用和精确预期wire字段。CloudRecordingDevice用{$type:CloudRecordingDevice,kwargs:{device_serial:EVAL_DEVICE,local_index:"2",validate_code:EVAL_VERIFY}}，devices放list。datetime用{$type:datetime,value:"2026-01-02T03:04:05"}。字段名是Python入口snake_case，fields是原始wire名，JSON设备数组是普通list[dict]。不要注入accessToken；不把JSON body字段放json_fields（该字段仅用于表单内JSON文本）。计划名使用EVAL_PLAN_1等合成值，ID可用42/43。至少覆盖一个显式False可选项、停止form bool字符串；请求字段不能从实现反推。

issues记录源日期时区未声明、2045的条件必填与2050严格endTime差异、2062“一般≤24h”非统一上限/设备列表边界未声明、HTTP状态与业务错误码不混用等真正源问题。源没有明确指出的约束不得补造，SDK自行施加的有效时长等校验要在review_notes标明。只整理候选，不联网、不修改真实账号或文件。
