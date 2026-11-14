# H012：macOS隔离Python执行原型

devin/swe-2、effort=max、零工具。输出JSON代码块files，仅含harness/macos_sandbox.py、tests/test_harness_macos_sandbox.py、docs/research/2026-11-15-macos-repair-sandbox.md。不得执行或声称探针通过，不启动Docker或改系统配置。SDK正常运行不依赖本工具。

这是维护实验的可选macOS后端，不是通用ADK/VM，也不能声称适合未经审查的任意代码。run_python(script: Path, work_root: Path, *, args: Sequence[str]=(), timeout: float=90.0, max_output_bytes: int=2097152)->dict。仅Darwin且/usr/bin/sandbox-exec存在可运行，否则明确拒绝，不回退无隔离执行。work_root必须是tempfile.gettempdir()下的独立真实子目录（不是整个临时根）；script须位于该根内的普通文件，args只是argv字符串不经shell。root/run为唯一可写区，预先创建日志和tmp目录；脚本/SDK/Oracle应放在run之外只读。

使用当前可信Python解释器（Path(sys.executable).resolve）执行-I -S，调用方脚本自己加入scratch内干净依赖目录；不得加载用户site、原项目editable .pth或继承凭据。子env从少量固定项构造：PATH=/usr/bin:/bin、UTF8 locale、TMPDIR=root/run/tmp、PYTHONDONTWRITEBYTECODE=1、PYTEST_DISABLE_PLUGIN_AUTOLOAD=1；不要继承HOME或任意TOKEN/KEY/模型配置。

Seatbelt profile须deny default。允许必要的当前Python runtime只读（sys.base_prefix/base_exec_prefix及可信解释器路径）、work_root只读、只读系统运行库(/System、/usr/lib、/usr/share，必要的/dev/null、/dev/urandom、/private/etc/ssl/localtime等)。只允许root/run写入；不得允许整个home、/Users、/private/var或/。可允许全局文件metadata查询，但不能读取任意文件内容；文档应区分。网络必须拒绝（无临时放开规则）；process-exec只允许可信Python，process-fork按需允许；不要allow process*或开放其它进程信息/环境读取，process-info如需要仅target self，sysctl读取限定系统/硬件信息而非kern.procargs等。不要开放任意mach-lookup；必要项应有解释。路径字符串须正确转义进profile，不能让路径内容注入策略。

stdout/stderr写入root/run的限量日志（不直接回显）；设置可行的日志文件大小/CPU限额并使用Popen start_new_session=True。超时杀死本次进程组，等待清理，不影响其它会话。返回固定字段backend、exit_code、timed_out、stdout_path/stderr_path、输出字节数/是否超限；只在sandbox-exec成功启动Python并结束时称执行完成，不把策略编译错误当隔离通过。不要泄漏子进程异常文本到公共错误，输出日志留本地供协调方核查。

同时提供一个可信探针写入函数或probe_source()字符串，协调方在work_root里保存并显式执行：确认读取run外且在work_root外的合成sentinel被拒绝、连接127.0.0.1 TCP被EPERM拒绝（不是仅连接拒绝）、修改SDK/Oracle只读文件被拒绝、run下临时写允许、asyncio.run可工作。不要读真实凭据。探针结果只是当前策略的实际观察，不是形式安全证明。

单元测试可mock Popen/profile构造等但不得把mock当隔离成功；覆盖参数/路径拒绝、env最小化、无shell、超时进程组清理、非Darwin fail-closed。实际native probe由协调方运行并另记。使用附带Apple系统profile示例的语法，遇到不确定请说明，不用allow default兜底。
