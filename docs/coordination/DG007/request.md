# DG007：精确类型与配置观察边界修订

固定devin/swe-2 effort=max，纯文本零工具，仅候选，不执行/不声称自测。唯一文件 src/ezviz_unified/diagnostic_report.py 和新增 tests/test_sdk_diagnostic_boundaries.py。输出简短JSON围栏 {"edits":[{"path":"src/ezviz_unified/diagnostic_report.py","old":unique_old,"new":replacement}],"files":{"tests/test_sdk_diagnostic_boundaries.py":"完整测试"},"notes":[]}。测试约80–130行，源码精简2处修正及必要import，不大重构。

Codex只读复核发现两项具体证据：
1. _classify仍用issubclass(exc_type,(ValueError,TypeError))。冻结设计未知自定义异常子类只归unknown/application，只有确切内建类型可归validation；改为exc_type is ValueError or exc_type is TypeError。取消/中断子类仍保留可信issubclass真实类型逻辑。
2. _region_scope_of对RegionConfig子类执行scope getter。现有Client允许RegionConfig子类，而诊断额外getter可吞掉首次ValueError使实际请求从失败变成功，改变行为。新增from .auth_models import RegionConfig，只有type(region) is RegionConfig才读取scope；未知子类region记unknown，绝不调用它的scope getter，不收紧原Client接受范围，也不改auth_models.py。

测试先于修订运行red由Codex组织：
- 同/异步构建RegionConfig子类，通过__getattribute__在scope读取时计数，构造阶段关闭armed，client创建之后armed=True。第一次scope访问抛同一个ValueError，之后正常。分别启用/关闭recorder调用client.request且固定anonymous/public Operation，无真实网络（MockTransport）。两种都必须原异常identity、同样一次scope读取、无HTTP。finally关闭client期间启用记录器也不可额外读取未知region.scope。enabled事件region全unknown。
- 未知ValueError/TypeError子类，在真实components.call前置payload校验里抛出：dict或Mapping subclass的items() raises marker；选择cn.components.face_voice_video.set,payload=该对象；设备serial仅合成。同步/异步都应无request、marker identity保留、call failed error_family unknown，而普通实际空白参数校验仍validation。不得发写请求，用拒绝MockTransport，原校验即抛出。
- 此外capture内未知子类仍application，可简单参数化。

以上两项作为方法/配置边界，不重写错误分类框架。输入现有源码及必要基线片段。
