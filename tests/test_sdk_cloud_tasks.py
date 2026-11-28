"""Offline contracts for the domestic cloud-recording 1.0 task list (doc 1371).

Wire: ``GET /api/v3/open/cloud/tasks`` with ``accessToken`` plus the optional
``startTime``/``endTime``/``pageNumber``/``pageSize`` all in the query.
Same-day bound refusal follows the doc-1374 ruling; ``pageSize`` has no proven
upper bound in the source, so only type and >=1 are checked. Error
classification and redaction follow evidence/codex-review.md (shared two-card
criteria).
"""

import asyncio
import json
import logging

import httpx
import pytest
from test_sdk_cloud_recording_v1 import Harness
from test_sdk_cloud_recording_v1_behaviour import (
    TOKEN_1,
    TOKEN_2,
    _events,
    _http,
    _kinds,
    _resp,
    _retries,
    _token,
    execute,
)

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

OP = "tasks"
PATH = "/api/v3/open/cloud/tasks"
TASK = "TASK_PRIVATE"
START = "20261118000000"
END = "20261118235959"
MSG = "BACKEND_PRIVATE"
STRING_FIELDS = ("taskId", "projectId", "deviceSerial", "startTime", "endTime", "createTime")
INT_FIELDS = ("taskType", "taskStatus", "fileNum", "totalSize")
NULL_ROW = dict.fromkeys((*STRING_FIELDS, *INT_FIELDS, "taskDetail"))
ROWS = [
    {
        "taskId": TASK,
        "projectId": "PROJECT_PRIVATE",
        "deviceSerial": "SERIAL_PRIVATE",
        "taskType": 1,
        "taskStatus": 2,
        "taskDetail": {"frameModel": 0},
        "fileNum": 5,
        "totalSize": 2**40,
        "startTime": START,
        "endTime": END,
        "createTime": "CREATE_PRIVATE",
        "ext": {"schema": "EXT_PRIVATE"},
    },
    {},
    NULL_ROW,
    {"taskDetail": {}},
    {"taskDetail": {"frameModel": None}},
    {"taskDetail": {"frameModel": 17, "extension": {"frameModel": "opaque"}}},
    {"taskId": "last", "future": {"remainder": None}},
]
MISS = object()


# --- structural diagnostic helpers -------------------------------------------
#
# ``tasks`` is not yet whitelisted in ``_diagnostic_registry`` (shared wiring
# belongs to a different change), so ``_diagnostic_scope`` projects its
# ``entrypoint``/``operation`` to ``"other"``. Assert on event *structure*
# rather than literal names — green both before and after registry integration.


def _facade_events(events):
    return [
        event for event in events if event["kind"] == "call" and event["parent_span_id"] is None
    ]


def _facade_span_any(events):
    """Assert exactly one started and one terminal root facade-call event."""
    calls = _facade_events(events)
    started = [event for event in calls if event["outcome"] == "started"]
    terminal = [event for event in calls if event["outcome"] != "started"]
    assert len(started) == 1, "facade call must emit exactly one started event"
    assert len(terminal) == 1, "facade call must emit exactly one terminal event"
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _facade_outcomes(events):
    return [event["outcome"] for event in _facade_events(events)]


def _attempts_any(events):
    """Started transport-attempt sub-events, independent of operation name."""
    return [
        event for event in events if event["kind"] == "attempt" and event["outcome"] == "started"
    ]


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def body(data=ROWS):
    return {
        "meta": {"code": 200, "message": MSG},
        "data": data,
        "extra": {"opaque": [None]},
    }


def expect_wire(request, token, extras):
    assert (
        request.method == "GET"
        and request.url.scheme == "https"
        and request.url.host == "open.ys7.com"
        and request.url.path == PATH
    )
    assert request.content == b"" and "content-type" not in request.headers
    assert set(request.headers).isdisjoint(
        {
            "accesstoken",
            "authorization",
            "deviceserial",
            "channelno",
            "projectid",
            "spaceid",
            "taskid",
        }
    )
    expected = [("accessToken", token), *extras]
    assert sorted(request.url.params.multi_items()) == sorted(expected)


