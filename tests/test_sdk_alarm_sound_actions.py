import asyncio
import logging

import httpx
import pytest
from test_sdk_device_utc import API_HOST, AUTH_HOST, TOKEN, _client

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

SERIAL = "MiXeD A-1"
PLAY = "play_audition"
SET = "set_alarm_sound_mode"
DEFAULTS = {SET: {"mode": 1}, PLAY: {"voice_index": 201}}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def env(code=200, **fields):
    return {"meta": {"code": 10002}, "body": {"meta": {"code": code, "message": "ok"}, **fields}}


def invoke(mode, name, *replies, serial=SERIAL, region=None, kwargs=None, positional=()):
    steps = iter(replies)

    def biz(request):
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json=step)

    client, rec, seen = _client(mode, biz=biz, region=region)
    kw = kwargs if kwargs is not None else DEFAULTS[name]
    if mode == "async":

        async def call():
            async with client as c:
                return await getattr(c.settings, name)(serial, *positional, **kw)

        return lambda: asyncio.run(call()), rec, seen

    def call():
        with client as c:
            return getattr(c.settings, name)(serial, *positional, **kw)

    return call, rec, seen


def outcomes(rec, mode, kind, name):
    ep = "settings." + name
    return [
        e.get("outcome")
        for e in rec.report()["events"]
        if e.get("mode") == mode
        and e.get("kind") == kind
        and (
            e.get("entrypoint") == ep
            if kind == "call"
            else e.get("operation") == ep and e.get("entrypoint") == "client.request"
        )
    ]


def _check_seen(seen):
    assert len(seen["auth"]) == 1
    assert seen["auth"][0].url.host == AUTH_HOST
    assert len(seen["biz"]) == 1
    return seen["biz"][0]


@pytest.mark.parametrize("name", [PLAY, SET])
@pytest.mark.parametrize("serial", ["", "SN序", "SN\x1f", "SN\x7f", " SN123", "SN123 "])
def test_serial_rejected(mode, name, serial):
    call, rec, seen = invoke(mode, name, serial=serial)
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", name) == []


@pytest.mark.parametrize("name", [PLAY, SET])
def test_china_region_domestic_rejected_before_auth(mode, name):
    call, rec, seen = invoke(mode, name, env(), region=RegionConfig.china())
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "call", name) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", name) == []


@pytest.mark.parametrize("name", [SET, PLAY])
def test_invalid_token_not_retried(mode, name):
    call, rec, seen = invoke(mode, name, env(10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed"]


@pytest.mark.parametrize("name", [PLAY, SET])
@pytest.mark.parametrize(
    "bad,exc", [("200", ApiError), (200.0, ProtocolError), (True, ProtocolError)]
)
def test_meta_code_wrong_type_rejected(mode, name, bad, exc):
    payload = env()
    payload["body"]["meta"]["code"] = bad
    call, rec, seen = invoke(mode, name, payload)
    with pytest.raises(exc):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [PLAY, SET])
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"code": 200}},
        {"code": 200},
        {"meta": {"code": 10002}, "body": {}},
    ],
)
def test_missing_body_meta_protocol_error(mode, name, payload):
    call, rec, seen = invoke(mode, name, payload)
    with pytest.raises(ProtocolError) as excinfo:
        call()
    assert excinfo.value.http_status == 200
    assert excinfo.value.code is None
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [PLAY, SET])
def test_permission_denied_10031(mode, name):
    call, rec, seen = invoke(mode, name, env(10031))
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert err.value.code == 10031 and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [PLAY, SET])
def test_generic_business_code_api_error(mode, name):
    call, rec, seen = invoke(mode, name, env(60020))
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert excinfo.value.code == 60020
    assert excinfo.value.http_status == 200


@pytest.mark.parametrize("name", [PLAY, SET])
def test_read_timeout_maps_transport_error(mode, name):
    call, rec, seen = invoke(mode, name, httpx.ReadTimeout("synthetic"))
    with pytest.raises(TransportError) as excinfo:
        call()
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [PLAY, SET])
def test_async_cancellation_propagates(name):
    call, rec, seen = invoke("async", name, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call()
    assert outcomes(rec, "async", "call", name) == ["started", "cancelled"]
    assert outcomes(rec, "async", "attempt", name) == ["started", "cancelled"]
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [PLAY, SET])
def test_forbidden(mode, name):
    call, rec, seen = invoke(mode, name, httpx.Response(403, text="denied"))
    with pytest.raises(PermissionDeniedError) as ei:
        call()
    assert ei.value.http_status == 403
    assert outcomes(rec, mode, "call", name) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed"]
    _check_seen(seen)


def _wire(req, path, want):
    assert req.method == "POST"
    assert req.url.scheme == "https" and req.url.host == API_HOST
    assert req.url.path == path
    h = req.headers
    assert h.get_list("accessToken") == [TOKEN] and h.get_list("deviceSerial") == [SERIAL]
    assert req.content == b"" and "content-type" not in h
    for k in (
        "authorization",
        "channelno",
        "type",
        "enable",
        "verifycode",
        "voiceindex",
        "volume",
        "mode",
    ):
        assert k not in h
    assert sorted(req.url.params.multi_items()) == sorted(want)


