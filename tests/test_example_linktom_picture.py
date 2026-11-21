"""Offline tests for the ``examples.linktom_picture`` demo CLI."""

import asyncio
import json
import os
import runpy
import sys
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified.errors import EzvizError
from ezviz_unified.providers import AsyncStaticTokenProvider, StaticTokenProvider
from ezviz_unified.snapshots import AsyncSnapshots, Snapshots

USAGE = "usage: python -m examples.linktom_picture --mode sync|async\n"
ERROR = "error: Linktom picture lookup example failed\n"


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
    monkeypatch.setattr(sys, "argv", ["examples.linktom_picture", *argv])
    monkeypatch.setattr(os, "environ", _NoConfigEnv())
    try:
        runpy.run_module("examples.linktom_picture", run_name="__main__")
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
    cls = AsyncSnapshots if mode == "async" else Snapshots
    name = "get_by_uuid"
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
    assert set(payload) == {"simulated", "picture_ref_present"} and all(
        v is True for v in payload.values()
    )
    assert calls == [("get_by_uuid", "overseas")]
    check_demo_requests(requests)


def check_demo_requests(requests):
    (req,) = requests
    assert isinstance(req, httpx.Request)
    assert req.method == "POST"
    assert req.url.scheme == "https"
    assert req.url.path == "/api/lapp/device/uuid/picture"
    assert not req.url.query
    assert req.headers.get("content-type") == "application/x-www-form-urlencoded"
    fields = parse_qsl(req.content.decode("utf-8"), keep_blank_values=True)
    assert sorted(name for name, _ in fields) == ["accessToken", "size", "uuid"]
    values = dict(fields)
    assert values["size"] == "640"
    assert values["accessToken"].strip()
    assert values["uuid"].strip()
    for header_name in req.headers.keys():
        lowered = header_name.lower()
        assert "token" not in lowered
        assert "uuid" not in lowered
        assert "size" not in lowered
    assert req.url.host != "open.ys7.com"


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


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_empty_reference(mode, monkeypatch, capsys):
    token_lookups = []
    seen_requests = []
    synthetic_body = {
        "code": "200",
        "msg": "SYNTH_PRIVATE",
        "data": {"picUrl": ""},
    }

    if mode == "sync":
        original_get_token = StaticTokenProvider.get_token

        def tracking_get_token(self, *args, **kwargs):
            token_lookups.append(mode)
            return original_get_token(self, *args, **kwargs)

        def transport_reply(self, request):
            seen_requests.append(request)
            return httpx.Response(200, json=synthetic_body)

        monkeypatch.setattr(StaticTokenProvider, "get_token", tracking_get_token)
        monkeypatch.setattr(httpx.MockTransport, "handle_request", transport_reply)
    else:
        original_get_token = AsyncStaticTokenProvider.get_token

        async def tracking_get_token(self, *args, **kwargs):
            token_lookups.append(mode)
            return await original_get_token(self, *args, **kwargs)

        async def transport_reply(self, request):
            seen_requests.append(request)
            return httpx.Response(200, json=synthetic_body)

        monkeypatch.setattr(AsyncStaticTokenProvider, "get_token", tracking_get_token)
        monkeypatch.setattr(httpx.MockTransport, "handle_async_request", transport_reply)

    assert _run_cli(["--mode", mode], monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    stdout_json = json.loads(captured.out)
    assert stdout_json == {"simulated": True, "picture_ref_present": False}
    assert stdout_json["picture_ref_present"] is False

    assert token_lookups == [mode]
    assert len(seen_requests) == 1
    assert seen_requests[0].url.path == "/api/lapp/device/uuid/picture"
