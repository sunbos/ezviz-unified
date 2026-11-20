"""Contract tests for video RTMP AAC transfer switch (domestic wire)."""

import pytest
from test_sdk_night_vision import Probe as _BaseProbe
from test_sdk_stream_archive import _invoke

from ezviz_unified import ApiResponse, RegionConfig

KINDS = ("sync", "async")
GET = "get_rtmp_aac_support"
SET = "set_rtmp_aac_enabled"
TOKEN = "SYN_TOKEN"
SERIAL = "Syn Header-9"
PATH = "/api/service/media/aac/transfer"
OMIT = "omit"


class Probe(_BaseProbe):
    def call(self, kind, name, *args, **kwargs):
        return _invoke(kind, "video", name, args, kwargs, self.options)


def _wire(request, channel, query):
    assert request.url.host == "open.ys7.com"
    assert request.method == ("POST" if query else "GET")
    assert request.url.path == PATH
    assert request.url.params.multi_items() == query
    assert request.content == b"" and "content-type" not in request.headers
    assert "authorization" not in request.headers
    assert request.headers.get_list("accessToken") == [TOKEN]
    assert request.headers["deviceSerial"] == SERIAL
    if channel is None:
        assert "localIndex" not in request.headers
    else:
        assert request.headers.get_list("localIndex") == [str(channel)]


def _kw(name, channel=OMIT):
    kwargs = {} if channel == OMIT else {"channel": channel}
    if name == SET:
        kwargs["enabled"] = True
    return kwargs


def _blocked(probe, excinfo):
    assert probe.auth == [] and probe.biz == []
    seen, current = set(), excinfo.value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        assert TOKEN not in text and SERIAL not in text
        assert "synthetic" not in text.lower()
        current = current.__cause__ or current.__context__


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("flag", (False, True))
@pytest.mark.parametrize("channel", (OMIT, None, 0, 2))
def test_get_wire(kind, flag, channel):
    payload = {
        "meta": {"code": 200, "message": "ok", "more": True},
        "data": {"enable": flag, "future": [1, {"x": "y"}]},
        "extra": {"trace": "t-1"},
    }
    probe = Probe(payload, tokens=(TOKEN,))
    result = probe.call(kind, GET, SERIAL, **({} if channel == OMIT else {"channel": channel}))
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    _wire(probe.biz[0], None if channel == OMIT else channel, [])
    assert isinstance(result, ApiResponse)
    assert result.raw == payload and result.data == payload["data"]
    assert result.data["enable"] is flag


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("flag", (False, True))
@pytest.mark.parametrize("channel", (OMIT, None, 0, 2))
def test_set_wire(kind, flag, channel):
    payload = {
        "meta": {"code": 200, "message": "ok"},
        "data": {"applied": flag, "echo": {"k": [1]}},
        "extra": {"trace": "t-2"},
    }
    probe = Probe(payload, tokens=(TOKEN,))
    result = probe.call(
        kind, SET, SERIAL, flag, **({} if channel == OMIT else {"channel": channel})
    )
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    _wire(probe.biz[0], None if channel == OMIT else channel, [("enable", "1" if flag else "0")])
    assert isinstance(result, ApiResponse)
    assert result.raw == payload and result.data == payload["data"]


@pytest.mark.parametrize("kind", KINDS)
def test_set_minimal_payload(kind):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    result = probe.call(kind, SET, SERIAL, enabled=True)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert "data" not in result.raw and result.data is None
    assert probe.biz[0].url.params.multi_items() == [("enable", "1")]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name,args", ((GET, (SERIAL, 0)), (SET, (SERIAL, True, 0))))
def test_positional_channel_rejected(kind, name, args):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises(TypeError):
        probe.call(kind, name, *args)
    assert probe.auth == [] and probe.biz == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", (GET, SET))
def test_overseas_blocked(kind, name):
    probe = Probe({"meta": {"code": 200}}, region=RegionConfig.overseas(), tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, name, SERIAL, **_kw(name))
    _blocked(probe, excinfo)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", (GET, SET))
@pytest.mark.parametrize(
    "serial",
    (
        "",
        123,
        "synthetic\rb",
        "synthetic\nb",
        "synthetic\tb",
        "synthetic\x7fb",
        " synthetic",
        "synthetic ",
        "synthetic\u4e2d",
    ),
)
def test_bad_serial_blocked(kind, name, serial):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, name, serial, **_kw(name))
    _blocked(probe, excinfo)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", (GET, SET))
@pytest.mark.parametrize("channel", (True, "0", 0.5, -1))
def test_bad_channel_blocked(kind, name, channel):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, name, SERIAL, **_kw(name, channel))
    _blocked(probe, excinfo)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("enabled", (0, 1, None, "yes"))
def test_bad_enabled_blocked(kind, enabled):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, SET, SERIAL, enabled)
    _blocked(probe, excinfo)


T2 = "SYN_TOKEN_TWO"
OK = {"meta": {"code": 200}, "data": {"enable": True}}
STALE = {"meta": {"code": 10002}}
BOTH = ((GET, "GET"), (SET, "POST"))


@pytest.fixture(name="err")
def _err():
    import ezviz_unified

    return ezviz_unified


def _ev(probe, kind, name):
    public = "video." + name
    events = [event for event in probe.rec.report()["events"] if event.get("mode") == kind]
    calls = [
        event["outcome"]
        for event in events
        if event.get("kind") == "call" and event.get("entrypoint") == public
    ]
    attempts = [
        event["outcome"]
        for event in events
        if event.get("kind") == "attempt"
        and event.get("entrypoint") == "client.request"
        and event.get("operation") == public
    ]
    return calls, attempts


