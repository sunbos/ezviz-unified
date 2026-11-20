import asyncio
import logging
from urllib.parse import parse_qsl

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
GET = "get_timing_plan"
SET = "set_timing_plan"
VALID = {"enabled": True, "start_time": "23:59", "end_time": "00:00", "week": "6,0,6"}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def env(code=200, **fields):
    return {"meta": {"code": 10002}, "body": {"meta": {"code": code, "message": "ok"}, **fields}}


def invoke(mode, name, *replies, serial=SERIAL, region=None, kwargs=None):
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
                return await getattr(c.settings, name)(serial, **kw)

        return lambda: asyncio.run(call()), rec, seen

    def call():
        with client as c:
            return getattr(c.settings, name)(serial, **kw)

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


def _check_wire(req, method, path):
    assert req.method == method
    assert req.url.scheme == "https"
    assert req.url.host == API_HOST
    assert req.url.path == path
    assert req.url.query == b""
    assert req.headers.get_list("accessToken") == [TOKEN]
    assert req.headers.get_list("deviceSerial") == [SERIAL]


def test_get_timing_plan_success(mode):
    data = {
        "subSerial": "synthetic",
        "subType": 2,
        "extension": [7],
        "weekPlans": [
            {},
            {
                "weekDay": "days as sent",
                "timePlan": [
                    {},
                    {"beginTime": "nonclock", "endTime": "", "eventArg": "99", "ext": 1},
                ],
            },
        ],
    }
    payload = env(data=data)
    call, rec, seen = invoke(mode, GET, payload)
    res = call()
    assert res.code == 200
    assert res.data == data
    assert res.raw == payload
    req = _check_seen(seen)
    _check_wire(req, "GET", "/api/v3/device/timing/plan/get")
    assert req.content == b""
    assert "content-type" not in req.headers
    assert outcomes(rec, mode, "call", GET) == ["started", "succeeded"]
    assert outcomes(rec, mode, "attempt", GET) == ["started", "succeeded"]


@pytest.mark.parametrize("m", [None, 0, 1, 2, 3])
@pytest.mark.parametrize("enabled", [True, False])
def test_set_timing_plan_success(mode, m, enabled):
    kw = dict(VALID, enabled=enabled)
    expect = [
        ("enable", "1" if enabled else "0"),
        ("startTime", "23:59"),
        ("endTime", "00:00"),
        ("week", "6,0,6"),
    ]
    if m is not None:
        kw["mode"] = m
        expect.append(("eventArg", str(m)))
    payload = env() if enabled else env(data={"extension": ["kept"]})
    call, rec, seen = invoke(mode, SET, payload, kwargs=kw)
    res = call()
    assert res.code == 200
    assert res.data == payload["body"].get("data")
    assert res.raw == payload
    req = _check_seen(seen)
    _check_wire(req, "POST", "/api/v3/device/timing/plan/set")
    assert req.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(expect)
    assert outcomes(rec, mode, "call", SET) == ["started", "succeeded"]
    assert outcomes(rec, mode, "attempt", SET) == ["started", "succeeded"]


@pytest.mark.parametrize("name", [GET, SET])
@pytest.mark.parametrize("serial", ["", "SN序", "SN\x1f", "SN\x7f", " SN123", "SN123 "])
def test_serial_rejected(mode, name, serial):
    call, rec, seen = invoke(mode, name, serial=serial, kwargs=dict(VALID) if name == SET else None)
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", name) == []


@pytest.mark.parametrize(
    "key,bad",
    [
        ("enabled", 0),
        ("enabled", 1),
        ("enabled", "1"),
        ("start_time", "0:00"),
        ("start_time", "24:00"),
        ("start_time", "12:60"),
        ("start_time", "１２:００"),
        ("start_time", " 12:00"),
        ("start_time", 1200),
        ("end_time", "0:00"),
        ("end_time", "24:00"),
        ("end_time", "12:60"),
        ("end_time", "１２:００"),
        ("end_time", " 12:00"),
        ("end_time", 1200),
        ("week", ""),
        ("week", 7),
        ("week", "00"),
        ("week", "1, 2"),
        ("week", "0,"),
        ("week", "8"),
        ("week", "-1"),
        ("week", "１"),
        ("mode", -1),
        ("mode", 4),
        ("mode", True),
        ("mode", "1"),
        ("mode", 1.5),
    ],
)
def test_set_kwarg_rejected(mode, key, bad):
    call, rec, seen = invoke(mode, SET, kwargs={**VALID, key: bad})
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", SET) == []


