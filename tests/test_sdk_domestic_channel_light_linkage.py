"""Offline tests for the domestic channel light linkage query."""

import asyncio

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_light_linkage import outcomes
from test_sdk_motion_area import TRANSPORT_HEADERS, Probe

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

OP = "get_channel_light_linkage"
PATH = "/api/v3/device/switchStatus/get"
SERIAL = "mIxEd-PRIVATE-SERIAL"
CHANNEL = "001-CHANNEL_PRIVATE"
KW = {"channel_no": CHANNEL, "linkage_type": "305"}
DATA = {
    "type": -7,
    "enable": False,
    "subSerial": "SUB_PRIVATE",
    "unknown": {"field": "EXT_PRIVATE"},
}
_MISS = object()


def reply(code=200, **fields):
    return {"code": str(code), "msg": "PRIVATE_UPSTREAM_MSG", **fields}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def invoke(mode, *steps):
    p = Probe(*steps)
    return (
        lambda: p.run(mode, "domestic", OP, SERIAL, **KW),
        p.recorder,
        {"auth": p.auth, "biz": p.biz},
    )


def test_overseas_region_rejected(mode):
    for serial, kw in (
        (SERIAL, KW),
        (object(), {"channel_no": object(), "linkage_type": object()}),
    ):
        p = Probe()
        with pytest.raises(
            ValueError, match="^this operation is only supported in the domestic region$"
        ) as exc:
            p.run(mode, "overseas", OP, serial, **kw)
        assert str(exc.value) == "this operation is only supported in the domestic region"
        assert p.auth == p.biz == []
        events = p.recorder.report()["events"]
        calls = [
            e
            for e in events
            if e["kind"] == "call" and e["entrypoint"] == "settings." + OP and e["mode"] == mode
        ]
        assert [e["outcome"] for e in calls] == ["started", "failed"]
        assert not [e for e in events if e["kind"] == "attempt"]
        assert not [e for e in events if e["kind"] in ("auth", "business")]


def test_permission_denied_10031(mode):
    call, rec, seen = invoke(mode, reply(10031))
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert str(err.value.code) == "10031" and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


def test_read_timeout_maps_transport_error(mode):
    call, rec, seen = invoke(mode, httpx.ReadTimeout("synthetic"))
    with pytest.raises(TransportError) as excinfo:
        call()
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(seen["auth"]) == len(seen["biz"]) == 1