WIRE_CASES = [
    # No optional field: only accessToken is serialized.
    ({}, []),
    # Explicit None fields are still omitted.
    (
        {"start_time": None, "end_time": None, "page_number": None, "page_size": None},
        [],
    ),
    ({"page_number": 0}, [("pageNumber", "0")]),
    # No proven upper bound: large page sizes serialize verbatim.
    ({"page_size": 50}, [("pageSize", "50")]),
    ({"page_size": 200}, [("pageSize", "200")]),
    ({"start_time": START}, [("startTime", START)]),
    ({"end_time": END}, [("endTime", END)]),
    (
        {"start_time": START, "end_time": END},
        [("startTime", START), ("endTime", END)],
    ),
    # Same instant / same-day reversed bounds are allowed.
    (
        {"start_time": START, "end_time": START},
        [("startTime", START), ("endTime", START)],
    ),
    (
        {"start_time": "20261231235959", "end_time": "20261231000000"},
        [("startTime", "20261231235959"), ("endTime", "20261231000000")],
    ),
    (
        {
            "page_number": 4294967295,
            "page_size": 1,
            "start_time": START,
            "end_time": END,
        },
        [
            ("pageNumber", "4294967295"),
            ("pageSize", "1"),
            ("startTime", START),
            ("endTime", END),
        ],
    ),
]


@pytest.mark.parametrize("kw,extras", WIRE_CASES)
def test_exact_query_and_raw_page(mode, kw, extras):
    payload = body()
    result, log = execute(mode, OP, args=(), kwargs=kw, script=[_resp(payload)])
    (req,) = _http(log)
    expect_wire(req, TOKEN_1, extras)
    assert _kinds(log) == ["get_token", "factory", "http"]
    assert result.code == 200 and type(result.code) is int
    assert result.raw == payload and result.data == ROWS and result.data is result.raw["data"]
    assert result.message == "***"


def test_empty_page(mode):
    payload = body([])
    result, log = execute(mode, OP, args=(), script=[_resp(payload)])
    assert result.data == [] and result.raw == payload and len(_http(log)) == 1


def test_overseas_guard_first(mode):
    errors = []
    for kw in [
        {"start_time": START, "end_time": END},
        {"page_size": False},
    ]:
        h = Harness(
            mode, lambda r: pytest.fail("unexpected request"), region=RegionConfig.overseas()
        )
        with pytest.raises(ValueError) as error:
            h.call(OP, **kw)
        errors.append(str(error.value))
        assert h.counts == {"auth": 0, "factory": 0, "http": 0}
        assert _facade_outcomes(_events(h.recorder)) == ["started", "failed"]
    assert errors[0] == errors[1]


CROSS_DAY = [
    ("20261118000000", "20261119000000"),
    ("20261118235959", "20261119000000"),
    ("20261231235959", "20270101000000"),
    ("20261119000000", "20261118235959"),
]


@pytest.mark.parametrize("start,end", CROSS_DAY)
def test_explicit_cross_day_refused(mode, start, end):
    """Both bounds given on different days: refused before auth, no request."""
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError):
        execute(
            mode,
            OP,
            args=(),
            kwargs={"start_time": start, "end_time": end},
            script=[],
            log=log,
            recorder=recorder,
        )
    assert log == []
    events = _events(recorder)
    terminal = _facade_span_any(events)
    assert terminal["outcome"] == "failed" and not _attempts_any(events)


BAD_INPUT = [
    *(
        ("start_time", value)
        for value in (
            "",
            "   ",
            "2026111",
            "202611180000000",
            "2026118a000000",
            "2026-11-18 00:00:00",
            "２０２６１１１８００００００",
            "20260230000000",
            "20261301000000",
            "20261118246000",
            "20261118000060",
            b"20261118000000",
            True,
            20261118000000,
            "\ud800",
        )
    ),
    *(("end_time", value) for value in ("", "20260229000000", "2026111800000a", False, 1.0, b"x")),
    *(("page_number", value) for value in (-1, True, False, 1.0, "1", b"1")),
    # No upper bound: only <1, non-int and bool are rejected.
    *(("page_size", value) for value in (0, -1, True, False, "1", 1.0, b"20")),
]


