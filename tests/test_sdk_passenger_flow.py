"""Frozen-contract coverage for the domestic passenger-flow facade (docs 688-693).

Every auth and business request is simulated through ``httpx.MockTransport``;
no live endpoints, real credentials, device identifiers, or media URLs appear.
"""

import asyncio
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

SN = "SN-PF-SYNTH9"
DAY = datetime(2019, 6, 21, tzinfo=timezone(timedelta(hours=8)))  # local midnight +08
LINE = {"x1": "0.1", "y1": "0.2", "x2": "0.9", "y2": "0.8"}
DIRECTION = {"x1": "0.5", "y1": "0.05", "x2": "0.5", "y2": "0.95"}
SET = "/api/lapp/passengerflow/config/set"
GEO = {
    "code": "200",
    "data": {
        "line": {"x1": "0.10", "y1": "0.20", "x2": "0.90", "y2": "0.80"},
        "direction": {"x1": "5.0", "y1": "0.5", "x2": "5.0", "y2": "9.5"},
        "vendorExtra": {"nested": "kept"},
    },
}


def _token(token="SYNTH_TOKEN"):
    return {
        "code": "200",
        "data": {
            "accessToken": token,
            "expireTime": 9999999999999,
            "areaDomain": "https://open.ys7.com",
        },
    }


def auth_ok(request):
    return httpx.Response(200, json=_token())


def _form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _expectations():
    """Per-path deque of (expected form minus accessToken, canned reply)."""
    return {
        "/api/lapp/passengerflow/switch/status": deque(
            [({"deviceSerial": SN}, {"code": "200", "data": {"enable": 1, "vendorFlag": "keep"}})]
        ),
        "/api/lapp/passengerflow/switch/set": deque(
            [
                ({"deviceSerial": SN, "enable": "1", "channelNo": "0"}, {"code": "200"}),
                ({"deviceSerial": SN, "enable": "0"}, {"code": "200"}),
            ]
        ),
        "/api/lapp/passengerflow/daily": deque(
            [
                (
                    {"deviceSerial": SN, "channelNo": "2", "date": "1561046400000"},
                    {
                        "code": "200",
                        "data": {"day": "2019-06-21", "inFlow": 5, "outFlow": 3, "extra": {"a": 1}},
                    },
                )
            ]
        ),
        "/api/lapp/passengerflow/hourly": deque(
            [
                (
                    {"deviceSerial": SN, "channelNo": "2", "date": "146744312353"},
                    {
                        "code": "200",
                        "data": [
                            {"hourIndex": 8, "inFlow": 1, "outFlow": 0},
                            {"hourIndex": 9, "inFlow": 4, "outFlow": 2},
                        ],
                    },
                ),
                (
                    {"deviceSerial": SN, "channelNo": "2"},
                    {
                        "code": "200",
                        "data": [{"hourIndex": 22, "inFlow": 2, "outFlow": 1, "vendorMark": "x"}],
                    },
                ),
            ]
        ),
        SET: deque(
            [
                (
                    {
                        "deviceSerial": SN,
                        "line": json.dumps(LINE, separators=(",", ":")),
                        "direction": json.dumps(DIRECTION, separators=(",", ":")),
                        "channelNo": "0",
                    },
                    {"code": "200"},
                ),
                (
                    {
                        "deviceSerial": SN,
                        "line": json.dumps(LINE, separators=(",", ":")),
                        "direction": json.dumps(DIRECTION, separators=(",", ":")),
                    },
                    {"code": "200"},
                ),
            ]
        ),
        "/api/lapp/passengerflow/config/get": deque(
            [({"deviceSerial": SN, "channelNo": "0"}, GEO), ({"deviceSerial": SN}, GEO)]
        ),
    }


def _handler(expect, calls):
    def handle(request):
        calls.append(request)
        assert request.method == "POST"
        assert "SYNTH_TOKEN" not in str(request.url)
        assert "SYNTH_TOKEN" not in str(request.headers)
        form = _form(request)  # parse_qs once on the raw body
        assert form.pop("accessToken") == "SYNTH_TOKEN"  # form body only
        if request.url.path == SET:
            # single encoding: %7B present, never double-encoded %257B
            assert b"%7B" in request.content and b"%257B" not in request.content
            raw_line, raw_dir = form["line"], form["direction"]
            assert raw_line == json.dumps(LINE, separators=(",", ":"))
            assert raw_dir == json.dumps(DIRECTION, separators=(",", ":"))
            assert json.loads(raw_line) == LINE
            assert json.loads(raw_dir) == DIRECTION
        expected, reply = expect[request.url.path].popleft()
        assert form == expected  # exact full-map equality, all keys retained
        return httpx.Response(200, json=reply)

    return handle


