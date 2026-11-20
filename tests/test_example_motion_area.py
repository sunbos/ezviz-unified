"""Offline tests for the ``examples.motion_area`` demo CLI."""

import asyncio
import json
import os
import runpy
import sys

import pytest

from ezviz_unified.errors import EzvizError
from ezviz_unified.settings import AsyncSettings, Settings

USAGE = "usage: python -m examples.motion_area --mode sync|async\n"
ERROR = "error: motion area example failed\n"


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
    monkeypatch.setattr(sys, "argv", ["examples.motion_area", *argv])
    monkeypatch.setattr(os, "environ", {})
    try:
        runpy.run_module("examples.motion_area", run_name="__main__")
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


def _spy_sdk(monkeypatch, mode, calls, fail_with=None):
    cls = AsyncSettings if mode == "async" else Settings
    for name in ("get_motion_area", "set_motion_area"):
        original = getattr(cls, name)

        def spy(self, *args, _orig=original, _name=name, **kwargs):
            calls.append((_name, self._client.region.scope))
            if fail_with is not None:
                raise fail_with
            return _orig(self, *args, **kwargs)

        async def aspy(self, *args, _orig=original, _name=name, **kwargs):
            calls.append((_name, self._client.region.scope))
            if fail_with is not None:
                raise fail_with
            return await _orig(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, aspy if mode == "async" else spy)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_motion_area_success(mode, monkeypatch, capsys):
    calls = []
    _spy_sdk(monkeypatch, mode, calls)
    assert _run_cli(["--mode", mode], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n") and "\n" not in captured.out[:-1]
    payload = json.loads(captured.out)
    assert payload == {
        "mode": mode,
        "domestic_read": True,
        "domestic_write": True,
        "overseas_write": True,
    }
    for key in ("domestic_read", "domestic_write", "overseas_write"):
        assert type(payload[key]) is bool
    gets = [scope for name, scope in calls if name == "get_motion_area"]
    sets = [scope for name, scope in calls if name == "set_motion_area"]
    assert gets == ["domestic"]
    assert sorted(sets) == ["domestic", "overseas"]


@pytest.mark.parametrize(
    ("mode", "exc", "code"),
    [
        ("sync", EzvizError("synthetic-private-token"), 1),
        ("async", EzvizError("synthetic-private-token"), 1),
        ("sync", ValueError("synthetic-private-token"), 1),
        ("async", ValueError("synthetic-private-token"), 1),
        ("sync", KeyboardInterrupt(), 130),
        ("async", asyncio.CancelledError(), 130),
    ],
)
def test_demo_call_failures(mode, exc, code, monkeypatch, capsys):
    _spy_sdk(monkeypatch, mode, [], fail_with=exc)
    assert _run_cli(["--mode", mode], monkeypatch) == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR
    assert "synthetic-private-token" not in captured.err


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_unexpected_error_propagates(mode, monkeypatch):
    _spy_sdk(monkeypatch, mode, [], fail_with=RuntimeError("synthetic-boom"))
    with pytest.raises(RuntimeError):
        _run_cli(["--mode", mode], monkeypatch)
