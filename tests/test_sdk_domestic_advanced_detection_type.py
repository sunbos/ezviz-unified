"""Offline tests for the domestic advanced alarm detection type query."""

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

OP = "get_advanced_alarm_detection_type"
PATH = "/api/v3/das/device/detect/switch/get"
SERIAL = "mIxEd-PRIVATE-SERIAL"
DATA = {"valueInfo": {"type": -7, "future": "EXT_PRIVATE"}, "device": "DEVICE_PRIVATE"}
_MISS = object()


def reply(code=200, **fields):
    return {"code": str(code), "msg": "PRIVATE_UPSTREAM_MSG", **fields}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def invoke(mode, *steps):
    p = Probe(*steps)
    return lambda: p.run(mode, "domestic", OP, SERIAL), p.recorder, {"auth": p.auth, "biz": p.biz}


def test_overseas_region_rejected(mode):
    for serial in (SERIAL, object()):
        p = Probe()
        with pytest.raises(
            ValueError, match="^this operation is only supported in the domestic region$"
        ) as exc:
            p.run(mode, "overseas", OP, serial)
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
            out = probe.run(mode, scope, OP, SERIAL)
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.run(mode, scope, OP, SERIAL)
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
        "DEVICE_PRIVATE",
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
            await resolve(getattr(client.settings, OP)(SERIAL))
        assert auth_calls == origins == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code,exc",
    [(403, 20018, PermissionDeniedError), (200, 20018, ApiError), (200, 60020, ApiError)],
)
def test_nonretryable_business_failures(mode, status, code, exc):
    p = Probe(httpx.Response(status, json=reply(code)))
    with pytest.raises(exc) as error:
        p.run(mode, "domestic", OP, SERIAL)
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
        p.run(mode, "domestic", OP, SERIAL)
    assert len(p.auth) == 1
    assert p.biz == []
    blob = repr(exc.value) + " " + str(exc.value)
    assert SERIAL not in blob
    assert "TOK1" not in blob


def refreshed(rec, mode, seen, terminal):
    first, second = seen["biz"]
    assert (first.headers["accessToken"], second.headers["accessToken"]) == ("TOK1", "TOK2")
    assert first.url == second.url and first.url.path == PATH and not first.url.query
    for req in (first, second):
        assert req.method == "GET" and req.content == b"" and "content-type" not in req.headers
        assert req.headers["deviceSerial"] == SERIAL
    assert outcomes(rec, mode, "retry", OP) == ["scheduled"]
    assert outcomes(rec, mode, "call", OP) == ["started", terminal]


@pytest.mark.parametrize("value", [0, 4, 8, -9, 2**48])
def test_read_wire(mode, value):
    data = {"valueInfo": {"type": value, "future": ["EXT_PRIVATE", None]}, "unknown": None}
    body = reply(data=data, extra={"opaque": True})
    probe = Probe(body)
    result = probe.run(mode, "domestic", OP, SERIAL)
    assert (
        result.code == "200"
        and result.raw == body
        and result.data == data
        and result.message == "***"
    )
    (auth,), (request,) = probe.auth, probe.biz
    assert (
        auth.url.scheme == request.url.scheme == "https"
        and auth.url.host == request.url.host == "open.ys7.com"
    )
    assert auth.method == "POST" and auth.url.path == "/api/lapp/token/get"
    assert (
        request.method == "GET"
        and request.url.path == PATH
        and request.url.query == request.content == b""
    )
    assert "content-type" not in request.headers
    assert set(request.headers) - TRANSPORT_HEADERS == {"accesstoken", "deviceserial"}
    assert request.headers.get_list("accessToken") == ["TOK1"] and request.headers.get_list(
        "deviceSerial"
    ) == [SERIAL]


BAD_DATA = [
    _MISS,
    None,
    {},
    [],
    {"type": 4},
    {"enable": 1},
    {"valueInfo": None},
    {"valueInfo": "4"},
    {"valueInfo": [4]},
    {"valueInfo": {}},
    {"valueInfo": {"type": True}},
    {"valueInfo": {"type": False}},
    {"valueInfo": {"type": "4"}},
    {"valueInfo": {"type": 4.0}},
    {"valueInfo": {"type": None}},
    {"valueInfo": {"type": [8]}},
    {"valueInfo": {"type": {}}},
    {"type": 4, "valueInfo": {"other": 8}},
]


@pytest.mark.parametrize("data", BAD_DATA)
def test_data_rejected(mode, data):
    p = Probe(reply(**({} if data is _MISS else {"data": data})))
    with pytest.raises(ProtocolError):
        p.run(mode, "domestic", OP, SERIAL)
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
    p = Probe({**body, "data": {"valueInfo": {"type": 4}}})
    with pytest.raises(error) as exc:
        p.run(mode, "domestic", OP, SERIAL)
    assert type(exc.value) is error and len(p.auth) == len(p.biz) == 1


@pytest.mark.parametrize(
    "bad",
    [None, False, 1, b"x", "", " ", " leading", "trailing ", "x\x00y", "x\x7fy", "中", "\ud800"],
)
def test_bad_serial(mode, bad):
    p = Probe()
    with pytest.raises(ValueError):
        p.run(mode, "domestic", OP, bad)
    assert p.auth == p.biz == [] and outcomes(p.recorder, mode, "attempt", OP) == []


@pytest.mark.parametrize(
    "args,kwargs",
    [
        ((SERIAL, 1), {}),
        ((SERIAL,), {"channel_no": None}),
        ((SERIAL,), {"detection_type": "8"}),
        ((), {}),
    ],
)
def test_signature(mode, args, kwargs):
    p = Probe()
    with pytest.raises(TypeError):
        p.run(mode, "domestic", OP, *args, **kwargs)
    assert p.auth == p.biz == []
