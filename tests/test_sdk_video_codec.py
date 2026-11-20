"""Offline contract tests for the domestic video codec query."""

import asyncio

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_rtmp_aac import (
    KINDS,
    OMIT,
    SERIAL,
    STALE,
    T2,
    TOKEN,
    Probe,
    _blocked,
    _ev,
    _f503,
    _fcancel,
    _ftimeout,
    _tokens,
)

from ezviz_unified import ApiResponse, RegionConfig, TransportError

GET = "get_video_codec"
PATH = "/api/v3/das/device/video/encode"
OK = {"meta": {"code": 200}, "data": {"videoCode": 5}}


SHAPE = [
    ({"data": {"meta": {"code": 200}}}, "ProtocolError"),
    ({"body": {"meta": {"code": 200}, "data": {"videoCode": 5}}}, "ProtocolError"),
    ({"code": 200}, "ProtocolError"),
    ({"meta": {"code": True}}, "ProtocolError"),
    ({"meta": {"code": 200.0}}, "ProtocolError"),
    ({"meta": {"code": "200"}}, "ApiError"),
]


@pytest.fixture(name="err")
def _err():
    import ezviz_unified

    return ezviz_unified


@pytest.mark.parametrize("kind", KINDS)
def test_get_refresh(kind):
    probe = Probe(STALE, OK, tokens=(TOKEN, T2))
    assert probe.call(kind, GET, SERIAL, stream=2).data["videoCode"] == 5
    assert len(probe.auth) == 2 and _tokens(probe, "GET") == [TOKEN, T2]
    for req in probe.biz:
        assert req.url.host == "open.ys7.com"
        assert req.url.path == PATH
        assert req.url.params.multi_items() == [("streamType", "2")]
        assert req.headers.get("deviceSerial") == SERIAL
        assert "channelNo" not in req.headers and "localIndex" not in req.headers
    assert _ev(probe, kind, GET) == (
        ["started", "succeeded"],
        ["started", "failed", "started", "succeeded"],
    )


@pytest.mark.parametrize("kind", KINDS)
def test_get_refresh_exhausted(kind, err):
    probe = Probe(STALE, STALE, tokens=(TOKEN, T2))
    with pytest.raises(err.AuthenticationError):
        probe.call(kind, GET, SERIAL, stream=2)
    assert len(probe.auth) == 2 and _tokens(probe, "GET") == [TOKEN, T2]
    assert _ev(probe, kind, GET) == (
        ["started", "failed"],
        ["started", "failed", "started", "failed"],
    )


@pytest.mark.parametrize("kind", KINDS)
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
def test_bad_serial_blocked(kind, serial):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, GET, serial, stream=1)
    _blocked(probe, excinfo)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("channel", (True, "0", 0.5, b"1"))
def test_bad_channel_blocked(kind, channel):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, GET, SERIAL, stream=1, channel=channel)
    _blocked(probe, excinfo)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("payload,exc", SHAPE)
def test_meta_shape(kind, payload, exc, err):
    probe = Probe(payload, tokens=(TOKEN,))
    with pytest.raises(getattr(err, exc)):
        probe.call(kind, GET, SERIAL, stream=1)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, GET)[0] == ["started", "failed"]


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
        {"videoCode": False},
        {"videoCode": True},
        {"videoCode": "5"},
        {"videoCode": 5.0},
        {"videoCode": None},
    ),
)
def test_get_data_shape(kind, data, err):
    payload = {"meta": {"code": 200}}
    if data != OMIT:
        payload["data"] = data
    probe = Probe(payload, tokens=(TOKEN,))
    with pytest.raises(err.ProtocolError):
        probe.call(kind, GET, SERIAL, stream=2)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, GET) == (["started", "failed"], ["started", "succeeded"])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("step,exc", ((_f503, "ApiError"), (_ftimeout, "TransportError")))
def test_biz_failure(kind, step, exc, err):
    probe = Probe(step, tokens=(TOKEN,))
    with pytest.raises(getattr(err, exc)):
        probe.call(kind, GET, SERIAL, stream=1)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, kind, GET) == (["started", "failed"], ["started", "failed"])


def test_async_cancel():
    import asyncio

    probe = Probe(_fcancel, tokens=(TOKEN,))
    with pytest.raises(asyncio.CancelledError):
        probe.call("async", GET, SERIAL, stream=1)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, "async", GET) == (["started", "cancelled"], ["started", "cancelled"])


@pytest.mark.parametrize("mode", KINDS)
def test_closed_client_never_authenticates(mode):
    async def scenario():
        client, auth_calls, origins = make_client(mode, lambda r: pytest.fail("unexpected request"))
        await close(client)
        await close(client)
        assert client.is_closed
        with pytest.raises(TransportError, match="closed"):
            await resolve(client.video.get_video_codec(SERIAL, stream=1))
        assert auth_calls == origins == []

    asyncio.run(scenario())


