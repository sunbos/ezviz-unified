"""Offline tests for domestic motion and human tracking switches."""

import asyncio
from urllib.parse import parse_qsl

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

MGET, MSET = "get_motion_tracking", "set_motion_tracking"
HGET, HSET = "get_human_tracking", "set_human_tracking"
NAMES = (MGET, MSET, HGET, HSET)
SERIAL, CHANNEL = "PRIVATE-SERIAL", "CH-PRIVATE"
OPTIONS = {
    MGET: {},
    MSET: {"enabled": True, "channel_no": CHANNEL},
    HGET: {},
    HSET: {"enabled": False},
}
DATA = {
    "enable": -7,
    "deviceSerial": "DEVICE_PRIVATE",
    "channelNo": 0,
    "extra": {"value": "EXT_PRIVATE", "other": None},
}
_MISS = object()


def reply(name, code=200, **fields):
    if name in (MGET, MSET):
        return {"code": str(code), "msg": "PRIVATE_UPSTREAM_MSG", **fields}
    return {"meta": {"code": code, "message": "PRIVATE_UPSTREAM_MSG"}, **fields}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def invoke(mode, scope, name, *steps):
    p = Probe(*steps)
    return (
        (lambda: p.run(mode, scope, name, SERIAL, **OPTIONS[name])),
        p.recorder,
        {"auth": p.auth, "biz": p.biz},
    )


@pytest.mark.parametrize("name", NAMES)
def test_overseas_region_rejected(mode, name):
    cases = [(SERIAL, OPTIONS[name]), (object(), dict.fromkeys(OPTIONS[name], object()))]
    for serial, kw in cases:
        p = Probe()
        with pytest.raises(
            ValueError, match="^this operation is only supported in the domestic region$"
        ) as exc:
            p.run(mode, "overseas", name, serial, **kw)
        assert str(exc.value) == "this operation is only supported in the domestic region"
        assert p.auth == p.biz == []
        events = p.recorder.report()["events"]
        calls = [
            e
            for e in events
            if e["kind"] == "call" and e["entrypoint"] == "settings." + name and e["mode"] == mode
        ]
        assert [e["outcome"] for e in calls] == ["started", "failed"]
        assert not [e for e in events if e["kind"] == "attempt"]
        assert not [e for e in events if e["kind"] in ("auth", "business")]