@pytest.mark.parametrize("field,bad", BAD_INPUT)
def test_invalid_field(mode, field, bad):
    log = []
    with pytest.raises(ValueError):
        execute(mode, OP, args=(), kwargs={field: bad}, script=[], log=log)
    assert log == []


@pytest.mark.parametrize(
    "args,kw",
    [
        ((0,), {}),  # all fields are keyword-only
        ((START,), {}),
        ((), {"page_start": 0}),
        ((), {"startTime": START}),
        ((), {"task_id": TASK}),
        ((), {"has_url": True}),
    ],
)
def test_signature_bounds(mode, args, kw):
    log = []
    with pytest.raises(TypeError):
        execute(mode, OP, args=args, kwargs=kw, script=[], log=log)
    assert log == []


INVALID_ROWS = [
    MISS,
    None,
    {},
    {"tasks": []},
    [None],
    [3],
    ["not-a-row"],
    [[], {}],
    *([{field: bad}] for field in STRING_FIELDS for bad in (True, 12, {}, [])),
    *([{field: bad}] for field in INT_FIELDS for bad in (True, 0.5, "12", {}, [])),
    *([{"taskDetail": bad}] for bad in (True, "x", [], 1)),
    *([{"taskDetail": {"frameModel": bad}}] for bad in (True, "1", 1.0, {}, [])),
]


@pytest.mark.parametrize("data", INVALID_ROWS)
def test_reject_bad_rows(mode, data):
    payload = {"meta": {"code": 200, "message": MSG}}
    if data is not MISS:
        payload["data"] = data
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ProtocolError):
        execute(mode, OP, args=(), script=[_resp(payload)], log=log, recorder=recorder)
    assert _kinds(log) == ["get_token", "factory", "http"] and not _retries(_events(recorder))


@pytest.mark.parametrize(
    "envelope,exc",
    [
        ({"code": "200", "data": []}, ProtocolError),
        ({"body": {"meta": {"code": 200}, "data": []}}, ProtocolError),
        ({"meta": {"code": "200"}, "data": []}, ApiError),
        ({"meta": {"code": True}, "data": []}, ProtocolError),
        ({"meta": {"code": 200.0}, "data": []}, ProtocolError),
    ],
)
def test_bad_envelope(mode, envelope, exc):
    log = []
    with pytest.raises(exc) as error:
        execute(mode, OP, args=(), script=[_resp(envelope)], log=log)
    assert type(error.value) is exc and len(_http(log)) == 1


@pytest.mark.parametrize("code", [10002, "10002"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_refresh_same_page_once(mode, code, exhausted):
    log = []
    recorder = DiagnosticRecorder()
    stale = {"meta": {"code": code, "message": MSG}}
    kw = {"start_time": START, "end_time": END, "page_number": 3, "page_size": 9}

    def run():
        return execute(
            mode,
            OP,
            args=(),
            kwargs=kw,
            script=[_resp(stale), _resp(stale if exhausted else body())],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
            recorder=recorder,
        )

    if exhausted:
        with pytest.raises(AuthenticationError) as err:
            run()
        assert err.value.code == code and type(err.value.code) is type(code)
    else:
        assert run()[0].data == ROWS
    calls = _http(log)
    assert len(calls) == 2
    extras = [
        ("startTime", START),
        ("endTime", END),
        ("pageNumber", "3"),
        ("pageSize", "9"),
    ]
    for req, token in zip(calls, (TOKEN_1, TOKEN_2), strict=True):
        expect_wire(req, token, extras)
    events = _events(recorder)
    assert len(_attempts_any(events)) == 2 and [v["outcome"] for v in _retries(events)] == [
        "scheduled"
    ]


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (200, 10031, PermissionDeniedError),
        (200, "10031", PermissionDeniedError),
        (403, MISS, PermissionDeniedError),
        (200, 400, ApiError),
        (200, "400", ApiError),
        (200, 403, ApiError),
        (200, "403", ApiError),
        (200, 500, ApiError),
        (200, "500", ApiError),
        (200, "200", ApiError),
        # 404 is not in the doc-1371 error table, so an unexpected code on a
        # non-200 status is masked by the redaction policy.
        (503, 404, ApiError),
        (503, "404", ApiError),
    ],
)
def test_visible_codes_and_permissions(mode, status, code, expected):
    meta = {"message": MSG}
    if code is not MISS:
        meta["code"] = code
    log = []
    with pytest.raises(expected) as error:
        execute(mode, OP, args=(), script=[_resp({"meta": meta}, status)], log=log)
    assert (
        type(error.value) is expected
        and error.value.http_status == status
        and error.value.message == "***"
    )
    if code is MISS:
        assert error.value.code is None
    elif status == 200:
        assert error.value.code == code and type(error.value.code) is type(code)
    else:
        # The policy exposes only the documented business codes; an
        # undocumented code on an error status stays masked.
        assert error.value.code == "***"
    assert _kinds(log) == ["get_token", "factory", "http"]


