# D002B：单设备检查

Tests only, independently from production implementation. Return JSON {"files":{"tests/test_sdk_inspection.py":"complete file"}}. Must use fixed public API in plan. MockTransport exercises real sync/async Client regional token routes plus allfivequerymethods; narrowmonkeypatches okay for CancelledError/programmererrors and exactresponse/erroridentity. Never run nativeprocesses/network or changeoriginaltests. No mocks of proposed helper internals. Validation tests ensure zeroauth andzerobusiness. Borrowedclient remains usable afterhelper; do not requirefuture extrafields or unspecified implementation. Keep tests focused, no massive Cartesian duplication. Zero tools/no execution. devin/swe-2 effort=max.

固定边界见[实施计划](../../plans/2026-11-15-device-inspection.md)。

补充 `tests/test_sdk_inspection_facades.py`，独立验证借用client现有门面而非构造替代对象；测试修订与拒收记录见review.md。