@pytest.mark.parametrize("key", ["start_time", "end_time", "week"])
@pytest.mark.parametrize("enabled", [True, False])
def test_set_missing_kwarg(mode, key, enabled):
    kwargs = {**VALID, "enabled": enabled}
    kwargs.pop(key)
    call, rec, seen = invoke(mode, SET, kwargs=kwargs)
    with pytest.raises(TypeError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", SET) == []


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


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"data": None},
        {"data": []},
        {"data": {}},
        {"data": {"weekPlans": {}}},
        {"data": {"weekPlans": [[]]}},
        {"data": {"weekPlans": [{"weekDay": 1, "timePlan": []}]}},
        {"data": {"weekPlans": [{"weekDay": "1", "timePlan": {}}]}},
        {"data": {"weekPlans": [{"weekDay": "1", "timePlan": ["x"]}]}},
        {
            "data": {
                "weekPlans": [
                    {
                        "weekDay": "1",
                        "timePlan": [{"beginTime": 800, "endTime": "09:00", "eventArg": "1"}],
                    }
                ]
            }
        },
        {
            "data": {
                "weekPlans": [
                    {
                        "weekDay": "1",
                        "timePlan": [{"beginTime": "08:00", "endTime": 900, "eventArg": "1"}],
                    }
                ]
            }
        },
        {
            "data": {
                "weekPlans": [
                    {
                        "weekDay": "1",
                        "timePlan": [{"beginTime": "08:00", "endTime": "09:00", "eventArg": 1}],
                    }
                ]
            }
        },
        {"data": {"weekPlans": [], "subSerial": 7}},
        {"data": {"weekPlans": [], "subType": True}},
        {"data": {"weekPlans": [], "subType": 0.5}},
    ],
)
def test_get_data_rejected(mode, fields):
    call, rec, seen = invoke(mode, GET, env(**fields))
    with pytest.raises(ProtocolError) as err:
        call()
    assert err.value.code is None and err.value.http_status == 200
    assert outcomes(rec, mode, "call", GET) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", GET) == ["started", "succeeded"]


def test_get_returns_empty_plan_list(mode):
    call, rec, seen = invoke(mode, GET, env(data={"weekPlans": []}))
    assert call().data == {"weekPlans": []}


def test_set_accepts_equal_start_and_end(mode):
    call, rec, seen = invoke(
        mode, SET, env(), kwargs={**VALID, "start_time": "10:00", "end_time": "10:00"}
    )
    call()
    assert len(seen["biz"]) == 1
    form = dict(parse_qsl(seen["biz"][0].content.decode()))
    assert form["startTime"] == form["endTime"] == "10:00"


def test_set_mode_int_subclass(mode):
    class Labelled(int):
        def __str__(self):
            return "mode-label"

    call, rec, seen = invoke(mode, SET, env(), kwargs={**VALID, "mode": Labelled(2)})
    call()
    form = dict(httpx.QueryParams(seen["biz"][-1].content.decode("utf-8")))
    assert form["eventArg"] == "2"


def test_get_invalid_token_retry_then_success(mode):
    call, rec, seen = invoke(mode, GET, env(10002), env(data={"weekPlans": []}))
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
            mode, GET, env(data={"weekPlans": [{"extra": "OPAQUE_PLAN_SENTINEL"}]})
        )
        gcall()
        scall, srec, _ = invoke(mode, SET, env(), kwargs=VALID)
        scall()
    blob = caplog.text + repr(rec.report()) + repr(grec.report()) + repr(srec.report())
    blob += repr(excinfo.value) + str(excinfo.value) + repr(excinfo.value.message)
    for leaked in (SERIAL, TOKEN, url, "OPAQUE_PLAN_SENTINEL"):
        assert leaked not in blob
    for r, n in ((grec, GET), (srec, SET)):
        assert outcomes(r, mode, "call", n) == ["started", "succeeded"]
        assert outcomes(r, mode, "attempt", n) == ["started", "succeeded"]
