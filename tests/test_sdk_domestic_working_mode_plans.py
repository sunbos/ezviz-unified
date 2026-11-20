"""Domestic working mode plan contracts and overseas regression coverage."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_light_linkage import outcomes
from test_sdk_motion_area import REGIONS, TRANSPORT_HEADERS, reply
from test_sdk_motion_area import Probe as _BaseProbe
from test_sdk_stream_archive import _invoke

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)


class Probe(_BaseProbe):
    def run(self, kind, scope, name, /, *args, **kwargs):
        options = dict(
            region=REGIONS[scope],
            auth_transport=httpx.MockTransport(self._auth),
            transport_factory=lambda origin: httpx.MockTransport(self._business),
            diagnostic_recorder=self.recorder,
        )
        return _invoke(kind, "settings", name, args, kwargs, options)


GET = "get_working_mode_plans"
SET = "set_huashu_4g_working_mode_plan"
OLD_GET = "get_timing_plan"
OLD_SET = "set_timing_plan"
NAMES = (GET, SET)
SERIAL = "PLAN-SN A9"
VALID = {"enabled": True, "start_time": "23:59", "end_time": "00:00", "week": "6,0,6"}
OPTIONS = {
    GET: {"channel": 0, "timing_plan_type": 19},
    SET: {**VALID, "stop_after_execution": False},
}
ROWS = [
    {"weekPlans": []},
    {
        "subSerial": "PLAN_SUB_PRIVATE",
        "subType": 41,
        "enable": 87,
        "weekPlans": [
            {},
            {
                "weekDay": "WEEK_PRIVATE",
                "timePlan": [
                    {},
                    {
                        "beginTime": "BEGIN_PRIVATE",
                        "endTime": "END_PRIVATE",
                        "eventArg": "mode:SCRIPT_PRIVATE",
                        "x": "EXT_PRIVATE",
                    },
                ],
            },
        ],
        "extra": None,
    },
]


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def env(code=200, **fields):
    return reply("domestic", code, **fields)


def invoke(mode, name, *steps, serial=SERIAL, kwargs=None):
    probe = Probe(*steps)
    kw = OPTIONS[name] if kwargs is None else kwargs

    def call():
        return probe.run(mode, "domestic", name, serial, **kw)

    return call, probe.recorder, {"auth": probe.auth, "biz": probe.biz}


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
        ("stop_after_execution", 0),
        ("stop_after_execution", 1),
        ("stop_after_execution", "1"),
    ],
)
def test_set_kwarg_rejected(mode, key, bad):
    call, rec, seen = invoke(mode, SET, kwargs={**VALID, key: bad})
    with pytest.raises(ValueError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", SET) == []


@pytest.mark.parametrize("key", ["enabled", "start_time", "end_time", "week"])
@pytest.mark.parametrize("enabled", [True, False])
def test_set_missing_kwarg(mode, key, enabled):
    kwargs = {**VALID, "enabled": enabled}
    kwargs.pop(key)
    call, rec, seen = invoke(mode, SET, kwargs=kwargs)
    with pytest.raises(TypeError):
        call()
    assert seen["auth"] == [] and seen["biz"] == []
    assert outcomes(rec, mode, "attempt", SET) == []


def test_set_accepts_equal_start_and_end(mode):
    call, rec, seen = invoke(
        mode, SET, env(), kwargs={**VALID, "start_time": "10:00", "end_time": "10:00"}
    )
    call()
    assert len(seen["biz"]) == 1
    form = dict(parse_qsl(seen["biz"][0].content.decode()))
    assert form["startTime"] == form["endTime"] == "10:00"


def test_get_invalid_token_retry_then_success(mode):
    call, rec, seen = invoke(mode, GET, env(10002), env(data=ROWS))
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", GET) == ["started", "failed", "started", "succeeded"]
    assert [r.headers["accessToken"] for r in seen["biz"]] == ["TOK1", "TOK2"]
    for req in seen["biz"]:
        assert req.url.host == "open.ys7.com" and req.url.path == "/api/v3/device/timing/plan/get"
        assert sorted(req.url.params.multi_items()) == [
            ("channelNo", "0"),
            ("timingPlanType", "19"),
        ]


def test_get_invalid_token_exhausted_raises(mode):
    call, rec, seen = invoke(mode, GET, env(10002), env(10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", GET) == ["started", "failed", "started", "failed"]
    assert [r.headers["accessToken"] for r in seen["biz"]] == ["TOK1", "TOK2"]
    for req in seen["biz"]:
        assert req.url.host == "open.ys7.com" and req.url.path == "/api/v3/device/timing/plan/get"
        assert sorted(req.url.params.multi_items()) == [
            ("channelNo", "0"),
            ("timingPlanType", "19"),
        ]


def test_set_invalid_token_not_retried(mode):
    call, rec, seen = invoke(mode, SET, env(10002), kwargs=VALID)
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", SET) == ["started", "failed"]


@pytest.mark.parametrize("name", [GET, SET])
def test_permission_denied_10031(mode, name):
    call, rec, seen = invoke(mode, name, env(10031), kwargs=VALID if name == SET else None)
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert err.value.code == "10031" and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("name", [GET, SET])
def test_generic_business_code_api_error(mode, name):
    call, rec, seen = invoke(mode, name, env(60020), kwargs=VALID if name == SET else None)
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert len(seen["auth"]) == len(seen["biz"]) == 1
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


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, name, ok, caplog):
    scope = "domestic"
    payload = reply(scope, 200 if ok else "PRIVATE_BACKEND_CODE", data=ROWS)
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
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx" and "/api/v3/device/timing/plan/" in r.getMessage()
    ]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        SERIAL,
        "TOK1",
        "PLAN_SUB_PRIVATE",
        "WEEK_PRIVATE",
        "BEGIN_PRIVATE",
        "END_PRIVATE",
        "SCRIPT_PRIVATE",
        "23:59",
        "6,0,6",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob


@pytest.mark.parametrize("stop_after_execution", [None, False, True])
@pytest.mark.parametrize("enabled", [True, False])
def test_domestic_set_wire(mode, stop_after_execution, enabled):
    kw = dict(VALID, enabled=enabled)
    expect = [
        ("enable", "1" if enabled else "0"),
        ("startTime", "23:59"),
        ("endTime", "00:00"),
        ("week", "6,0,6"),
    ]
    if stop_after_execution is not None:
        kw["stop_after_execution"] = stop_after_execution
        expect.append(("eventArg", str(int(stop_after_execution))))
    payload = env() if enabled else env(data={"extension": ["kept"]})
    call, rec, seen = invoke(mode, SET, payload, kwargs=kw)
    res = call()
    assert res.code == "200"
    assert res.data == payload.get("data")
    assert res.raw == payload
    req = _check_seen(seen)
    _check_wire(req, "POST", "/api/v3/device/timing/plan/set")
    assert req.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(expect)
    assert outcomes(rec, mode, "call", SET) == ["started", "succeeded"]
    assert outcomes(rec, mode, "attempt", SET) == ["started", "succeeded"]


def _check_seen(seen):
    assert len(seen["auth"]) == 1
    assert seen["auth"][0].url.host == "open.ys7.com"
    assert seen["auth"][0].url.path == "/api/lapp/token/get"
    assert seen["auth"][0].method == "POST"
    assert len(seen["biz"]) == 1
    return seen["biz"][0]


def _check_wire(req, method, path, query=()):
    assert req.method == method
    assert req.url.scheme == "https"
    assert req.url.host == "open.ys7.com"
    assert req.url.path == path
    assert sorted(req.url.params.multi_items()) == sorted(query)
    assert req.headers.get_list("accessToken") == ["TOK1"]
    assert req.headers.get_list("deviceSerial") == [SERIAL]
    assert set(req.headers.keys()) - TRANSPORT_HEADERS == {"accesstoken", "deviceserial"}


@pytest.mark.parametrize(
    "kw,query",
    [
        ({}, []),
        ({"channel": None, "timing_plan_type": None}, []),
        ({"channel": 0}, [("channelNo", "0")]),
        ({"channel": -2, "timing_plan_type": 17}, [("channelNo", "-2"), ("timingPlanType", "17")]),
        ({"timing_plan_type": 19}, [("timingPlanType", "19")]),
    ],
)
@pytest.mark.parametrize("data", [[], ROWS])
def test_domestic_get_wire(mode, kw, query, data):
    payload = env(data=data)
    call, rec, seen = invoke(mode, GET, payload, kwargs=kw)
    res = call()
    assert res.code == "200"
    assert res.data == data
    assert res.raw == payload
    req = _check_seen(seen)
    _check_wire(req, "GET", "/api/v3/device/timing/plan/get", query)
    assert req.content == b""
    assert "content-type" not in req.headers
    assert outcomes(rec, mode, "call", GET) == ["started", "succeeded"]
    assert outcomes(rec, mode, "attempt", GET) == ["started", "succeeded"]


BAD_SELECTORS = [("channel", x) for x in (True, 1.5, "1")] + [
    ("timing_plan_type", x) for x in (16, 18, 20, True, 17.0, "17")
]
BAD_SIGNATURES = [
    (GET, (SERIAL, 1), {}),
    (GET, (SERIAL,), {"channel_no": 1}),
    (SET, (SERIAL, True), VALID),
    (SET, (SERIAL,), {**VALID, "mode": 0}),
    (SET, (SERIAL,), {**VALID, "channel": 1}),
    (SET, (SERIAL,), {**VALID, "eventArg": "0"}),
    (SET, (SERIAL,), {**VALID, "timing_plan_type": 17}),
]
LEGACY_CASES = (
    (OLD_GET, {}, {"weekPlans": [], "legacy": "kept"}),
    (OLD_SET, dict(VALID, mode=3), None),
)
CROSS_CASES = (
    (GET, "overseas", {}),
    (SET, "overseas", VALID),
    (OLD_GET, "domestic", {}),
    (OLD_SET, "domestic", VALID),
)


def test_legacy_scope_and_plans(mode):
    for name, kw, data in LEGACY_CASES:
        body = {"meta": {"code": 200, "message": "ok"}}
        if data is not None:
            body["data"] = data
        wrapped = {"body": body}
        p = Probe(wrapped)
        result = p.run(mode, "overseas", name, SERIAL, **kw)
        assert len(p.auth) == len(p.biz) == 1
        assert p.auth[0].url.host == "synthetic-auth.ezvizlife.com"
        req = p.biz[0]
        assert req.url.host == "synthetic-api.ezvizlife.com"
        assert not req.url.query
        assert req.headers.get_list("accessToken") == ["TOK1"]
        assert req.headers.get_list("deviceSerial") == [SERIAL]
        assert "TOK1" not in str(req.url)
        assert "token" not in req.url.params
        if data is None:
            assert req.method == "POST"
            assert "application/x-www-form-urlencoded" in req.headers["content-type"]
            assert dict(parse_qsl(req.content.decode()))["eventArg"] == "3"
            assert result.raw == wrapped
            assert result.data is None
            assert result.code == 200
            assert type(result.code) is int
            assert req.url.path == "/api/v3/device/timing/plan/set"
        else:
            assert req.method == "GET"
            assert not req.content
            assert result.raw == wrapped
            assert result.data == data
            assert result.code == 200
            assert type(result.code) is int
            assert req.url.path == "/api/v3/device/timing/plan/get"
    p = Probe({"code": "200", "msg": "ok", "data": [{"weekPlans": []}]})
    with pytest.raises(ProtocolError):
        p.run(mode, "overseas", OLD_GET, SERIAL)


def test_new_and_old_regions_do_not_cross(mode):
    for name, region, kw in CROSS_CASES:
        p = Probe()
        with pytest.raises(ValueError) as good:
            p.run(mode, region, name, SERIAL, **kw)
        with pytest.raises(ValueError) as bad:
            p.run(mode, region, name, object(), **kw)
        assert str(good.value) == str(bad.value)
        assert not p.auth and not p.biz


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": [None]},
        {"data": [{}]},
        {"data": [{"weekPlans": {}}]},
        {"data": [{"weekPlans": [[]]}]},
        {"data": [{"weekPlans": [{"weekDay": 1, "timePlan": []}]}]},
        {"data": [{"weekPlans": [{"weekDay": "1", "timePlan": {}}]}]},
        {"data": [{"weekPlans": [{"weekDay": "1", "timePlan": ["x"]}]}]},
        {
            "data": [
                {
                    "weekPlans": [
                        {
                            "weekDay": "1",
                            "timePlan": [{"beginTime": 800, "endTime": "09:00", "eventArg": "1"}],
                        }
                    ]
                }
            ]
        },
        {
            "data": [
                {
                    "weekPlans": [
                        {
                            "weekDay": "1",
                            "timePlan": [{"beginTime": "08:00", "endTime": 900, "eventArg": "1"}],
                        }
                    ]
                }
            ]
        },
        {
            "data": [
                {
                    "weekPlans": [
                        {
                            "weekDay": "1",
                            "timePlan": [{"beginTime": "08:00", "endTime": "09:00", "eventArg": 1}],
                        }
                    ]
                }
            ]
        },
        {"data": [{"weekPlans": [], "subSerial": 7}]},
        {"data": [{"weekPlans": [], "subType": True}]},
        {"data": [{"weekPlans": [], "subType": 0.5}]},
        {"data": [{"enable": True, "weekPlans": []}]},
        {"data": [{"enable": 1.5, "weekPlans": []}]},
        {"data": [{"enable": "1", "weekPlans": []}]},
    ],
)
def test_get_data_rejected(mode, fields):
    call, rec, seen = invoke(mode, GET, env(**fields))
    with pytest.raises(ProtocolError) as err:
        call()
    assert err.value.code is None and err.value.http_status == 200
    assert outcomes(rec, mode, "call", GET) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", GET) == ["started", "succeeded"]


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


@pytest.mark.parametrize("key,bad", BAD_SELECTORS)
def test_get_selectors_rejected(mode, key, bad):
    p = Probe()
    with pytest.raises(ValueError):
        p.run(mode, "domestic", GET, SERIAL, **{key: bad})
    assert p.auth == p.biz == []
    assert outcomes(p.recorder, mode, "attempt", GET) == []


@pytest.mark.parametrize("name,args,kw", BAD_SIGNATURES)
def test_bad_signature(mode, name, args, kw):
    p = Probe()
    with pytest.raises(TypeError):
        p.run(mode, "domestic", name, *args, **kw)
    assert p.auth == p.biz == []


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"code": 200}},
        {"body": {"meta": {"code": 200}, "data": {"weekPlans": []}}},
        {"code": 200, "data": []},
    ],
)
def test_refuse_non_domestic_envelope(mode, name, payload):
    call, rec, seen = invoke(mode, name, payload)
    with pytest.raises((ProtocolError, ApiError)):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