def _tokens(probe, method):
    out = []
    for req in probe.biz:
        assert req.method == method and not req.content
        assert TOKEN not in str(req.url) and T2 not in str(req.url)
        out.append(req.headers["accessToken"])
    return out


def _f503(_req):
    import httpx

    return httpx.Response(503, json={"meta": {"code": 200}})


def _ftimeout(_req):
    from test_sdk_charging_status import _timeout

    return _timeout(_req)


def _fcancel(_req):
    import asyncio

    raise asyncio.CancelledError()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name,method", BOTH)
def test_success_events(kind, name, method):
    probe = Probe(OK, tokens=(TOKEN,))
    probe.call(kind, name, SERIAL, **_kw(name))
    assert len(probe.auth) == 1 and _tokens(probe, method) == [TOKEN]
    assert _ev(probe, kind, name) == (["started", "succeeded"], ["started", "succeeded"])


SHAPE = [
    ({"data": {"meta": {"code": 200}}}, "ProtocolError"),
    ({"code": 200}, "ProtocolError"),
    ({"meta": {"code": True}}, "ProtocolError"),
    ({"meta": {"code": 200.0}}, "ProtocolError"),
    ({"meta": {"code": "200"}}, "ApiError"),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", (GET, SET))
@pytest.mark.parametrize("payload,exc", SHAPE)
def test_meta_shape(kind, name, payload, exc, err):
    probe = Probe(payload, tokens=(TOKEN,))
    with pytest.raises(getattr(err, exc)):
        probe.call(kind, name, SERIAL, **_kw(name))
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, name)[0] == ["started", "failed"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "data",
    (
        OMIT,
        None,
        0,
        "x",
        [],
        {},
        {"enable": 0},
        {"enable": 1},
        {"enable": "true"},
        {"enable": "false"},
        {"enable": None},
    ),
)
def test_get_data_shape(kind, data, err):
    payload = {"meta": {"code": 200}}
    if data != OMIT:
        payload["data"] = data
    probe = Probe(payload, tokens=(TOKEN,))
    with pytest.raises(err.ProtocolError):
        probe.call(kind, GET, SERIAL)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, GET) == (["started", "failed"], ["started", "succeeded"])


@pytest.mark.parametrize("kind", KINDS)
def test_get_refresh(kind):
    probe = Probe(STALE, OK, tokens=(TOKEN, T2))
    assert probe.call(kind, GET, SERIAL).data["enable"] is True
    assert len(probe.auth) == 2 and _tokens(probe, "GET") == [TOKEN, T2]
    assert _ev(probe, kind, GET) == (
        ["started", "succeeded"],
        ["started", "failed", "started", "succeeded"],
    )


@pytest.mark.parametrize("kind", KINDS)
def test_get_refresh_exhausted(kind, err):
    probe = Probe(STALE, STALE, tokens=(TOKEN, T2))
    with pytest.raises(err.AuthenticationError):
        probe.call(kind, GET, SERIAL)
    assert len(probe.auth) == 2 and _tokens(probe, "GET") == [TOKEN, T2]
    assert _ev(probe, kind, GET) == (
        ["started", "failed"],
        ["started", "failed", "started", "failed"],
    )


@pytest.mark.parametrize("kind", KINDS)
def test_set_stale_no_replay(kind, err):
    probe = Probe(STALE, tokens=(TOKEN, T2))
    with pytest.raises(err.AuthenticationError):
        probe.call(kind, SET, SERIAL, enabled=True)
    assert len(probe.auth) == 1 and _tokens(probe, "POST") == [TOKEN]
    assert _ev(probe, kind, SET) == (["started", "failed"], ["started", "failed"])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name,method", BOTH)
def test_denied_sanitized(kind, name, method, caplog, err):
    msg = f"denied tok={TOKEN} ser={SERIAL}"
    probe = Probe({"meta": {"code": 10031, "message": msg}}, tokens=(TOKEN,))
    with caplog.at_level("INFO", logger="httpx"):
        with pytest.raises(err.PermissionDeniedError) as excinfo:
            probe.call(kind, name, SERIAL, **_kw(name))
    hits = [r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelname == "INFO"]
    assert any(method in m and PATH in m for m in hits)
    blob = str(excinfo.value) + repr(excinfo.value) + caplog.text + probe.rec.to_json()
    assert TOKEN not in blob and SERIAL not in blob
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, name) == (["started", "failed"], ["started", "failed"])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", (GET, SET))
@pytest.mark.parametrize("step,exc", ((_f503, "ApiError"), (_ftimeout, "TransportError")))
def test_biz_failure(kind, name, step, exc, err):
    probe = Probe(step, tokens=(TOKEN,))
    with pytest.raises(getattr(err, exc)):
        probe.call(kind, name, SERIAL, **_kw(name))
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, name) == (["started", "failed"], ["started", "failed"])


@pytest.mark.parametrize("name", (GET, SET))
def test_async_cancel(name):
    import asyncio

    probe = Probe(_fcancel, tokens=(TOKEN,))
    with pytest.raises(asyncio.CancelledError):
        probe.call("async", name, SERIAL, **_kw(name))
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, "async", name) == (["started", "cancelled"], ["started", "cancelled"])
