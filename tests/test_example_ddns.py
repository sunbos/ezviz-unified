"""Synthetic offline tests for ``examples.ddns`` (overseas DDNS, read-only).

The real ``Client`` / ``AsyncClient`` constructors are wrapped so the auth
request and the business request each land on a dedicated
``httpx.MockTransport`` recorder.  Nothing here touches the network,
``sys.argv`` or real credentials.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

import examples.ddns as ddns
from ezviz_unified import AsyncClient, Client

AUTH_HOST = "https://synthetic-auth.ezvizlife.com"
API_HOST = "https://synthetic-api.ezvizlife.com"
AUTH_URL = f"{AUTH_HOST}/api/lapp/token/get"
DDNS_URL = {
    "get": f"{API_HOST}/api/lapp/ddns/get",
    "list": f"{API_HOST}/api/lapp/ddns/list",
    "shared": f"{API_HOST}/api/lapp/ddns/share/list",
}
USAGE = "usage: python -m examples.ddns --mode sync|async --query get|list|shared\n"
ERROR = "error: DDNS query failed\n"
_APP_KEY = "synthetic" + "-key"
_APP_SECRET = "synthetic" + "-secret"
_ENV = {
    "EZVIZ_REGION": "overseas",
    "EZVIZ_APP_KEY": _APP_KEY,
    "EZVIZ_APP_SECRET": _APP_SECRET,
    "EZVIZ_AUTH_BASE_URL": AUTH_HOST,
}
_AUTH = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": API_HOST,
    },
}
_ROW = {
    "domain": "fixture.invalid",
    "deviceSerial": "SYNTHDEV",
    "deviceIp": "192.0.2.21",
    "accessToken": "SYNTH_TOKEN",
    "appKey": _APP_KEY,
    "futureField": {"nested": "synthetic-marker"},
}
_DATA = {
    "get": {"code": "200", "data": dict(_ROW)},
    "list": {
        "code": "200",
        "data": [dict(_ROW)],
        "page": {"total": 1, "pageStart": 0, "pageSize": 1},
    },
    "shared": {
        "code": "200",
        "data": [],
        "page": {"total": 0},
        "accessToken": "SYNTH_TOKEN",
        "appKey": _APP_KEY,
    },
}
_MARKERS = (
    "SYNTH_TOKEN",
    _APP_KEY,
    _APP_SECRET,
    "SYNTHDEV",
    "fixture.invalid",
    "192.0.2.21",
    "synthetic-marker",
)


class Recorder:
    """One MockTransport handler: record each request, reply canned JSON."""

    def __init__(self) -> None:
        self.payloads: list = []
        self.response: httpx.Response | None = None
        self.error: BaseException | None = None
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return httpx.Response(200, json=self.payloads.pop(0))


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@pytest.fixture()
def rig(monkeypatch):
    auth, business, built = Recorder(), Recorder(), []
    auth.payloads = [_AUTH]

    def wrap(cls):
        def factory(*args, **kwargs):
            built.append(
                cls(
                    *args,
                    auth_transport=httpx.MockTransport(auth),
                    transport_factory=lambda origin: httpx.MockTransport(business),
                    **kwargs,
                )
            )
            return built[-1]

        return factory

    monkeypatch.setattr(ddns, "Client", wrap(Client))
    monkeypatch.setattr(ddns, "AsyncClient", wrap(AsyncClient))
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("EZVIZ_DEVICE_SERIAL", "EZVIZ_DDNS_DOMAIN"):
        monkeypatch.delenv(key, raising=False)
    return SimpleNamespace(auth=auth, business=business, built=built)


def _argv(mode: str, query: str) -> list[str]:
    return ["--mode", mode, "--query", query]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("query", ["get", "list", "shared"])
def test_main_ok(rig, monkeypatch, capsys, mode, query):
    if query == "get":
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "SYNTHDEV")
    rig.business.payloads = [_DATA[query]]
    assert ddns.main(_argv(mode, query)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "overseas",
        "mode": mode,
        "query": query,
        "status": "ok",
    }
    for marker in _MARKERS:
        assert marker not in captured.out
    assert len(rig.built) == 1
    assert len(rig.auth.calls) == len(rig.business.calls) == 1
    auth_req, biz_req = rig.auth.calls[0], rig.business.calls[0]
    assert auth_req.method == biz_req.method == "POST"
    assert str(auth_req.url) == AUTH_URL
    assert _form(auth_req) == {"appKey": _APP_KEY, "appSecret": _APP_SECRET}
    assert str(biz_req.url) == DDNS_URL[query]
    for req in (auth_req, biz_req):
        assert req.url.query == b""
        assert "authorization" not in req.headers
        assert "token" not in req.headers
    if query == "get":
        expected = {"accessToken": "SYNTH_TOKEN", "deviceSerial": "SYNTHDEV"}
    else:
        expected = {"accessToken": "SYNTH_TOKEN", "pageStart": "0", "pageSize": "1"}
    assert _form(biz_req) == expected


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "serial,domain",
    [
        ("SYNTHDEV", None),
        (None, "fixture.invalid"),
        ("SYNTHDEV", "fixture.invalid"),
    ],
)
def test_get_selectors(rig, monkeypatch, capsys, mode, serial, domain):
    if serial is not None:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    if domain is not None:
        monkeypatch.setenv("EZVIZ_DDNS_DOMAIN", domain)
    rig.business.payloads = [_DATA["get"]]
    assert ddns.main(_argv(mode, "get")) == 0
    capsys.readouterr()
    expected = {"accessToken": "SYNTH_TOKEN"}
    if serial is not None:
        expected["deviceSerial"] = serial
    if domain is not None:
        expected["domain"] = domain
    assert _form(rig.business.calls[0]) == expected


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_get_requires_selector(rig, capsys, mode):
    assert ddns.main(_argv(mode, "get")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ERROR
    assert rig.built == [] and rig.auth.calls == rig.business.calls == []


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help(rig, monkeypatch, capsys, flag):
    monkeypatch.setattr(ddns, "load_config", pytest.fail)
    assert ddns.main([flag]) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE and captured.err == ""
    assert rig.built == [] and rig.auth.calls == rig.business.calls == []


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "sync"],
        ["--mode", "sync", "--query"],
        ["--query", "get", "--mode", "sync"],
        ["mode", "sync", "query", "get"],
        ["--mode", "bogus", "--query", "get"],
        ["--mode", "sync", "--query", "bogus"],
        ["--mode", "sync", "--query", "get", "--verbose"],
    ],
)
def test_bad_argv(rig, monkeypatch, capsys, argv):
    monkeypatch.setattr(ddns, "load_config", pytest.fail)
    assert ddns.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == USAGE
    assert rig.built == [] and rig.auth.calls == rig.business.calls == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_domestic_refused(rig, monkeypatch, capsys, mode):
    monkeypatch.setenv("EZVIZ_REGION", "domestic")
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    assert ddns.main(_argv(mode, "list")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ERROR
    assert rig.built == [] and rig.auth.calls == rig.business.calls == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("kind", ["transport", "bad_code", "bad_json"])
def test_business_failure(rig, capsys, mode, kind):
    if kind == "transport":
        rig.business.error = httpx.ConnectError("synthetic")
    elif kind == "bad_json":
        rig.business.response = httpx.Response(200, content=b"not json")
    else:
        rig.business.payloads = [{"code": "500", "msg": "synthetic"}]
    assert ddns.main(_argv(mode, "list")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ERROR


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("exc", [KeyboardInterrupt("stop"), asyncio.CancelledError("stop")])
def test_interrupted(rig, capsys, mode, exc):
    rig.business.error = exc
    assert ddns.main(_argv(mode, "list")) == 130
    captured = capsys.readouterr()
    assert captured.out == "" and ERROR in captured.err
