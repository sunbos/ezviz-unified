"""Offline contracts for one domestic cloud-recording task-file page."""

import asyncio
import json
import logging

import httpx
import pytest
from test_sdk_cloud_recording_v1 import Harness
from test_sdk_cloud_recording_v1_behaviour import (
    TOKEN_1,
    TOKEN_2,
    _attempts,
    _events,
    _facade_span,
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

OP = "task_files"
PATH = "/api/v3/open/cloud/task/files"
TASK = " 任务 TASK_PRIVATE +&%/ " + "X" * 43
SORT = " 字段 SORT_PRIVATE +&%/ " + "Y" * 49
FILE = "E1" + "f" * 101 + "FILE_PRIVATE"
QUERY = {
    "page_number": 19,
    "page_size": 50,
    "sort_field": SORT,
    "sort_rule": "desc",
    "has_url": True,
}
MSG = "BACKEND_PRIVATE"
URL = "https://synthetic-media.invalid/NO_MEDIA_PRIVATE"
STRING_FIELDS = ("projectId", "fileId", "expireTime", "taskId")
INT_FIELDS = ("fileType", "status", "fileSize", "duration", "storageType", "resultSpaceId")
NULL_ROW = dict.fromkeys((*STRING_FIELDS, *INT_FIELDS, "downloadUrls"))
ROWS = [
    {
        "fileId": FILE,
        "projectId": "PROJECT_PRIVATE",
        "taskId": TASK,
        "expireTime": "TIME_PRIVATE",
        "fileType": 78,
        "status": -7,
        "duration": 0,
        "fileSize": 2**40,
        "storageType": 97,
        "resultSpaceId": 0,
        "downloadUrls": [URL, {"future": "FUTURE_PRIVATE"}, None],
        "ext": {"schema": "EXT_PRIVATE"},
    },
    {},
    NULL_ROW,
    {"fileId": "last", "future": {"remainder": None}},
]
MISS = object()


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def body(data=ROWS):
    return {
        "meta": {"code": 200, "message": MSG, "moreInfo": None},
        "data": data,
        "extra": {"opaque": [None]},
    }


def expect_wire(request, token, task, number, size, extras):
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
    expected = [
        ("accessToken", token),
        ("taskId", task),
        ("pageNumber", str(number)),
        ("pageSize", str(size)),
        *extras,
    ]
    assert sorted(request.url.params.multi_items()) == sorted(expected)


WIRE_CASES = [
    (
        {"page_number": 0, "page_size": 1},
        [],
    ),
    (
        {
            "page_number": 0,
            "page_size": 50,
            "sort_field": None,
            "sort_rule": None,
            "has_url": None,
        },
        [],
    ),
    (
        {
            "page_number": 4294967295,
            "page_size": 50,
            "sort_field": SORT,
            "sort_rule": "desc",
            "has_url": True,
        },
        [("sortField", SORT), ("sortRule", "desc"), ("hasUrl", "true")],
    ),
    (
        {"page_number": 1, "page_size": 50, "sort_field": SORT},
        [("sortField", SORT)],
    ),
    (
        {"page_number": 1, "page_size": 50, "sort_rule": "asc"},
        [("sortRule", "asc")],
    ),
    (
        {"page_number": 1, "page_size": 50, "has_url": False},
        [("hasUrl", "false")],
    ),
    (
        {"page_number": 1, "page_size": 50, "has_url": True},
        [("hasUrl", "true")],
    ),
]


@pytest.mark.parametrize("kw,extras", WIRE_CASES)
def test_exact_query_and_raw_page(mode, kw, extras):
    payload = body()
    result, log = execute(mode, OP, args=(TASK,), kwargs=kw, script=[_resp(payload)])
    (req,) = _http(log)
    expect_wire(req, TOKEN_1, TASK, kw["page_number"], kw["page_size"], extras)
    assert _kinds(log) == ["get_token", "factory", "http"]
    assert result.code == 200 and type(result.code) is int
    assert result.raw == payload and result.data == ROWS and result.data is result.raw["data"]
    assert result.message == "***"


def test_empty_page(mode):
    payload = body([])
    result, log = execute(
        mode, OP, args=("0",), kwargs={"page_number": 0, "page_size": 1}, script=[_resp(payload)]
    )
    assert result.data == [] and result.raw == payload and len(_http(log)) == 1


def test_overseas_guard_first(mode):
    errors = []
    for args, kw in [
        ((TASK,), QUERY),
        ((object(),), {"page_number": False, "page_size": False, "sort_rule": object()}),
    ]:
        h = Harness(
            mode, lambda r: pytest.fail("unexpected request"), region=RegionConfig.overseas()
        )
        with pytest.raises(ValueError) as error:
            h.call(OP, *args, **kw)
        errors.append(str(error.value))
        assert h.counts == {"auth": 0, "factory": 0, "http": 0}
        assert [v["outcome"] for v in h.spans(OP)] == ["started", "failed"]
    assert errors[0] == errors[1]


BAD_INPUT = [
    *(("task_id", value) for value in ("", "   ", b"task-id", True, 7, "\ud800", None)),
    *(("sort_field", value) for value in ("", "   ", b"fileName", False, 3, "\udfff")),
    *(("page_number", value) for value in (-1, True, False, 1.0, "1", None)),
    *(("page_size", value) for value in (0, 51, -1, True, False, "1", 1.0, None)),
    *(("sort_rule", value) for value in ("ASC", " desc", "ascending", "", 1, True, b"asc")),
    *(("has_url", value) for value in (0, 1, 1.0, "true", "false", [True], b"true")),
]


@pytest.mark.parametrize("field,bad", BAD_INPUT)
def test_invalid_field(mode, field, bad):
    args = (bad,) if field == "task_id" else (TASK,)
    kw = QUERY if field == "task_id" else {**QUERY, field: bad}
    log = []
    with pytest.raises(ValueError):
        execute(mode, OP, args=args, kwargs=kw, script=[], log=log)
    assert log == []


@pytest.mark.parametrize(
    "args,kw",
    [
        ((), {"page_number": 0, "page_size": 1}),
        ((TASK,), {}),
        ((TASK,), {"page_number": 0}),
        ((TASK,), {"page_size": 1}),
        ((TASK, 0, 1), {}),
        ((TASK,), {"page_start": 0, "page_size": 1}),
        ((TASK,), {"page_number": 0, "size": 1}),
        ((TASK,), {"page_number": 0, "page_size": 1, "project_id": "private"}),
    ],
)
def test_required_keyword_bounds(mode, args, kw):
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
    *([{"downloadUrls": bad}] for bad in ("x", {}, False, 1)),
]


