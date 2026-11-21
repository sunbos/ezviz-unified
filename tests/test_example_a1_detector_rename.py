"""Offline tests for the ``examples.a1_detector_rename`` demo CLI."""

import asyncio
import json
import os
import runpy
import sys
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified.detectors import AsyncDetectors, Detectors
from ezviz_unified.errors import EzvizError

USAGE = "usage: python -m examples.a1_detector_rename --mode sync|async\n"
ERROR = "error: A1 detector rename example failed\n"


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


class _NoConfigEnv(dict):
    def _trap(self, key):
        assert not str(key).startswith("EZVIZ_")

    def get(self, key, default=None):
        self._trap(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._trap(key)
        return super().__getitem__(key)

    def _bulk(self, *args, **kwargs):
        raise AssertionError("unexpected environment enumeration")

    __iter__ = keys = items = values = copy = _bulk

    def __contains__(self, key):
        self._trap(key)
        return super().__contains__(key)


def _run_cli(argv, monkeypatch):
    monkeypatch.setattr(
        "examples._common.load_config", lambda: pytest.fail("unexpected configuration read")
    )
    monkeypatch.setattr(sys, "argv", ["examples.a1_detector_rename", *argv])
    monkeypatch.setattr(os, "environ", _NoConfigEnv())
    try:
        runpy.run_module("examples.a1_detector_rename", run_name="__main__")
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
        ["--mode", "sync", "--mode", "async"],
        ["--mode", "sync", "--token", "SYNTH_PRIVATE_ARG"],
    ],
)
def test_demo_usage_errors(argv, monkeypatch, capsys):
    for attr in ("handle_request", "handle_async_request"):
        monkeypatch.setattr(
            httpx.MockTransport, attr, lambda *a, **kw: pytest.fail("unexpected request")
        )
    assert _run_cli(argv, monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_demo_help(flag, monkeypatch, capsys):
    for attr in ("handle_request", "handle_async_request"):
        monkeypatch.setattr(
            httpx.MockTransport, attr, lambda *a, **kw: pytest.fail("unexpected request")
        )
    assert _run_cli([flag], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE
    assert captured.err == ""


def _spy_sdk(monkeypatch, mode, calls, fail_with=None):
    cls = AsyncDetectors if mode == "async" else Detectors
    name = "rename"
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
def test_demo_success(mode, monkeypatch, capsys):
    calls, requests = [], []
    attr = "handle_request" if mode == "sync" else "handle_async_request"
    original_transport = getattr(httpx.MockTransport, attr)
    if mode == "sync":

        def record(self, request):
            requests.append(request)
            return original_transport(self, request)
    else:

        async def record(self, request):
            requests.append(request)
            return await original_transport(self, request)

    monkeypatch.setattr(httpx.MockTransport, attr, record)
    _spy_sdk(monkeypatch, mode, calls)
    assert _run_cli(["--mode", mode], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n") and "\n" not in captured.out[:-1]
    payload = json.loads(captured.out)
    assert set(payload) == {"simulated", "renamed"} and all(v is True for v in payload.values())
    assert calls == [("rename", "overseas")]
    check_demo_requests(requests)


def check_demo_requests(requests):
    assert len(requests) == 2
    auth, business = requests

    for req in (auth, business):
        assert req.method == "POST"
        assert req.url.scheme == "https"
        assert not req.url.query
        assert (
            req.headers["content-type"].split(";")[0].strip() == "application/x-www-form-urlencoded"
        )

    assert auth.url.path == "/api/lapp/token/get"
    assert business.url.path == "/api/lapp/detector/name/change"
    assert (auth.url.host, auth.url.port) != (business.url.host, business.url.port)
    assert sum(r.url.path == "/api/lapp/detector/name/change" for r in requests) == 1

    auth_pairs = parse_qsl(auth.content.decode(), keep_blank_values=True)
    assert sorted(name for name, _ in auth_pairs) == ["appKey", "appSecret"]
    assert all(value for _, value in auth_pairs)
    for name in ("appKey", "appSecret"):
        assert name not in auth.headers

    business_pairs = parse_qsl(business.content.decode(), keep_blank_values=True)
    expected = ["accessToken", "deviceSerial", "detectorSerial", "newName"]
    assert sorted(name for name, _ in business_pairs) == sorted(expected)
    assert all(value for _, value in business_pairs)
    for name in expected:
        assert name not in business.headers


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