def test_async_cancellation_propagates():
    call, rec, seen = invoke("async", asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call()
    assert outcomes(rec, "async", "call", OP) == ["started", "cancelled"]
    assert outcomes(rec, "async", "attempt", OP) == ["started", "cancelled"]
    assert len(seen["auth"]) == len(seen["biz"]) == 1


def test_get_invalid_token_retry_then_success(mode):
    call, rec, seen = invoke(mode, reply(10002), reply(data=DATA))
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", OP) == ["started", "failed", "started", "succeeded"]
    refreshed(rec, mode, seen, "succeeded")


def test_get_invalid_token_exhausted_raises(mode):
    call, rec, seen = invoke(mode, reply(10002), reply(10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", OP) == ["started", "failed", "started", "failed"]
    refreshed(rec, mode, seen, "failed")


@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, ok, caplog):
    scope = "domestic"
    payload = reply(200 if ok else "PRIVATE_BACKEND_CODE", data=DATA)
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(wire)
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.run(mode, scope, OP, SERIAL, **KW)
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.run(mode, scope, OP, SERIAL, **KW)
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [r.getMessage() for r in caplog.records if r.name == "httpx" and PATH in r.getMessage()]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        SERIAL,
        "TOK1",
        "PRIVATEKEY",
        "PRIVATESECRET",
        CHANNEL,
        "SUB_PRIVATE",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob


def test_closed_client_never_authenticates(mode):
    async def scenario():
        client, auth_calls, origins = make_client(mode, lambda r: pytest.fail("unexpected request"))
        await close(client)
        await close(client)
        assert client.is_closed
        with pytest.raises(TransportError, match="closed"):
            await resolve(getattr(client.settings, OP)(SERIAL, **KW))
        assert auth_calls == origins == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code,exc",
    [(403, 20018, PermissionDeniedError), (200, 20018, ApiError), (200, 60020, ApiError)],
)
def test_nonretryable_business_failures(mode, status, code, exc):
    p = Probe(httpx.Response(status, json=reply(code)))
    with pytest.raises(exc) as error:
        p.run(mode, "domestic", OP, SERIAL, **KW)
    assert type(error.value) is exc
    assert error.value.code == "***"
    assert error.value.message == "***"
    assert len(p.auth) == len(p.biz) == 1
    assert outcomes(p.recorder, mode, "attempt", OP) == ["started", "failed"]


def test_auth_failure(mode):
    p = Probe()

    def deny(request):
        p.auth.append(request)
        return httpx.Response(403, json={"code": "10031", "msg": "PRIVATE_DENIAL"})

    p._auth = deny
    with pytest.raises(ApiError) as exc:
        p.run(mode, "domestic", OP, SERIAL, **KW)
    assert len(p.auth) == 1
    assert p.biz == []
    blob = repr(exc.value) + " " + str(exc.value)
    assert SERIAL not in blob
    assert "TOK1" not in blob


def refreshed(rec, mode, seen, terminal):
    first, second = seen["biz"]
    assert [r.headers.get_list("accessToken") for r in (first, second)] == [["TOK1"], ["TOK2"]]
    assert (
        first.url == second.url
        and first.url.path == PATH
        and first.url.params.multi_items() == [("type", KW["linkage_type"])]
    )
    for r in (first, second):
        assert r.method == "GET" and r.content == b"" and "content-type" not in r.headers
        assert r.headers.get_list("deviceSerial") == [SERIAL] and r.headers.get_list(
            "channelNo"
        ) == [CHANNEL]
    assert outcomes(rec, mode, "retry", OP) == ["scheduled"] and outcomes(
        rec, mode, "call", OP
    ) == ["started", terminal]


@pytest.mark.parametrize("selector", ["301", "305"])
@pytest.mark.parametrize("returned,enabled", [(301, True), (305, False), (-9, True)])
def test_read_wire(mode, selector, returned, enabled):
    data = {
        "type": returned,
        "enable": enabled,
        "subSerial": "SUB_PRIVATE",
        "extra": {"values": [1, None]},
    }
    body = reply(data=data, extra={"opaque": True}, body={"meta": {"code": 10002}})
    p = Probe(body)
    result = p.run(mode, "domestic", OP, SERIAL, channel_no="0007", linkage_type=selector)
    assert (
        result.code == "200"
        and result.raw == body
        and result.data == data
        and result.data["enable"] is enabled
    )
    (auth,), (r,) = p.auth, p.biz
    assert (
        auth.url.scheme == r.url.scheme == "https" and auth.url.host == r.url.host == "open.ys7.com"
    )
    assert auth.method == "POST" and auth.url.path == "/api/lapp/token/get"
    assert (
        r.method == "GET"
        and r.url.path == PATH
        and r.url.params.multi_items() == [("type", selector)]
    )
    assert r.content == b"" and "content-type" not in r.headers
    assert set(r.headers) - TRANSPORT_HEADERS == {"accesstoken", "deviceserial", "channelno"}
    assert (
        r.headers.get_list("accessToken") == ["TOK1"]
        and r.headers.get_list("deviceSerial") == [SERIAL]
        and r.headers.get_list("channelNo") == ["0007"]
    )


@pytest.mark.parametrize("extra", [{}, {"subSerial": ""}])
def test_optional_subserial(mode, extra):
    data = {"type": 305, "enable": False, **extra}
    p = Probe(reply(data=data))
    result = p.run(mode, "domestic", OP, SERIAL, **KW)
    assert result.data == data and len(p.auth) == len(p.biz) == 1


BAD_DATA = [
    _MISS,
    None,
    True,
    305,
    "305",
    [],
    [{"type": 305, "enable": True}],
    [[305, True]],
    {},
    {"enable": True},
    {"type": 305},
    {"type": True, "enable": True},
    {"type": False, "enable": True},
    {"type": 301.0, "enable": True},
    {"type": "301", "enable": True},
    {"type": None, "enable": True},
    {"type": [], "enable": True},
    {"type": [305], "enable": True},
    {"type": {"value": 305}, "enable": True},
    {"type": 305, "enable": 0},
    {"type": 305, "enable": 1},
    {"type": 305, "enable": 0.0},
    {"type": 305, "enable": "true"},
    {"type": 305, "enable": "1"},
    {"type": 305, "enable": None},
    {"type": 305, "enable": []},
    {"type": 305, "enable": [True]},
    {"type": 305, "enable": {"value": True}},
    {"type": 301, "enable": False, "subSerial": None},
    {"type": 301, "enable": False, "subSerial": 101},
    {"type": 301, "enable": False, "subSerial": 101.0},
    {"type": 301, "enable": False, "subSerial": True},
    {"type": 301, "enable": False, "subSerial": {"serial": "101"}},
    {"type": 301, "enable": False, "subSerial": []},
    {"type": 301, "enable": False, "subSerial": ["101"]},
    {"body": {"meta": {"type": 305, "enable": True}}},
    {"meta": {"type": 305, "enable": True}},
    {"valueInfo": {"type": 305, "enable": True}},
    {"data": {"type": 305, "enable": True}},
]


@pytest.mark.parametrize("data", BAD_DATA)
def test_data_rejected(mode, data):
    p = Probe(reply(**({} if data is _MISS else {"data": data})))
    with pytest.raises(ProtocolError):
        p.run(mode, "domestic", OP, SERIAL, **KW)
    assert len(p.auth) == len(p.biz) == 1


@pytest.mark.parametrize(
    "body,error",
    [
        ({"code": 200}, ApiError),
        ({"code": True}, ProtocolError),
        ({"code": 200.0}, ProtocolError),
        ({"meta": {"code": 200}}, ProtocolError),
        ({"body": {"meta": {"code": 200}}}, ProtocolError),
    ],
)
def test_wrong_envelope(mode, body, error):
    p = Probe({**body, "data": {"type": 305, "enable": False}})
    with pytest.raises(error) as exc:
        p.run(mode, "domestic", OP, SERIAL, **KW)
    assert type(exc.value) is error and len(p.auth) == len(p.biz) == 1


@pytest.mark.parametrize(
    "bad",
    [None, False, 1, b"x", "", " ", " leading", "trailing ", "x\x00y", "x\x7fy", "中", "\ud800"],
)
@pytest.mark.parametrize("field", ["serial", "channel"])
def test_bad_header(mode, bad, field):
    p = Probe()
    with pytest.raises(ValueError):
        p.run(
            mode,
            "domestic",
            OP,
            bad if field == "serial" else SERIAL,
            channel_no=bad if field == "channel" else CHANNEL,
            linkage_type="301",
        )
    assert p.auth == p.biz == [] and outcomes(p.recorder, mode, "attempt", OP) == []


@pytest.mark.parametrize(
    "bad", [None, False, 301, 305, 305.0, "", " 301", "301 ", "0301", "8", "302", "３０１", b"301"]
)
def test_invalid_selector(mode, bad):
    p = Probe()
    with pytest.raises(ValueError):
        p.run(mode, "domestic", OP, SERIAL, channel_no=CHANNEL, linkage_type=bad)
    assert p.auth == p.biz == [] and outcomes(p.recorder, mode, "attempt", OP) == []


@pytest.mark.parametrize(
    "args,kwargs",
    [
        ((SERIAL,), {}),
        ((SERIAL,), {"channel_no": CHANNEL}),
        ((SERIAL,), {"linkage_type": "301"}),
        ((SERIAL, CHANNEL, "305"), {}),
        ((SERIAL,), {"channel": CHANNEL, "linkage_type": "301"}),
        ((SERIAL,), {"channel_no": CHANNEL, "type": "301"}),
    ],
)
def test_signature(mode, args, kwargs):
    p = Probe()
    with pytest.raises(TypeError):
        p.run(mode, "domestic", OP, *args, **kwargs)
    assert p.auth == p.biz == []
