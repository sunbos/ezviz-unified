"""Spec-level unit tests for the optional macOS Seatbelt sandbox prototype.

* ``subprocess.Popen`` is mocked everywhere: no native execution happens, so a
  green run proves argument/policy plumbing only -- it does NOT prove real
  sandbox isolation. The native probe is executed separately by the
  coordinator and reported outside this file.
* ``os.getpgid``/``os.killpg`` are stubbed for EVERY test (autouse guard):
  the fake child reports the arbitrary pid 4242 and run_python() sweeps the
  child process group on every outcome -- a real killpg could hit an
  unrelated process group on this host.
* ``sys.platform`` and ``SANDBOX_EXEC_PATH`` are stubbed so the Darwin-only
  contract is exercised on any host.
* Assertions target the public API (``run_python``, ``build_child_env``,
  ``build_profile``, ``probe_source``, ``SandboxError``, ``SANDBOX_EXEC_PATH``)
  and stay semantic: no private helpers, no exact profile formatting.
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness.macos_sandbox as ms

# ---------------------------------------------------------------------------
# Doubles / fixtures
# ---------------------------------------------------------------------------


class _FakeProc:
    """Minimal Popen stand-in honouring wait()/kill()/returncode."""

    def __init__(self, pid=4242, returncode=0):
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = []  # timeout arg of every wait()/communicate()
        self.wait_errors = []  # popped + raised by wait()/communicate()
        self.kill_calls = 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        return self.returncode

    def communicate(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        return (b"", b"")

    def poll(self):
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        self.returncode = -signal.SIGKILL

    def terminate(self):
        self.returncode = -signal.SIGTERM

    def send_signal(self, sig):
        self.returncode = -sig


@pytest.fixture(autouse=True)
def darwin_backend(monkeypatch, tmp_path):
    """Force the Darwin branch with a fake (never executed) sandbox-exec."""
    monkeypatch.setattr(sys, "platform", "darwin")
    fake = tmp_path / "sandbox-exec"
    fake.write_text("# test stub -- never executed\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(ms, "SANDBOX_EXEC_PATH", fake)
    return fake


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    """Guard: a mocked run must never reach the real OS process table.

    run_python() sweeps the child's process group via os.killpg() on EVERY
    outcome (success included), and the scripted fake proc reports the
    arbitrary pid 4242 -- which may coincide with a real, unrelated process
    group on this host.  getpgid/killpg are therefore stubbed for all tests
    (the lifecycle tests re-stub killpg with their own recorder), and any
    code reaching subprocess.Popen without popen_spy fails loudly instead
    of launching a real child.
    """
    state = SimpleNamespace(killpg_calls=[], popen_attempts=[])

    def blocked_popen(cmd, *args, **kwargs):
        state.popen_attempts.append(list(cmd))
        raise AssertionError("subprocess.Popen reached without popen_spy: %r" % (cmd,))

    monkeypatch.setattr(ms.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(ms.os, "killpg", lambda pgid, sig: state.killpg_calls.append((pgid, sig)))
    monkeypatch.setattr(ms.subprocess, "Popen", blocked_popen)
    return state


@pytest.fixture
def popen_spy(monkeypatch):
    """Patch subprocess.Popen: record argv/kwargs, return a scripted proc.

    run_python() closes the child's stdout/stderr log handles before the
    test inspects the recorded call, so writability is sampled here while
    they are still open.  A supplied preexec_fn is preserved (the real
    child needs it) but wrapped so it can never run in this process.
    """
    state = SimpleNamespace(calls=[], proc=_FakeProc(), writer=None, preexec_invoked=False)

    def fake_popen(cmd, *args, **kwargs):
        rec = SimpleNamespace(
            cmd=list(cmd), args=args, kwargs=kwargs, preexec_fn=kwargs.get("preexec_fn")
        )
        for name in ("stdout", "stderr"):
            handle = kwargs.get(name)
            setattr(
                rec, name + "_writable", handle.writable() if hasattr(handle, "writable") else None
            )
        if rec.preexec_fn is not None:

            def _guarded_preexec(*_a, **_k):
                state.preexec_invoked = True
                raise AssertionError("preexec_fn executed inside the pytest process")

            kwargs["preexec_fn"] = _guarded_preexec
        state.calls.append(rec)
        if state.writer is not None:
            state.writer(kwargs)  # lets a test fake child output
        return state.proc

    monkeypatch.setattr(ms.subprocess, "Popen", fake_popen)
    return state


def _make_scratch(prefix="sbx-"):
    """Dedicated real work_root inside the actual tempfile temp root."""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "src").mkdir()
    (root / "run").mkdir()
    script = root / "src" / "job.py"
    script.write_text("# probe stand-in\n", encoding="utf-8")
    return SimpleNamespace(
        root=root.resolve(),
        run=(root / "run").resolve(),
        script=script.resolve(),
    )


@pytest.fixture
def scratch():
    sc = _make_scratch()
    yield sc
    shutil.rmtree(sc.root, ignore_errors=True)


def _outside_dir(prefix="sbx-out-"):
    """Real dir inside the temp root but outside any given work_root."""
    return Path(tempfile.mkdtemp(prefix=prefix)).resolve()


# --- profile helpers: semantic line/sexp scans, not formatting --------------


def _policy_lines(text):
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith(";"):
            yield line


def _allows(line):
    return re.search(r"\ballow\b", line) is not None


def _unescape(value):
    return value.replace('\\"', '"').replace("\\\\", "\\")


def _targets(line, kind):
    pattern = r"\(%s\s+\"((?:[^\"\\]|\\.)*)\"" % kind
    return [_unescape(m.group(1)) for m in re.finditer(pattern, line)]


def _subpaths(line):
    return _targets(line, "subpath")


def _literals(line):
    return _targets(line, "literal")


# ---------------------------------------------------------------------------
# Backend gating: fail closed, never launch
# ---------------------------------------------------------------------------


class TestBackendGate:
    def test_non_darwin_fails_closed(self, monkeypatch, popen_spy, scratch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, scratch.root)
        assert popen_spy.calls == []

    def test_missing_sandbox_exec_fails_closed(self, monkeypatch, tmp_path, popen_spy, scratch):
        monkeypatch.setattr(ms, "SANDBOX_EXEC_PATH", tmp_path / "absent-sandbox-exec")
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, scratch.root)
        assert popen_spy.calls == []

    def test_public_surface(self):
        assert issubclass(ms.SandboxError, Exception)
        assert str(ms.SANDBOX_EXEC_PATH).endswith("sandbox-exec")
        for name in ("run_python", "build_child_env", "build_profile", "probe_source"):
            assert callable(getattr(ms, name))


# ---------------------------------------------------------------------------
# work_root / script path rules
# ---------------------------------------------------------------------------


class TestWorkRootRules:
    def test_rejects_temp_root_itself(self, popen_spy, scratch):
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, Path(tempfile.gettempdir()))
        assert popen_spy.calls == []

    def test_rejects_root_outside_temp(self, popen_spy, scratch):
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, Path("/"))
        assert popen_spy.calls == []

    def test_rejects_missing_work_root(self, popen_spy, scratch):
        missing = Path(tempfile.gettempdir()) / "h012b-no-such-dir"
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, missing)
        assert popen_spy.calls == []

    def test_rejects_file_work_root(self, popen_spy, scratch):
        afile = scratch.root / "afile"
        afile.write_text("x", encoding="utf-8")
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, afile)
        assert popen_spy.calls == []

    def test_rejects_symlink_work_root_escaping(self, popen_spy, scratch):
        link = Path(tempfile.mkdtemp(prefix="h012b-link-"))
        link.rmdir()
        link.symlink_to("/", target_is_directory=True)
        try:
            with pytest.raises(ms.SandboxError):
                ms.run_python(scratch.script, link)
            assert popen_spy.calls == []
        finally:
            link.unlink()


class TestScriptRules:
    def test_script_outside_work_root(self, popen_spy, scratch):
        other = _make_scratch(prefix="sbx-other-")
        try:
            with pytest.raises(ms.SandboxError):
                ms.run_python(other.script, scratch.root)
            assert popen_spy.calls == []
        finally:
            shutil.rmtree(other.root, ignore_errors=True)

    def test_script_inside_writable_run(self, popen_spy, scratch):
        inner = scratch.run / "job-inside-run.py"
        inner.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(ms.SandboxError):
            ms.run_python(inner, scratch.root)
        assert popen_spy.calls == []

    def test_script_missing_or_directory(self, popen_spy, scratch):
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.root / "src" / "absent.py", scratch.root)
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.root / "src", scratch.root)
        assert popen_spy.calls == []

    def test_script_symlink_escaping_root(self, popen_spy, scratch):
        other = _make_scratch(prefix="sbx-esc-")
        link = scratch.root / "src" / "linked.py"
        link.symlink_to(other.script)
        try:
            with pytest.raises(ms.SandboxError):
                ms.run_python(link, scratch.root)
            assert popen_spy.calls == []
        finally:
            shutil.rmtree(other.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Writable destinations: no symlink escapes, no nonregular preexisting files
# ---------------------------------------------------------------------------


class TestWritableDestinations:
    def test_run_symlink_escaping_rejected(self, popen_spy, scratch):
        outside = _outside_dir()
        try:
            (scratch.root / "run").rmdir()
            (scratch.root / "run").symlink_to(outside, target_is_directory=True)
            with pytest.raises(ms.SandboxError):
                ms.run_python(scratch.script, scratch.root)
            assert popen_spy.calls == []
            assert list(outside.iterdir()) == []  # nothing leaked outside
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_run_symlink_inside_root_still_rejected(self, popen_spy, scratch):
        inner = scratch.root / "real-run"
        inner.mkdir()
        (scratch.root / "run").rmdir()
        (scratch.root / "run").symlink_to(inner, target_is_directory=True)
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, scratch.root)
        assert popen_spy.calls == []
        assert list(inner.iterdir()) == []

    @pytest.mark.parametrize("name", ["logs", "tmp"])
    def test_run_subdir_symlinks_rejected(self, name, popen_spy, scratch):
        outside = _outside_dir()
        try:
            (scratch.run / name).symlink_to(outside, target_is_directory=True)
            with pytest.raises(ms.SandboxError):
                ms.run_python(scratch.script, scratch.root)
            assert popen_spy.calls == []
            assert list(outside.iterdir()) == []
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @staticmethod
    def _profile_path(call):
        cmd = call.cmd
        return Path(cmd[cmd.index("-f") + 1])

    def test_profile_symlink_destination_rejected(self, popen_spy, scratch):
        ms.run_python(scratch.script, scratch.root)
        profile = self._profile_path(popen_spy.calls[0])
        outside = _outside_dir(prefix="sbx-pf-")
        target = outside / "policy.sb"
        try:
            profile.unlink()
            profile.symlink_to(target)
            with pytest.raises(ms.SandboxError):
                ms.run_python(scratch.script, scratch.root)
            assert len(popen_spy.calls) == 1
            assert not target.exists()
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_profile_nonregular_destination_rejected(self, popen_spy, scratch):
        ms.run_python(scratch.script, scratch.root)
        profile = self._profile_path(popen_spy.calls[0])
        profile.unlink()
        profile.mkdir()
        with pytest.raises(ms.SandboxError):
            ms.run_python(scratch.script, scratch.root)
        assert len(popen_spy.calls) == 1

    @pytest.mark.parametrize("which", ["stdout_path", "stderr_path"])
    @pytest.mark.parametrize("kind", ["symlink", "dir"])
    def test_existing_log_destination_rejected(self, which, kind, popen_spy, scratch):
        first = ms.run_python(scratch.script, scratch.root)
        log_path = Path(first[which])
        log_path.unlink()
        outside = None
        target = None
        if kind == "symlink":
            outside = _outside_dir(prefix="sbx-cap-")
            target = outside / "captured.txt"
            target.write_text("untouched\n", encoding="utf-8")
            log_path.symlink_to(target)
        else:
            log_path.mkdir()
        try:
            with pytest.raises(ms.SandboxError):
                ms.run_python(scratch.script, scratch.root)
            assert len(popen_spy.calls) == 1
            if target is not None:
                assert target.read_text(encoding="utf-8") == "untouched\n"
        finally:
            if outside is not None:
                shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestArgumentValidation:
    @pytest.mark.parametrize(
        "bad_timeout",
        [
            True,
            False,
            0,
            -1,
            -0.5,
            float("inf"),
            float("-inf"),
            float("nan"),
            "30",
            None,
        ],
    )
    def test_timeout_must_be_finite_positive_number(self, bad_timeout, popen_spy, scratch):
        with pytest.raises((TypeError, ValueError)):
            ms.run_python(scratch.script, scratch.root, timeout=bad_timeout)
        assert popen_spy.calls == []

    @pytest.mark.parametrize("ok_timeout", [1, 0.5, 30, 90.0])
    def test_timeout_accepts_positive_numbers(self, ok_timeout, popen_spy, scratch):
        ms.run_python(scratch.script, scratch.root, timeout=ok_timeout)
        assert len(popen_spy.calls) == 1
        assert popen_spy.proc.wait_calls
        assert popen_spy.proc.wait_calls[0] == ok_timeout

    @pytest.mark.parametrize("bad_cap", [True, False, 0, -8, 1.5, "1024", None])
    def test_max_output_bytes_strict_positive_int(self, bad_cap, popen_spy, scratch):
        with pytest.raises((TypeError, ValueError)):
            ms.run_python(scratch.script, scratch.root, max_output_bytes=bad_cap)
        assert popen_spy.calls == []

    @pytest.mark.parametrize(
        "bad_args",
        [
            "--flag",  # str is a sequence but must be rejected
            b"--flag",  # bytes likewise
            ["ok", 7],  # non-str element
            [b"bytes"],
            ["has\0nul"],  # embedded NUL
            42,  # not a sequence at all
            None,
        ],
    )
    def test_args_must_be_sequence_of_str(self, bad_args, popen_spy, scratch):
        with pytest.raises((TypeError, ValueError)):
            ms.run_python(scratch.script, scratch.root, args=bad_args)
        assert popen_spy.calls == []

    def test_args_passed_verbatim_after_script(self, popen_spy, scratch):
        argv = ["--alpha", "a b", ""]
        ms.run_python(scratch.script, scratch.root, args=argv)
        cmd = popen_spy.calls[0].cmd
        script_idx = cmd.index(str(scratch.script))
        assert cmd[script_idx + 1 :] == argv


# ---------------------------------------------------------------------------
# argv / env shape
# ---------------------------------------------------------------------------


class TestArgvAndEnv:
    def test_invocation_shape(self, popen_spy, scratch):
        ms.run_python(scratch.script, scratch.root)
        call = popen_spy.calls[0]
        cmd = call.cmd
        assert all(isinstance(part, str) for part in cmd)
        assert Path(cmd[0]).name == "sandbox-exec"
        assert "-f" in cmd
        profile_arg = Path(cmd[cmd.index("-f") + 1]).resolve()
        assert profile_arg == scratch.run or scratch.run in profile_arg.parents
        interp = str(Path(sys.executable).resolve())
        assert interp in cmd
        i_interp = cmd.index(interp)
        i_script = cmd.index(str(scratch.script))
        for flag in ("-I", "-S", "-B"):  # isolated, no site, no bytecode
            assert flag in cmd
            assert i_interp < cmd.index(flag) < i_script
        assert not {"sh", "bash", "-c"} & set(cmd)  # no shell anywhere
        kw = call.kwargs
        assert kw.get("stdin") == subprocess.DEVNULL
        assert not kw.get("shell")
        assert kw.get("start_new_session") is True
        # a real preexec_fn was supplied for the child but must never run
        # in this pytest process; popen_spy records any invocation
        assert callable(call.preexec_fn)
        assert not popen_spy.preexec_invoked
        # stdout/stderr were real writable file handles (size-capped logs),
        # never echoed through the parent's own streams; run_python closes
        # them before returning, so writability was sampled inside Popen
        assert call.stdout_writable is True
        assert call.stderr_writable is True
        cwd = Path(kw["cwd"]).resolve()
        assert cwd == scratch.run or scratch.run in cwd.parents

    def test_child_env_is_fixed_and_minimal(self, popen_spy, scratch):
        ms.run_python(scratch.script, scratch.root)
        env = popen_spy.calls[0].kwargs["env"]
        allowed = {
            "PATH",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TMPDIR",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONNOUSERSITE",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            "PYTHONHASHSEED",
            "__CF_USER_TEXT_ENCODING",
        }
        assert set(env) <= allowed
        assert env["PATH"] == "/usr/bin:/bin"
        for leaked in ("HOME", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            assert leaked not in env
        for key in env:
            assert "TOKEN" not in key
            assert "SECRET" not in key
            assert "PASSWORD" not in key
        assert "TMPDIR" in env
        tmpdir = Path(env["TMPDIR"]).resolve()
        assert tmpdir == (scratch.run / "tmp").resolve() or scratch.run in tmpdir.parents

    def test_build_child_env_public(self):
        env = ms.build_child_env(Path("/some/tmp"))
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["TMPDIR"] == "/some/tmp"
        assert "HOME" not in env
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"


# ---------------------------------------------------------------------------
# Process-group lifecycle
# ---------------------------------------------------------------------------


class TestProcessGroupCleanup:
    @staticmethod
    def _wire_killpg(monkeypatch, proc, mark_dead=False):
        kills = []
        monkeypatch.setattr(ms.os, "getpgid", lambda pid: pid)

        def fake_killpg(pgid, sig):
            kills.append((pgid, sig))
            if mark_dead:
                proc.returncode = -signal.SIGKILL

        monkeypatch.setattr(ms.os, "killpg", fake_killpg)
        return kills

    def test_timeout_kills_own_group_then_waits(self, monkeypatch, popen_spy, scratch):
        proc = popen_spy.proc
        proc.wait_errors = [subprocess.TimeoutExpired("cmd", 1)]
        kills = self._wire_killpg(monkeypatch, proc, mark_dead=True)
        result = ms.run_python(scratch.script, scratch.root, timeout=1)
        assert result["timed_out"] is True
        assert (proc.pid, signal.SIGKILL) in kills
        # start_new_session: the child is its own group leader -- only that
        # group may be signalled, never any other session
        assert all(pgid == proc.pid for pgid, _ in kills)
        assert len(proc.wait_calls) >= 2  # initial wait + post-kill reap
        assert result["exit_code"] == proc.returncode

    def test_completed_run_sweeps_own_leftover_group(self, monkeypatch, popen_spy, scratch):
        proc = popen_spy.proc
        kills = self._wire_killpg(monkeypatch, proc)
        result = ms.run_python(scratch.script, scratch.root)
        assert result["timed_out"] is False
        assert result["exit_code"] == 0
        assert kills, "parent must sweep its own leftover process group"
        assert all(pgid == proc.pid for pgid, _ in kills)
        assert any(sig == signal.SIGKILL for _, sig in kills)


# ---------------------------------------------------------------------------
# Result contract / output accounting
# ---------------------------------------------------------------------------


class TestResultContract:
    def test_fixed_fields_and_log_locations(self, popen_spy, scratch):
        result = ms.run_python(scratch.script, scratch.root)
        for key in (
            "backend",
            "exit_code",
            "timed_out",
            "stdout_path",
            "stderr_path",
            "stdout_bytes",
            "stderr_bytes",
            "stdout_truncated",
            "stderr_truncated",
        ):
            assert key in result
        assert "sandbox" in result["backend"]
        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        logs = (scratch.run / "logs").resolve()
        assert logs.is_dir()
        assert (scratch.run / "tmp").is_dir()
        for path_key, bytes_key in (
            ("stdout_path", "stdout_bytes"),
            ("stderr_path", "stderr_bytes"),
        ):
            p = Path(result[path_key]).resolve()
            assert p.parent == logs or logs in p.parents
            assert p.is_file()
            assert isinstance(result[bytes_key], int)
        assert not result["stdout_truncated"]
        assert not result["stderr_truncated"]

    def test_output_size_flags(self, popen_spy, scratch):
        cap = 1024

        def write_outputs(kw):
            kw["stdout"].write(b"s" * (cap + 5))
            kw["stderr"].write(b"e" * 8)

        popen_spy.writer = write_outputs
        result = ms.run_python(scratch.script, scratch.root, max_output_bytes=cap)
        assert result["stdout_bytes"] == cap + 5
        assert result["stderr_bytes"] == 8
        assert result["stdout_truncated"]
        assert not result["stderr_truncated"]

    def test_sigxfsz_exit_marks_output_truncated(self, popen_spy, scratch):
        popen_spy.proc.returncode = -signal.SIGXFSZ
        result = ms.run_python(scratch.script, scratch.root)
        assert result["exit_code"] == -signal.SIGXFSZ
        assert result["stdout_truncated"]

    def test_launch_failure_wrapped_without_leaking(self, monkeypatch, popen_spy, scratch):
        def boom(cmd, *args, **kwargs):
            raise OSError("SECRET-LAUNCH-DETAIL")

        monkeypatch.setattr(ms.subprocess, "Popen", boom)
        with pytest.raises(ms.SandboxError) as excinfo:
            ms.run_python(scratch.script, scratch.root)
        assert "SECRET-LAUNCH-DETAIL" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Seatbelt profile invariants (text-level; does NOT prove native isolation)
# ---------------------------------------------------------------------------


class TestProfileInvariants:
    @staticmethod
    def _profile(scratch):
        return ms.build_profile(scratch.root, scratch.run)

    def test_deny_default_and_no_network_allowances(self, scratch):
        text = self._profile(scratch)
        assert re.search(r"\(deny\s+default\)", text)
        assert re.search(r"\(deny\s+network", text)
        for line in _policy_lines(text):
            if not _allows(line):
                continue
            assert "network" not in line
            # no connect/bind of any kind, incl. unix sockets -- only the
            # socketpair creation primitive (system-socket) may remain
            assert "socket" not in line.replace("system-socket", "")

    def test_process_introspection_scoped_to_self(self, scratch):
        text = self._profile(scratch)
        offending = [
            line
            for line in _policy_lines(text)
            if _allows(line)
            and any(tok in line for tok in ("process-info", "pidinfo", "codesignature", "signal"))
            and "target self" not in line
        ]
        assert offending == []

    def test_no_arbitrary_mach_or_procargs(self, scratch):
        text = self._profile(scratch)
        for line in _policy_lines(text):
            assert not (_allows(line) and "mach-lookup" in line)
            assert "procargs" not in line

    def test_read_roots_are_whitelisted(self, scratch):
        text = self._profile(scratch)
        allowed = [
            scratch.root,
            Path(sys.base_prefix).resolve(),
            Path(sys.base_exec_prefix).resolve(),
            Path("/System"),
            Path("/usr/lib"),
            Path("/usr/libexec"),
            Path("/usr/share"),
            Path("/private/var/db"),
            Path("/private/etc"),
            Path("/dev"),
        ]
        banned = [
            "/usr",
            "/usr/local",
            "/opt",
            "/opt/homebrew",
            "/opt/local",
            "/Users",
            "/home",
            "/var",
            "/private/var",
            "/Library",
            "/Network",
            "/etc",
            "/private",
        ]
        for line in _policy_lines(text):
            content_grant = any(op in line for op in ("file-read*", "file-read-data", "file-write"))
            if not content_grant:
                continue  # metadata-only rules (stat/readdir) may query "/"
            for sp in _subpaths(line):
                p = Path(sp)
                assert p != Path("/"), line
                ok = any(p == a or a in p.parents for a in allowed)
                assert ok, "read root outside whitelist: %s" % sp
                for b in banned:
                    assert not (b == str(p) or b.startswith(str(p) + "/")), line

    def test_root_dir_literal_not_widened_to_subpath(self, scratch):
        # Native evidence: under the real default-deny profile dyld first
        # aborted with -6; granting exactly
        #   (allow file-read-data (literal "/"))
        # let it open the root DIRECTORY itself and the real probes then
        # passed.  Keep that narrow literal grant -- it must never be
        # "fixed" by widening to (subpath "/"), which would expose file
        # contents across the entire filesystem.
        text = self._profile(scratch)
        saw_root_literal = False
        for line in _policy_lines(text):
            if not _allows(line):
                continue
            if "/" in _literals(line):
                assert "file-write" not in line, line
                if "file-read-data" in line or "file-read*" in line:
                    saw_root_literal = True
            if any(op in line for op in ("file-read*", "file-read-data", "file-write")):
                for sp in _subpaths(line):
                    assert sp != "/", "filesystem-wide grant: %s" % line
        assert saw_root_literal, (
            'missing (allow file-read-data (literal "/")): dyld aborts '
            "-6 without the root-directory open grant"
        )

    def test_no_repo_venv_or_packager_roots(self, scratch):
        text = self._profile(scratch)
        interp = Path(sys.executable).resolve()
        for extra in {Path(sys.prefix).resolve(), Path(sys.exec_prefix).resolve()}:
            if extra in (Path(sys.base_prefix).resolve(), Path(sys.base_exec_prefix).resolve()):
                continue
            if interp == extra or extra in interp.parents:
                continue  # trusted interpreter itself lives there (literal)
            assert str(extra) not in text
        banned_subpaths = {
            "/usr/local",
            "/opt/homebrew",
            "/opt/local",
            "/Users",
            "/home",
            "/var",
            "/private/var",
        }
        for line in _policy_lines(text):
            for sp in _subpaths(line):
                assert sp not in banned_subpaths

    def test_only_run_is_writable(self, scratch):
        text = self._profile(scratch)
        write_lines = [line for line in _policy_lines(text) if "file-write" in line]
        assert write_lines, "profile must grant one writable area"
        for line in write_lines:
            for sp in _subpaths(line):
                p = Path(sp)
                assert p == scratch.run or scratch.run in p.parents, line
            for lit in _literals(line):
                lp = Path(lit)
                assert lit == "/dev/null" or lp == scratch.run or scratch.run in lp.parents, line

    def test_references_interpreter_root_and_run(self, scratch):
        text = self._profile(scratch)
        interp = str(Path(sys.executable).resolve())
        assert interp in text
        assert str(scratch.root) in text
        write_lines = [line for line in _policy_lines(text) if "file-write" in line]
        assert any(str(scratch.run) in line for line in write_lines)
        exec_lines = [line for line in _policy_lines(text) if "process-exec" in line]
        assert exec_lines and all(interp in line for line in exec_lines)

    def test_path_strings_are_escaped(self):
        root = Path(tempfile.mkdtemp(prefix='sbx-q"uote-'))
        try:
            (root / "run").mkdir()
            text = ms.build_profile(root.resolve(), (root / "run").resolve())
            name = root.resolve().name  # contains a literal '"'
            assert name not in text  # raw quote must never appear
            assert name.replace('"', '\\"') in text
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# probe_source
# ---------------------------------------------------------------------------


class TestProbeSource:
    def test_probe_source_is_valid_python_with_contract(self):
        src = ms.probe_source()
        assert isinstance(src, str) and src.strip()
        compile(src, "<probe>", "exec")  # syntactically valid; not executed
        for token in ("--sentinel", "asyncio", "json"):
            assert token in src
