# H011B 验收记录

接受。27项独立Oracle helper回归通过。协调方复现50与50.0相等造成的越界漏检，Claude改为严格预期AST比较，并修复重复目标函数、UTF8字节定位与异常信息。Oracle作者未收到待测模型补丁。原SDK在新Oracle下4失败11通过，原样补丁在副本中使完整1978项通过。

证据见 `docs/validation/maintenance/2026-11-15-controlled-repair/`。生产SDK上限仍为50，未调用真实API或授权发布。
