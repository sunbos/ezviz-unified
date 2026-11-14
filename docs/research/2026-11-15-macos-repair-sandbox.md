# macOS Seatbelt 修复沙盒（实验原型）

> 状态：实验原型。**已有一轮窄样本机探针观察**，完整记录见
> `docs/validation/maintenance/2026-11-15-controlled-repair/README.md`
>（随该验证批次另行提交）。仅用于受控修复实验中的**已审查代码**；不是
> 通用 ADK、不是 VM，**不能**用于运行未经审查的任意代码或恶意样本。
> SDK 正常运行不依赖该后端，也**不存在无隔离回退**。下文隔离结论仅
> 覆盖本次实跑观测到的行为，不是形式化安全证明。

## 边界与契约

- 仅 Darwin 且 `/usr/bin/sandbox-exec` 可执行时可用；否则抛 `SandboxError`，
  **不回退到无隔离执行**。
- `work_root` 必须是 `tempfile.gettempdir()` 下的独立真实子目录（非临时根
  本身、非逃逸符号链接、非普通文件）。唯一可写区 `<work_root>/run`，含
  `run/logs`（限量日志）与 `run/tmp`（即 `TMPDIR`）；脚本/SDK/Oracle 放在
  `run` 之外只读。
- run/logs/tmp、profile、stdout/stderr 日志等可写目标若是符号链接或已存在
  的非普通文件，一律拒绝；`run` 的规范路径必须仍在 `work_root` 内。
- 解释器固定 `Path(sys.executable).resolve()`，以 `-I -S -B` 启动，无 shell；
  `args` 仅接受 str 序列（拒绝 str/bytes 整体、非 str 元素与内嵌 NUL）。
- 子 env 仅固定少量项：`PATH=/usr/bin:/bin`、UTF-8 locale、`TMPDIR=run/tmp`、
  `PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；不继承
  HOME、TOKEN/KEY、模型配置等。
- `timeout` 为有限正数（拒绝 bool/inf/nan）；`max_output_bytes` 为正 int
  （拒绝 bool/非 int）。
- stdout/stderr 只写本地限量日志（RLIMIT_FSIZE/CPU 兜底），不回显；返回固定
  字段 `backend/exit_code/timed_out/stdout_path/stderr_path/stdout_bytes/
  stderr_bytes/stdout_truncated/stderr_truncated`。
- `start_new_session=True`；超时 SIGKILL **本次进程组** 并 wait；正常结束后
  同样清扫本组残留，不触及其它会话。
- 启动失败只暴露 `SandboxError`，不泄漏子进程异常文本；日志留本地供核查。

## Seatbelt profile 要点（deny default）

- `deny network*`，不放开任何 connect/bind（含 Unix socket）；仅保留
  socketpair 所需的 `system-socket`。
- 只读+可执行映射：`sys.base_prefix`/`base_exec_prefix`、解释器本体、
  `work_root`、/System、/usr/lib、/usr/share、dyld 缓存与时区数据、
  /dev/null、/dev/urandom 等；**不**引入 /usr/local、/opt/homebrew 等包
  管理器整树，也不读整个 home、/Users、/private/var 或 `/` 的内容树。
- 全局仅 `file-read-metadata`（stat/列名）；唯一的文件内容例外是
  `(allow file-read-data (literal "/"))`：dyld 需要打开根目录本身，实测
  缺它启动即 abort -6。该条目必须保持 `literal`，**绝不**放宽为
  `(subpath "/")`。
- `process-exec` 限同一解释器；`process-info`/`signal` 仅 `target self`；
  sysctl 限 hw.*/machdep.* 与少量 kern.* 名称（无 kern.procargs）；不开放
  任意 mach-lookup；路径经转义进策略，防注入。

## API 示例（协调方）

```python
import tempfile
from pathlib import Path
from harness.macos_sandbox import run_python, probe_source

root = Path(tempfile.mkdtemp(prefix="sbx-"))    # 必须是 tmp 下独立目录
(root / "run").mkdir(); (root / "src").mkdir()
probe = root / "src" / "probe.py"
probe.write_text(probe_source())
with tempfile.TemporaryDirectory(prefix="sbx-out-") as outside:
    sentinel = Path(outside) / "sentinel.txt"   # 自有的合成文件，非真实凭据
    sentinel.write_text("synthetic")
    res = run_python(probe, root, args=["--sentinel", str(sentinel)],
                     timeout=60, max_output_bytes=1 << 20)
print(res["exit_code"], res["stdout_path"])    # 日志留在本地核查
```

## 本机探针：首轮观察（窄样本，非证明）

协调方已在本机对真实 default-deny profile 完成一次实跑（非 mock）：

- 初版 profile 启动即 dyld **abort -6**；补上精确条目
  `(allow file-read-data (literal "/"))` 后正常——该 literal 只允许打开
  根目录本身，**不等于** `(subpath "/")` 的整树授权。
- 探针观察：`work_root` 外合成 sentinel 读 **EPERM**；SDK 与 oracle 的
  只读源文件写 **EPERM**；127.0.0.1 TCP connect **EPERM**；`run/tmp`
  写入与 `asyncio.run` 正常。
- 命令、profile 与原始输出将汇总在
  `docs/validation/maintenance/2026-11-15-controlled-repair/README.md`
  中另行提交，本文仅作摘要。
- 该结果只是当前策略在本机的一次观测，不是形式化安全证明；需在更多
  macOS 版本与配置下复跑后才可扩大结论。本沙盒依旧**不**声称能防护
  任意恶意代码，源码维持实验性质、无无隔离回退。
