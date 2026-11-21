"""Offline tests for domestic intelligent detection switches and alarm detection modes."""

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

SWITCH_GET, SWITCH_SET = "get_intelligent_detection_switch", "set_intelligent_detection_switch"
MODE_GET, MODE_SET = "get_alarm_detection_mode", "select_alarm_detection_mode"
NAMES = (SWITCH_GET, SWITCH_SET, MODE_GET, MODE_SET)
SERIAL, CHANNEL = "PRIVATE-SERIAL", "CH-PRIVATE"
OPTIONS = {
    SWITCH_GET: {"detection_type": "302"},
    SWITCH_SET: {"enabled": True, "channel_no": CHANNEL},
    MODE_GET: {},
    MODE_SET: {"detection_type": "5"},
}
SWITCH_DATA = {
    "enable": -7,
    "deviceSerial": "DEVICE_PRIVATE",
    "channelNo": 0,
    "extra": {"value": "EXT_PRIVATE", "other": None},
}
_MISS = object()


def reply(code=200, **fields):
    return {"code": str(code), "msg": "PRIVATE_UPSTREAM_MSG", **fields}


def read_data(name):
    if name in (MODE_GET, MODE_SET):
        return {"valueInfo": {"type": -7, "unknown": "EXT_PRIVATE"}, "device": "DEVICE_PRIVATE"}
    return SWITCH_DATA


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
    call, rec, seen = invoke(mode, "domestic", name, reply(10031))
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


