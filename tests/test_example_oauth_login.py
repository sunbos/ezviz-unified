import json
import os
import runpy
import sys

import pytest

USAGE = "usage: python -m examples.oauth_login --mode sync|async\n"


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    def spy(*args, **kwargs):
        pytest.fail("forbidden external call")

    for target in (
        "httpx.Client.send",
        "httpx.AsyncClient.send",
        "webbrowser.open",
        "webbrowser.open_new",
        "webbrowser.open_new_tab",
    ):
        monkeypatch.setattr(target, spy)


def _run_cli(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["examples.oauth_login", *argv])
    monkeypatch.setattr(os, "environ", {})
    try:
        runpy.run_module("examples.oauth_login", run_name="__main__")
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_success(mode, monkeypatch, capsys):
    assert _run_cli(["--mode", mode], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "mode": mode,
        "login_url_built": True,
        "redirect_parsed": True,
    }
    assert payload["login_url_built"] is True
    assert payload["redirect_parsed"] is True


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode"],
        ["--mode", "synthetic-invalid-mode"],
        ["--mode", "sync", "extra-arg"],
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
@pytest.mark.parametrize("method", ["build_login_url", "parse_redirect"])
def test_demo_sdk_method_failure(mode, method, monkeypatch, capsys):
    from ezviz_unified.oauth import AsyncOAuth, OAuth

    calls = []

    def stub(*args, **kwargs):
        calls.append(True)
        raise ValueError("synthetic-private-input")

    monkeypatch.setattr(OAuth if mode == "sync" else AsyncOAuth, method, stub)

    assert _run_cli(["--mode", mode], monkeypatch) == 1
    assert calls == [True]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OAuth login example failed\n"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_parse_redirect_unexpected_error(mode, monkeypatch, capsys):
    from ezviz_unified.oauth import AsyncOAuth, OAuth

    def stub(*args, **kwargs):
        raise RuntimeError("synthetic-programming-error")

    monkeypatch.setattr(OAuth if mode == "sync" else AsyncOAuth, "parse_redirect", stub)

    with pytest.raises(RuntimeError, match="synthetic-programming-error"):
        _run_cli(["--mode", mode], monkeypatch)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
