import importlib
import os
import subprocess
from pathlib import Path

import pytest
from test_release_artifacts import artifacts


def module():
    assert Path("tools/release/verify_installations.py").exists(), "Install verifier missing"
    return importlib.import_module("tools.release.verify_installations")


@pytest.fixture
def isolated_commands(tmp_path, monkeypatch):
    # Assertions must never include the host's credential-bearing environment.
    monkeypatch.setattr(os, "environ", {"PATH": os.environ.get("PATH", "")})
    dist, project = artifacts(tmp_path)
    smoke = tmp_path / "tools/release/smoke_installed.py"
    smoke.parent.mkdir(parents=True)
    smoke.write_text("pass\n")
    calls = []

    def run(args, **kwargs):
        calls.append((list(map(str, args)), kwargs))
        if "-m" in args and "venv" in args:
            target = Path(args[-1])
            folder = target / ("Scripts" if os.name == "nt" else "bin")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ("python.exe" if os.name == "nt" else "python")).touch()
        output = (
            "12 passed in 0.1s\n"
            if "pytest" in args
            else "Installed SDK sync/async mock smoke passed\n"
        )
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("unexpected real subprocess")
    )
    return dist, project, calls


def test_install_workflow_is_isolated_offline_and_binds_artifact_hashes(
    isolated_commands, monkeypatch
):
    dist, project, calls = isolated_commands
    monkeypatch.setenv("ANTHROPIC_API_KEY", "PRIVATE")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE")
    monkeypatch.setenv("PYTHONPATH", "PRIVATE")
    monkeypatch.setenv("PYTHONHOME", "PRIVATE_HOME")
    monkeypatch.setenv("PIP_TARGET", "PRIVATE_TARGET")
    result = module().verify_installations(dist, project, installer="uv", offline=True)
    assert (
        result["validation"] == "clean_install_and_offline_smoke"
        and result["real_api_used"] is False
    )
    assert {r["kind"] for r in result["results"]} == {"wheel", "sdist"}
    assert all(len(r["artifact_sha256"]) == 64 for r in result["results"])
    assert any("pytest" in args for args, _ in calls)
    installs = [args for args, _ in calls if "install" in args]
    assert len(installs) == 2 and all("--offline" in args for args in installs)
    for args, kw in calls:
        assert not kw.get("shell", False) and kw.get("timeout") == 300
        assert not any(
            k in kw["env"]
            for k in (
                "ANTHROPIC_API_KEY",
                "EZVIZ_APP_SECRET",
                "PYTHONPATH",
                "PYTHONHOME",
                "PIP_TARGET",
            )
        )
    assert os.environ["ANTHROPIC_API_KEY"] == "PRIVATE"


@pytest.mark.parametrize("installer,offline", [("pip", True), ("unknown", False)])
def test_bad_installer_mode_never_runs_commands(isolated_commands, installer, offline):
    dist, project, calls = isolated_commands
    with pytest.raises(ValueError):
        module().verify_installations(dist, project, installer=installer, offline=offline)
    assert not calls


def test_command_failure_stops_without_echoing_subprocess_logs(isolated_commands, monkeypatch):
    dist, project, calls = isolated_commands

    def fail(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.CalledProcessError(
            1, args, output="PRIVATE_OUTPUT", stderr="PRIVATE_SECRET"
        )

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises((ValueError, RuntimeError)) as caught:
        module().verify_installations(dist, project)
    assert "PRIVATE" not in str(caught.value) and len(calls) == 1


@pytest.mark.parametrize("alias", ["same", "symlink"])
def test_cli_rejects_output_alias_before_installing(isolated_commands, alias, monkeypatch):
    dist, project, calls = isolated_commands
    before = project.read_bytes()
    output = project
    if alias == "symlink":
        output = project.with_name("alias.json")
        output.symlink_to(project)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_installations",
            "--dist",
            str(dist),
            "--pyproject",
            str(project),
            "--output",
            str(output),
        ],
    )
    try:
        code = module().main()
    except SystemExit as exc:
        code = exc.code
    assert code == 1 and not calls and project.read_bytes() == before


def test_invalid_archive_is_rejected_before_installing(isolated_commands):
    dist, project, calls = isolated_commands
    next(dist.glob("*.whl")).write_bytes(b"bad archive")
    with pytest.raises(ValueError):
        module().verify_installations(dist, project)
    assert not calls


def test_source_tests_can_be_explicitly_skipped_without_claiming_them(isolated_commands):
    dist, project, calls = isolated_commands
    result = module().verify_installations(dist, project, source_tests=False)
    assert not any("pytest" in args for args, _ in calls)
    assert not any("source_archive_tests" in r for r in result["results"])