@pytest.mark.parametrize("data", INVALID_ROWS)
def test_reject_bad_rows(mode, data):
    payload = {"meta": {"code": 200, "message": MSG}}
    if data is not MISS:
        payload["data"] = data
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ProtocolError):
        execute(
            mode,
            OP,
            args=(TASK,),
            kwargs=QUERY,
            script=[_resp(payload)],
            log=log,
            recorder=recorder,
        )
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
        execute(mode, OP, args=(TASK,), kwargs=QUERY, script=[_resp(envelope)], log=log)
    assert type(error.value) is exc and len(_http(log)) == 1


@pytest.mark.parametrize("code", [10002, "10002"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_refresh_same_page_once(mode, code, exhausted):
    log = []
    recorder = DiagnosticRecorder()
    stale = {"meta": {"code": code, "message": MSG}}

    def run():
        return execute(
            mode,
            OP,
            args=(TASK,),
            kwargs=QUERY,
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
    for req, token in zip(calls, (TOKEN_1, TOKEN_2), strict=True):
        expect_wire(
            req,
            token,
            TASK,
            19,
            50,
            [("sortField", SORT), ("sortRule", "desc"), ("hasUrl", "true")],
        )
    events = _events(recorder)
    assert len(_attempts(events, OP)) == 2 and [v["outcome"] for v in _retries(events)] == [
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
        execute(
            mode, OP, args=(TASK,), kwargs=QUERY, script=[_resp({"meta": meta}, status)], log=log
        )
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
    payload["meta"].update(code=raw_code, message=MSG + TASK + URL + FILE)
    resp = httpx.Response(
        200,
        json=payload,
        extensions={"http_version": b"VERSION_PRIVATE", "reason_phrase": b"REASON_PRIVATE"},
    )
    recorder = DiagnosticRecorder()
    log = []

    def run():
        return execute(
            mode, OP, args=(TASK,), kwargs=QUERY, script=[resp], recorder=recorder, log=log
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
    assert len(_attempts(events, OP)) == 1 and not _retries(events)
    assert _facade_span(events, OP)["outcome"] == ("succeeded" if raw_code == 200 else "failed")
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
        "SORT_PRIVATE",
        "PROJECT_PRIVATE",
        "FILE_PRIVATE",
        "NO_MEDIA_PRIVATE",
        "FUTURE_PRIVATE",
        "EXT_PRIVATE",
        "TIME_PRIVATE",
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
        execute(mode, OP, args=(TASK,), kwargs=QUERY, script=[problem], log=log, recorder=rec)
    assert len(_http(log)) == 1 and not _retries(_events(rec))


def test_async_cancel_propagates():
    log = []
    rec = DiagnosticRecorder()
    with pytest.raises(asyncio.CancelledError):
        execute(
            "async",
            OP,
            args=(TASK,),
            kwargs=QUERY,
            script=[asyncio.CancelledError()],
            log=log,
            recorder=rec,
        )
    assert len(_http(log)) == 1 and _facade_span(_events(rec), OP)["outcome"] == "cancelled"
