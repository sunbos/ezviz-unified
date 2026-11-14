"""Optional macOS Seatbelt backend for the controlled SDK-repair exercise.

Experimental local backend only -- NOT a general-purpose ADK or VM, and NOT
suitable for unreviewed arbitrary code. The normal SDK does not depend on it.

Contract
--------
* Runs only on Darwin with ``/usr/bin/sandbox-exec`` present; anything else
  raises :class:`SandboxError`. There is NO unsandboxed fallback.
* ``work_root`` must be a dedicated real directory strictly inside
  ``tempfile.gettempdir()`` -- never the temp root itself.
* Only ``<work_root>/run`` is writable; ``run/logs`` and ``run/tmp`` are
  created up front and ``TMPDIR`` points at ``run/tmp``. Scripts, SDK copies
  and oracle material must live under ``work_root`` but outside ``run``.
* The trusted interpreter is ``Path(sys.executable).resolve()`` invoked as
  ``python -I -S -B <script> [args...]`` (no site, no user site, no inherited
  .pth/editable installs, no HOME/credentials/model config). Callers place
  clean dependency dirs inside the scratch root and sys.path them themselves.
  ``args`` are plain argv strings -- never a shell.
* The generated profile is ``deny default``:
    - read-only + exec-map for the interpreter, its BASE install prefixes
      (sys.base_prefix/sys.base_exec_prefix only -- never sys.prefix venv
      roots or package-manager trees like /usr/local or /opt/homebrew),
      ``work_root``, /System, /usr/lib, /usr/share, dyld cache and timezone
      data, plus a few /dev nodes;
    - global ``file-read-metadata`` (stat/readdir names) plus one literal
      ``file-read-data`` on the root directory "/" itself -- measured on
      real macOS: the loader (dyld CacheFinder/ignite) opens "/" during
      startup and the interpreter aborts before main without it. A
      literal-directory allowance is NOT ``(subpath "/")`` and grants no
      arbitrary file-content reads;
    - all networking denied (TCP/UDP AND unix-socket connect/bind); only the
      ``socket()`` syscall stays allowed so ``socketpair()`` -- needed for
      asyncio's self-pipe -- still works; TCP connects get EPERM;
    - ``process-exec`` limited to the same trusted interpreter; fork kept;
      ``process-info``/``signal`` limited to target self -- no global
      pidinfo/codesignature;
    - ``sysctl-read`` limited to hw.*/machdep.*/a few kern.* OS names -- no
      kern.procargs; no mach-lookup rules at all (fixed TMPDIR means no
      user lookup; re-add one specific service only if a real crash is
      demonstrated by the coordinator's probe);
    - all paths are quoted via :func:`_sb` so a path cannot inject policy.

stdout/stderr go to size-capped logs under ``run/logs`` and are never echoed
to the parent console. On timeout the child *process group* is SIGKILLed;
other sessions are untouched (``start_new_session=True``). A policy compile
error or failed launch yields a nonzero exit_code (or SandboxError) -- it is
never reported as an isolated success.

``probe_source()`` returns trusted probe source the coordinator saves inside
``work_root`` and executes explicitly via :func:`run_python`; its JSON report
is an *observation* of the current policy, not a formal security proof.
"""

from __future__ import annotations

import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

__all__ = [
    "SANDBOX_EXEC_PATH",
    "BACKEND",
    "SandboxError",
    "build_child_env",
    "build_profile",
    "probe_source",
    "run_python",
]

SANDBOX_EXEC_PATH = Path("/usr/bin/sandbox-exec")
BACKEND = "sandbox-exec"
RUN_DIRNAME = "run"
_LOCALE = "en_US.UTF-8"


class SandboxError(RuntimeError):
    """Backend unavailable or inputs rejected; never falls back to bare exec."""


def _sb(text: str) -> str:
    """Quote ``text`` as a Seatbelt string literal (backslash/quote escaped)."""
    out = ['"']
    for ch in text:
        if ch in ('"', "\\"):
            out.append("\\")
        out.append(ch)
    out.append('"')
    return "".join(out)


def _require_backend() -> Path:
    if sys.platform != "darwin":
        raise SandboxError("backend %r requires macOS (Darwin)" % BACKEND)
    exe = SANDBOX_EXEC_PATH
    if not (exe.is_file() and os.access(exe, os.X_OK)):
        raise SandboxError("%s unavailable; refusing to run without isolation" % exe)
    return exe


