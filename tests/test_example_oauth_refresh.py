"""Offline tests for the ``examples.oauth_refresh`` demo CLI."""

import asyncio
import json
import os
import runpy
import sys
from types import SimpleNamespace

import pytest

USAGE = "usage: python -m examples.oauth_refresh --mode sync|async\n"
ERROR = "error: OAuth refresh example failed\n"


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
    monkeypatch.setattr(sys, "argv", ["examples.oauth_refresh", *argv])
    monkeypatch.setattr(os, "environ", {})
    try:
        runpy.run_module("examples.oauth_refresh", run_name="__main__")
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


def _oauth_cls(mode):
    from ezviz_unified.oauth import AsyncOAuth, OAuth

    return OAuth if mode == "sync" else AsyncOAuth


def _value_error():
    return ValueError("synthetic-sensitive-example-user")


def _ezviz_error():
    from ezviz_unified.errors import EzvizError

    try:
        return EzvizError("synthetic-sensitive-example-user")
    except TypeError:
        return EzvizError("synthetic-code", "synthetic-sensitive-example-user")


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_success(mode, monkeypatch, capsys):
    assert _run_cli(["--mode", mode], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {"mode": mode, "refreshed": True}
    assert payload["refreshed"] is True


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


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "make_error",
    [_value_error, _ezviz_error],
    ids=["value-error", "ezviz-error"],
)
def test_demo_refresh_token_failure(mode, make_error, monkeypatch, capsys):
    calls = []

    def stub(*args, **kwargs):
        calls.append(True)
        raise make_error()

    monkeypatch.setattr(_oauth_cls(mode), "refresh_token", stub)

    assert _run_cli(["--mode", mode], monkeypatch) == 1
    assert calls == [True]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_unexpected_tokens(mode, monkeypatch, capsys):
    def stub(*args, **kwargs):
        return SimpleNamespace(
            access_token="synthetic-wrong-access",
            refresh_token="synthetic-wrong-refresh",
        )

    async def async_stub(*args, **kwargs):
        return stub()

    cls = _oauth_cls(mode)
    monkeypatch.setattr(cls, "refresh_token", async_stub if mode == "async" else stub)

    assert _run_cli(["--mode", mode], monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_demo_interruption(mode, exc_type, monkeypatch, capsys):
    def stub(*args, **kwargs):
        raise exc_type

    monkeypatch.setattr(_oauth_cls(mode), "refresh_token", stub)

    assert _run_cli(["--mode", mode], monkeypatch) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_unexpected_error_propagates(mode, monkeypatch, capsys):
    def stub(*args, **kwargs):
        raise RuntimeError("synthetic-programming-error")

    monkeypatch.setattr(_oauth_cls(mode), "refresh_token", stub)

    with pytest.raises(RuntimeError, match="synthetic-programming-error"):
        _run_cli(["--mode", mode], monkeypatch)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