def _done(rec, mode, name):
    assert outcomes(rec, mode, "attempt", name) == ["started", "succeeded"]
    assert outcomes(rec, mode, "call", name) == ["started", "succeeded"]


@pytest.mark.parametrize(
    "name,path,kwargs,positional,query,fields",
    [
        (SET, "/api/v3/device/alarmSound/enabled/set", {}, (0,), [("type", "0")], {}),
        (
            SET,
            "/api/v3/device/alarmSound/enabled/set",
            {"mode": 1},
            (),
            [("type", "1")],
            {"data": {"extension": ["RAW-ALARM", {"value": 3}], "unknown": None}},
        ),
        (
            SET,
            "/api/v3/device/alarmSound/enabled/set",
            {},
            (2,),
            [("type", "2")],
            {"data": {"extension": ["RAW-ALARM", {"value": 3}], "unknown": None}},
        ),
        (PLAY, "/api/v3/device/audition", {}, (200,), [("voiceIndex", "200")], {}),
        (
            PLAY,
            "/api/v3/device/audition",
            {"voice_index": 201, "volume": None},
            (),
            [("voiceIndex", "201")],
            {},
        ),
        (
            PLAY,
            "/api/v3/device/audition",
            {"volume": 1},
            (202,),
            [("voiceIndex", "202"), ("volume", "1")],
            {"data": {"extension": ["RAW-ALARM", {"value": 3}], "unknown": None}},
        ),
        (
            PLAY,
            "/api/v3/device/audition",
            {"voice_index": 200, "volume": 50},
            (),
            [("voiceIndex", "200"), ("volume", "50")],
            {"data": {"extension": ["RAW-ALARM", {"value": 3}], "unknown": None}},
        ),
        (
            PLAY,
            "/api/v3/device/audition",
            {"volume": 100},
            (201,),
            [("voiceIndex", "201"), ("volume", "100")],
            {},
        ),
    ],
)
def test_action(mode, name, path, kwargs, positional, query, fields):
    r = env(**fields)
    call, rec, seen = invoke(mode, name, r, kwargs=kwargs, positional=positional)
    res = call()
    _wire(_check_seen(seen), path, query)
    assert res.code == 200 and res.raw == r and res.data == fields.get("data")
    _done(rec, mode, name)


CASES = (
    [(SET, {"mode": v}, (), ValueError) for v in (-1, 3, False, True, 1.0, "1", None)]
    + [
        (PLAY, {"voice_index": v}, (), ValueError)
        for v in (0, 2, 199, 203, True, 200.0, "200", None)
    ]
    + [
        (PLAY, {"voice_index": 200, "volume": v}, (), ValueError)
        for v in (0, 101, -1, True, 1.0, "1")
    ]
    + [(name, {}, (), TypeError) for name in (SET, PLAY)]
    + [(PLAY, {}, (200, 50), TypeError)]
    + [
        (name, {**base, extra: 1}, (), TypeError)
        for name, base in ((SET, {"mode": 0}), (PLAY, {"voice_index": 200}))
        for extra in ("channel_no", "verify_code", "type", "voiceIndex")
    ]
    + [(SET, {"mode": 0, "volume": 1}, (), TypeError)]
)


@pytest.mark.parametrize("name,kw,pos,exc", CASES)
def test_alarm_sound_rejected_inputs(mode, name, kw, pos, exc):
    call, rec, seen = invoke(mode, name, kwargs=kw, positional=pos)
    with pytest.raises(exc):
        call()
    assert seen == {"auth": [], "biz": []}
    assert outcomes(rec, mode, "attempt", name) == []


@pytest.mark.parametrize("name", [SET, PLAY])
def test_alarm_sound_action_redacts_secrets(mode, caplog, name):
    url = "https://synthetic.invalid/?serial=" + SERIAL + "&token=" + TOKEN
    bad = env(60020)
    bad["body"]["meta"]["message"] = url
    good = env(data={"x": ["PRIVATE-RESPONSE-SENTINEL"]})
    with caplog.at_level(logging.INFO):
        ecall, erec, _ = invoke(mode, name, bad)
        with pytest.raises(ApiError) as ei:
            ecall()
        scall, srec, _ = invoke(mode, name, good)
        res = scall()
    blob = str(ei.value) + repr(ei.value) + ei.value.message + caplog.text
    blob += repr(erec.report()) + repr(srec.report())
    for secret in (SERIAL, TOKEN, url, "PRIVATE-RESPONSE-SENTINEL"):
        assert secret not in blob
    assert res.data == good["body"]["data"] and res.raw == good
    for kind in ("call", "attempt"):
        assert outcomes(erec, mode, kind, name) == ["started", "failed"]
        assert outcomes(srec, mode, kind, name) == ["started", "succeeded"]
