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
GET = "get_light_linkage"
SET = "set_light_linkage"
VALID = {"enabled": True}


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
    kw = kwargs if kwargs is not None else (VALID if name == SET else {})
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


@pytest.mark.parametrize("name", [GET, SET])
@pytest.mark.parametrize("serial", ["", "SN序", "SN\x1f", "SN\x7f", " SN123", "SN123 "])
def test_serial_rejected(mode, name, serial):
    call, rec, seen = invoke(mode, name, serial=serial, kwargs=dict(VALID) if name == SET else None)
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", name) == []


@pytest.mark.parametrize("name", [GET, SET])
def test_china_region_domestic_rejected_before_auth(mode, name):
    call, rec, seen = invoke(
        mode, name, env(), region=RegionConfig.china(), kwargs=VALID if name == SET else None
    )
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "call", name) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", name) == []


def test_get_invalid_token_retry_then_success(mode):
    call, rec, seen = invoke(mode, GET, env(10002), env(data={"type": 301, "enable": 1}))
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", GET) == ["started", "failed", "started", "succeeded"]


def test_get_invalid_token_exhausted_raises(mode):
    call, rec, seen = invoke(mode, GET, env(10002), env(10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", GET) == ["started", "failed", "started", "failed"]


def test_set_invalid_token_not_retried(mode):
    call, rec, seen = invoke(mode, SET, env(10002), kwargs=VALID)
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", SET) == ["started", "failed"]


@pytest.mark.parametrize("name", [GET, SET])
@pytest.mark.parametrize(
    "bad,exc", [("200", ApiError), (200.0, ProtocolError), (True, ProtocolError)]
)
def test_meta_code_wrong_type_rejected(mode, name, bad, exc):
    payload = env()
    payload["body"]["meta"]["code"] = bad
    call, rec, seen = invoke(mode, name, payload, kwargs=VALID if name == SET else None)
    with pytest.raises(exc):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [GET, SET])
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"code": 200}},
        {"code": 200},
        {"meta": {"code": 10002}, "body": {}},
    ],
)
def test_missing_body_meta_protocol_error(mode, name, payload):
    call, rec, seen = invoke(mode, name, payload, kwargs=VALID if name == SET else None)
    with pytest.raises(ProtocolError) as excinfo:
        call()
    assert excinfo.value.http_status == 200
    assert excinfo.value.code is None
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [GET, SET])
def test_permission_denied_10031(mode, name):
    call, rec, seen = invoke(mode, name, env(10031), kwargs=VALID if name == SET else None)
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert err.value.code == 10031 and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [GET, SET])
def test_generic_business_code_api_error(mode, name):
    call, rec, seen = invoke(mode, name, env(60020), kwargs=VALID if name == SET else None)
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert excinfo.value.code == 60020
    assert excinfo.value.http_status == 200


@pytest.mark.parametrize("name", [GET, SET])
def test_read_timeout_maps_transport_error(mode, name):
    call, rec, seen = invoke(
        mode, name, httpx.ReadTimeout("synthetic"), kwargs=VALID if name == SET else None
    )
    with pytest.raises(TransportError) as excinfo:
        call()
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [GET, SET])
def test_async_cancellation_propagates(name):
    call, rec, seen = invoke(
        "async", name, asyncio.CancelledError(), kwargs=VALID if name == SET else None
    )
    with pytest.raises(asyncio.CancelledError):
        call()
    assert outcomes(rec, "async", "call", name) == ["started", "cancelled"]
    assert outcomes(rec, "async", "attempt", name) == ["started", "cancelled"]
    assert len(seen["auth"]) == len(seen["biz"]) == 1


def test_error_and_success_never_echo_secrets(mode, caplog):
    url = "https://synthetic.invalid/x?s=" + SERIAL + "&token=" + TOKEN
    payload = env(60020)
    payload["body"]["meta"]["message"] = url
    with caplog.at_level(logging.INFO):
        call, rec, _ = invoke(mode, GET, payload)
        with pytest.raises(ApiError) as excinfo:
            call()
        gcall, grec, _ = invoke(
            mode,
            GET,
            env(
                data={
                    "type": 301,
                    "enable": 0,
                    "subSerial": "SUB_LINKAGE_PRIVATE",
                    "extra": "OPAQUE_LINKAGE_SENTINEL",
                }
            ),
        )
        gcall()
        scall, srec, _ = invoke(mode, SET, env(), kwargs=VALID)
        scall()
    blob = caplog.text + repr(rec.report()) + repr(grec.report()) + repr(srec.report())
    blob += repr(excinfo.value) + str(excinfo.value) + repr(excinfo.value.message)
    for leaked in (SERIAL, TOKEN, url, "SUB_LINKAGE_PRIVATE", "OPAQUE_LINKAGE_SENTINEL"):
        assert leaked not in blob
    for r, n in ((grec, GET), (srec, SET)):
        assert outcomes(r, mode, "call", n) == ["started", "succeeded"]
        assert outcomes(r, mode, "attempt", n) == ["started", "succeeded"]