@pytest.mark.parametrize("name", NAMES)
def test_permission_denied_10031(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply(name, 10031))
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert str(err.value.code) == "10031" and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", NAMES)
def test_read_timeout_maps_transport_error(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, httpx.ReadTimeout("synthetic"))
    with pytest.raises(TransportError) as excinfo:
        call()
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", NAMES)
def test_async_cancellation_propagates(name):
    call, rec, seen = invoke("async", "domestic", name, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call()
    assert outcomes(rec, "async", "call", name) == ["started", "cancelled"]
    assert outcomes(rec, "async", "attempt", name) == ["started", "cancelled"]
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", (MSET, HSET))
def test_set_invalid_token_not_retried(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply(name, 10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed"]
    assert outcomes(rec, mode, "retry", name) == []


@pytest.mark.parametrize("name", (MGET, HGET))
def test_get_invalid_token_retry_then_success(mode, name):
    call, rec, seen = invoke(
        mode, "domestic", name, reply(name, 10002), reply(name, data={"enable": 1})
    )
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed", "started", "succeeded"]
    refreshed(rec, mode, name, seen, "succeeded")


@pytest.mark.parametrize("name", (MGET, HGET))
def test_get_invalid_token_exhausted_raises(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply(name, 10002), reply(name, 10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed", "started", "failed"]
    refreshed(rec, mode, name, seen, "failed")


def refreshed(rec, mode, name, seen, terminal):
    assert [
        dict(parse_qsl(r.content.decode()))["accessToken"]
        if name == MGET
        else r.headers["accessToken"]
        for r in seen["biz"]
    ] == ["TOK1", "TOK2"]
    first, second = seen["biz"]
    assert (first.method, first.url) == (second.method, second.url)
    assert (
        first.headers["Content-Type"]
        == second.headers["Content-Type"]
        == "application/x-www-form-urlencoded"
    )
    if name == MGET:
        assert sorted(parse_qsl(first.content.decode())) == [
            ("accessToken", "TOK1"),
            ("deviceSerial", SERIAL),
        ]
        assert sorted(parse_qsl(second.content.decode())) == [
            ("accessToken", "TOK2"),
            ("deviceSerial", SERIAL),
        ]
    else:
        assert first.content == second.content == b""
        assert first.headers["deviceSerial"] == second.headers["deviceSerial"] == SERIAL
    assert outcomes(rec, mode, "retry", name) == ["scheduled"]
    assert outcomes(rec, mode, "call", name) == ["started", terminal]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, name, ok, caplog):
    scope = "domestic"
    payload = reply(name, 200 if ok else "PRIVATE_BACKEND_CODE", data=DATA)
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(wire)
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.run(mode, scope, name, SERIAL, **OPTIONS[name])
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.run(mode, scope, name, SERIAL, **OPTIONS[name])
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx"
        and ("/mobile/status/" in r.getMessage() or "/switch/human/" in r.getMessage())
    ]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        SERIAL,
        "TOK1",
        CHANNEL,
        "PRIVATEKEY",
        "PRIVATESECRET",
        "DEVICE_PRIVATE",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob


@pytest.mark.parametrize("name", NAMES)
def test_closed_client_never_authenticates(mode, name):
    async def scenario():
        client, auth_calls, origins = make_client(mode, lambda r: pytest.fail("unexpected request"))
        await close(client)
        await close(client)
        assert client.is_closed
        with pytest.raises(TransportError, match="closed"):
            await resolve(getattr(client.settings, name)(SERIAL, **OPTIONS[name]))
        assert auth_calls == origins == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code,exc",
    [(403, 20018, PermissionDeniedError), (200, 20018, ApiError), (200, 60020, ApiError)],
)
@pytest.mark.parametrize("name", NAMES)
def test_nonretryable_business_failures(mode, name, status, code, exc):
    p = Probe(httpx.Response(status, json=reply(name, code)))
    with pytest.raises(exc) as error:
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert type(error.value) is exc
    assert error.value.code == ("***" if name in (MGET, MSET) else code)
    assert error.value.message == "***"
    assert len(p.auth) == len(p.biz) == 1
    assert outcomes(p.recorder, mode, "attempt", name) == ["started", "failed"]


@pytest.mark.parametrize(
    "name,kwargs",
    [
        (MGET, {}),
        (HGET, {}),
        (MSET, {"enabled": True, "channel_no": " 001+%&= 项 "}),
        (MSET, {"enabled": False}),
        (MSET, {"enabled": True, "channel_no": None}),
        (MSET, {"enabled": False, "channel_no": "001"}),
        (HSET, {"enabled": True}),
        (HSET, {"enabled": False}),
    ],
)
def test_tracking_wire(mode, name, kwargs):
    motion = name in (MGET, MSET)
    serial = "私+& ABC" if motion else "mIx-01"
    data = DATA if name in (MGET, HGET) else {"future": [0, None]}
    payload = reply(name, data=data)
    probe = Probe(payload)
    res = probe.run(mode, "domestic", name, serial, **kwargs)
    (auth,) = probe.auth
    (business,) = probe.biz
    assert auth.method == "POST"
    assert str(auth.url) == "https://open.ys7.com/api/lapp/token/get"

    assert not business.url.query
    assert business.headers["content-type"] == "application/x-www-form-urlencoded"
    if motion:
        assert business.method == "POST"
        suffix = "get" if name == MGET else "set"
        assert str(business.url) == "https://open.ys7.com/api/lapp/device/mobile/status/" + suffix
        assert not set(business.headers.keys()) - TRANSPORT_HEADERS
        expected = [("accessToken", "TOK1"), ("deviceSerial", serial)]
        if name == MSET:
            expected.append(("enable", "1" if kwargs["enabled"] else "0"))
            channel = kwargs.get("channel_no", _MISS)
            if channel is not _MISS and channel is not None:
                expected.append(("channelNo", channel))
        assert sorted(parse_qsl(business.content.decode(), keep_blank_values=True)) == sorted(
            expected
        )
    else:
        assert str(business.url) == "https://open.ys7.com/api/v3/device/switch/human/track"
        assert set(business.headers.keys()) - TRANSPORT_HEADERS == {"accesstoken", "deviceserial"}
        assert business.headers.get_list("accesstoken") == ["TOK1"]
        assert business.headers.get_list("deviceserial") == [serial]
        if name == HGET:
            assert business.method == "GET"
            assert business.content == b""
        else:
            assert business.method == "PUT"
            assert sorted(parse_qsl(business.content.decode(), keep_blank_values=True)) == [
                ("enable", "1" if kwargs["enabled"] else "0")
            ]

    assert res.raw == payload
    assert res.data == data
    assert res.code == ("200" if motion else 200)
    assert res.message == "***"


INVALID_FIELDS = [
    *(
        (n, "serial", v)
        for n in NAMES
        for v in ("", " ", None, 123, 1.5, b"S", ["S"], {"s": 1}, "\ud800", "SER\ud800IAL")
    ),
    *((n, "serial", v) for n in (MGET, MSET) for v in ("private-serial", "Private-Serial")),
    *(
        (n, "serial", v)
        for n in (HGET, HSET)
        for v in ("私-A", "SERI\x7fL", "\tSERIAL", "SERIAL\t", " SERIAL", "SERIAL ")
    ),
    *((n, "enabled", v) for n in (MSET, HSET) for v in (None, 1, 0, "1", 1.0)),
    *((MSET, "channel_no", v) for v in ("", " ", "CH\ud800", 123, 1.5, b"C", ["C"], {"c": 1})),
]

INVALID_SIGNATURES = [
    (MSET, (SERIAL,), {"channel_no": CHANNEL}),
    (MSET, (SERIAL, True), {"channel_no": CHANNEL}),
    (HSET, (SERIAL,), {}),
    (HSET, (SERIAL, False), {}),
    (MGET, (SERIAL,), {"channel_no": CHANNEL}),
    (HGET, (SERIAL,), {"channel_no": CHANNEL}),
    (HSET, (SERIAL,), {"enabled": True, "channel_no": CHANNEL}),
    (MGET, (SERIAL,), {"enabled": True}),
    (HGET, (SERIAL,), {"enabled": False}),
    (MSET, (SERIAL,), {"enabled": True, "channel": CHANNEL}),
    (MGET, (SERIAL, "EXTRA"), {}),
    (HGET, (SERIAL, "EXTRA"), {}),
]

BAD_DATA = [
    *((n, p) for n in (MGET, HGET) for p in (_MISS, None, [], {})),
    *((n, {"enable": e}) for n in (MGET, HGET) for e in (True, False, 1.5, "1", None)),
    *(
        (n, {"enable": 1, "deviceSerial": v})
        for n in (MGET, HGET)
        for v in (123, 1.5, None, ["S"], {"s": 1}, True)
    ),
    *(
        (n, {"enable": 1, "channelNo": v})
        for n in (MGET, HGET)
        for v in ("0", "CH", 1.5, [0], {"c": 0}, True)
    ),
]


@pytest.mark.parametrize("name,field,bad", INVALID_FIELDS)
def test_invalid_input(mode, name, field, bad):
    kw = OPTIONS[name] if field == "serial" else {**OPTIONS[name], field: bad}
    p = Probe()
    with pytest.raises(ValueError):
        p.run(mode, "domestic", name, bad if field == "serial" else SERIAL, **kw)
    assert p.auth == p.biz == []


@pytest.mark.parametrize("name,args,kw", INVALID_SIGNATURES)
def test_invalid_signature(mode, name, args, kw):
    p = Probe()
    with pytest.raises(TypeError):
        p.run(mode, "domestic", name, *args, **kw)
    assert p.auth == p.biz == []


@pytest.mark.parametrize("name,data", BAD_DATA)
def test_invalid_read_data(mode, name, data):
    p = Probe(reply(name, **({} if data is _MISS else {"data": data})))
    with pytest.raises(ProtocolError):
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.biz) == len(p.auth) == 1


@pytest.mark.parametrize("name", [MSET, HSET])
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(_MISS, id="missing"),
        pytest.param(None, id="null"),
        pytest.param({}, id="empty"),
        pytest.param({"ext": {"nested": None}, "tag": "sp550"}, id="extension"),
    ],
)
def test_set_ack_data_variants(mode, name, data):
    body = reply(name) if data is _MISS else reply(name, data=data)
    p = Probe(body)
    res = p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.auth) == len(p.biz) == 1
    assert res.data == (None if data is _MISS else data)
    assert res.raw == body


