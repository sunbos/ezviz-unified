# S021B：独立Webhook验收

devin/swe-2、effort=max，零工具，不接收实现。只输出JSON代码块 `{"files":{"tests/test_sdk_webhooks.py":"完整测试"},"notes":[]}`。按附带计划定义的API编写pytest，不修改源码/预期向量，不执行或声称通过。

覆盖：RFC2202 SHA1 case2固定向量（key='Jefe'，data='what do ya want for nothing?'，digest='effcdf6ae5eb2fa2d27416d5f184df9c259a7c79'；可把data分成raw_body=b'what do ya want for '和timestamp='nothing?'）；主导方提供的固定UTF8/中文JSON向量；错误key/body/timestamp、大小写hex等价、非ASCII/空/短长/带空格hex拒绝；不strip原始body/timestamp或重序列化；普通verify可验证非JSON UTF8，parse必须验签后才JSON解析。

解析测试覆盖body为str/list/object/null等JSON值、未知字段和type保留、body.payload字符串不二次解析、messageId/type缺失或空/错误类型、顶层/headershape不符、重复keys、NaN/Infinity、坏UTF8、过深嵌套；ProtocolError/AuthenticationError不带原始PRIVATE标记及异常链。边界长度等于max_bytes通过、超限拒绝；max_bytes bool/非正/非int拒绝。错误签名+非法JSON应AuthenticationError，而有效签名+非法JSON应ProtocolError。

DTO repr不含messageId/type/body/raw/secret等payload；ack返回新dict，仅messageId，修改一次返回dict不影响下次ack。明确未验签入口可解析，但不能把其输出当已认证；不要自行追加未规定的auth flags测试。SDK没有Webhook网络服务，测试不发网络、不读取真实配置。

签名新向量的预期值不能调用被测SDK计算；除固定向量外可用标准库hmac生成其他测试输入，主要正例必须断言固定的独立预期。不要用同一生成逻辑自证所有情况，不要绕过真实异常构造。保持测试精简且覆盖有意义行为，不扫描sys.modules或依赖加载顺序。导出尚不存在时允许测试明确失败。
