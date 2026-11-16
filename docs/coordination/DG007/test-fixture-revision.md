# DG007R：修正测试任务的输入缺口

固定devin/swe-2 effort=max，零工具，仅候选。唯一tests/test_sdk_diagnostic_boundaries.py；输出JSON {"edits":[{"path":"tests/test_sdk_diagnostic_boundaries.py","old":unique_old,"new":replacement}],"notes":[]}。不改生产代码/断言。

先前任务未给Client构造源码，候选猜测transport=导致10个测试全部在构造阶段TypeError，因此该red不能证明目标缺陷；现在修正任务输入和fixture路线。SDK真实参数是transport_factory: Callable[[str], httpx.BaseTransport|None]（Async对应AsyncBaseTransport），没有transport=。anonymous=True不能带app_key/app_secret/auth_transport。HTTPX MockTransport同时实现sync/async且接受同步handler。

请仅修测试fixture：_RejectingTransport应真正继承httpx.MockTransport，调用其constructor(handler=self的拒绝handler)，拒绝handler递增calls然后固定AssertionError；对sync/async都生效。_client用cls(region=region, anonymous=True, transport_factory=lambda _origin: transport, diagnostic_recorder=recorder)，不传凭据。不发真实HTTP。加import httpx，删虚构send/arequest等__getattr__探测，close可直接继承MockTransport。其它既有测试和断言都保持，禁止放宽。

真实Client已支持diagnostic_recorder；本次只修错fixture后重新观察regiongetter和unknown异常类别的red，再由Codex应用原DG007生产修订。不要重新输出源码修复。