@pytest.mark.parametrize("name", (SWITCH_SET, MODE_SET))
def test_set_invalid_token_not_retried(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply(10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed"]
    assert outcomes(rec, mode, "retry", name) == []


@pytest.mark.parametrize("name", (SWITCH_GET, MODE_GET))
def test_get_invalid_token_retry_then_success(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply(10002), reply(data=read_data(name)))
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed", "started", "succeeded"]
    refreshed(rec, mode, name, seen, "succeeded")


@pytest.mark.parametrize("name", (SWITCH_GET, MODE_GET))
def test_get_invalid_token_exhausted_raises(mode, name):
    call, rec, seen = invoke(mode, "domestic", name, reply(10002), reply(10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed", "started", "failed"]
    refreshed(rec, mode, name, seen, "failed")


def refreshed(rec, mode, name, seen, terminal):
    assert [
        dict(parse_qsl(r.content.decode()))["accessToken"]
        if name == SWITCH_GET
        else r.headers["accessToken"]
        for r in seen["biz"]
    ] == ["TOK1", "TOK2"]
    first, second = seen["biz"]
    assert (first.method, first.url) == (second.method, second.url)
    if name == SWITCH_GET:
        assert (
            first.headers["content-type"]
            == second.headers["content-type"]
            == "application/x-www-form-urlencoded"
        )
        assert sorted(parse_qsl(first.content.decode())) == [
            ("accessToken", "TOK1"),
            ("deviceSerial", SERIAL),
            ("type", "302"),
        ]
        assert sorted(parse_qsl(second.content.decode())) == [
            ("accessToken", "TOK2"),
            ("deviceSerial", SERIAL),
            ("type", "302"),
        ]
    else:
        assert "content-type" not in first.headers and "content-type" not in second.headers
        assert first.content == second.content == b""
        assert first.headers["deviceSerial"] == second.headers["deviceSerial"] == SERIAL
    assert outcomes(rec, mode, "retry", name) == ["scheduled"]
    assert outcomes(rec, mode, "call", name) == ["started", terminal]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, name, ok, caplog):
    scope = "domestic"
    payload = reply(200 if ok else "PRIVATE_BACKEND_CODE", data=read_data(name))
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
        and (
            "/intelligence/detection/switch/" in r.getMessage()
            or "/alarm/detect/switch/" in r.getMessage()
        )
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
    p = Probe(httpx.Response(status, json=reply(code)))
    with pytest.raises(exc) as error:
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert type(error.value) is exc
    assert error.value.code == "***"
    assert error.value.message == "***"
    assert len(p.auth) == len(p.biz) == 1
    assert outcomes(p.recorder, mode, "attempt", name) == ["started", "failed"]


WIRE_CASES = [
    (SWITCH_GET, {}, []),
    (SWITCH_GET, {"detection_type": None}, []),
    (SWITCH_GET, {"detection_type": "302"}, [("type", "302")]),
    (SWITCH_GET, {"detection_type": "304"}, [("type", "304")]),
    (SWITCH_SET, {"enabled": True}, [("enable", "1")]),
    (
        SWITCH_SET,
        {"enabled": False, "detection_type": "302", "channel_no": "001"},
        [("enable", "0"), ("type", "302"), ("channelNo", "001")],
    ),
    (
        SWITCH_SET,
        {"enabled": True, "detection_type": "304", "channel_no": " 01+&= 项 "},
        [("enable", "1"), ("type", "304"), ("channelNo", " 01+&= 项 ")],
    ),
    (SWITCH_SET, {"enabled": False, "detection_type": None, "channel_no": None}, [("enable", "0")]),
    (MODE_GET, {}, []),
    (MODE_SET, {"detection_type": "1"}, [("type", "1")]),
    (MODE_SET, {"detection_type": "5"}, [("type", "5")]),
]


@pytest.mark.parametrize("name,kwargs,expected_fields", WIRE_CASES)
def test_detection_wire(mode, name, kwargs, expected_fields):
    switch = name in (SWITCH_GET, SWITCH_SET)
    serial = "私+& ABC" if switch else "mIx-01"
    data = read_data(name) if name in (SWITCH_GET, MODE_GET) else {"future": [0, None]}
    payload = reply(data=data)
    probe = Probe(payload)
    res = probe.run(mode, "domestic", name, serial, **kwargs)
    (auth,) = probe.auth
    (business,) = probe.biz
    assert auth.method == "POST"
    assert str(auth.url) == "https://open.ys7.com/api/lapp/token/get"

    assert not business.url.query
    if name != MODE_GET:
        assert business.headers["content-type"] == "application/x-www-form-urlencoded"
    else:
        assert "content-type" not in business.headers
    if switch:
        assert business.method == "POST"
        suffix = "status" if name == SWITCH_GET else "set"
        assert (
            str(business.url)
            == "https://open.ys7.com/api/lapp/device/intelligence/detection/switch/" + suffix
        )
        assert not set(business.headers.keys()) - TRANSPORT_HEADERS
        fields = [("accessToken", "TOK1"), ("deviceSerial", serial), *expected_fields]
    else:
        suffix = "get" if name == MODE_GET else "set"
        assert (
            str(business.url) == "https://open.ys7.com/api/v3/device/alarm/detect/switch/" + suffix
        )
        assert set(business.headers.keys()) - TRANSPORT_HEADERS == {"accesstoken", "deviceserial"}
        assert business.headers.get_list("accesstoken") == ["TOK1"]
        assert business.headers.get_list("deviceserial") == [serial]
        assert business.method == ("GET" if name == MODE_GET else "POST")
        if name == MODE_GET:
            assert business.content == b""
        fields = expected_fields
    assert sorted(parse_qsl(business.content.decode(), keep_blank_values=True)) == sorted(fields)
    assert res.raw == payload and res.data == data and res.code == "200"
    assert res.message == "***"


INVALID_FIELDS = [
    *(
        (n, "serial", v)
        for n in NAMES
        for v in ("", " ", None, 123, 1.5, b"S", ["S"], {"s": 1}, "\ud800", "SER\ud800IAL")
    ),
    *(
        (n, "serial", v)
        for n in (SWITCH_GET, SWITCH_SET)
        for v in ("private-serial", "Private-Serial")
    ),
    *(
        (n, "serial", v)
        for n in (MODE_GET, MODE_SET)
        for v in ("私-A", "SERI\x7fL", "\tSERIAL", "SERIAL\t", " SERIAL", "SERIAL ")
    ),
    *((SWITCH_SET, "enabled", v) for v in (None, 1, 0, "1", 1.0)),
    *(
        (SWITCH_SET, "channel_no", v)
        for v in ("", " ", "CH\ud800", 123, 1.5, b"C", ["C"], {"c": 1})
    ),
]

INVALID_SIGNATURES = [
    (SWITCH_GET, (SERIAL, "302"), {}),
    (SWITCH_GET, (SERIAL,), {"channel_no": CHANNEL}),
    (SWITCH_SET, (SERIAL,), {}),
    (SWITCH_SET, (SERIAL, True), {}),
    (SWITCH_SET, (SERIAL,), {"enabled": True, "channel": CHANNEL}),
    (MODE_GET, (SERIAL, "5"), {}),
    (MODE_GET, (SERIAL,), {"detection_type": "5"}),
    (MODE_GET, (SERIAL,), {"channel_no": None}),
    (MODE_SET, (SERIAL,), {}),
    (MODE_SET, (SERIAL, "1"), {}),
    (MODE_SET, (SERIAL,), {"detection_type": "1", "enabled": True}),
    (MODE_SET, (SERIAL,), {"detection_type": "5", "channel_no": CHANNEL}),
]
BAD_DATA = [
    *((n, p) for n in (SWITCH_GET, MODE_GET) for p in (_MISS, None, [], {})),
    *((SWITCH_GET, {"enable": e}) for e in (True, False, 1.5, "1", None)),
    *(
        (SWITCH_GET, {"enable": 1, k: v})
        for k, v in (
            ("deviceSerial", 1),
            ("deviceSerial", None),
            ("channelNo", "0"),
            ("channelNo", True),
        )
    ),
    *((MODE_GET, {"valueInfo": e}) for e in (True, [], {}, "5", None)),
    *((MODE_GET, {"valueInfo": {"type": e}}) for e in (True, 1.0, "1", None)),
    (SWITCH_GET, {"valueInfo": {"type": 1}}),
    (MODE_GET, {"enable": 1}),
    (MODE_GET, {"type": 5}),
]
TYPE_NEGATIVES = [
    (method, "detection_type", value)
    for method, values in [
        (SWITCH_GET, ["1", "5", "3", "4", "701", "0302", " 302", "", 302, True, b"302", "UNKNOWN"]),
        (SWITCH_SET, ["1", "5", "3", "4", "701", "0302", " 302", "", 302, True, b"302", "UNKNOWN"]),
        (MODE_SET, [None, "302", "304", "3", "4", "701", "01", " 1", "0", "", 1, True, b"5"]),
    ]
    for value in values
]


@pytest.mark.parametrize(
    "name,data", [(SWITCH_GET, {"enable": 1}), (MODE_GET, {"valueInfo": {"type": 5}})]
)
def test_minimal_read_payload(mode, name, data):
    body = reply(data=data)
    probe = Probe(body)
    result = probe.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert result.raw == body and result.data == data and result.code == "200"
    assert len(probe.auth) == len(probe.biz) == 1


@pytest.mark.parametrize("name,field,bad", [*INVALID_FIELDS, *TYPE_NEGATIVES])
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
    p = Probe(reply(**({} if data is _MISS else {"data": data})))
    with pytest.raises(ProtocolError):
        p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.biz) == len(p.auth) == 1


@pytest.mark.parametrize("name", [SWITCH_SET, MODE_SET])
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(_MISS, id="missing"),
        pytest.param(None, id="null"),
        pytest.param({}, id="empty"),
        pytest.param({"ext": {"nested": None}, "tag": "synthetic"}, id="extension"),
    ],
)
def test_set_ack_data_variants(mode, name, data):
    body = reply() if data is _MISS else reply(data=data)
    p = Probe(body)
    res = p.run(mode, "domestic", name, SERIAL, **OPTIONS[name])
    assert len(p.auth) == len(p.biz) == 1
    assert res.data == (None if data is _MISS else data)
    assert res.raw == body


@pytest.mark.parametrize("name", [SWITCH_GET, SWITCH_SET, MODE_GET, MODE_SET])
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
    data = read_data(name)
    if bad == "other-family":
        body = {"meta": {"code": 200, "message": "private"}, "data": data}
    elif bad == "overseas":
        body = {"body": {"meta": {"code": 200, "message": "private"}, "data": data}}
    else:
        body = {"code": 200 if bad == "wrong-success-type" else bad, "msg": "private", "data": data}
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