def _client(handler, recorder=None, auth=auth_ok):
    return Client(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(handler),
        diagnostic_recorder=recorder or DiagnosticRecorder(),
    )


CALLS = [
    ("switch_status", (SN,), {}, lambda r: r.data["vendorFlag"] == "keep"),
    ("set_switch", (SN,), {"enable": 1, "channel_no": 0}, lambda r: r.data is None),
    ("set_switch", (SN,), {"enable": 0}, lambda r: r.data is None),
    ("daily", (SN, 2), {"day": DAY}, lambda r: r.data["extra"] == {"a": 1}),
    (
        "hourly",
        (SN, 2),
        {"date_ms": 146744312353},
        lambda r: (
            r.data
            == [
                {"hourIndex": 8, "inFlow": 1, "outFlow": 0},
                {"hourIndex": 9, "inFlow": 4, "outFlow": 2},
            ]
        ),
    ),
    (
        "hourly",
        (SN, 2),
        {},
        lambda r: r.data == [{"hourIndex": 22, "inFlow": 2, "outFlow": 1, "vendorMark": "x"}],
    ),
    (
        "set_config",
        (SN,),
        {"line": LINE, "direction": DIRECTION, "channel_no": 0},
        lambda r: r.data is None,
    ),
    ("set_config", (SN,), {"line": LINE, "direction": DIRECTION}, lambda r: r.data is None),
    (
        "get_config",
        (SN,),
        {"channel_no": 0},
        lambda r: r.data["line"]["x1"] == "0.10" and r.data["vendorExtra"] == {"nested": "kept"},
    ),
    ("get_config", (SN,), {}, lambda r: r.data["direction"]["y2"] == "9.5"),
]


def _facade_trace(recorder):
    """Ordered facade ``call`` events for the ``passenger_flow.*`` scopes only.

    ``recorder.report()["events"]`` also lists ``client.request`` ``call``
    rows plus authentication/transport_setup/attempt children, so both the
    kind and the entrypoint prefix must be filtered; each facade invocation
    yields an in-order ``started`` then terminal event on the same span.
    """
    events = recorder.report()["events"]
    return [
        e
        for e in events
        if e.get("kind") == "call" and e.get("entrypoint", "").startswith("passenger_flow.")
    ]


def _assert_invocations(trace, expected, mode):
    """Assert the complete ordered started/terminal facade trace.

    ``expected`` is an ordered list of ``(method, terminal_outcome)``; each
    invocation must contribute a ``started`` row then its terminal row, in
    sequence, on one shared ``span_id`` — never a bare outcome set.
    """
    actual = [(e["entrypoint"], e["outcome"]) for e in trace]
    wanted = [
        (f"passenger_flow.{name}", outcome)
        for name, terminal in expected
        for outcome in ("started", terminal)
    ]
    assert actual == wanted
    assert all(e["mode"] == mode for e in trace)
    for started, terminal in zip(trace[0::2], trace[1::2]):
        assert started["outcome"] == "started"
        assert terminal["outcome"] != "started"
        assert started["span_id"] == terminal["span_id"]


def test_facade_calls_sync():
    expect, calls, recorder = _expectations(), [], DiagnosticRecorder()
    with _client(_handler(expect, calls), recorder) as client:
        pf = client.passenger_flow
        for name, args, kwargs, check in CALLS:
            assert check(getattr(pf, name)(*args, **kwargs)), name
    assert len(calls) == 10 and all(not q for q in expect.values())
    _assert_invocations(
        _facade_trace(recorder),
        [(name, "succeeded") for name, _a, _k, _c in CALLS],
        "sync",
    )


def test_facade_calls_async():
    asyncio.run(_run_async())


