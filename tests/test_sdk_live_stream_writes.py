"""Contract tests for the five domestic live-stream write operations.

Covered operations (all ``replay=False``):

* ``create_device``   — POST ``/api/service/media/streammanage/stream`` (form)
* ``create_rtmp``     — POST ``/api/service/media/streammanage/stream`` (query)
* ``update_window``   — PUT  ``/api/service/media/streammanage/stream`` (query)
* ``delete``          — DELETE ``/api/service/media/streammanage/stream`` (query)
* ``set_status``      — PUT  ``/api/service/media/streammanage/stream/status`` (query)

Every credential, identifier and endpoint used here is a synthetic sentinel.
The auth transport and the per-origin business transport are fully mocked, so
no network traffic is ever produced. ``Client`` (sync) and ``AsyncClient``
(async) are exercised through the same scenario tables.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, quote, quote_plus

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

# --------------------------------------------------------------------------
# Synthetic sentinels — no real credentials, IDs, endpoints or media URLs.
# --------------------------------------------------------------------------

APP_KEY = "SYNTH_APP"
APP_SECRET = "SYNTH_SECRET"
ACCESS_TOKEN = "TOKEN_A"
AREA_DOMAIN = "https://open.ys7.com"

DEVICE_SERIAL = "SYNTH-DEVICE-7"
STREAM_ID = "PRIV STREAM/42"  # opaque, nonnumeric, needs URL encoding
RECORD_SPACE_ID = "PRIV RECORD/7"
RESP_STREAM_ID = "STR-0042-XYZ"  # opaque nonnumeric streamId from server

TOKEN_PATH = "/api/lapp/token/get"
STREAM_PATH = "/api/service/media/streammanage/stream"
STATUS_PATH = "/api/service/media/streammanage/stream/status"

AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": ACCESS_TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": AREA_DOMAIN,
    },
}

# Private inputs that must never appear in diagnostics or logs, encoded or not.
PRIVATE_INPUTS = (
    APP_KEY,
    APP_SECRET,
    ACCESS_TOKEN,
    DEVICE_SERIAL,
    STREAM_ID,
    RECORD_SPACE_ID,
)

T1 = datetime(2026, 3, 1, 0, 0, 0)
T2 = datetime(2026, 3, 5, 12, 30, 45)
T3 = datetime(2026, 3, 20, 8, 0, 0)

CREATE_KW = dict(start_time=T1, end_time=T2)


def ts(value):
    """Second-precision timestamp format used by the domestic stream API."""
    return value.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _caplog_all_levels(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


# --------------------------------------------------------------------------
# Harness helpers.
# --------------------------------------------------------------------------


def build(biz_handler):
    """Return (recorder, request-log, auth transport, business transport)."""
    recorder = DiagnosticRecorder()
    state = {"auth": [], "biz": []}

    def _auth(request):
        state["auth"].append(request)
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _biz(request):
        state["biz"].append(request)
        return biz_handler(request)

    return recorder, state, httpx.MockTransport(_auth), httpx.MockTransport(_biz)


def invoke(mode, op, args, kwargs, recorder, auth_transport, biz_transport):
    """Call ``client.live_streams.<op>(*args, **kwargs)`` in the given mode."""
    options = dict(
        region=RegionConfig.china(),
        auth_transport=auth_transport,
        transport_factory=lambda origin: biz_transport,
        diagnostic_recorder=recorder,
    )
    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **options) as client:
            return getattr(client.live_streams, op)(*args, **kwargs)

    async def _run():
        async with AsyncClient(APP_KEY, APP_SECRET, **options) as client:
            return await getattr(client.live_streams, op)(*args, **kwargs)

    return asyncio.run(_run())


def outcomes(recorder, kind, name, mode):
    """Outcome sequence for call or attempt events of one operation."""
    key = "entrypoint" if kind == "call" else "operation"
    return [
        event["outcome"]
        for event in recorder.report()["events"]
        if event["kind"] == kind and event[key] == name and event["mode"] == mode
    ]


def _query_dict(request):
    raw = request.url.query
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return dict(parse_qsl(raw, keep_blank_values=True))


def _form_dict(request):
    return dict(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))


def assert_wire(request, *, method, path, headers, query, form):
    """Assert strict method/path plus exact header/form/query placement."""
    assert request.method == method
    assert request.url.path == path
    assert _query_dict(request) == query
    assert _form_dict(request) == form
    if form:
        assert "application/x-www-form-urlencoded" in request.headers.get("content-type", "")
    else:
        # Query-style operations must not carry a request body.
        assert request.content == b""
    for name, value in headers.items():
        assert request.headers[name] == value
        # Original header-name casing and value casing are preserved on the wire.
        assert (name.encode("latin-1"), value.encode("latin-1")) in request.headers.raw


def assert_single_flow(state):
    """Exactly one auth POST followed by exactly one business request."""
    assert len(state["auth"]) == 1
    auth_req = state["auth"][0]
    assert auth_req.method == "POST"
    assert auth_req.url.path == TOKEN_PATH
    assert len(state["biz"]) == 1
    return state["biz"][0]


def assert_no_private_leaks(recorder, caplog):
    blob = json.dumps(recorder.report(), default=str)
    blob += "\n" + "\n".join(record.getMessage() for record in caplog.records)
    for secret in PRIVATE_INPUTS:
        assert secret not in blob
        for encoded in {quote(secret, safe=""), quote_plus(secret, safe="")}:
            assert encoded not in blob


def test_id_encoding_privacy_check_is_nonvacuous():
    """Guard: sentinels needing URL encoding must really change when encoded,
    so the leak scan above would actually catch a leaked encoded value."""
    nonvacuous = []
    for secret in PRIVATE_INPUTS:
        variants = {quote(secret, safe=""), quote_plus(secret, safe="")}
        if variants != {secret}:
            nonvacuous.append(secret)
            # Strict: no variant may collapse back to the raw secret.
            assert secret not in variants
    # STREAM_ID and RECORD_SPACE_ID contain space+'/' and must encode; if the
    # sentinels were ever swapped to plain alphanumerics this test fails.
    assert STREAM_ID in nonvacuous
    assert RECORD_SPACE_ID in nonvacuous


# --------------------------------------------------------------------------
# Happy-path wire contracts for all five write operations x both modes.
# --------------------------------------------------------------------------

HAPPY_SCENARIOS = [
    dict(
        id="create_device/with-channel",
        op="create_device",
        args=(DEVICE_SERIAL,),
        kwargs=dict(start_time=T1, end_time=T2, local_index=3),
        method="POST",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN, "deviceSerial": DEVICE_SERIAL},
        forbidden_headers=(),
        query={},
        form={
            "accessType": "1",
            "localIndex": "3",
            "startTime": ts(T1),
            "endTime": ts(T2),
        },
        resp={
            "meta": {"code": 200, "message": "created"},
            "data": {
                "streamId": RESP_STREAM_ID,
                "extraFlag": "KEEP-ME",
                "extraNum": 7,
                "extraList": [1, "a"],
            },
        },
    ),
    dict(
        id="create_device/default-channel",
        op="create_device",
        args=(DEVICE_SERIAL,),
        kwargs=dict(start_time=T1, end_time=T2),
        method="POST",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN, "deviceSerial": DEVICE_SERIAL},
        forbidden_headers=(),
        query={},
        # localIndex omitted entirely when the kwarg is left as None.
        form={"accessType": "1", "startTime": ts(T1), "endTime": ts(T2)},
        resp={
            "meta": {"code": 200, "message": "created"},
            "data": {"streamId": RESP_STREAM_ID},
        },
    ),
    dict(
        id="create_rtmp/minimal",
        op="create_rtmp",
        args=(),
        kwargs=dict(start_time=T1, end_time=T3),
        method="POST",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        query={"accessType": "2", "startTime": ts(T1), "endTime": ts(T3)},
        form={},
        resp={
            "meta": {"code": 200, "message": "created"},
            "data": {"streamId": RESP_STREAM_ID, "rawExtra": "kept"},
        },
    ),
    dict(
        id="create_rtmp/full-record-options",
        op="create_rtmp",
        args=(),
        kwargs=dict(
            start_time=T1,
            end_time=T3,
            enable_record=1,
            record_space_id=RECORD_SPACE_ID,
            record_duration=900,
            record_format=2,
            delay_time=15,
        ),
        method="POST",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        query={
            "accessType": "2",
            "startTime": ts(T1),
            "endTime": ts(T3),
            "enableRecord": "1",
            "recordSpaceId": RECORD_SPACE_ID,
            "recordDuration": "900",
            "recordFormat": "2",
            "delayTime": "15",
        },
        form={},
        resp={
            "meta": {"code": 200, "message": "created"},
            "data": {"streamId": RESP_STREAM_ID},
        },
    ),
    dict(
        # Documented upper endpoints: recordDuration 7200, delayTime 21600.
        id="create_rtmp/record-option-upper-bounds",
        op="create_rtmp",
        args=(),
        kwargs=dict(
            start_time=T1,
            end_time=T3,
            enable_record=1,
            record_space_id=RECORD_SPACE_ID,
            record_duration=7200,
            record_format=2,
            delay_time=21600,
        ),
        method="POST",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        query={
            "accessType": "2",
            "startTime": ts(T1),
            "endTime": ts(T3),
            "enableRecord": "1",
            "recordSpaceId": RECORD_SPACE_ID,
            "recordDuration": "7200",
            "recordFormat": "2",
            "delayTime": "21600",
        },
        form={},
        resp={
            "meta": {"code": 200, "message": "created"},
            "data": {"streamId": RESP_STREAM_ID},
        },
    ),
    dict(
        id="update_window",
        op="update_window",
        args=(STREAM_ID,),
        kwargs=dict(start_time=T1, end_time=T2),
        method="PUT",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        query={"streamId": STREAM_ID, "startTime": ts(T1), "endTime": ts(T2)},
        form={},
        resp={"meta": {"code": 200, "message": "ack"}},
    ),
    dict(
        id="delete",
        op="delete",
        args=(STREAM_ID,),
        kwargs={},
        method="DELETE",
        path=STREAM_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        query={"streamId": STREAM_ID},
        form={},
        resp={"meta": {"code": 200, "message": "ack"}},
    ),
    dict(
        id="set_status/disable",
        op="set_status",
        args=(STREAM_ID, 0),
        kwargs={},
        method="PUT",
        path=STATUS_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        # status=0 is falsy but must still be transmitted.
        query={"streamId": STREAM_ID, "status": "0"},
        form={},
        resp={"meta": {"code": 200, "message": "ack"}},
    ),
    dict(
        id="set_status/enable",
        op="set_status",
        args=(STREAM_ID, 1),
        kwargs={},
        method="PUT",
        path=STATUS_PATH,
        headers={"accessToken": ACCESS_TOKEN},
        forbidden_headers=("deviceSerial",),
        query={"streamId": STREAM_ID, "status": "1"},
        form={},
        resp={"meta": {"code": 200, "message": "ack"}},
    ),
]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("sc", HAPPY_SCENARIOS, ids=[s["id"] for s in HAPPY_SCENARIOS])
def test_write_wire_and_response(mode, sc, caplog):
    recorder, state, auth_t, biz_t = build(lambda req: httpx.Response(200, json=sc["resp"]))
    result = invoke(mode, sc["op"], sc["args"], sc["kwargs"], recorder, auth_t, biz_t)

    request = assert_single_flow(state)
    assert_wire(
        request,
        method=sc["method"],
        path=sc["path"],
        headers=sc["headers"],
        query=sc["query"],
        form=sc["form"],
    )
    for name in sc["forbidden_headers"]:
        assert request.headers.get(name) is None

    assert result.code == 200
    assert result.message == sc["resp"]["meta"]["message"]
    assert result.operation == "live_streams." + sc["op"]
    if "data" in sc["resp"]:
        # Shaped data keeps the raw payload including extra fields, and the
        # streamId stays an opaque nonnumeric string.
        assert result.data == sc["resp"]["data"]
        assert result.data["streamId"] == RESP_STREAM_ID
        assert type(result.data["streamId"]) is str
    else:
        # ACK-only operations (update_window/delete/set_status) expose no data.
        assert result.data is None
    if sc["id"] == "create_device/with-channel":
        assert result.raw == sc["resp"]

    name = "live_streams." + sc["op"]
    assert outcomes(recorder, "call", name, mode) == ["started", "succeeded"]
    assert outcomes(recorder, "attempt", name, mode) == ["started", "succeeded"]
    assert_no_private_leaks(recorder, caplog)


# --------------------------------------------------------------------------
# Interval boundaries: device create caps at 7 days, RTMP does not, and
# update_window applies no local 7/720-day ceiling.
# --------------------------------------------------------------------------

BOUNDARY_OK = [
    (
        "create_device/exactly-seven-days",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time=T1, end_time=T1 + timedelta(days=7)),
    ),
    (
        "create_rtmp/thirty-days-allowed",
        "create_rtmp",
        (),
        dict(start_time=T1, end_time=T1 + timedelta(days=30)),
    ),
    (
        "update_window/beyond-720-days-allowed",
        "update_window",
        (STREAM_ID,),
        dict(start_time=T1, end_time=T1 + timedelta(days=800)),
    ),
]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("case", BOUNDARY_OK, ids=[c[0] for c in BOUNDARY_OK])
def test_interval_boundaries_allowed(mode, case, caplog):
    _, op, args, kwargs = case
    resp = {"meta": {"code": 200, "message": "ok"}}
    if op.startswith("create"):
        resp["data"] = {"streamId": RESP_STREAM_ID}
    recorder, state, auth_t, biz_t = build(lambda req: httpx.Response(200, json=resp))

    result = invoke(mode, op, args, kwargs, recorder, auth_t, biz_t)

    request = assert_single_flow(state)
    wire = _form_dict(request) if op == "create_device" else _query_dict(request)
    assert wire["startTime"] == ts(kwargs["start_time"])
    assert wire["endTime"] == ts(kwargs["end_time"])

    name = "live_streams." + op
    assert outcomes(recorder, "call", name, mode) == ["started", "succeeded"]
    assert outcomes(recorder, "attempt", name, mode) == ["started", "succeeded"]
    if op.startswith("create"):
        assert result.data["streamId"] == RESP_STREAM_ID
    else:
        assert result.data is None
    assert_no_private_leaks(recorder, caplog)


# --------------------------------------------------------------------------
# Client-side validation: bad inputs fail before any auth/business request.
# --------------------------------------------------------------------------

BAD_CALLS = [
    # id, op, args, kwargs, expected_exc, check_call_diagnostics
    # Rows that reach the method through the correct signature but carry a
    # wrong-typed or out-of-range argument must surface ValueError exactly.
    # Only genuine signature violations (positional args, unknown kwargs)
    # surface TypeError.
    ("create_device/blank-serial", "create_device", ("",), dict(CREATE_KW), ValueError, True),
    ("create_device/space-serial", "create_device", ("   ",), dict(CREATE_KW), ValueError, True),
    ("create_device/int-serial", "create_device", (123,), dict(CREATE_KW), ValueError, True),
    (
        "create_device/equal-times",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time=T1, end_time=T1),
        ValueError,
        True,
    ),
    (
        "create_device/reversed-times",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time=T2, end_time=T1),
        ValueError,
        True,
    ),
    (
        "create_device/over-seven-days",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time=T1, end_time=T1 + timedelta(days=7, seconds=1)),
        ValueError,
        True,
    ),
    (
        "create_device/str-start",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time="2026-03-01", end_time=T2),
        ValueError,
        True,
    ),
    (
        "create_device/int-end",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time=T1, end_time=1770000000),
        ValueError,
        True,
    ),
    (
        "create_device/date-start",
        "create_device",
        (DEVICE_SERIAL,),
        dict(start_time=date(2026, 3, 1), end_time=T2),
        ValueError,
        True,
    ),
    (
        "create_device/bool-local",
        "create_device",
        (DEVICE_SERIAL,),
        dict(CREATE_KW, local_index=True),
        ValueError,
        True,
    ),
    (
        "create_device/str-local",
        "create_device",
        (DEVICE_SERIAL,),
        dict(CREATE_KW, local_index="3"),
        ValueError,
        True,
    ),
    (
        "create_rtmp/equal-times",
        "create_rtmp",
        (),
        dict(start_time=T1, end_time=T1),
        ValueError,
        True,
    ),
    (
        "create_rtmp/reversed-times",
        "create_rtmp",
        (),
        dict(start_time=T2, end_time=T1),
        ValueError,
        True,
    ),
    (
        "create_rtmp/str-end",
        "create_rtmp",
        (),
        dict(start_time=T1, end_time="not-a-time"),
        ValueError,
        True,
    ),
    (
        "create_rtmp/bool-enable",
        "create_rtmp",
        (),
        dict(CREATE_KW, enable_record=True),
        ValueError,
        True,
    ),
    (
        "create_rtmp/int-space-id",
        "create_rtmp",
        (),
        dict(CREATE_KW, record_space_id=123),
        ValueError,
        True,
    ),
    # recordDuration endpoints: documented range 900..7200, both rejected
    # before any auth request is issued.
    (
        "create_rtmp/record-duration-899",
        "create_rtmp",
        (),
        dict(CREATE_KW, enable_record=1, record_duration=899),
        ValueError,
        True,
    ),
    (
        "create_rtmp/record-duration-7201",
        "create_rtmp",
        (),
        dict(CREATE_KW, enable_record=1, record_duration=7201),
        ValueError,
        True,
    ),
    # delayTime endpoints: documented range 15..21600, both rejected
    # before any auth request is issued.
    (
        "create_rtmp/delay-time-14",
        "create_rtmp",
        (),
        dict(CREATE_KW, enable_record=1, delay_time=14),
        ValueError,
        True,
    ),
    (
        "create_rtmp/delay-time-21601",
        "create_rtmp",
        (),
        dict(CREATE_KW, enable_record=1, delay_time=21601),
        ValueError,
        True,
    ),
    ("create_rtmp/positional-forbidden", "create_rtmp", (T1, T2), {}, TypeError, False),
    ("update_window/blank-id", "update_window", ("",), dict(CREATE_KW), ValueError, True),
    ("update_window/none-id", "update_window", (None,), dict(CREATE_KW), ValueError, True),
    ("update_window/int-id", "update_window", (9,), dict(CREATE_KW), ValueError, True),
    (
        "update_window/equal-times",
        "update_window",
        (STREAM_ID,),
        dict(start_time=T1, end_time=T1),
        ValueError,
        True,
    ),
    (
        "update_window/str-start",
        "update_window",
        (STREAM_ID,),
        dict(start_time="2026-03-01", end_time=T2),
        ValueError,
        True,
    ),
    ("delete/blank-id", "delete", ("",), {}, ValueError, True),
    ("delete/space-id", "delete", ("  ",), {}, ValueError, True),
    ("delete/none-id", "delete", (None,), {}, ValueError, True),
    ("delete/int-id", "delete", (123,), {}, ValueError, True),
    ("delete/foreign-kwarg", "delete", (STREAM_ID,), {"unexpected": 1}, TypeError, False),
    ("set_status/out-of-range", "set_status", (STREAM_ID, 2), {}, ValueError, True),
    ("set_status/negative", "set_status", (STREAM_ID, -1), {}, ValueError, True),
    ("set_status/bool", "set_status", (STREAM_ID, True), {}, ValueError, True),
    ("set_status/str", "set_status", (STREAM_ID, "1"), {}, ValueError, True),
    ("set_status/float", "set_status", (STREAM_ID, 1.0), {}, ValueError, True),
    ("set_status/blank-id", "set_status", ("", 1), {}, ValueError, True),
]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("case", BAD_CALLS, ids=[c[0] for c in BAD_CALLS])
def test_invalid_inputs_blocked_before_auth(mode, case, caplog):
    _, op, args, kwargs, exc_type, check_call_diag = case
    recorder, state, auth_t, biz_t = build(
        lambda req: httpx.Response(
            200,
            json={"meta": {"code": 200, "message": "ok"}, "data": {"streamId": RESP_STREAM_ID}},
        )
    )

    with pytest.raises(exc_type) as excinfo:
        invoke(mode, op, args, kwargs, recorder, auth_t, biz_t)
    # Precise exception, not a subclass/sibling: e.g. a signature violation
    # must not be reported as an argument-validation ValueError.
    assert type(excinfo.value) is exc_type

    # Pre-auth validation: zero auth and zero business requests.
    assert state["auth"] == []
    assert state["biz"] == []
    name = "live_streams." + op
    assert outcomes(recorder, "attempt", name, mode) == []
    if check_call_diag:
        assert outcomes(recorder, "call", name, mode) == ["started", "failed"]
    assert_no_private_leaks(recorder, caplog)


# --------------------------------------------------------------------------
# Business error mapping. Even on failures: one auth + one business request.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "meta_code,exc_type",
    [
        (10002, AuthenticationError),
        (10031, PermissionDeniedError),
        (10500, ApiError),
    ],
    ids=["10002-auth", "10031-permission", "10500-generic"],
)
def test_business_code_errors(mode, meta_code, exc_type, caplog):
    recorder, state, auth_t, biz_t = build(
        lambda req: httpx.Response(200, json={"meta": {"code": meta_code, "message": "denied"}})
    )

    with pytest.raises(exc_type) as excinfo:
        invoke(mode, "delete", (STREAM_ID,), {}, recorder, auth_t, biz_t)

    assert type(excinfo.value) is exc_type
    if exc_type is ApiError:
        assert not isinstance(
            excinfo.value,
            (AuthenticationError, PermissionDeniedError, ProtocolError, TransportError),
        )
    # No re-auth, no retry: exactly one auth + one business request.
    assert len(state["auth"]) == 1
    assert len(state["biz"]) == 1
    name = "live_streams.delete"
    assert outcomes(recorder, "call", name, mode) == ["started", "failed"]
    assert outcomes(recorder, "attempt", name, mode) == ["started", "failed"]
    assert_no_private_leaks(recorder, caplog)


def test_error_types_are_distinct():
    assert AuthenticationError is not PermissionDeniedError


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_read_timeout_maps_transport_error(mode, caplog):
    def _boom(request):
        raise httpx.ReadTimeout("boom", request=request)

    recorder, state, auth_t, biz_t = build(_boom)
    with pytest.raises(TransportError) as excinfo:
        invoke(mode, "update_window", (STREAM_ID,), dict(CREATE_KW), recorder, auth_t, biz_t)

    assert excinfo.value.reason == "timeout"
    assert len(state["auth"]) == 1
    assert len(state["biz"]) == 1
    name = "live_streams.update_window"
    assert outcomes(recorder, "call", name, mode) == ["started", "failed"]
    assert outcomes(recorder, "attempt", name, mode) == ["started", "failed"]
    assert_no_private_leaks(recorder, caplog)


# --------------------------------------------------------------------------
# Response-shape boundary: meta.code == 200 but malformed `data` must raise
# ProtocolError *after* the attempt succeeded.
# --------------------------------------------------------------------------

OK_META = {"meta": {"code": 200, "message": "ok"}}

BAD_CREATE_RESPONSES = [
    ("data-list", dict(OK_META, data=[])),
    ("data-str", dict(OK_META, data="text")),
    ("data-int", dict(OK_META, data=5)),
    ("data-empty-dict", dict(OK_META, data={})),
    ("streamId-blank", dict(OK_META, data={"streamId": ""})),
    ("streamId-space", dict(OK_META, data={"streamId": "   "})),
    ("streamId-int", dict(OK_META, data={"streamId": 123})),
    ("streamId-none", dict(OK_META, data={"streamId": None})),
    ("streamId-bool", dict(OK_META, data={"streamId": True})),
    ("streamId-missing", dict(OK_META, data={"other": "x"})),
    ("data-missing", dict(OK_META)),
]

CREATE_TARGETS = [
    ("create_device", (DEVICE_SERIAL,), dict(CREATE_KW)),
    ("create_rtmp", (), dict(CREATE_KW)),
]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("target", CREATE_TARGETS, ids=[t[0] for t in CREATE_TARGETS])
@pytest.mark.parametrize(
    "resp", [r for _, r in BAD_CREATE_RESPONSES], ids=[i for i, _ in BAD_CREATE_RESPONSES]
)
def test_create_malformed_data_is_protocol_error(mode, target, resp, caplog):
    op, args, kwargs = target
    recorder, state, auth_t, biz_t = build(lambda req: httpx.Response(200, json=resp))

    with pytest.raises(ProtocolError):
        invoke(mode, op, args, kwargs, recorder, auth_t, biz_t)

    assert len(state["auth"]) == 1
    assert len(state["biz"]) == 1
    name = "live_streams." + op
    # Attempt succeeded; shaping the response is what failed.
    assert outcomes(recorder, "call", name, mode) == ["started", "failed"]
    assert outcomes(recorder, "attempt", name, mode) == ["started", "succeeded"]
    assert_no_private_leaks(recorder, caplog)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_string_meta_code_is_not_success(mode, caplog):
    resp = {"meta": {"code": "200", "message": "ok"}, "data": {"streamId": RESP_STREAM_ID}}
    recorder, state, auth_t, biz_t = build(lambda req: httpx.Response(200, json=resp))

    with pytest.raises(ApiError) as excinfo:
        invoke(mode, "create_device", (DEVICE_SERIAL,), dict(CREATE_KW), recorder, auth_t, biz_t)

    # A string meta.code '200' is a generic API failure, never a protocol
    # violation and never one of the specialized subclasses.
    assert type(excinfo.value) is ApiError
    assert len(state["biz"]) == 1
    name = "live_streams.create_device"
    assert outcomes(recorder, "call", name, mode) == ["started", "failed"]
    assert_no_private_leaks(recorder, caplog)