def test_changed_artifact_is_rejected_before_commands(isolated_commands, monkeypatch):
    dist, project, calls = isolated_commands
    mod = module()
    original = mod.check_artifacts.check_artifacts

    def changed(*args, **kwargs):
        report = original(*args, **kwargs)
        next(dist.glob("*.whl")).write_bytes(b"changed after check")
        return report

    monkeypatch.setattr(mod.check_artifacts, "check_artifacts", changed)
    with pytest.raises(ValueError):
        mod.verify_installations(dist, project)
    assert not calls


def test_smoke_output_must_confirm_execution(isolated_commands, monkeypatch):
    dist, project, calls = isolated_commands
    previous = subprocess.run

    def empty(args, **kwargs):
        result = previous(args, **kwargs)
        if any(str(a).endswith("smoke_installed.py") for a in args):
            result.stdout = ""
        return result

    monkeypatch.setattr(subprocess, "run", empty)
    with pytest.raises(ValueError):
        module().verify_installations(dist, project)


def test_test_summary_cannot_echo_unexpected_subprocess_content(isolated_commands, monkeypatch):
    dist, project, calls = isolated_commands
    previous = subprocess.run

    def unsafe(args, **kwargs):
        result = previous(args, **kwargs)
        if "pytest" in args:
            result.stdout = "PRIVATE_VALUE 12 passed in 0.1s\n"
        return result

    monkeypatch.setattr(subprocess, "run", unsafe)
    with pytest.raises(ValueError) as caught:
        module().verify_installations(dist, project)
    assert "PRIVATE" not in str(caught.value)


STAGES = (
    ("all", "artifacts"),
    ("all", "snapshot"),
    ("wheel", "venv"),
    ("wheel", "install"),
    ("wheel", "smoke"),
    ("sdist", "venv"),
    ("sdist", "install"),
    ("sdist", "smoke"),
    ("sdist", "source_tests"),
    ("all", "report"),
)


def _stage(pair):
    return f"verify-install artifact={pair[0]} stage={pair[1]}"


@pytest.mark.parametrize("show", [False, True])
@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("to_file", [False, True])
def test_stage_cli_success(isolated_commands, tmp_path, capsys, show, skip, to_file):
    import json

    dist, project, calls = isolated_commands
    verify = module()
    out_file = tmp_path / "SYNTH_PRIVATE_OUTPUT_DIR" / "report.json"
    argv = ["--dist", str(dist), "--pyproject", str(project)]
    if show:
        argv += ["--show-stages"]
    if skip:
        argv += ["--skip-source-tests"]
    if to_file:
        argv += ["--output", str(out_file)]
    assert verify.main(argv) == 0
    captured = capsys.readouterr()
    if to_file:
        assert captured.out == ""
        report = json.loads(out_file.read_text())
    else:
        report = json.loads(captured.out)
    assert report["source_tests"] is (not skip)
    results = report["results"]
    assert [result["kind"] for result in results] == ["wheel", "sdist"]
    assert ("source_archive_tests" in results[1]) is (not skip)
    assert any("pytest" in args for args, _ in calls) is (not skip)
    if show:
        expected_err = (
            "\n".join(
                _stage(pair) for pair in STAGES if not (skip and pair == ("sdist", "source_tests"))
            )
            + "\n"
        )
    else:
        expected_err = ""
    assert captured.err == expected_err
    assert "status" not in report
    for stream in (captured.out, captured.err):
        assert str(tmp_path) not in stream
        assert "SYNTH_PRIVATE_OUTPUT_DIR" not in stream


@pytest.mark.parametrize("show", [False, True])
@pytest.mark.parametrize(
    ("pair", "failure_kind"),
    [
        (("wheel", "venv"), "exit"),
        (("wheel", "install"), "exit"),
        (("wheel", "smoke"), "exit"),
        (("sdist", "venv"), "exit"),
        (("sdist", "install"), "exit"),
        (("sdist", "smoke"), "exit"),
        (("sdist", "source_tests"), "exit"),
        (("wheel", "smoke"), "invalid_output"),
        (("sdist", "smoke"), "invalid_output"),
        (("sdist", "source_tests"), "invalid_output"),
    ],
)
def test_stage_child_failure(
    pair, failure_kind, show, isolated_commands, monkeypatch, capsys, tmp_path
):
    dist, project, calls = isolated_commands
    idx = STAGES.index(pair)
    count = idx - 1
    previous = subprocess.run

    def run(args, **kwargs):
        completed = previous(args, **kwargs)
        if len(calls) == count:
            completed.stderr = "SYNTH_ERR_PRIVATE"
            completed.stdout = "SYNTH_OUT_PRIVATE_INVALID"
            if failure_kind == "exit":
                completed.returncode = 42
        return completed

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("EZVIZ_APP_SECRET", "SYNTH_ENV_PRIVATE")
    output = tmp_path / "SYNTH_PRIVATE_DIR" / "report.json"
    argv = ["--dist", str(dist), "--pyproject", str(project), "--output", str(output)]
    if show:
        argv.append("--show-stages")
    assert module().main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = [_stage(stage) for stage in STAGES[: idx + 1]] if show else []
    lines.append("installation verification failed")
    assert captured.err == "\n".join(lines) + "\n"
    assert len(calls) == count
    assert not output.exists()
    for leaked in (
        "SYNTH_ERR_PRIVATE",
        "SYNTH_OUT_PRIVATE_INVALID",
        "SYNTH_ENV_PRIVATE",
        str(output),
        str(tmp_path),
        "SYNTH_PRIVATE_DIR",
    ):
        assert leaked not in captured.out
        assert leaked not in captured.err


