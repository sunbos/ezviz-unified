"""Offline tests for the ``examples.alarm_sound`` demo CLI."""

import asyncio
import json
import os
import runpy
import sys

import pytest

from ezviz_unified.errors import EzvizError
from ezviz_unified.settings import AsyncSettings, Settings

USAGE = "usage: python -m examples.alarm_sound --mode sync|async\n"
ERROR = "error: alarm sound example failed\n"


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    def spy(*args, **kwargs):
        pytest.fail("forbidden external call")

    for target in (
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
        "socket.create_connection",
        "webbrowser.open",
        "webbrowser.open_new",
        "webbrowser.open_new_tab",
    ):
        monkeypatch.setattr(target, spy)


def _run_cli(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["examples.alarm_sound", *argv])
    monkeypatch.setattr(os, "environ", {})
    try:
        runpy.run_module("examples.alarm_sound", run_name="__main__")
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["sync"],
        ["--mode"],
        ["--mode", "synthetic-invalid-mode"],
        ["--mode", "sync", "extra-arg"],
        ["--mode", "sync", "--synthetic-private-option"],
        ["--mode=sync"],
    ],
)
def test_demo_usage_errors(argv, monkeypatch, capsys):
    assert _run_cli(argv, monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_demo_help(flag, monkeypatch, capsys):
    assert _run_cli([flag], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE
    assert captured.err == ""


def _install_spies(monkeypatch, mode, calls, fail_at=None, exc=None):
    cls = Settings if mode == "sync" else AsyncSettings
    original_set = cls.set_alarm_sound_mode
    original_play = cls.play_audition
    if mode == "sync":

        def set_spy(self, *args, **kwargs):
            calls.append("set_alarm_sound_mode")
            if fail_at == "set_alarm_sound_mode":
                raise exc
            return original_set(self, *args, **kwargs)

        def play_spy(self, *args, **kwargs):
            calls.append("play_audition")
            if fail_at == "play_audition":
                raise exc
            return original_play(self, *args, **kwargs)

    else:

        async def set_spy(self, *args, **kwargs):
            calls.append("set_alarm_sound_mode")
            if fail_at == "set_alarm_sound_mode":
                raise exc
            return await original_set(self, *args, **kwargs)

        async def play_spy(self, *args, **kwargs):
            calls.append("play_audition")
            if fail_at == "play_audition":
                raise exc
            return await original_play(self, *args, **kwargs)

    monkeypatch.setattr(cls, "set_alarm_sound_mode", set_spy)
    monkeypatch.setattr(cls, "play_audition", play_spy)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_example_alarm_sound_success(mode, monkeypatch, capsys):
    calls = []
    _install_spies(monkeypatch, mode, calls)

    exit_code = _run_cli(["--mode", mode], monkeypatch)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == ["set_alarm_sound_mode", "play_audition"]
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "mode": mode,
        "mode_set": True,
        "audition_acknowledged": True,
    }
    assert payload["mode_set"] is True
    assert payload["audition_acknowledged"] is True


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("fail_at", ["set_alarm_sound_mode", "play_audition"])
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        pytest.param(ValueError("synthetic-value"), 1, id="value-error"),
        pytest.param(EzvizError("synthetic-secret"), 1, id="ezviz-error"),
        pytest.param(KeyboardInterrupt(), 130, id="keyboard-interrupt"),
        pytest.param(asyncio.CancelledError(), 130, id="cancelled"),
    ],
)
def test_example_alarm_sound_failure(mode, fail_at, exc, expected_code, monkeypatch, capsys):
    calls = []
    _install_spies(monkeypatch, mode, calls, fail_at=fail_at, exc=exc)

    exit_code = _run_cli(["--mode", mode], monkeypatch)
    captured = capsys.readouterr()

    assert exit_code == expected_code
    assert captured.out == ""
    assert captured.err == ERROR
    expected_calls = ["set_alarm_sound_mode"]
    if fail_at == "play_audition":
        expected_calls.append("play_audition")
    assert calls == expected_calls


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("fail_at", ["set_alarm_sound_mode", "play_audition"])
def test_example_alarm_sound_runtime_error(mode, fail_at, monkeypatch, capsys):
    calls = []
    _install_spies(
        monkeypatch,
        mode,
        calls,
        fail_at=fail_at,
        exc=RuntimeError("synthetic-bug"),
    )

    with pytest.raises(RuntimeError):
        _run_cli(["--mode", mode], monkeypatch)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    expected_calls = ["set_alarm_sound_mode"]
    if fail_at == "play_audition":
        expected_calls.append("play_audition")
    assert calls == expected_calls
