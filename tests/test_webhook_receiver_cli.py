"""CLI contract tests for ``examples.webhook_receiver`` (task C).

Only ``main(argv)`` is exercised: ``getpass`` and ``BoundedWebhookReceiver``
are replaced by fakes, so no real TCP listener, terminal, env credential or
secret file is involved. Wire-level behaviour is covered by the HTTP-layer
tests. ``import examples.webhook_receiver`` failing before B delivers is the
expected red state.
"""

from __future__ import annotations

import getpass
import io
import json
import sys
from types import SimpleNamespace

import pytest

import examples.webhook_receiver as cli

_SENTINEL_SECRET = "cli-sentinel-secret-01"
_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "connections",
        "request_errors",
        "stop_reason",
        "accepted",
        "duplicate",
        "authentication_failed",
        "invalid_message",
        "body_too_large",
        "id_conflict",
        "capacity_rejected",
        "deadline_exceeded",
        "closed",
    }
)
_COUNTER_KEYS = _SUMMARY_KEYS - {"schema_version", "stop_reason"}
_STOP_REASONS = frozenset(
    {
        "deadline",
        "max_connections",
        "interrupted",
        "closed",
    }
)


class _TtyInput(io.StringIO):
    """stdin stand-in that reports ``isatty() is True``.

    ``cli_env`` installs this because pytest's captured stdin is non-TTY,
    which would force every case down the refusal path and never reach the
    faked getpass prompt. Tests that need refusal re-patch stdin with a
    plain ``StringIO`` themselves.
    """

    def isatty(self):
        return True


class _Prompt:
    """Works for both ``import getpass`` and ``from getpass import getpass``."""

    def __init__(self, secret):
        self.secret = secret
        self.prompts = []

    def __call__(self, prompt="", *args, **kwargs):
        return self.getpass(prompt, *args, **kwargs)

    def getpass(self, prompt="", *args, **kwargs):
        self.prompts.append(prompt)
        return self.secret


class _FakeProcessor:
    made = []

    def __init__(self, config, *, webhook_secret):
        self.config = config
        self.webhook_secret = webhook_secret
        self.closed = False
        type(self).made.append(self)

    def close(self):
        self.closed = True


class _FakeReceiver:
    made = []
    run_exc = None
    run_result = {
        "schema_version": 1,
        "connections": 1,
        "request_errors": 0,
        "stop_reason": "max_connections",
        "accepted": 1,
        "duplicate": 0,
        "authentication_failed": 0,
        "invalid_message": 0,
        "body_too_large": 0,
        "id_conflict": 0,
        "capacity_rejected": 0,
        "deadline_exceeded": 0,
        "closed": 0,
    }

    def __init__(self, processor, *, port):
        self.processor = processor
        self.port_kw = port
        self.closed = False
        type(self).made.append(self)

    @property
    def port(self):
        return self.port_kw

    def run(self):
        if type(self).run_exc is not None:
            raise type(self).run_exc
        return dict(type(self).run_result)

    def close(self):
        self.closed = True


def _invoke(argv):
    """Return ``cli.main``'s exit code as a plain ``int``.

    ``main`` must return a real ``int`` — ``None``/str/bool results are
    contract violations, not silent ``0`` or ``1``. A ``SystemExit`` is only
    honoured when its code is itself a plain ``int``.
    """
    try:
        rc = cli.main(list(argv))
    except SystemExit as exc:
        assert type(exc.code) is int, f"SystemExit code must be a plain int, got {exc.code!r}"
        return exc.code
    assert type(rc) is int, f"main() must return a plain int, got {rc!r}"
    return rc


@pytest.fixture
def cli_env(monkeypatch):
    _FakeProcessor.made.clear()
    _FakeReceiver.made.clear()
    _FakeReceiver.run_exc = None
    monkeypatch.setattr(cli, "ReceiverProcessor", _FakeProcessor, raising=False)
    monkeypatch.setattr(cli, "BoundedWebhookReceiver", _FakeReceiver, raising=False)
    prompt = _Prompt(_SENTINEL_SECRET)
    monkeypatch.setattr(getpass, "getpass", prompt.getpass)
    monkeypatch.setattr(cli, "getpass", prompt, raising=False)
    # Default to a TTY stdin so the happy path reaches getpass; without this,
    # pytest's non-TTY capture would make error tests pass vacuously via the
    # early refusal branch.
    monkeypatch.setattr(sys, "stdin", _TtyInput())
    monkeypatch.setattr(
        "builtins.input",
        lambda *a, **k: pytest.fail("input() must not be used"),
    )
    for var in ("EZVIZ_APP_KEY", "EZVIZ_APP_SECRET", "EZVIZ_SECRET", "WEBHOOK_SECRET"):
        monkeypatch.setenv(var, "env-sentinel-" + var)
    return SimpleNamespace(
        prompt=prompt,
        processors=_FakeProcessor.made,
        receivers=_FakeReceiver.made,
    )


