"""Offline contracts for one domestic cloud-recording 1.0 project-file page (doc 1374).

Wire: ``GET /api/open/cloud/v1/files`` with ``accessToken``/``projectId`` and the
optional ``startTime``/``endTime``/``pageNumber``/``pageSize`` all in the query.
Same-day bound refusal, error classification and redaction follow the reviewed
rulings in evidence/codex-review.md (shared two-card criteria).
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

OP = "project_files"
PATH = "/api/open/cloud/v1/files"
PROJECT = " 项目 PROJECT_PRIVATE +&%/ " + "X" * 43
FILE = "E1" + "f" * 101 + "FILE_PRIVATE"
TASK = "TASK_PRIVATE"
START = "20261118000000"
END = "20261118235959"
MSG = "BACKEND_PRIVATE"
URL = "https://synthetic-media.invalid/NO_MEDIA_PRIVATE"
STRING_FIELDS = ("projectId", "fileId", "taskId", "createTime")
INT_FIELDS = ("fileType", "status", "fileSize", "duration", "storageType")
NULL_ROW = dict.fromkeys((*STRING_FIELDS, *INT_FIELDS))
ROWS = [
    {
        "projectId": "PROJECT_PRIVATE",
        "fileId": FILE,
        "taskId": TASK,
        "createTime": "CREATE_PRIVATE",
        "fileType": 78,
        "status": -7,
        "duration": 0,
        "fileSize": 2**40,
        "storageType": 97,
        "fileUrl": URL,
        "ext": {"schema": "EXT_PRIVATE"},
    },
    {},
    NULL_ROW,
    {"fileId": "last", "future": {"remainder": None}},
]
MISS = object()


# --- diagnostic-name assertions ----------------------------------------------
#
# ``project_files`` is whitelisted in ``_diagnostic_registry`` as
# ``cloud_recording_v1.project_files``, so ``_diagnostic_scope`` now projects
# the facade ``entrypoint`` and the transport ``operation`` onto that literal
# name. These helpers assert the registered name permanently: any regression
# that drops the entrypoint/operation back to ``"other"`` fails CI here.
#
#   * the facade call span is the unique ``kind=="call"`` pair emitted at the
#     root (``parent_span_id is None``); its ``entrypoint`` must equal
#     ``"cloud_recording_v1.project_files"`` on both edges;
#   * transport attempts are ``kind=="attempt"`` events, with the 1-based try
#     number in ``attempt`` and ``outcome=="started"`` marking the started edge;
#     their ``operation`` must equal ``"cloud_recording_v1.project_files"``;
#   * retries are ``kind=="retry"`` events (already structural via ``_retries``).

_ENTRY = "cloud_recording_v1.project_files"


def _facade_events(events):
    return [
        event for event in events if event["kind"] == "call" and event["parent_span_id"] is None
    ]


def _facade_span_any(events):
    """Assert exactly one started and one terminal root facade-call event.

    Both edges must name the registered ``cloud_recording_v1.project_files``
    entrypoint — a span projected to ``"other"`` fails here.
    """
    calls = _facade_events(events)
    started = [event for event in calls if event["outcome"] == "started"]
    terminal = [event for event in calls if event["outcome"] != "started"]
    assert len(started) == 1, "facade call must emit exactly one started event"
    assert len(terminal) == 1, "facade call must emit exactly one terminal event"
    assert started[0]["span_id"] == terminal[0]["span_id"]
    assert started[0]["entrypoint"] == terminal[0]["entrypoint"] == _ENTRY
    return terminal[0]


def _facade_outcomes(events):
    calls = _facade_events(events)
    assert calls and all(event["entrypoint"] == _ENTRY for event in calls)
    return [event["outcome"] for event in calls]


def _attempts_any(events):
    """Started transport-attempt sub-events naming ``project_files``."""
    attempts = [
        event for event in events if event["kind"] == "attempt" and event["outcome"] == "started"
    ]
    assert all(event["operation"] == _ENTRY for event in attempts)
    return attempts


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def body(data=ROWS):
    return {
        "meta": {"code": 200, "message": MSG, "moreInfo": None},
        "data": data,
        "extra": {"opaque": [None]},
    }


def expect_wire(request, token, project, extras):
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
    expected = [("accessToken", token), ("projectId", project), *extras]
    assert sorted(request.url.params.multi_items()) == sorted(expected)


WIRE_CASES = [
    # No optional field: only accessToken + projectId are serialized.
    ({}, []),
    # Explicit None fields are still omitted.
    (
        {"start_time": None, "end_time": None, "page_number": None, "page_size": None},
        [],
    ),
    (
        {"start_time": START},
        [("startTime", START)],
    ),
    (
        {"end_time": END},
        [("endTime", END)],
    ),
    (
        {"start_time": START, "end_time": END},
        [("startTime", START), ("endTime", END)],
    ),
    # Same instant / same day boundaries are allowed.
    (
        {"start_time": START, "end_time": START},
        [("startTime", START), ("endTime", START)],
    ),
    (
        {"start_time": "20261231235959", "end_time": "20261231000000"},
        [("startTime", "20261231235959"), ("endTime", "20261231000000")],
    ),
    (
        {"page_number": 0},
        [("pageNumber", "0")],
    ),
    (
        {"page_number": 4294967295, "page_size": 1},
        [("pageNumber", "4294967295"), ("pageSize", "1")],
    ),
    (
        {"page_size": 20},
        [("pageSize", "20")],
    ),
    (
        {"start_time": START, "end_time": END, "page_number": 19, "page_size": 20},
        [
            ("startTime", START),
            ("endTime", END),
            ("pageNumber", "19"),
            ("pageSize", "20"),
        ],
    ),
]


@pytest.mark.parametrize("kw,extras", WIRE_CASES)
def test_exact_query_and_raw_page(mode, kw, extras):
    payload = body()
    result, log = execute(mode, OP, args=(PROJECT,), kwargs=kw, script=[_resp(payload)])
    (req,) = _http(log)
    expect_wire(req, TOKEN_1, PROJECT, extras)
    assert _kinds(log) == ["get_token", "factory", "http"]
    assert result.code == 200 and type(result.code) is int
    assert result.raw == payload and result.data == ROWS and result.data is result.raw["data"]
    assert result.message == "***"


def test_empty_page(mode):
    payload = body([])
    result, log = execute(mode, OP, args=("0",), script=[_resp(payload)])
    assert result.data == [] and result.raw == payload and len(_http(log)) == 1


def test_overseas_guard_first(mode):
    errors = []
    for args, kw in [
        ((PROJECT,), {"start_time": START, "end_time": END}),
        ((object(),), {"page_size": False}),
    ]:
        h = Harness(
            mode, lambda r: pytest.fail("unexpected request"), region=RegionConfig.overseas()
        )
        with pytest.raises(ValueError) as error:
            h.call(OP, *args, **kw)
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
            args=(PROJECT,),
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
    *(("project_id", value) for value in ("", "   ", b"p", True, 7, "\ud800", None)),
    *(
        ("start_time", value)
        for value in (
            "",
            "   ",
            "2026111",  # too short
            "202611180000000",  # too long
            "2026118a000000",
            "2026-11-18 00:00:00",
            "２０２６１１１８００００００",  # full-width digits are not ASCII
            "20260230000000",  # Feb 30 is not a calendar day
            "20261301000000",  # month 13
            "20261118246000",  # hour 24
            "20261118000060",  # second 60
            "2026111800000",  # 13 digits
            b"20261118000000",
            True,
            20261118000000,
            None if False else "\ud800",
        )
    ),
    *(("end_time", value) for value in ("", "20260229000000", "2026111800000a", False, 1.0, b"x")),
    *(("page_number", value) for value in (-1, True, False, 1.0, "1", b"1")),
    *(("page_size", value) for value in (0, 21, -1, True, False, "1", 1.0, b"20")),
]


@pytest.mark.parametrize("field,bad", BAD_INPUT)
def test_invalid_field(mode, field, bad):
    args = (bad,) if field == "project_id" else (PROJECT,)
    kw = {} if field == "project_id" else {field: bad}
    log = []
    with pytest.raises(ValueError):
        execute(mode, OP, args=args, kwargs=kw, script=[], log=log)
    assert log == []


@pytest.mark.parametrize(
    "args,kw",
    [
        ((), {}),
        ((PROJECT, START), {}),  # start_time is keyword-only
        ((PROJECT,), {"page_start": 0}),
        ((PROJECT,), {"startTime": START}),
        ((PROJECT,), {"task_id": TASK}),
        ((PROJECT,), {"has_url": True}),
        ((PROJECT,), {"sort_field": "createTime"}),
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
    {"files": []},
    [None],
    [3],
    ["not-a-row"],
    [[], {}],
    *([{field: bad}] for field in STRING_FIELDS for bad in (True, 12, {}, [])),
    *([{field: bad}] for field in INT_FIELDS for bad in (True, 0.5, "12", {}, [])),
]


@pytest.mark.parametrize("data", INVALID_ROWS)
def test_reject_bad_rows(mode, data):
    payload = {"meta": {"code": 200, "message": MSG}}
    if data is not MISS:
        payload["data"] = data
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ProtocolError):
        execute(mode, OP, args=(PROJECT,), script=[_resp(payload)], log=log, recorder=recorder)
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
        execute(mode, OP, args=(PROJECT,), script=[_resp(envelope)], log=log)
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
            args=(PROJECT,),
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
        expect_wire(req, token, PROJECT, extras)
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
        (200, 404, ApiError),
        (200, "404", ApiError),
        (200, 429, ApiError),
        (200, "429", ApiError),
        (200, 500, ApiError),
        (200, "500", ApiError),
        (200, "200", ApiError),
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
        execute(mode, OP, args=(PROJECT,), script=[_resp({"meta": meta}, status)], log=log)
    assert (
        type(error.value) is expected
        and error.value.http_status == status
        and error.value.message == "***"
    )
    if code is MISS:
        assert error.value.code is None
    else:
        assert error.value.code == code and type(error.value.code) is type(code)
    assert _kinds(log) == ["get_token", "factory", "http"]


@pytest.mark.parametrize("raw_code", [200, "UNTRUSTED_PRIVATE", 92851743])
def test_privacy(mode, raw_code, caplog):
    payload = body()
    payload["meta"].update(code=raw_code, message=MSG + PROJECT + URL + FILE + TASK)
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
            args=(PROJECT,),
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
        "PROJECT_PRIVATE",
        "TASK_PRIVATE",
        "FILE_PRIVATE",
        "NO_MEDIA_PRIVATE",
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
        execute(mode, OP, args=(PROJECT,), script=[problem], log=log, recorder=rec)
    assert len(_http(log)) == 1 and not _retries(_events(rec))


def test_async_cancel_propagates():
    log = []
    rec = DiagnosticRecorder()
    with pytest.raises(asyncio.CancelledError):
        execute(
            "async",
            OP,
            args=(PROJECT,),
            script=[asyncio.CancelledError()],
            log=log,
            recorder=rec,
        )
    assert len(_http(log)) == 1 and _facade_span_any(_events(rec))["outcome"] == "cancelled"


def test_project_id_surrogate_error_is_clean(mode):
    """Regression 1374: a surrogate-bearing ``project_id`` fails cleanly.

    Raising ``ValueError`` inside ``except UnicodeEncodeError`` — even
    ``from None`` — keeps the encoder error on ``__context__``, whose
    ``object``/``args`` carry the raw private input. The validation error
    must surface with neither ``__cause__`` nor ``__context__`` and no
    private bytes in its message or ``args``; auth, the token factory and
    the wire must see nothing, and the named call span must fail before
    any attempt event.
    """
    project = "PROJECT_\ud800_PRIVATE_" + "Y" * 41
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError) as excinfo:
        execute(mode, OP, args=(project,), script=[], log=log, recorder=recorder)
    err = excinfo.value
    assert str(err).strip()
    assert "PRIVATE" not in " ".join([str(err), repr(err), repr(err.args)])
    # The single precise assertion the old ``raise ... from None`` fails:
    assert err.__context__ is None
    assert err.__cause__ is None
    assert log == []
    events = _events(recorder)
    assert _facade_span_any(events)["outcome"] == "failed" and not _attempts_any(events)


@pytest.mark.parametrize("bad", ["20260230000000", "20261301000000", "20261118246000"])
def test_calendar_text_error_is_clean(mode, bad):
    """Non-calendar ``YYYYMMDDHHMMSS`` text is refused before any request."""
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError) as excinfo:
        execute(
            mode,
            OP,
            args=(PROJECT,),
            kwargs={"start_time": bad},
            script=[],
            log=log,
            recorder=recorder,
        )
    assert str(excinfo.value).strip() and log == []
    events = _events(recorder)
    assert _facade_span_any(events)["outcome"] == "failed" and not _attempts_any(events)