@pytest.mark.parametrize(
    "pair",
    [("all", "artifacts"), ("all", "snapshot"), ("all", "report")],
)
def test_stage_nonchild_failure(isolated_commands, monkeypatch, tmp_path, capsys, pair):
    dist, project, calls = isolated_commands
    mod = module()
    if pair == ("all", "report"):

        def fail_report(*args, **kwargs):
            raise OSError("SYNTH_PRIVATE_ERR")

        monkeypatch.setattr(mod, "_write_report", fail_report)
    else:
        original = mod.check_artifacts.check_artifacts
        fail_at = 1 if pair == ("all", "artifacts") else 2
        count = [0]

        def counting_check(*args, **kwargs):
            count[0] += 1
            if count[0] == fail_at:
                raise ValueError("SYNTH_PRIVATE_ERR")
            return original(*args, **kwargs)

        monkeypatch.setattr(mod.check_artifacts, "check_artifacts", counting_check)
    output = tmp_path / "SYNTH_PRIVATE_PATH" / "report.json"
    result = mod.main(
        ["--dist", str(dist), "--pyproject", str(project), "--output", str(output), "--show-stages"]
    )
    captured = capsys.readouterr()
    expected_err = (
        "\n".join(
            [_stage(stage) for stage in STAGES[: STAGES.index(pair) + 1]]
            + ["installation verification failed"]
        )
        + "\n"
    )
    assert result == 1
    assert captured.out == ""
    assert captured.err == expected_err
    assert "SYNTH_PRIVATE_ERR" not in captured.err
    assert "SYNTH_PRIVATE_PATH" not in captured.err
    assert not output.exists()
    assert len(calls) == (7 if pair == ("all", "report") else 0)


def test_stage_interrupt(isolated_commands, monkeypatch, capsys):
    dist, project, calls = isolated_commands
    mod = module()
    previous_run = subprocess.run

    def interrupting_run(*args, **kwargs):
        previous_run(*args, **kwargs)
        raise KeyboardInterrupt("SYNTH_PRIVATE_INTERRUPT")

    monkeypatch.setattr(subprocess, "run", interrupting_run)
    with pytest.raises(KeyboardInterrupt):
        mod.main(["--dist", str(dist), "--pyproject", str(project), "--show-stages"])
    captured = capsys.readouterr()
    assert len(calls) == 1
    assert captured.out == ""
    assert captured.err == "\n".join(_stage(stage) for stage in STAGES[:3]) + "\n"
    assert "installation verification failed" not in captured.err


def test_library_default_report_and_flush(isolated_commands, monkeypatch, capsys):
    import io

    dist, project, calls = isolated_commands
    mod = module()

    baseline = mod.verify_installations(dist, project)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    calls.clear()

    flushed = []

    class _RecordingStderr(io.StringIO):
        def flush(self):
            text = self.getvalue()
            if text.endswith("\n"):
                flushed.append(text.splitlines()[-1])
            super().flush()

    check_count = 0
    original_check = mod.check_artifacts.check_artifacts

    def tracking_check(*args, **kwargs):
        nonlocal check_count
        assert flushed[-1] == _stage(STAGES[check_count])
        check_count += 1
        return original_check(*args, **kwargs)

    previous_run = mod.subprocess.run

    def tracking_run(*args, **kwargs):
        assert flushed[-1] == _stage(STAGES[2 + len(calls)])
        return previous_run(*args, **kwargs)

    monkeypatch.setattr(mod.check_artifacts, "check_artifacts", tracking_check)
    monkeypatch.setattr(mod.subprocess, "run", tracking_run)
    monkeypatch.setattr(mod.sys, "stderr", _RecordingStderr())

    explicit = mod.verify_installations(dist, project, show_stages=True)

    assert explicit == baseline
    assert check_count == 2
    assert len(calls) == 7
    assert flushed == [_stage(pair) for pair in STAGES[:-1]]
    assert _stage(STAGES[-1]) not in flushed
