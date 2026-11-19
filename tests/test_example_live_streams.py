"""Tests for the live-stream examples; all HTTP stays inside MockTransports."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

import examples.live_stream_lifecycle as lifecycle
import examples.live_streams as live_streams
from examples._common import ExampleConfig
from ezviz_unified import AsyncClient, Client, RegionConfig

HOST = "open.ys7.com"
TOKEN = "/api/lapp/token/get"
LIST = "/api/service/media/streammanage/stream/list"
STREAM = "/api/service/media/streammanage/stream"
STATUS = STREAM + "/status"
FORM = [("pageSize", "1"), ("pageStart", "0")]
USAGE = "usage: python -m examples.live_streams --mode sync|async"
ERR = "error: live stream list failed"
TOKEN_BODY = {
    "code": "200",
    "data": {
        "accessToken": "test-access-token",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}


class _Rec:
    """MockTransport handler that records requests, then delegates."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.handler(request)


def _reject():
    return httpx.Response(200, json={"meta": {"code": 400, "message": "unexpected"}})


def _auth(request):
    if (request.method, request.url.host, request.url.path) != ("POST", HOST, TOKEN):
        return httpx.Response(400)
    return httpx.Response(200, json=dict(TOKEN_BODY))


def _list(payload):
    def handler(request):
        good = (
            (request.method, request.url.host, request.url.path) == ("POST", HOST, LIST)
            and sorted(httpx.QueryParams(request.content.decode("utf-8")).multi_items()) == FORM
            and request.url.scheme == "https"
            and not request.url.query
            and request.headers.get_list("accessToken") == [TOKEN_BODY["data"]["accessToken"]]
        )
        if not good:
            return _reject()
        body = {"meta": {"code": 200, "message": "ok"}, "data": payload}
        return httpx.Response(200, json=body)

    return handler


@pytest.fixture
def wired(monkeypatch):
    """Make the example's Client/AsyncClient build MockTransport-backed clients."""
    state = SimpleNamespace(auth=_Rec(_auth), biz=_Rec(_list([])), loads=0)

    def factory(real):
        def build(app_key, app_secret, region=None):
            return real(
                app_key,
                app_secret,
                region=region,
                auth_transport=httpx.MockTransport(state.auth),
                transport_factory=lambda origin: httpx.MockTransport(state.biz),
            )

        return build

    def fake_config():
        state.loads += 1
        return ExampleConfig(
            app_key="app-key",
            app_secret="app-secret",
            region="domestic",
            region_config=RegionConfig.china(),
        )

    monkeypatch.setattr(live_streams, "Client", factory(Client))
    monkeypatch.setattr(live_streams, "AsyncClient", factory(AsyncClient))
    monkeypatch.setattr(live_streams, "load_config", fake_config)
    return state


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "payload",
    [[], [{"streamId": "HIDDEN-ID", "deviceSerial": "HIDDEN-SER", "unknown": 1}]],
    ids=["empty", "one-item"],
)
def test_list_ok(wired, capsys, mode, payload):
    wired.biz.handler = _list(payload)
    assert live_streams.main(["--mode", mode]) == 0
    captured = capsys.readouterr()
    expected = {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "returned_items": len(payload),
    }
    assert captured.out == json.dumps(expected, separators=(",", ":")) + "\n"
    assert captured.err == ""
    assert wired.loads == 1
    assert all(s not in captured.out for s in ("HIDDEN-ID", "HIDDEN-SER", "test-access-token"))
    auth = [(r.method, r.url.host, r.url.path) for r in wired.auth.requests]
    assert auth == [("POST", HOST, TOKEN)]
    biz = [
        (
            r.method,
            r.url.host,
            r.url.path,
            sorted(httpx.QueryParams(r.content.decode("utf-8")).multi_items()),
        )
        for r in wired.biz.requests
    ]
    assert biz == [("POST", HOST, LIST, FORM)]


@pytest.mark.parametrize(
    "argv,rc,chan",
    [
        (["-h"], 0, "out"),
        (["--help"], 0, "out"),
        ([], 1, "err"),
        (["--mode"], 1, "err"),
        (["--mode", "nope"], 1, "err"),
        (["--mode", "sync", "x"], 1, "err"),
    ],
)
def test_usage(wired, capsys, argv, rc, chan):
    assert live_streams.main(argv) == rc
    captured = capsys.readouterr()
    assert getattr(captured, chan) == USAGE + "\n"
    assert getattr(captured, "err" if chan == "out" else "out") == ""
    assert wired.loads == 0
    assert not wired.auth.requests and not wired.biz.requests


