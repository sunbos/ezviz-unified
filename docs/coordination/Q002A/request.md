# Q002A：已复现的核心配置校验修补

固定devin/swe-2、effort=max、零工具。只输出JSON代码块：`{"files":{"tests/test_sdk_alpha_validation.py":"新回归测试"},"edits":[{"path":"...","old":"唯一原文","new":"替换"}],"notes":[]}`。edits只允许src/ezviz_unified/client.py、src/ezviz_unified/transport.py和README.md的日志范围一句。不要重发完整源码，不引入依赖、不修改协议或重试次数，不执行或声称测试通过。

主导方已复现：四种Client/Transport构造传timeout=10**400均抛OverflowError；AsyncClient允许async def invalidate的provider，10002路径出现未等待协程且失效回调未执行；普通def get_token返回asyncio.Future的合法provider工作正常且构造时不调用getter。

修补要求：

1. 两个_validate_timeout仅捕获float转换OverflowError，转为现有固定ValueError；不回显输入值，不吞其他编程错误。正常、bool、inf/NaN和非正值行为保持。
2. Client/AsyncClient在选择外部provider时拒绝明确的coroutine-function invalidate，错误为固定TypeError。invalidate协议为同步，不调用它来探测。无需泛化处理每一种动态代理或所有错误用户实现。
3. AsyncClient首次取token时检查get_token返回对象是否awaitable；若不是抛固定TypeError，不回显对象。仍保持惰性，不能在构造时调用getter；不能以iscoroutinefunction(get_token)作为必需条件，因为AsyncTokenProvider声明的是返回Awaitable[Token]，普通def返回Future合法。get_token自身抛出的编程异常仍传播，不广泛捕获。
4. README明确SDK只对其请求上下文内的`httpx` logger请求日志脱敏；用户自行记录原始响应及其他依赖库调试输出不由此保证。没有确认真实httpcore泄漏，不增加全局日志钩子或修改应用handler。
5. 新测试用真实SDK和MockTransport，覆盖四类超大timeout、两类Client拒绝async invalidate且构造不调用getter/不发网络、AsyncClient拒绝非awaitable结果且没有业务请求、普通def返回Future仍可用且构造惰性。保留只读10002单次恢复和写不重放的现有回归，不复制整套原测试。

以下附当前文件及现有相关测试参考。所有fixture必须合成，避免通过__new__绕过真实构造或吞TypeError掩盖签名猜测。
