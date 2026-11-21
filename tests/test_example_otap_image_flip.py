"""Offline tests for the ``examples.otap_image_flip`` demo CLI."""

import asyncio
import json
import os
import runpy
import sys
from functools import partial
from itertools import permutations

import httpx
import pytest
import test_sdk_otap as otap_fixture
from test_sdk_otap import _build, _invoke, _shutdown

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)
from ezviz_unified.errors import EzvizError
from ezviz_unified.otap import AsyncOtap, Otap
from ezviz_unified.providers import AsyncStaticTokenProvider, StaticTokenProvider

USAGE = "usage: python -m examples.otap_image_flip --mode sync|async --style LEFTRIGHT|UPDOWN|CENTER|AUTO --enabled true|false\n"
ERROR = "error: OTAP image flip example failed\n"


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
    monkeypatch.setattr(sys, "argv", ["examples.otap_image_flip", *argv])
    monkeypatch.setattr(os, "environ", _NoConfigEnv())
    try:
        runpy.run_module("examples.otap_image_flip", run_name="__main__")
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
        ["--mode", "sync", "--style", "CENTER"],
        ["--mode", "sync", "--enabled", "true"],
        ["--style", "CENTER", "--enabled", "true"],
        ["--mode", "sync", "--style", "center", "--enabled", "true"],
        ["--mode", "sync", "--style", "SIDEWAYS", "--enabled", "true"],
        ["--mode", "sync", "--style", "CENTER", "--enabled", "0"],
        ["--mode", "sync", "--style", "CENTER", "--enabled", "True"],
        ["--mode", "sync", "--style", "CENTER", "--enabled", "true", "--style", "CENTER"],
        ["--mode", "sync", "--style", "CENTER", "--enabled", "true", "--enabled", "false"],
        ["--mode", "sync", "--style", "CENTER", "--enabled", "true", "--mode", "async"],
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
    cls = AsyncOtap if mode == "async" else Otap
    name = "set_property"
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
@pytest.mark.parametrize("style", ["LEFTRIGHT", "UPDOWN", "CENTER", "AUTO"])
@pytest.mark.parametrize("enabled", [False, True])
def test_demo_success(mode, style, enabled, monkeypatch, capsys):
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
    flags = [("--mode", mode), ("--style", style), ("--enabled", "true" if enabled else "false")]
    if mode == "async":
        flags.reverse()
    assert _run_cli([arg for pair in flags for arg in pair], monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n") and "\n" not in captured.out[:-1]
    payload = json.loads(captured.out)
    assert set(payload) == {"simulated", "acknowledged"} and all(
        v is True for v in payload.values()
    )
    assert calls == [("set_property", "domestic")]
    (request,) = requests
    check_wire(
        request,
        style,
        enabled,
        serial=request.headers["deviceSerial"],
        index=request.headers["localIndex"],
        token=request.headers["accessToken"],
    )
    assert (
        request.headers["deviceSerial"].strip()
        and request.headers["localIndex"].strip()
        and request.headers["accessToken"].strip()
    )


def check_wire(request, style, enabled, *, serial, index, token):
    assert request.method == "PUT"
    assert request.url.scheme == "https"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == PROP_PATH
    assert not request.url.query

    headers = request.headers
    business = {
        "accesstoken": token,
        "deviceserial": serial,
        "localindex": index,
        "resourcecategory": "Video",
        "domainidentifier": "VideoAdjustment",
        "propidentifier": "ImageFlip",
    }
    for name, expected in business.items():
        assert headers.get_list(name) == [expected]

    assert headers.get_list("content-type") == ["application/json"]

    allowed = set(business) | {
        "host",
        "user-agent",
        "accept",
        "accept-encoding",
        "connection",
        "content-length",
        "content-type",
    }
    for key in headers.keys():
        assert key.lower() in allowed

    body = json.loads(request.content)
    assert set(body) == {"enabled", "imageFlipStyle"}
    assert body["enabled"] is enabled
    assert body["imageFlipStyle"] == style


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
    assert _run_cli(["--style", "AUTO", "--mode", mode, "--enabled", "false"], monkeypatch) == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR
    assert "synthetic-private-token" not in captured.err


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_unexpected_error_propagates(mode, monkeypatch):
    _spy_sdk(monkeypatch, mode, [], fail_with=RuntimeError("synthetic-boom"))
    with pytest.raises(RuntimeError):
        _run_cli(["--style", "AUTO", "--mode", mode, "--enabled", "false"], monkeypatch)


PROP_PATH = "/api/v3/device/otap/prop"
SERIAL = "MixED-FLIP-DEVICE-PRIVATE"
INDEX = "0019"
SELECTORS = {
    "resource_category": "Video",
    "domain_identifier": "VideoAdjustment",
    "property_identifier": "ImageFlip",
    "local_index": INDEX,
}


def sdk_case(kind, region, handler, payload):
    client, _, business, auth = _build(kind, region, handler)

    async def run():
        try:
            return await _invoke(kind, client.otap, "set_property", SERIAL, payload, **SELECTORS)
        finally:
            await _shutdown(kind, client)

    return lambda: asyncio.run(run()), business, auth


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("style", ["LEFTRIGHT", "UPDOWN", "CENTER", "AUTO"])
@pytest.mark.parametrize("enabled", [True, False])
def test_credential_sdk_wire(kind, style, enabled):
    body = {"enabled": enabled, "imageFlipStyle": style}
    expected = dict(body)
    ack = {"meta": {"code": 200, "message": "ok", "moreInfo": None}}
    call, requests, auth = sdk_case(
        kind, RegionConfig.china(), lambda r: httpx.Response(200, json=ack), body
    )
    result = call()
    assert (
        result.raw == ack
        and result.data is None
        and result.code == 200
        and type(result.code) is int
    )
    (auth_req,), (request,) = auth, requests
    assert (
        auth_req.method == "POST"
        and auth_req.url.host == "open.ys7.com"
        and auth_req.url.path == "/api/lapp/token/get"
    )
    check_wire(request, style, enabled, serial=SERIAL, index=INDEX, token="TOKEN-1")
    assert body == expected and body["enabled"] is enabled


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_sdk_ack_extensions(kind):
    ack = {
        "meta": {"code": 200, "message": "ack", "moreInfo": {"extra": "ACK-META"}},
        "data": {"unknown": ["ACK-DATA", None]},
        "extra": 1,
    }
    call, requests, auth = sdk_case(
        kind,
        RegionConfig.china(),
        lambda r: httpx.Response(200, json=ack),
        {"enabled": False, "imageFlipStyle": "UPDOWN"},
    )
    result = call()
    assert result.data == ack["data"] and result.raw == ack
    assert len(auth) == len(requests) == 1


SDK_FAILURES = [
    (200, {"code": "200"}, ProtocolError),
    (200, {"meta": {"code": "200"}}, ApiError),
    (200, {"meta": {"code": True}}, ProtocolError),
    (200, {"meta": {"code": 200.0}}, ProtocolError),
    (200, {"meta": {"code": 10002}}, AuthenticationError),
    (200, {"meta": {"code": 10031}}, PermissionDeniedError),
    (403, {"meta": {"code": 20018}}, PermissionDeniedError),
    (200, {"meta": {"code": 20007}}, ApiError),
    (200, {"meta": {"code": 20018}}, ApiError),
    (503, {"meta": {"code": 200}}, ApiError),
    (None, None, TransportError),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("status,reply,error", SDK_FAILURES)
def test_typed_failure_once(kind, status, reply, error):
    def handler(request):
        if reply is None:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(status, json=reply)

    call, requests, auth = sdk_case(
        kind, RegionConfig.china(), handler, {"enabled": False, "imageFlipStyle": "LEFTRIGHT"}
    )
    with pytest.raises(error) as failure:
        call()
    assert type(failure.value) is error and len(requests) == len(auth) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_overseas_no_requests(kind):
    call, requests, auth = sdk_case(
        kind,
        RegionConfig.overseas(auth_base_url="https://mock-auth.ezvizlife.com"),
        lambda r: pytest.fail("unexpected"),
        {"enabled": True, "imageFlipStyle": "AUTO"},
    )
    with pytest.raises(ValueError):
        call()
    assert requests == auth == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_diagnostic_labels(kind, monkeypatch):
    recorder = DiagnosticRecorder()
    cls_name = "AsyncClient" if kind == "async" else "Client"
    monkeypatch.setattr(
        otap_fixture,
        cls_name,
        partial(getattr(otap_fixture, cls_name), diagnostic_recorder=recorder),
    )
    body = {"enabled": False, "imageFlipStyle": "CENTER"}
    call, requests, auth = sdk_case(
        kind,
        RegionConfig.china(),
        lambda r: httpx.Response(200, json={"meta": {"code": 200}}),
        body,
    )
    call()
    assert len(auth) == len(requests) == 1
    events = recorder.report()["events"]
    operation = "otap.set_property"
    calls = [
        e
        for e in events
        if e["kind"] == "call" and e["entrypoint"] == operation and e["mode"] == kind
    ]
    assert [e["outcome"] for e in calls] == ["started", "succeeded"]
    attempts = [e for e in events if e["kind"] == "attempt" and e["operation"] == operation]
    assert [e["outcome"] for e in attempts] == ["started", "succeeded"]
    assert all(e["region"] == "domestic" for e in attempts)
    rendered = json.dumps(events, ensure_ascii=False)
    for private in (
        SERIAL,
        INDEX,
        "TOKEN-1",
        "VideoAdjustment",
        "ImageFlip",
        "imageFlipStyle",
        "CENTER",
    ):
        assert private not in rendered


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("order", list(permutations(("mode", "style", "enabled"))))
def test_example_otap_image_flip_option_order(mode, order, monkeypatch, capsys):
    lookups = []
    if mode == "async":
        original_get_token = AsyncStaticTokenProvider.get_token

        async def recording_get_token(self):
            lookups.append("async")
            return await original_get_token(self)

        monkeypatch.setattr(AsyncStaticTokenProvider, "get_token", recording_get_token)
    else:
        original_get_token = StaticTokenProvider.get_token

        def recording_get_token(self):
            lookups.append("sync")
            return original_get_token(self)

        monkeypatch.setattr(StaticTokenProvider, "get_token", recording_get_token)

    values = {"mode": mode, "style": "CENTER", "enabled": "false"}
    argv = []
    for option in order:
        argv.extend([f"--{option}", values[option]])

    assert _run_cli(argv, monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"simulated": True, "acknowledged": True}
    assert lookups == [mode]