def test_foreign_region(wired, capsys, monkeypatch):
    cfg = ExampleConfig(
        app_key="k",
        app_secret="s",
        region="overseas",
        region_config=RegionConfig.china(),
    )
    monkeypatch.setattr(live_streams, "load_config", lambda: cfg)
    assert live_streams.main(["--mode", "sync"]) == 1
    assert capsys.readouterr() == ("", ERR + "\n")
    assert not wired.auth.requests and not wired.biz.requests


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_rejected(wired, capsys, mode):
    wired.biz.handler = lambda request: _reject()
    assert live_streams.main(["--mode", mode]) == 1
    assert capsys.readouterr() == ("", ERR + "\n")


@pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
def test_interrupted(wired, capsys, monkeypatch, exc):
    def boom():
        raise exc()

    monkeypatch.setattr(live_streams, "load_config", boom)
    assert live_streams.main(["--mode", "sync"]) == 130
    assert capsys.readouterr() == ("", ERR + "\n")


@pytest.fixture
def calls(monkeypatch):
    """Record every request seen by MockTransport.handle_request (passthrough)."""
    seen = []
    original = httpx.MockTransport.handle_request

    def spy(self, request):
        seen.append(request)
        return original(self, request)

    monkeypatch.setattr(httpx.MockTransport, "handle_request", spy)
    return seen


def _route(request):
    return (request.method, request.url.host, request.url.path)


def test_lifecycle_ok(calls, capsys):
    assert lifecycle.main([]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "region": "domestic",
        "status": "ok",
        "simulation": True,
    }
    assert captured.err == ""
    assert [_route(r) for r in calls] == [
        ("POST", HOST, TOKEN),
        ("POST", HOST, STREAM),
        ("PUT", HOST, STREAM),
        ("PUT", HOST, STATUS),
        ("DELETE", HOST, STREAM),
    ]
    assert calls[1].content
    for request in calls[2:]:
        assert request.url.params["streamId"] == lifecycle._STREAM_ID
    assert calls[3].url.params["status"] == "0"


def test_lifecycle_alt_id(calls, capsys, monkeypatch):
    fresh = "FRESH-STREAM-ID-4242"

    def biz(request):
        if request.url.host != HOST:
            return lifecycle._reject()
        method, path = request.method, request.url.path
        sid = request.url.params.get("streamId")
        if (method, path) == ("POST", STREAM):
            ok = bool(request.content)
            return lifecycle._meta({"streamId": fresh}) if ok else lifecycle._reject()
        if (method, path) in (("PUT", STREAM), ("DELETE", STREAM)):
            return lifecycle._meta() if sid == fresh else lifecycle._reject()
        if (method, path) == ("PUT", STATUS):
            ok = sid == fresh and request.url.params.get("status") == "0"
            return lifecycle._meta() if ok else lifecycle._reject()
        return lifecycle._reject()

    monkeypatch.setattr(lifecycle, "biz", biz)
    assert lifecycle.main([]) == 0
    for request in calls[2:]:
        assert request.url.params["streamId"] == fresh
    assert calls[3].url.params["status"] == "0"


def test_lifecycle_create_fails(calls, capsys, monkeypatch):
    monkeypatch.setattr(lifecycle, "biz", lambda request: lifecycle._reject())
    assert lifecycle.main([]) == 1
    assert capsys.readouterr().out == ""
    assert [_route(r) for r in calls] == [("POST", HOST, TOKEN), ("POST", HOST, STREAM)]


def test_lifecycle_update_fails(calls, capsys, monkeypatch):
    real_biz = lifecycle.biz

    def biz(request):
        if (request.method, request.url.path) == ("PUT", STREAM):
            return lifecycle._reject()
        return real_biz(request)

    monkeypatch.setattr(lifecycle, "biz", biz)
    assert lifecycle.main([]) == 1
    assert [_route(r) for r in calls] == [
        ("POST", HOST, TOKEN),
        ("POST", HOST, STREAM),
        ("PUT", HOST, STREAM),
    ]


@pytest.mark.parametrize(
    "exc,rc,err",
    [
        (KeyboardInterrupt, 130, "error: interrupted\n"),
        (asyncio.CancelledError, 130, "error: interrupted\n"),
        (ValueError, 1, "error: simulated live stream lifecycle failed\n"),
    ],
)
def test_lifecycle_errors(monkeypatch, capsys, exc, rc, err):
    def boom():
        raise exc()

    monkeypatch.setattr(lifecycle, "_run", boom)
    assert lifecycle.main([]) == rc
    assert capsys.readouterr() == ("", err)