def build_child_env(tmp_dir: Path) -> dict:
    """Fixed minimal child env: no HOME, no tokens/keys, no user site."""
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": _LOCALE,
        "LC_ALL": _LOCALE,
        "TMPDIR": str(Path(tmp_dir)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def _dedup(paths):
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def build_profile(work_root: Path, run_dir: Path, *, interpreter: Path | None = None) -> str:
    """Build the deny-by-default Seatbelt profile text for one run."""
    interp = Path(interpreter) if interpreter is not None else Path(sys.executable)
    interp = interp.resolve()
    root = Path(work_root).resolve()
    run = Path(run_dir).resolve()

    py_roots = _dedup(Path(p).resolve() for p in (sys.base_prefix, sys.base_exec_prefix))
    # Read+exec-map: interpreter BASE install tree only (never sys.prefix --
    # it may be a venv inside the repo), the scratch root, and the fixed
    # system runtime. If a real run reports a specific non-system dylib
    # missing, the coordinator adds that exact path -- never a whole
    # package-manager root.
    map_roots = _dedup(
        [
            *py_roots,
            root,
            Path("/System"),
            Path("/usr/lib"),
            Path("/System/Volumes/Preboot"),
            Path("/private/var/db/dyld"),
        ]
    )
    read_roots = _dedup([Path("/usr/share"), Path("/private/var/db/timezone")])

    lines = [
        "(version 1)",
        "(deny default)",
        "",
        ";; Deny ALL network ops incl. unix-socket connect/bind. Only the",
        ";; socket() syscall stays allowed so socketpair() -- which asyncio's",
        ";; self-pipe needs -- still works; TCP/UDP connects get EPERM.",
        "(deny network*)",
        "(allow system-socket)",
        "",
        ";; stat/readdir metadata allowed globally; file CONTENT is not.",
        '(allow file-read-metadata (subpath "/"))',
        "",
        ";; Measured requirement: the macOS loader (dyld CacheFinder/ignite)",
        ";; opens the root directory itself at startup and aborts before",
        ';; main without it. Literal-dir grant on "/" only -- NOT',
        ';; (subpath "/"); allows no file-content reads below "/".',
        '(allow file-read-data (literal "/"))',
        "",
        ";; Trusted interpreter + python/system runtime, read-only + mappable.",
        "(allow file-read* file-map-executable (literal %s))" % _sb(str(interp)),
    ]
    lines += [
        "(allow file-read* file-map-executable (subpath %s))" % _sb(str(p)) for p in map_roots
    ]
    lines += ["(allow file-read* (subpath %s))" % _sb(str(p)) for p in read_roots]
    lines += [
        '(allow file-read* (literal "/private/etc/localtime"))',
        '(allow file-read* (literal "/dev/urandom") (literal "/dev/random") (literal "/dev/zero"))',
        '(allow file-read* file-write* (literal "/dev/null"))',
        "",
        ";; The ONLY writable area.",
        "(allow file-read* file-write* (subpath %s))" % _sb(str(run)),
        "",
        ";; Only the same trusted interpreter may be re-exec'd.",
        "(allow process-fork)",
        "(allow process-exec (literal %s))" % _sb(str(interp)),
        "(allow signal (target self))",
        "(allow process-info* (target self))",
        "",
        ";; Hardware/OS introspection only -- no kern.procargs or proc info.",
        "(allow sysctl-read",
        '    (sysctl-name-prefix "hw.")',
        '    (sysctl-name-prefix "machdep.")',
        '    (sysctl-name "kern.ostype")',
        '    (sysctl-name "kern.osrelease")',
        '    (sysctl-name "kern.osversion")',
        '    (sysctl-name "kern.hostname")',
        '    (sysctl-name "kern.maxproc"))',
    ]
    return "\n".join(lines)


def run_python(
    script: Path,
    work_root: Path,
    *,
    args: Sequence[str] = (),
    timeout: float = 90.0,
    max_output_bytes: int = 2097152,
) -> dict:
    """Run ``script`` under sandbox-exec; contract in the module docstring.

    Returns a dict with fixed keys: ``backend``, ``exit_code``, ``timed_out``,
    ``stdout_path``, ``stderr_path``, ``stdout_bytes``, ``stderr_bytes``,
    ``stdout_truncated``, ``stderr_truncated``. A nonzero ``exit_code`` may be
    the script's status OR sandbox-exec itself (e.g. profile compile error) --
    inspect ``stderr_path`` locally; it is never claimed an isolated success.
    """
    sandbox = _require_backend()
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a finite positive number of seconds")
    if (
        isinstance(max_output_bytes, bool)
        or not isinstance(max_output_bytes, int)
        or max_output_bytes <= 0
    ):
        raise ValueError("max_output_bytes must be a positive int")

    tmp_root = Path(tempfile.gettempdir()).resolve()
    if Path(work_root).is_symlink():
        raise SandboxError("work_root must be a real dir, not a symlink: %s" % work_root)
    root = Path(work_root).resolve()
    if root == tmp_root or tmp_root not in root.parents:
        raise SandboxError(
            "work_root must be a dedicated subdirectory inside %s, not the temp root" % tmp_root
        )
    if not root.is_dir():
        raise SandboxError("work_root is not an existing directory: %s" % root)

    run_dir = root / RUN_DIRNAME
    if run_dir.resolve() != run_dir:
        raise SandboxError("writable run/ path must not be a symlink: %s" % run_dir)
    if run_dir.exists() and not run_dir.is_dir():
        raise SandboxError("run/ exists but is not a directory: %s" % run_dir)
    script_p = Path(script).resolve()
    try:
        script_p.relative_to(root)
    except ValueError:
        raise SandboxError("script must live inside work_root: %s" % script_p) from None
    if not script_p.is_file():
        raise SandboxError("script is not a regular file: %s" % script_p)
    if script_p == run_dir or run_dir in script_p.parents:
        raise SandboxError("script must live outside the writable run/ area")

    if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
        raise TypeError("args must be a sequence of str, not %s" % type(args).__name__)
    argv = []
    for a in args:
        if not isinstance(a, str):
            raise TypeError("args must be a sequence of str")
        if "\x00" in a:
            raise ValueError("args must not contain NUL characters")
        argv.append(a)

    logs_dir = run_dir / "logs"
    tmp_dir = run_dir / "tmp"
    for wdir in (logs_dir, tmp_dir):
        if wdir.is_symlink():
            raise SandboxError("writable path must not be a symlink: %s" % wdir)
        if wdir.exists() and not wdir.is_dir():
            raise SandboxError("writable path is not a directory: %s" % wdir)
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise SandboxError("could not create run/ workspace") from None

    interp = Path(sys.executable).resolve()
    profile_path = run_dir / "harness.sb"
    stdout_path = logs_dir / "stdout.log"
    stderr_path = logs_dir / "stderr.log"
    for out_path in (profile_path, stdout_path, stderr_path):
        if out_path.is_symlink() or (out_path.exists() and not out_path.is_file()):
            raise SandboxError("refusing non-regular or linked writable file: %s" % out_path)
    try:
        profile_path.write_text(build_profile(root, run_dir, interpreter=interp), encoding="utf-8")
    except OSError:
        raise SandboxError("could not write sandbox profile") from None
    cap = int(max_output_bytes)
    cpu_seconds = int(math.ceil(float(timeout))) + 5  # backstop only

    def _apply_limits() -> None:  # runs in child after setsid(), before exec
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    cmd = [
        str(sandbox),
        "-f",
        str(profile_path),
        str(interp),
        "-I",
        "-S",
        "-B",
        str(script_p),
        *argv,
    ]
    timed_out = False
    with open(stdout_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=out_fh,
                stderr=err_fh,
                cwd=str(run_dir),
                env=build_child_env(tmp_dir),
                start_new_session=True,
                preexec_fn=_apply_limits,
            )
        except OSError as exc:
            raise SandboxError(
                "sandbox-exec failed to launch (errno %s); see local logs" % exc.errno
            ) from None
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        # Whether the parent exited or timed out, descendants may linger in
        # this run's process group. SIGKILL the whole group so none survive;
        # proc.pid is the group leader (start_new_session) so no other
        # session can be affected.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
        exit_code = proc.returncode

    def _size(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0

    out_bytes, err_bytes = _size(stdout_path), _size(stderr_path)
    hit_fsize = exit_code == -signal.SIGXFSZ
    return {
        "backend": BACKEND,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_bytes": out_bytes,
        "stderr_bytes": err_bytes,
        "stdout_truncated": out_bytes >= cap or hit_fsize,
        "stderr_truncated": err_bytes >= cap or hit_fsize,
    }


_PROBE_SOURCE = '''"""Trusted sandbox probe -- coordinator saves this inside work_root and
runs it explicitly via harness.macos_sandbox.run_python, e.g.::

    probe = work_root / "probe.py"
    probe.write_text(probe_source())
    sentinel = Path(tempfile.gettempdir()) / "sentinel-outside.txt"
    sentinel.write_text("synthetic-not-a-secret")
    result = run_python(probe, work_root, args=["--sentinel", str(sentinel)])

Invocation args (argparse):
    --sentinel PATH   required; synthetic file OUTSIDE work_root -- reading it
                      must be denied (never point at real credentials)
    --readonly PATH   file under work_root but outside run/ that must stay
                      unwritable (default: this probe file itself)
    --run-dir PATH    writable scratch (default: $TMPDIR == <work_root>/run/tmp)
    --tcp-host HOST   default 127.0.0.1
    --tcp-port PORT   default 9

Prints a JSON report of *observed* policy behaviour (not a security proof) and
exits 0 only when every expectation held.
"""

import argparse
import asyncio
import errno
import json
import os
import socket
import sys

_DENIED = {errno.EPERM, errno.EACCES}


def _expect_denied(fn):
    try:
        fn()
    except OSError as exc:
        if exc.errno in _DENIED:
            return True, "denied: errno %d (%s)" % (exc.errno, exc.strerror)
        return False, "unexpected OSError errno %s (%s)" % (exc.errno, exc)
    return False, "operation unexpectedly succeeded"


def _expect_allowed(fn):
    try:
        fn()
    except Exception as exc:
        return False, "unexpected %s: %s" % (type(exc).__name__, exc)
    return True, "completed"


def _tcp_check(host, port):
    sock = socket.socket()
    sock.settimeout(5.0)
    try:
        sock.connect((host, port))
    except OSError as exc:
        if exc.errno == errno.EPERM:
            return True, "connect() denied with EPERM"
        if exc.errno == errno.ECONNREFUSED:
            return False, "ECONNREFUSED -- socket op reached the network, NOT sandboxed"
        if exc.errno in _DENIED:
            return False, "denied with errno %d, expected EPERM" % exc.errno
        return False, "unexpected OSError errno %s (%s)" % (exc.errno, exc)
    finally:
        sock.close()
    return False, "TCP connect to %s:%d succeeded -- NOT isolated" % (host, port)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sentinel", required=True)
    p.add_argument("--readonly", default=os.path.abspath(__file__))
    p.add_argument("--run-dir", default=os.environ.get("TMPDIR", "."))
    p.add_argument("--tcp-host", default="127.0.0.1")
    p.add_argument("--tcp-port", type=int, default=9)
    ns = p.parse_args(argv)

    checks = []
    checks.append(("sentinel_outside_read_denied",)
                  + _expect_denied(lambda: open(ns.sentinel, "rb").read(64)))

    def _w_readonly():
        with open(ns.readonly, "ab") as fh:
            fh.write(b"")
    checks.append(("readonly_source_write_denied",) + _expect_denied(_w_readonly))

    def _w_run():
        probe_file = os.path.join(ns.run_dir, ".probe-write-test")
        with open(probe_file, "wb") as fh:
            fh.write(b"probe")
        os.remove(probe_file)
    checks.append(("run_tmp_write_allowed",) + _expect_allowed(_w_run))

    checks.append(("tcp_connect_eperm_denied",)
                  + _tcp_check(ns.tcp_host, ns.tcp_port))

    async def _amain():
        await asyncio.sleep(0)
        return True
    checks.append(("asyncio_run_allowed",)
                  + _expect_allowed(lambda: asyncio.run(_amain())))

    report = {
        "probe": "macos-sandbox-harness",
        "note": "Observed behaviour of the current seatbelt policy; not a security proof.",
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
    }
    report["all_ok"] = all(c["ok"] for c in report["checks"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def probe_source() -> str:
    """Return trusted probe source; invocation contract is in the probe's
    own docstring (coordinator writes it under ``work_root`` then calls
    ``run_python(probe, work_root, args=["--sentinel", ...])``). Results are
    observations of the current policy, never a security proof."""
    return _PROBE_SOURCE