@pytest.mark.parametrize("raw_code", [200, "UNTRUSTED_PRIVATE", 92851743])
def test_privacy(mode, raw_code, caplog):
    payload = body()
    payload["meta"].update(code=raw_code, message=MSG + TASK + "SERIAL_PRIVATE")
    resp = httpx.Response(
        200,
        json=payload,
        extensions={"http_version": b"VERSION_PRIVATE", "reason_phrase": b"REASON_PRIVATE"},
    )
    recorder = DiagnosticRecorder()
    log = []

    def run():
        return execute(
            mode,
            OP,
            args=(),
            kwargs={"start_time": START, "end_time": END},
            script=[resp],
            recorder=recorder,
            log=log,
        )[0]

    with caplog.at_level(logging.INFO, logger="httpx"):
        if raw_code == 200:
            result = run()
            assert result.raw == payload and result.data == ROWS and result.message == "***"
            surfaces = [str(result), repr(result), result.message]
        else:
            with pytest.raises(ApiError) as exc:
                run()
            err = exc.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), repr(err.args)]
    assert len(_http(log)) == 1
    events = _events(recorder)
    assert len(_attempts_any(events)) == 1 and not _retries(events)
    assert _facade_span_any(events)["outcome"] == ("succeeded" if raw_code == 200 else "failed")
    lines = [
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    ]
    assert len(lines) == 1 and "?" not in lines[0] and '"HTTP 200 ***"' in lines[0]
    shown = " ".join(
        surfaces + [json.dumps(events, ensure_ascii=False), recorder.to_json(), caplog.text]
    )
    for marker in (
        TOKEN_1,
        "TASK_PRIVATE",
        "PROJECT_PRIVATE",
        "SERIAL_PRIVATE",
        "EXT_PRIVATE",
        "CREATE_PRIVATE",
        START,
        END,
        MSG,
        "REASON_PRIVATE",
        "VERSION_PRIVATE",
        "UNTRUSTED_PRIVATE",
        "92851743",
    ):
        assert marker not in shown


@pytest.mark.parametrize(
    "problem,error",
    [
        (httpx.ReadTimeout("TIMEOUT_PRIVATE"), TransportError),
        (httpx.ConnectError("CONNECT_PRIVATE"), TransportError),
    ],
)
def test_transport_failure_is_final(mode, problem, error):
    log = []
    rec = DiagnosticRecorder()
    with pytest.raises(error):
        execute(mode, OP, args=(), script=[problem], log=log, recorder=rec)
    assert len(_http(log)) == 1 and not _retries(_events(rec))


def test_async_cancel_propagates():
    log = []
    rec = DiagnosticRecorder()
    with pytest.raises(asyncio.CancelledError):
        execute(
            "async",
            OP,
            args=(),
            script=[asyncio.CancelledError()],
            log=log,
            recorder=rec,
        )
    assert len(_http(log)) == 1 and _facade_span_any(_events(rec))["outcome"] == "cancelled"
