"""Offline wire and validation tests for the domestic traffic facades.

``Client.traffic`` and ``AsyncClient.traffic`` are exercised directly against
real ``Client``/``AsyncClient`` instances whose transports are
``httpx.MockTransport`` objects with fully synthetic authentication.  Nothing
reaches the network and no pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

APP_KEY = "SYNTH_KEY"
APP_SECRET = "SYNTH_SECRET"
TOKEN = "SYNTH_TOKEN"
SERIAL = "Dev-7_Mixed"
LEAK = "PRIVATE_MARKER_7f3a"

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

MODES = ("sync", "async")

SUMMARY_PATH = "/api/lapp/traffic/user/total"
ACCOUNT_DETAILS_PATH = "/api/lapp/traffic/user/detail"
DAY_DETAILS_PATH = "/api/lapp/traffic/day/detail"
DEVICE_DETAILS_PATH = "/api/lapp/traffic/device/detail"

AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}

ENTRY_PATHS = {
    "account_summary": SUMMARY_PATH,
    "account_details": ACCOUNT_DETAILS_PATH,
    "day_details": DAY_DETAILS_PATH,
    "device_details": DEVICE_DETAILS_PATH,
}

ENTRY_SHAPES = {
    "account_summary": "object",
    "account_details": "array",
    "day_details": "array",
    "device_details": "array",
}

ENTRY_ARGS = {
    "account_summary": (),
    "account_details": (),
    "day_details": (),
    "device_details": (SERIAL,),
}

VERBATIM_SERIALS = ("MiXeD-9_AbC", "DEV 12", "cam７７７", "9", " Dev-8 ")

BAD_SERIALS = (
    None,
    0,
    123,
    1.5,
    True,
    b"DEV-1",
    ["DEV-1"],
    "",
    "   ",
    "\t\n",
    "DEV\ud800",
)

# (entry, args, kwargs, expect_terminal_event).  ``False`` marks pure Python
# signature errors where the call never reaches the SDK validation layer.
INVALID_CALLS = [
    ("account_summary", (), {"page_size": 3}, False),
    ("account_summary", (), {"start_time": 1}, False),
    ("account_details", (), {"start_time": True}, True),
    ("account_details", (), {"end_time": False}, True),
    ("account_details", (), {"start_time": 1.5}, True),
    ("account_details", (), {"end_time": "100"}, True),
    ("account_details", (), {"start_time": b"1"}, True),
    ("account_details", (), {"start_time": INT64_MIN - 1}, True),
    ("account_details", (), {"end_time": INT64_MAX + 1}, True),
    ("account_details", (), {"page_start": -1}, True),
    ("account_details", (), {"page_start": True}, True),
    ("account_details", (), {"page_start": 2.5}, True),
    ("account_details", (), {"page_start": "0"}, True),
    ("account_details", (), {"page_size": 0}, True),
    ("account_details", (), {"page_size": 51}, True),
    ("account_details", (), {"page_size": True}, True),
    ("account_details", (), {"page_size": 10.0}, True),
    ("account_details", (), {"page_size": "50"}, True),
    ("day_details", (), {"flow_time": 3.14}, True),
    ("day_details", (), {"flow_time": True}, True),
    ("day_details", (), {"flow_time": INT64_MIN - 1}, True),
    ("day_details", (), {"flow_time": INT64_MAX + 1}, True),
    ("day_details", (), {"page_start": -2}, True),
    ("day_details", (), {"page_size": 0}, True),
    ("day_details", (), {"page_size": 51}, True),
    ("device_details", (SERIAL,), {"start_time": b"1"}, True),
    ("device_details", (SERIAL,), {"end_time": 0.5}, True),
    ("device_details", (SERIAL,), {"page_start": -1}, True),
    ("device_details", (SERIAL,), {"page_size": 100}, True),
]

# (entry, body) success responses whose ``data`` layer violates the contract.
BAD_BODIES = [
    ("account_summary", {"code": "200", "msg": "ok"}),
    ("account_summary", {"code": "200", "msg": "ok", "data": None}),
    ("account_summary", {"code": "200", "msg": "ok", "data": [{"row": LEAK}]}),
    ("account_summary", {"code": "200", "msg": "ok", "data": LEAK}),
    ("account_summary", ["unexpected", LEAK]),
    ("account_details", {"code": "200", "msg": "ok"}),
    ("account_details", {"code": "200", "msg": "ok", "data": None}),
    ("account_details", {"code": "200", "msg": "ok", "data": {"rows": LEAK}}),
    ("account_details", {"code": "200", "msg": "ok", "data": LEAK}),
    ("day_details", {"code": "200", "msg": "ok", "data": None}),
    ("day_details", {"code": "200", "msg": "ok", "data": {"rows": LEAK}}),
    ("device_details", {"code": "200", "msg": "ok"}),
    ("device_details", {"code": "200", "msg": "ok", "data": {"rows": LEAK}}),
]

# (entry, kwargs, expected form fields beyond accessToken).
OPTIONAL_CASES = [
    (
        "account_details",
        {"page_start": 0, "page_size": 1},
        {"pageStart": "0", "pageSize": "1"},
    ),
    (
        "account_details",
        {
            "start_time": 1700000000000,
            "end_time": 1700003600000,
            "page_start": 3,
            "page_size": 50,
        },
        {
            "startTime": "1700000000000",
            "endTime": "1700003600000",
            "pageStart": "3",
            "pageSize": "50",
        },
    ),
    # Reversed bounds are forwarded verbatim; no platform-side guessing.
    (
        "account_details",
        {"start_time": 2000, "end_time": 1000},
        {"startTime": "2000", "endTime": "1000"},
    ),
    (
        "account_details",
        {"start_time": INT64_MIN},
        {"startTime": str(INT64_MIN)},
    ),
    (
        "account_details",
        {"end_time": INT64_MAX},
        {"endTime": str(INT64_MAX)},
    ),
    ("account_details", {"end_time": -5}, {"endTime": "-5"}),
    ("account_details", {"page_start": 401}, {"pageStart": "401"}),
    (
        "account_details",
        {"page_start": 100000, "page_size": 50},
        {"pageStart": "100000", "pageSize": "50"},
    ),
    (
        "day_details",
        {"flow_time": 1700000000000, "page_start": 2, "page_size": 50},
        {"flowTime": "1700000000000", "pageStart": "2", "pageSize": "50"},
    ),
    ("day_details", {"flow_time": INT64_MAX}, {"flowTime": str(INT64_MAX)}),
    (
        "day_details",
        {"flow_time": -1, "page_start": 999},
        {"flowTime": "-1", "pageStart": "999"},
    ),
    (
        "device_details",
        {
            "start_time": INT64_MIN,
            "end_time": -1,
            "page_start": 4001,
            "page_size": 50,
        },
        {
            "startTime": str(INT64_MIN),
            "endTime": "-1",
            "pageStart": "4001",
            "pageSize": "50",
        },
    ),
    ("device_details", {"end_time": 0}, {"endTime": "0"}),
]


def _region(label: str) -> RegionConfig:
    """Return the real RegionConfig for ``label`` ("domestic"/"overseas")."""
    if label == "domestic":
        return RegionConfig.china()
    return RegionConfig.overseas()


def _payload(shape: str) -> dict:
    """Return a synthetic success body honouring the contracted data layer."""
    if shape == "object":
        return {
            "code": "200",
            "msg": "ok",
            "data": {"totalFlow": 4096, "usedFlow": 1024, "nested": {"keep": True}},
            "unknownTop": "kept",
        }
    return {
        "code": "200",
        "msg": "ok",
        "data": [
            {"deviceSerial": "cam-1", "flow": 7},
            {"deviceSerial": "cam-2", "flow": 9, "extra": None},
        ],
        "unknownTop": "kept",
    }


def _responder(payload):
    """Build a MockTransport handler returning ``payload`` as JSON."""

    def _respond(request):
        body = dict(payload) if isinstance(payload, dict) else payload
        return httpx.Response(200, json=body)

    return _respond


class _Harness:
    """Synthetic auth and business transports wrapped around a real client."""

    def __init__(self, *, region="domestic", responder=None, recorder=None):
        self.region = region
        self.responder = responder or _responder(_payload("object"))
        self.recorder = recorder if recorder is not None else DiagnosticRecorder()
        self.auth_calls = []
        self.business_calls = []
        self.factory_calls = []

    def _handle_auth(self, request):
        self.auth_calls.append(request)
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _handle_business(self, request):
        self.business_calls.append(request)
        return self.responder(request)

    def _factory(self, origin):
        self.factory_calls.append(origin)
        return httpx.MockTransport(self._handle_business)

    def _kwargs(self):
        return {
            "region": _region(self.region),
            "auth_transport": httpx.MockTransport(self._handle_auth),
            "transport_factory": self._factory,
            "diagnostic_recorder": self.recorder,
        }

    def sync_client(self):
        return Client(APP_KEY, APP_SECRET, **self._kwargs())

    def async_client(self):
        return AsyncClient(APP_KEY, APP_SECRET, **self._kwargs())

    def no_io(self):
        """No HTTP request of any kind reached the mocked transports."""
        assert self.auth_calls == []
        assert self.business_calls == []


def _call_sync(harness, entry, args=(), kwargs=None):
    client = harness.sync_client()
    with client:
        return getattr(client.traffic, entry)(*args, **(kwargs or {}))


def _call_async(harness, entry, args=(), kwargs=None):
    client = harness.async_client()

    async def _go():
        async with client:
            return await getattr(client.traffic, entry)(*args, **(kwargs or {}))

    return asyncio.run(_go())


def _call(harness, mode, entry, args=(), kwargs=None):
    if mode == "sync":
        return _call_sync(harness, entry, args, kwargs)
    return _call_async(harness, entry, args, kwargs)


def _form(request):
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _assert_wire(request, path, expected_fields):
    assert request.method == "POST"
    assert request.url.path == path
    assert not request.url.query
    content_type = request.headers.get("content-type", "")
    assert "application/x-www-form-urlencoded" in content_type
    form = _form(request)
    expected = {"accessToken": TOKEN, **expected_fields}
    assert set(form) == set(expected)
    for key, value in expected.items():
        assert form[key] == [value]


def _assert_response(response, body):
    assert response.http_status == 200
    assert response.code == "200"
    assert response.message == "ok"
    assert response.data == body["data"]
    # ``raw`` is the original response dict, preserved verbatim.
    assert response.raw == body
    assert response.raw["unknownTop"] == "kept"


def _call_events(recorder, entry):
    events = recorder.report()["events"]
    return [e for e in events if e.get("entrypoint") == f"traffic.{entry}"]


def _assert_terminal_event(recorder, entry, mode, outcome):
    events = _call_events(recorder, entry)
    assert events, f"missing diagnostic call event for traffic.{entry}"
    last = events[-1]
    assert last.get("kind") == "call"
    assert last.get("outcome") == outcome
    assert last.get("mode") == mode


def _assert_no_private_leaks(harness, *values):
    report_json = json.dumps(harness.recorder.report())
    for value in values:
        assert value not in report_json


def test_traffic_facade_exposes_entries():
    harness = _Harness()
    client = harness.sync_client()
    with client:
        for entry in ENTRY_PATHS:
            assert callable(getattr(client.traffic, entry))
    harness.no_io()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("entry", tuple(ENTRY_PATHS))
def test_traffic_entry_wire_defaults(entry, mode):
    """Each entry hits its frozen path with only required fields sent."""
    body = _payload(ENTRY_SHAPES[entry])
    harness = _Harness(responder=_responder(body))
    response = _call(harness, mode, entry, ENTRY_ARGS[entry])
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    expected = {"deviceSerial": SERIAL} if entry == "device_details" else {}
    _assert_wire(harness.business_calls[0], ENTRY_PATHS[entry], expected)
    _assert_response(response, body)
    rendered = repr(response)
    assert APP_SECRET not in rendered
    assert TOKEN not in rendered
    _assert_no_private_leaks(harness, APP_SECRET, TOKEN)
    _assert_terminal_event(harness.recorder, entry, mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("entry", "kwargs", "expected_fields"), OPTIONAL_CASES)
def test_traffic_optional_parameters_sent_verbatim(entry, kwargs, expected_fields, mode):
    """Optional parameters become wire fields verbatim when provided."""
    body = _payload("array")
    harness = _Harness(responder=_responder(body))
    response = _call(harness, mode, entry, ENTRY_ARGS[entry], kwargs)
    assert len(harness.business_calls) == 1
    expected = dict(expected_fields)
    if entry == "device_details":
        expected["deviceSerial"] = SERIAL
    _assert_wire(harness.business_calls[0], ENTRY_PATHS[entry], expected)
    _assert_response(response, body)
    _assert_terminal_event(harness.recorder, entry, mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("serial", VERBATIM_SERIALS)
def test_device_details_serial_sent_verbatim(serial, mode):
    body = _payload("array")
    harness = _Harness(responder=_responder(body))
    response = _call(harness, mode, "device_details", (serial,))
    _assert_wire(harness.business_calls[0], DEVICE_DETAILS_PATH, {"deviceSerial": serial})
    _assert_response(response, body)
    _assert_terminal_event(harness.recorder, "device_details", mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
def test_device_details_serial_keyword(mode):
    body = _payload("array")
    harness = _Harness(responder=_responder(body))
    _call(harness, mode, "device_details", (), {"device_serial": SERIAL})
    _assert_wire(harness.business_calls[0], DEVICE_DETAILS_PATH, {"deviceSerial": SERIAL})


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("entry", ("account_details", "day_details", "device_details"))
def test_details_entries_accept_empty_rows(entry, mode):
    body = {"code": "200", "msg": "ok", "data": []}
    harness = _Harness(responder=_responder(body))
    response = _call(harness, mode, entry, ENTRY_ARGS[entry])
    assert response.data == []
    assert response.raw == body
    _assert_terminal_event(harness.recorder, entry, mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("entry", "body"), BAD_BODIES)
def test_unexpected_payload_shape_raises_protocol_error(entry, body, mode):
    """Missing, null, or wrong-layer ``data`` raises a fixed ProtocolError."""
    harness = _Harness(responder=_responder(body))
    with pytest.raises(ProtocolError):
        _call(harness, mode, entry, ENTRY_ARGS[entry])
    assert len(harness.business_calls) == 1
    _assert_terminal_event(harness.recorder, entry, mode, "failed")
    # The raw payload value must not leak into the diagnostic scope.
    _assert_no_private_leaks(harness, LEAK)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("entry", "args", "kwargs", "expect_event"), INVALID_CALLS)
def test_invalid_arguments_rejected_before_io(entry, args, kwargs, expect_event, mode):
    harness = _Harness()
    with pytest.raises((TypeError, ValueError)):
        _call(harness, mode, entry, args, kwargs)
    harness.no_io()
    if expect_event:
        _assert_terminal_event(harness.recorder, entry, mode, "failed")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("serial", BAD_SERIALS)
def test_invalid_device_serial_rejected_before_io(serial, mode):
    harness = _Harness()
    with pytest.raises((TypeError, ValueError)):
        _call(harness, mode, "device_details", (serial,))
    harness.no_io()
    _assert_terminal_event(harness.recorder, "device_details", mode, "failed")


@pytest.mark.parametrize("mode", MODES)
def test_device_details_requires_serial(mode):
    harness = _Harness()
    with pytest.raises(TypeError):
        _call(harness, mode, "device_details")
    harness.no_io()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("entry", tuple(ENTRY_PATHS))
def test_non_domestic_region_rejected(entry, mode):
    harness = _Harness(region="overseas")
    with pytest.raises(ValueError):
        _call(harness, mode, entry, ENTRY_ARGS[entry])
    harness.no_io()
    events = _call_events(harness.recorder, entry)
    assert len(events) == 2
    assert all(event.get("kind") == "call" for event in events)
    assert [event.get("outcome") for event in events] == ["started", "failed"]
    assert all(event.get("entrypoint") == f"traffic.{entry}" for event in events)
    assert all(event.get("mode") == mode for event in events)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("code", ("403", "10031"))
def test_business_error_code_raises_api_error(code, mode):
    """Permission-style codes surface as ordinary ApiError (no special-casing)."""
    body = {"code": code, "msg": "denied"}
    harness = _Harness(responder=_responder(body))
    with pytest.raises(ApiError) as excinfo:
        _call(harness, mode, "account_summary")
    assert len(harness.business_calls) == 1
    error_code = getattr(excinfo.value, "code", None)
    if error_code is not None:
        assert error_code == code
    _assert_terminal_event(harness.recorder, "account_summary", mode, "failed")
    _assert_no_private_leaks(harness, APP_SECRET)


@pytest.mark.parametrize("mode", MODES)
def test_expired_token_refreshes_once_and_recovers(mode):
    """A single 10002 triggers the bounded read-only refresh then succeeds."""
    attempts = []
    payload = _payload("object")

    def responder(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(200, json={"code": "10002", "msg": "accessToken expired"})
        return httpx.Response(200, json=dict(payload))

    harness = _Harness(responder=responder)
    response = _call(harness, mode, "account_summary")
    assert response.code == "200"
    assert response.data == payload["data"]
    assert len(harness.business_calls) == 2
    assert len(harness.auth_calls) == 2
    for request in harness.business_calls:
        assert request.url.path == SUMMARY_PATH
        assert _form(request).get("accessToken") == [TOKEN]
    _assert_terminal_event(harness.recorder, "account_summary", mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
def test_huge_page_start_rejected_before_io(mode):
    """A pageStart beyond the interpreter int->str digit cap is a fixed
    ValueError naming ``pageStart`` and never reaches auth or HTTP."""
    limit = sys.get_int_max_str_digits()
    if limit == 0:
        pytest.skip("interpreter has no int-to-str digit boundary")
    huge = 10 ** (limit + 1)
    harness = _Harness()
    with pytest.raises(ValueError, match="pageStart"):
        _call(harness, mode, "account_details", (), {"page_start": huge})
    harness.no_io()


@pytest.mark.parametrize("mode", MODES)
def test_persistent_expired_token_is_bounded(mode):
    """Refresh never loops forever: a persistent 10002 still fails fast."""
    body = {"code": "10002", "msg": "accessToken expired"}
    harness = _Harness(responder=_responder(body))
    with pytest.raises(ApiError):
        _call(harness, mode, "account_summary")
    assert 2 <= len(harness.business_calls) <= 3
    assert len(harness.auth_calls) <= 3
    _assert_terminal_event(harness.recorder, "account_summary", mode, "failed")