async def _run_async():
    expect, calls, recorder = _expectations(), [], DiagnosticRecorder()
    async with AsyncClient(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth_ok),
        transport_factory=lambda origin: httpx.MockTransport(_handler(expect, calls)),
        diagnostic_recorder=recorder,
    ) as client:
        for name, args, kwargs, check in CALLS:
            assert check(await getattr(client.passenger_flow, name)(*args, **kwargs)), name
    assert len(calls) == 10
    _assert_invocations(
        _facade_trace(recorder),
        [(name, "succeeded") for name, _a, _k, _c in CALLS],
        "async",
    )


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda pf: pf.set_switch(SN, enable=1), id="set_switch"),
        pytest.param(lambda pf: pf.set_config(SN, line=LINE, direction=DIRECTION), id="set_config"),
    ],
)
def test_write_10002_raises_auth_error_without_replay(invoke, mode):
    counts = {"auth": 0, "biz": 0}

    def auth(request):
        counts["auth"] += 1
        return auth_ok(request)

    def biz(request):
        counts["biz"] += 1
        return httpx.Response(200, json={"code": "10002"})

    async def _run():
        async with AsyncClient(
            "SYNTHKEY",
            "SYNTHSECRET",
            region=RegionConfig.china(),
            auth_transport=httpx.MockTransport(auth),
            transport_factory=lambda origin: httpx.MockTransport(biz),
        ) as client:
            with pytest.raises(AuthenticationError):
                await invoke(client.passenger_flow)

    if mode == "sync":
        with _client(biz, auth=auth) as client:
            with pytest.raises(AuthenticationError):
                invoke(client.passenger_flow)
    else:
        asyncio.run(_run())
    # identical evidence in both modes: one auth, one business POST, no replay
    assert counts == {"auth": 1, "biz": 1}


def test_read_10002_recovers_via_shared_reauth():
    counts, seen = {"auth": 0, "biz": 0}, []

    def auth(request):
        counts["auth"] += 1
        return httpx.Response(200, json=_token(f"SYNTH_TOKEN{counts['auth']}"))

    def biz(request):
        counts["biz"] += 1
        seen.append(_form(request)["accessToken"])
        if counts["biz"] == 1:
            return httpx.Response(200, json={"code": "10002"})
        return httpx.Response(200, json={"code": "200", "data": {"enable": 0, "tag": "r"}})

    with _client(biz, auth=auth) as client:
        assert client.passenger_flow.switch_status(SN).data["tag"] == "r"
    assert counts == {"auth": 2, "biz": 2}
    assert seen == ["SYNTH_TOKEN1", "SYNTH_TOKEN2"]


@pytest.mark.parametrize("code", ["60022", "60025"])
def test_business_errors_surface_as_apierror(code):
    counts = {"biz": 0}

    def biz(request):
        counts["biz"] += 1
        return httpx.Response(200, json={"code": code, "msg": "synthetic"})

    with _client(biz) as client:
        with pytest.raises(ApiError) as exc:
            client.passenger_flow.get_config(SN)
    assert str(exc.value.code) == code
    assert not isinstance(exc.value, AuthenticationError)
    assert counts["biz"] == 1


@pytest.mark.parametrize(
    "invoke,bad",
    [
        pytest.param(
            lambda pf: pf.switch_status(SN), {"code": "200", "data": [1, 2]}, id="status_array"
        ),
        pytest.param(
            lambda pf: pf.hourly(SN, 2), {"code": "200", "data": {"hour": 1}}, id="hourly_object"
        ),
        pytest.param(
            lambda pf: pf.get_config(SN),
            {"code": "200", "data": {"line": {"x1": "0.1"}}},
            id="geometry_missing_direction",
        ),
    ],
)
def test_shape_mismatch_is_protocol_error(invoke, bad):
    def biz(request):
        return httpx.Response(200, json=bad)

    with _client(biz) as client:
        with pytest.raises(ProtocolError):
            invoke(client.passenger_flow)


def test_diagnostics_events_structured_and_sanitized():
    recorder = DiagnosticRecorder()
    replies = {
        "/api/lapp/passengerflow/config/set": {"code": "200"},
        "/api/lapp/passengerflow/config/get": {"code": "60025", "msg": "x"},
    }

    def biz(request):
        return httpx.Response(200, json=replies[request.url.path])

    line = {"x1": "0.7654321", "y1": "0.1111111", "x2": "0.2222222", "y2": "0.3333333"}
    direction = {"x1": "7.25", "y1": "8.5", "x2": "9.75", "y2": "6.125"}
    with _client(biz, recorder) as client:
        pf = client.passenger_flow
        assert pf.set_config(SN, line=line, direction=direction).data is None
        with pytest.raises(ApiError):
            pf.get_config(SN)
    trace = _facade_trace(recorder)
    _assert_invocations(
        trace,
        [("set_config", "succeeded"), ("get_config", "failed")],
        "sync",
    )
    failed = trace[-1]
    assert failed["error_family"] == "api"
    assert failed["code_category"] == "other"  # 60025 is not a token code
    blob = json.dumps(recorder.report()["events"])
    assert SN not in blob and "SYNTH_TOKEN" not in blob
    assert "0.7654321" not in blob and "7.25" not in blob