def _wire(request, channel, stream):
    assert request.url.host == "open.ys7.com"
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.path == PATH
    assert request.url.params.multi_items() == [("streamType", str(stream))]
    assert request.content == b"" and "content-type" not in request.headers
    assert "authorization" not in request.headers
    assert request.headers.get_list("accessToken") == [TOKEN]
    assert request.headers["deviceSerial"] == SERIAL
    assert "localIndex" not in request.headers
    if channel is None:
        assert "channelNo" not in request.headers
    else:
        assert request.headers.get_list("channelNo") == [str(channel)]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", (0, 1, 2, 3, 4, 5, 6, 7, 901, -9))
@pytest.mark.parametrize("channel", (OMIT, None, 0, 1, -2))
@pytest.mark.parametrize("stream", (1, 2))
def test_get_wire(kind, code, channel, stream):
    payload = {
        "meta": {"code": 200, "message": "ok", "more": True},
        "data": {"videoCode": code, "future": [1, {"x": "y"}]},
        "extra": {"trace": "t-1"},
    }
    probe = Probe(payload, tokens=(TOKEN,))
    result = probe.call(
        kind, GET, SERIAL, stream=stream, **({} if channel == OMIT else {"channel": channel})
    )
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert probe.auth[0].method == "POST"
    assert probe.auth[0].url.scheme == "https"
    assert probe.auth[0].url.host == "open.ys7.com"
    assert probe.auth[0].url.path == "/api/lapp/token/get"
    _wire(probe.biz[0], None if channel == OMIT else channel, stream)
    assert isinstance(result, ApiResponse)
    assert result.raw == payload and result.data == payload["data"]
    assert type(result.data["videoCode"]) is int and result.data["videoCode"] == code


BAD_STREAM = (0, 3, True, False, None, "1", 1.0)
SIGNATURE_CASES = [
    ((SERIAL,), {}),
    ((SERIAL, 1), {}),
    ((SERIAL, 1, 2), {}),
    ((SERIAL,), {"stream": 1, "channel_no": 1}),
]
REFUSAL_CASES = [
    (403, 20018, "PermissionDeniedError"),
    (200, 10031, "PermissionDeniedError"),
    (200, 20018, "ApiError"),
    (200, 60020, "ApiError"),
    (200, 20008, "ApiError"),
    (200, 10001, "ApiError"),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status,code,typename", REFUSAL_CASES)
def test_sdk_business_refusals_not_replayed(kind, status, code, typename, err):
    p = Probe(
        lambda r: httpx.Response(
            status, json={"meta": {"code": code, "message": "PRIVATE_DENIAL"}}
        ),
        tokens=(TOKEN,),
    )
    with pytest.raises(getattr(err, typename)) as error:
        p.call(kind, GET, SERIAL, stream=1)
    assert type(error.value) is getattr(err, typename)
    assert type(error.value.code) is int and error.value.code == code
    assert "PRIVATE_DENIAL" not in str(error.value)
    assert len(p.auth) == 1 and len(p.biz) == 1
    calls, attempts = _ev(p, kind, GET)
    assert calls == ["started", "failed"] and attempts == ["started", "failed"]


@pytest.mark.parametrize("kind", KINDS)
def test_overseas_region_rejected(kind):
    cases = [(SERIAL, {"stream": 1}), (object(), {"stream": object(), "channel": object()})]
    for serial, kw in cases:
        p = Probe(region=RegionConfig.overseas(), tokens=(TOKEN,))
        with pytest.raises(
            ValueError, match="^video operations are only supported in the domestic region$"
        ) as exc:
            p.call(kind, GET, serial, **kw)
        assert str(exc.value) == "video operations are only supported in the domestic region"
        assert p.auth == p.biz == []
        assert _ev(p, kind, GET) == (["started", "failed"], [])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("args,kwargs", SIGNATURE_CASES)
def test_signature_rejected(kind, args, kwargs):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises(TypeError):
        probe.call(kind, GET, *args, **kwargs)
    assert probe.auth == [] and probe.biz == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stream", BAD_STREAM)
def test_bad_stream_blocked(kind, stream):
    probe = Probe({"meta": {"code": 200}}, tokens=(TOKEN,))
    with pytest.raises((ValueError, TypeError)) as excinfo:
        probe.call(kind, GET, SERIAL, stream=stream)
    _blocked(probe, excinfo)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(kind, ok, caplog, err):
    payload = {
        "meta": {"code": 200 if ok else "PRIVATE_BACKEND_CODE", "message": "PRIVATE_UPSTREAM_MSG"},
        "data": {"videoCode": 9, "extra": "EXT_PRIVATE"},
    }
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(lambda request: wire, tokens=(TOKEN,))
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.call(kind, GET, SERIAL, stream=2)
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(err.ApiError) as ei:
                probe.call(kind, GET, SERIAL, stream=2)
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [r.getMessage() for r in caplog.records if r.name == "httpx" and PATH in r.getMessage()]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.rec.report()), caplog.text])
    for marker in (
        SERIAL,
        TOKEN,
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob
