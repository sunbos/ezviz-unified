# DG002

输入基线 b2b6dca；冻结设计与覆盖清单随输入。模型 devin/swe-2，effort=max。

负责SDK集成。由于264方法直接输出极长，交付单个Python围栏：可审查、可重复执行前严格检查基线的机械转换脚本。脚本仅用pathlib/ast/json，限定本任务文件；不执行shell/网络/读取其它文件。对facade用AST精确定位 class 和方法的首条非docstring语句到end_lineno，倒序缩进原代码并加 with _diagnostic_scope(self._client, entrypoint="固定prefix.method", mode="sync/async")。不得改signature、docstring和其它方法，不包装静态metadata。imports只增加必要hook。继承类各自只插桩其定义方法。Client和inspection用精确片段或AST转换但不重写整文件格式；Client两构造新可选参数及类型检查(None或type严格DiagnosticRecorder)，保存_private属性；request、close/aclose、_cleanup scope覆盖原完整体，_execute原地加auth与attempt作用域和retry事件，旧顺序保留；attempt=1/2明确，origin/factory/注入归attempt，auth含provider获取+await+validate。request outer operation传原Operation，facet operation留other。__init__只增公开导出。不改_transport/auth/errors/其他函数。唯一输出脚本，Codex审查后执行，不能执行也不能声称测试通过。

唯一候选文件：
- `src/ezviz_unified/alarms.py`
- `src/ezviz_unified/arming.py`
- `src/ezviz_unified/cloud_recording.py`
- `src/ezviz_unified/cloud_recording_plans.py`
- `src/ezviz_unified/cloud_storage.py`
- `src/ezviz_unified/components.py`
- `src/ezviz_unified/cruise.py`
- `src/ezviz_unified/delegation.py`
- `src/ezviz_unified/devices.py`
- `src/ezviz_unified/diagnostics.py`
- `src/ezviz_unified/illumination.py`
- `src/ezviz_unified/imaging.py`
- `src/ezviz_unified/maintenance.py`
- `src/ezviz_unified/management.py`
- `src/ezviz_unified/otap.py`
- `src/ezviz_unified/ownership.py`
- `src/ezviz_unified/playback.py`
- `src/ezviz_unified/ptz.py`
- `src/ezviz_unified/recordings.py`
- `src/ezviz_unified/security.py`
- `src/ezviz_unified/settings.py`
- `src/ezviz_unified/snapshots.py`
- `src/ezviz_unified/storage.py`
- `src/ezviz_unified/subaccounts.py`
- `src/ezviz_unified/video.py`
- `src/ezviz_unified/client.py`
- `src/ezviz_unified/inspection.py`
- `src/ezviz_unified/__init__.py`

验收命令由后续受限Bash会话执行：`.venv/bin/python -m pytest -q tests/test_sdk_diagnostic_report.py tests/test_sdk_diagnostic_coverage.py`、`.venv/bin/python -m mypy src/ezviz_unified`、Ruff。验收条件以冻结计划 A01–A12 相应项为准。禁止修改其他文件、联网、读取配置或凭据。