def _wire(req, en=None):
    assert req.method == ("GET" if en is None else "POST")
    assert req.url.scheme == "https" and req.url.host == API_HOST
    assert req.url.path == "/api/v3/device/switchStatus/" + ("get" if en is None else "set")
    h = req.headers
    assert h.get_list("accessToken") == [TOKEN] and h.get_list("deviceSerial") == [SERIAL]
    assert req.content == b"" and "content-type" not in h
    for k in ("authorization", "channelno", "type", "enable", "verifycode"):
        assert k not in h
    want = [("type", "301")] + ([("enable", en)] if en is not None else [])
    assert sorted(req.url.params.multi_items()) == sorted(want)


def _done(rec, mode, name):
    assert outcomes(rec, mode, "attempt", name) == ["started", "succeeded"]
    assert outcomes(rec, mode, "call", name) == ["started", "succeeded"]


@pytest.mark.parametrize(
    "t,e,sub", [(301, 1, None), (301, 0, "s-1"), (2, 1, None), (302, 0, "s-2"), (301, -9, "s-3")]
)
def test_get(mode, t, e, sub):
    data = {"type": t, "enable": e, "ext": {"pair": [t, e]}}
    if sub is not None:
        data["subSerial"] = sub
    r = env(data=data)
    call, rec, seen = invoke(mode, GET, r)
    res = call()
    _wire(_check_seen(seen))
    assert res.code == 200 and res.raw == r and res.data == data
    assert type(res.data["type"]) is int and type(res.data["enable"]) is int
    _done(rec, mode, GET)


@pytest.mark.parametrize(
    "val,fields,pos",
    [
        (True, {}, True),
        (False, {}, False),
        (True, {"data": {"type": 301, "x": [1]}, "extra": {"a": 1}}, False),
        (False, {"data": {"k": "v"}}, True),
    ],
)
def test_set(mode, val, fields, pos):
    r = env(**fields)
    call, rec, seen = invoke(
        mode, SET, r, kwargs={} if pos else {"enabled": val}, positional=(val,) if pos else ()
    )
    res = call()
    _wire(_check_seen(seen), "1" if val else "0")
    assert res.code == 200 and res.raw == r and res.data == fields.get("data")
    _done(rec, mode, SET)


def _proto_fail(mode, reply):
    call, rec, seen = invoke(mode, GET, reply)
    with pytest.raises(ProtocolError) as ei:
        call()
    assert ei.value.code is None and ei.value.http_status == 200
    assert outcomes(rec, mode, "call", GET) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", GET) == ["started", "succeeded"]
    _check_seen(seen)


def _presend(mode, name, err, kw):
    call, rec, seen = invoke(mode, name, kwargs=kw)
    with pytest.raises(err):
        call()
    assert seen == {"auth": [], "biz": []}
    assert outcomes(rec, mode, "attempt", name) == []


@pytest.mark.parametrize("reply", [env(), env(data=None), env(data="x"), env(data=[1])])
def test_get_bad_data(mode, reply):
    _proto_fail(mode, reply)


@pytest.mark.parametrize("data", [{"enable": 1}, {"type": 2}])
def test_get_missing_field(mode, data):
    _proto_fail(mode, env(data=data))


@pytest.mark.parametrize("field", ["type", "enable"])
@pytest.mark.parametrize("bad", [None, True, 1.5, "1"])
def test_get_invalid_field(mode, field, bad):
    _proto_fail(mode, env(data={"type": 2, "enable": 1, field: bad}))


@pytest.mark.parametrize("bad", [None, 7, True])
def test_get_invalid_subserial(mode, bad):
    _proto_fail(mode, env(data={"type": 2, "enable": 1, "subSerial": bad}))


@pytest.mark.parametrize("bad", [0, 1, "1", 1.0, None])
def test_set_invalid_enabled(mode, bad):
    _presend(mode, SET, ValueError, {"enabled": bad})


def test_set_missing_enabled(mode):
    _presend(mode, SET, TypeError, {})


@pytest.mark.parametrize("name", [GET, SET])
@pytest.mark.parametrize("extra", [{"channelNo": 1}, {"type": 1}, {"verifyCode": "x"}])
def test_extra_kwargs(mode, name, extra):
    if name == SET:
        extra = {"enabled": True, **extra}
    _presend(mode, name, TypeError, extra)


@pytest.mark.parametrize("name", [GET, SET])
def test_forbidden(mode, name):
    call, rec, seen = invoke(mode, name, httpx.Response(403, text="denied"))
    with pytest.raises(PermissionDeniedError) as ei:
        call()
    assert ei.value.http_status == 403
    assert outcomes(rec, mode, "call", name) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", name) == ["started", "failed"]
    _check_seen(seen)
