"""Offline tests for the two domestic device sensitivity APIs."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_light_linkage import outcomes
from test_sdk_motion_area import TRANSPORT_HEADERS, Probe, reply

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

MGET, MSET = "get_motion_sensitivity", "set_motion_sensitivity"
DGET, DSET = "get_detection_sensitivity", "set_detection_sensitivity"
NAMES = (MGET, MSET, DGET, DSET)
SERIAL, CHANNEL = "PRIVATE-SERIAL", "001"
OPTIONS = {
    MGET: {},
    MSET: {"value": 6},
    DGET: {"channel_no": CHANNEL},
    DSET: {"channel_no": CHANNEL, "detection_type": "4", "value": "001"},
}


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
    call, rec, seen = invoke(mode, "domestic", name, reply("domestic", 10031))
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert str(err.value.code) == "10031" and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", NAMES)
def test_generic_business_code_api_error(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply("domestic", 60020))
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert excinfo.value.http_status == 200


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


@pytest.mark.parametrize("name", (MSET, DSET))
def test_set_invalid_token_not_retried(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply("domestic", 10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed"]
    assert outcomes(rec, mode, "retry", name) == []


@pytest.mark.parametrize("name", (MGET, DGET))
def test_get_invalid_token_retry_then_success(mode, name):
    call, rec, seen = invoke(
        mode, "domestic", name, reply("domestic", 10002), reply("domestic", data=[])
    )
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed", "started", "succeeded"]
    refreshed(rec, mode, name, seen, "succeeded")


@pytest.mark.parametrize("name", (MGET, DGET))
def test_get_invalid_token_exhausted_raises(mode, name):
    call, rec, seen = invoke(
        mode, "domestic", name, reply("domestic", 10002), reply("domestic", 10002)
    )
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
    assert outcomes(rec, mode, "retry", name) == ["scheduled"]
    assert outcomes(rec, mode, "call", name) == ["started", terminal]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, name, ok, caplog):
    scope = "domestic"
    payload = reply(
        scope,
        200 if ok else "PRIVATE_BACKEND_CODE",
        data=[{"channel": 1, "type": "UNKNOWN", "value": "900", "extra": "EXT_PRIVATE"}],
    )
    node = payload
    node["msg"] = "PRIVATE_UPSTREAM_MSG"
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
        r.getMessage() for r in caplog.records if r.name == "httpx" and "/config/" in r.getMessage()
    ]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        SERIAL,
        "TOK1",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob


@pytest.mark.parametrize(
    "name,kw",
    [
        (MGET, {}),
        (MSET, {"value": 0}),
        (MSET, {"value": 6, "channel_no": None}),
        (MSET, {"value": 0, "channel_no": 0}),
        (MSET, {"value": 6, "channel_no": -2}),
        (DGET, {"channel_no": CHANNEL}),
    ]
    + [
        (DSET, {"channel_no": CHANNEL, "detection_type": t, "value": v})
        for t in ("3", "4")
        for v in ("001", "100", "0" * 5000 + "1")
    ],
)
def test_detection_sensitivity_wire_success(mode, name, kw):
    if name == MGET:
        data = [
            {"channel": "01", "type": "0", "value": 3},
            {"channel": 1, "type": "0", "value": "3"},
            {"channel": "9", "type": "99", "value": "-42", "extra": {"nested": []}},
        ]
    elif name == DGET:
        data = [
            {"channel": 1, "type": "3", "value": "5"},
            {"channel": 1, "type": "4", "value": "100"},
            {"channel": 1, "type": "77", "value": "-42", "future": {"nested": [0]}},
        ]
    else:
        data = {"extra": {"opaque": [1, "z", None]}, "blob": {"a": {"b": 2}}}
    body = reply("domestic", data=data)
    p = Probe(body)
    res = p.run(mode, "domestic", name, SERIAL, **kw)
    assert res.raw == body and res.data == body["data"] and res.code == "200"
    (auth,), (biz,) = p.auth, p.biz
    assert auth.url.path == "/api/lapp/token/get"
    assert (
        (auth.url.scheme, auth.url.host)
        == (biz.url.scheme, biz.url.host)
        == ("https", "open.ys7.com")
    )
    assert not biz.url.query
    if name in (MGET, MSET):
        form = [("accessToken", "TOK1"), ("deviceSerial", SERIAL)]
        if name == MSET:
            form += [("type", "0"), ("value", str(kw["value"]))]
            if kw.get("channel_no") is not None:
                form.append(("channelNo", str(kw["channel_no"])))
        sent = sorted(parse_qsl(biz.content.decode()))
        assert biz.method == "POST" and set(biz.headers.keys()) <= TRANSPORT_HEADERS
        assert biz.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert biz.url.path == "/api/lapp/device/algorithm/config/" + (
            "get" if name == MGET else "set"
        )
        assert sent == sorted(form) and "channel" not in dict(sent)
    else:
        assert set(biz.headers.keys()) - TRANSPORT_HEADERS == {
            "accesstoken",
            "deviceserial",
            "channelno",
        }
        assert (
            biz.headers["accesstoken"],
            biz.headers["deviceserial"],
            biz.headers["channelno"],
        ) == ("TOK1", SERIAL, kw["channel_no"])
        assert biz.url.path == "/api/v3/device/detect/config/" + ("get" if name == DGET else "set")
        if name == DGET:
            assert biz.method == "GET" and biz.content == b"" and "content-type" not in biz.headers
        else:
            assert biz.method == "POST"
            assert biz.headers["content-type"].startswith("application/x-www-form-urlencoded")
            assert sorted(parse_qsl(biz.content.decode())) == sorted(
                [("type", kw["detection_type"]), ("value", kw["value"])]
            )


@pytest.mark.parametrize("name", list(NAMES))
def test_detection_sensitivity_wire_absent_data(mode, name):
    body = reply("domestic", data=[])
    if name in (MSET, DSET):
        del body["data"]
    p = Probe(body)
    res = p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert res.raw == body and res.code == "200" and len(p.auth) == len(p.biz) == 1
    if name in (MGET, DGET):
        assert res.data == []
    else:
        assert res.data is None


_MISS = object()

INVALID_FIELDS = [
    *[(n, "serial", s) for n in NAMES for s in (None, "", "\ud800")],
    *[(n, "serial", SERIAL.lower()) for n in (MGET, MSET)],
    *[(n, "serial", "私") for n in (DGET, DSET)],
    *[
        (n, f, s)
        for n in (DGET, DSET)
        for f in ("serial", "channel_no")
        for s in ("\x00x", "x\x7f", " x", "x ")
    ],
    *[(n, "channel_no", c) for n in (DGET, DSET) for c in (None, True, 1, 1.0)],
    *[(MSET, "value", v) for v in (-1, 7, True, 1.5, "3", None)],
    *[(MSET, "channel_no", c) for c in (True, 1.0, "1")],
    *[(DSET, "detection_type", t) for t in (3, True, "0", "03")],
    *[
        (DSET, "value", v)
        for v in ("", " ", 3, True, "0", "101", "1 ", "١", "１", "+1", "1.0", "-1", "1٠")
    ],
    pytest.param(DSET, "value", "9" * 5000, id="too-large"),
]

INVALID_SIGNATURES = [
    (MGET, (SERIAL, CHANNEL), {}),
    (MGET, (SERIAL,), {"channel_no": CHANNEL}),
    (MSET, (SERIAL,), {}),
    (MSET, (SERIAL, 3, CHANNEL), {}),
    (MSET, (SERIAL,), {"value": 3, "type": 0}),
    (DGET, (SERIAL,), {}),
    (DGET, (SERIAL, CHANNEL, "x"), {}),
    (DSET, (SERIAL,), {}),
    (DSET, (SERIAL, CHANNEL), {}),
    (DSET, (SERIAL, CHANNEL, "3"), {}),
    (DSET, (SERIAL,), {"value": "3", "detection_type": "4"}),
    (DSET, (SERIAL,), {"channel_no": CHANNEL, "detection_type": "4"}),
    (DSET, (SERIAL,), {"channel_no": CHANNEL, "value": "3"}),
]

BAD_DATA = [
    *[(n, d) for n in (MGET, DGET) for d in (_MISS, None, {}, [None], [{}])],
    *[
        (n, [r])
        for n in (MGET, DGET)
        for r in [
            {"type": "0", "value": "1"},
            {"channel": 1, "value": "1"},
            {"channel": 1, "type": "0"},
            {"channel": True, "type": "0", "value": "1"},
            {"channel": 1, "type": 0, "value": "1"},
            *({"channel": 1, "type": "0", "value": v} for v in (True, 1.5, ["1"], {"v": 1})),
        ]
    ],
    *[
        (MGET, [r])
        for r in [
            *({"channel": c, "type": "0", "value": "1"} for c in (None, 1.5, [CHANNEL], {"c": 1})),
            {"channel": CHANNEL, "type": "0", "value": None},
        ]
    ],
    *[
        (DGET, [r])
        for r in [
            {"channel": CHANNEL, "type": "0", "value": "1"},
            {"channel": None, "type": "0", "value": "1"},
            {"channel": 1.5, "type": "0", "value": "1"},
            {"channel": 1, "type": "0", "value": 1},
            {"channel": 1, "type": "0", "value": None},
        ]
    ],
]


@pytest.mark.parametrize("name", NAMES)
def test_serial_policy(mode, name):
    serial = "私+& ABC" if name in (MGET, MSET) else "mIx-01"
    p = Probe(reply("domestic", data=[]))
    p.run(mode, "domestic", name, serial, **OPTIONS[name])
    (req,) = p.biz
    if name in (MGET, MSET):
        assert dict(parse_qsl(req.content.decode()))["deviceSerial"] == serial
    else:
        assert req.headers["deviceSerial"] == serial


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
    p = Probe(reply("domestic", **({} if data is _MISS else {"data": data})))
    with pytest.raises(ProtocolError):
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.biz) == len(p.auth) == 1


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body",
    [
        {"code": 200, "data": []},
        {"meta": {"code": 200}, "data": []},
        {"body": {"meta": {"code": 200}, "data": []}},
    ],
)
def test_invalid_envelope(mode, name, body):
    p = Probe(body)
    with pytest.raises((ApiError, ProtocolError)):
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.biz) == 1


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