@pytest.mark.parametrize("name", [MGET, MSET, HGET, HSET])
@pytest.mark.parametrize(
    "bad,expected",
    [
        pytest.param("wrong-success-type", ApiError, id="wrong-success-type"),
        pytest.param(200.0, ProtocolError, id="float-code"),
        pytest.param(True, ProtocolError, id="bool-code"),
        pytest.param([], ProtocolError, id="list-code"),
        pytest.param("other-family", ProtocolError, id="other-family-envelope"),
        pytest.param("overseas", ProtocolError, id="overseas-body-meta"),
    ],
)
def test_bad_envelope_code_type_or_family(mode, name, bad, expected):
    data = {"enable": 1}
    if bad == "other-family":
        body = (
            {"meta": {"code": 200, "message": "private"}, "data": data}
            if name in (MGET, MSET)
            else {"code": "200", "msg": "private", "data": data}
        )
    elif bad == "overseas":
        body = {"body": {"meta": {"code": 200, "message": "private"}, "data": data}}
    elif name in (MGET, MSET):
        body = {
            "code": 200 if bad == "wrong-success-type" else bad,
            "msg": "private",
            "data": data,
        }
    else:
        body = {
            "meta": {
                "code": "200" if bad == "wrong-success-type" else bad,
                "message": "private",
            },
            "data": data,
        }
    p = Probe(body)
    with pytest.raises(expected) as exc:
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.auth) == len(p.biz) == 1
    assert type(exc.value) is expected


@pytest.mark.parametrize("name", NAMES)
def test_auth_failure(mode, name):
    p = Probe()

    def deny(request):
        p.auth.append(request)
        return httpx.Response(403, json={"code": "10031", "msg": "PRIVATE_DENIAL"})

    p._auth = deny
    with pytest.raises(ApiError) as exc:
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.auth) == 1
    assert p.biz == []
    blob = repr(exc.value) + " " + str(exc.value)
    assert SERIAL not in blob
    assert "TOK1" not in blob