def test_help_prints_static_help_without_prompt(cli_env, capsys):
    rc = _invoke(["--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "--profile" in captured.out
    assert cli_env.prompt.prompts == []
    assert "Traceback" not in captured.out + captured.err


def test_non_tty_stdin_is_refused(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("visible-fallback-secret\n"))
    rc = _invoke(["--profile", "domestic", "--port", "0", "--max-seconds", "1"])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert rc == 1
    assert cli_env.prompt.prompts == []  # refused before any prompt
    assert _SENTINEL_SECRET not in blob
    assert "visible-fallback-secret" not in blob
    assert "Traceback" not in blob


def test_parse_errors_do_not_echo_values(cli_env, capsys):
    for bad in (
        ["--port", "SENTINEL-port-9x"],
        ["--profile", "SENTINEL-profile-zz"],
        ["--max-seconds", "SENTINEL-seconds-kq"],
    ):
        rc = _invoke(bad)
        captured = capsys.readouterr()
        assert rc == 1
        assert bad[-1] not in captured.out
        assert bad[-1] not in captured.err
        assert "Traceback" not in captured.err
    assert cli_env.prompt.prompts == []


def test_defaults_and_domestic_config(cli_env, capsys):
    rc = _invoke(["--max-seconds", "1"])
    assert rc == 0
    assert len(cli_env.processors) == 1
    proc = cli_env.processors[0]
    assert proc.webhook_secret == _SENTINEL_SECRET
    assert cli_env.prompt.prompts  # hidden getpass prompt was used
    cfg = proc.config
    assert cfg.profile == "domestic"
    assert cfg.timestamp_tolerance_ms == 300000
    assert cli_env.receivers[0].port_kw == 8765
    assert _SENTINEL_SECRET not in capsys.readouterr().out


def test_explicit_flags_build_config_and_port(cli_env):
    rc = _invoke(
        [
            "--profile",
            "domestic",
            "--port",
            "0",
            "--max-seconds",
            "1",
            "--max-connections",
            "3",
            "--max-body-bytes",
            "4096",
        ]
    )
    assert rc == 0
    cfg = cli_env.processors[-1].config
    assert cfg.max_run_seconds == 1
    assert cfg.max_connections == 3
    assert cfg.max_body_bytes == 4096
    assert cli_env.receivers[-1].port_kw == 0


def test_overseas_profile_uses_none_tolerance(cli_env):
    rc = _invoke(["--profile", "overseas", "--port", "0", "--max-seconds", "1"])
    assert rc == 0
    cfg = cli_env.processors[-1].config
    assert cfg.profile == "overseas"
    assert cfg.timestamp_tolerance_ms is None


def test_stdout_json_has_only_fixed_fields(cli_env, capsys):
    rc = _invoke(["--port", "0", "--max-seconds", "1"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # exact frozen key set, and every value round-trips the fake's run result
    assert set(payload) == _SUMMARY_KEYS
    assert payload == _FakeReceiver.run_result
    assert type(payload["schema_version"]) is int
    assert payload["schema_version"] == 1
    assert type(payload["stop_reason"]) is str
    assert payload["stop_reason"] in _STOP_REASONS
    for key in _COUNTER_KEYS:
        assert type(payload[key]) is int
        assert payload[key] >= 0


def test_config_error_exits_one_without_detail(cli_env, monkeypatch, capsys):
    def _boom(config, *, webhook_secret):
        raise ValueError("cfg-error-SENTINEL-detail")

    monkeypatch.setattr(cli, "ReceiverProcessor", _boom, raising=False)
    rc = _invoke(["--port", "0"])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert rc == 1
    assert "cfg-error-SENTINEL-detail" not in blob
    assert "Traceback" not in blob


def test_unknown_server_error_exits_one(cli_env, capsys):
    _FakeReceiver.run_exc = RuntimeError("server-SENTINEL-detail")
    rc = _invoke(["--port", "0"])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert rc == 1
    assert "server-SENTINEL-detail" not in blob
    assert "Traceback" not in blob


def test_keyboard_interrupt_exits_130(cli_env, capsys):
    _FakeReceiver.run_exc = KeyboardInterrupt()
    rc = _invoke(["--port", "0"])
    captured = capsys.readouterr()
    assert rc == 130
    assert "Traceback" not in captured.out + captured.err


def test_secret_only_from_getpass_never_env_or_dotenv(cli_env, monkeypatch, tmp_path, capsys):
    (tmp_path / ".env").write_text("WEBHOOK_SECRET=dotenv-sentinel\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rc = _invoke(["--port", "0", "--max-seconds", "1"])
    assert rc == 0
    proc = cli_env.processors[-1]
    assert proc.webhook_secret == _SENTINEL_SECRET
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert "dotenv-sentinel" not in blob
    assert "env-sentinel" not in blob
    assert _SENTINEL_SECRET not in blob


def test_no_secret_in_logs(cli_env, caplog):
    with caplog.at_level("DEBUG"):
        rc = _invoke(["--port", "0", "--max-seconds", "1"])
    assert rc == 0
    assert _SENTINEL_SECRET not in caplog.text
    assert "env-sentinel" not in caplog.text
