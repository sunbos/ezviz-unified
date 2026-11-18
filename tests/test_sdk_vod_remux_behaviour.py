"""Behavioural tests for ``client.vod`` remux/task/address methods (SP169).

Fully offline and synthetic: scripted token providers plus
``httpx.MockTransport`` / gated ``httpx.AsyncBaseTransport`` doubles only.
Diagnostics off means no ``diagnostic_recorder`` at all (a real ``None``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    RegionConfig,
    TransportError,
)
from ezviz_unified.auth_models import Token
from ezviz_unified.diagnostic_report import DiagnosticRecorder

ORIGIN = "https://open.ys7.com"
FAR_FUTURE = 9_999_999_999.0

# Method -> (read_only, entrypoint tail). The two POSTs are read_only=False:
# create_remux_task is declared non-idempotent; recording_urls is only
# documented as same-parameter idempotent, so no invalid-token replay either.
READ_METHODS = ("task", "tasks")
WRITE_METHODS = ("create_remux_task", "recording_urls")
METHODS = ("create_remux_task", "task", "tasks", "recording_urls")

# Long-but-legal synthetic sentinels actually sent on the wire.
DEVICE = "C" + "a" * 31  # non-blank ASCII header value
LOCAL = "1"
TASK_ID = "TASK-" + "t" * 52
TOKEN_1 = "CRV1TOKEN_" + "x" * 56
TOKEN_2 = "CRV1TOKEN_" + "y" * 56
HLS_URL = "https://hls.example.invalid/vod/" + "h" * 64
VALIDATE = "VC-" + "v" * 60
# Long legal synthetic resultSpaceId actually sent on the wire — deliberately
# not the short ``10002`` value; that collision case has its own test below.
RESULT_SPACE_ID = 987654321098

T0 = datetime(2026, 1, 2, 3, 4, 5)
T1 = T0 + timedelta(minutes=10)

BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {"taskId": TASK_ID, "unknownField": {"nested": [1]}},
    "unrelatedTopLevel": "preserved",
}
STALE = {"meta": {"code": 10002, "message": "stale token"}, "data": None}
STALE_AGAIN = {"meta": {"code": 10002, "message": "stale again"}, "data": None}
FAILED = {"meta": {"code": 5000, "message": "synthetic failure"}, "data": None}
SHORT_CODE_COLLISION = {
    "meta": {"code": 2000, "message": "upstream 10002 reference"},
    "data": None,
}


def _token(value: str) -> Token:
    return Token(
        value=value,
        expires_at=FAR_FUTURE,
        refresh_at=FAR_FUTURE - 60.0,
        api_base_url=ORIGIN,
    )


def _resp(body=None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=BODY if body is None else body)


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("synthetic timeout", request=request)


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("synthetic net down", request=request)


class _SyncProvider:
    """Scripted provider recording the get_token/invalidate call order."""

    can_refresh = True

    def __init__(self, log: list, script: list) -> None:
        self._log = log
        self._script = list(script)

    def get_token(self) -> Token:
        self._log.append(("get_token",))
        if not self._script:
            raise AssertionError("unscripted get_token call")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item

    def invalidate(self, expected_value: str | None = None) -> None:
        self._log.append(("invalidate", expected_value))


class _AsyncProvider:
    can_refresh = True

    def __init__(self, log: list, script: list) -> None:
        self._log = log
        self._script = list(script)

    async def get_token(self) -> Token:
        self._log.append(("get_token",))
        if not self._script:
            raise AssertionError("unscripted get_token call")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item

    def invalidate(self, expected_value: str | None = None) -> None:
        self._log.append(("invalidate", expected_value))


class _BlockingTransport(httpx.AsyncBaseTransport):
    """Holds the request open until the awaiting task is cancelled.

    Records ``("http", request)`` at the real entry point and captures the
    ``CancelledError`` instance the transport itself raised.
    """

    def __init__(self, log: list) -> None:
        self._log = log
        self.request_count = 0
        self.started = asyncio.Event()
        self.cancelled_error = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._log.append(("http", request))
        self.request_count += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            self.cancelled_error = error
            raise
        raise AssertionError("gate released unexpectedly")

    async def aclose(self) -> None:
        return None


def _scripted_factory(log: list, script: list):
    items = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(("http", request))
        if not items:
            raise AssertionError("unscripted HTTP request")
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item(request) if callable(item) else item

    transport = httpx.MockTransport(handler)

    def factory(origin: str):
        log.append(("factory", origin))
        return transport

    return factory


def _fixed_factory(log: list, transport):
    def factory(origin: str):
        log.append(("factory", origin))
        return transport

    return factory


def _http(log: list) -> list:
    return [entry[1] for entry in log if entry[0] == "http"]


def _kinds(log: list) -> list:
    return [entry[0] for entry in log]


def _fingerprint(request: httpx.Request) -> tuple:
    return (
        request.method,
        request.url.raw_path.decode("ascii"),
        request.content,
        tuple(sorted(request.headers.items())),
    )


_EVENT_KEYS = frozenset(
    {
        "kind",
        "entrypoint",
        "span_id",
        "parent_span_id",
        "outcome",
        "error_family",
        "reason",
        "code_category",
        "http_status_category",
        "attempt",
        "mode",
    }
)


def _events(recorder: DiagnosticRecorder) -> list:
    """Recorded events from one frozen ``report()["events"]`` snapshot."""
    events = recorder.report()["events"]
    assert isinstance(events, list)
    for event in events:
        assert isinstance(event, dict)
        assert _EVENT_KEYS <= frozenset(event)
    return events


def _facade_span(events: list, method: str) -> dict:
    """Exactly one started and one terminal event for the facade call."""
    entrypoint = "vod." + method
    calls = [
        event for event in events if event["kind"] == "call" and event["entrypoint"] == entrypoint
    ]
    started = [event for event in calls if event["outcome"] == "started"]
    terminal = [event for event in calls if event["outcome"] != "started"]
    assert len(started) == 1, f"{entrypoint} must emit exactly one started event"
    assert len(terminal) == 1, f"{entrypoint} must emit exactly one terminal event"
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _close_span(events: list) -> dict:
    """Exactly one started and one terminal close-scope event."""
    closes = [event for event in events if event["kind"] == "close"]
    started = [event for event in closes if event["outcome"] == "started"]
    terminal = [event for event in closes if event["outcome"] != "started"]
    assert len(started) == 1, "close must emit exactly one started event"
    assert len(terminal) == 1, "close must emit exactly one terminal event"
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _attempts(events: list, method: str) -> list:
    """Started transport-attempt sub-events for the facade operation."""
    return [
        event
        for event in events
        if event["kind"] == "attempt"
        and event["operation"] == "vod." + method
        and event["outcome"] == "started"
    ]


def _retries(events: list) -> list:
    return [event for event in events if event["kind"] == "retry"]


def _dump_events(recorder: DiagnosticRecorder) -> str:
    return json.dumps(_events(recorder), default=repr, ensure_ascii=False) + recorder.to_json()


def _call_args(method: str, args, kwargs):
    """Resolve per-method default args, returning (args, kwargs)."""
    if args is not None or kwargs is not None:
        return (() if args is None else args, {} if kwargs is None else kwargs)
    if method == "create_remux_task":
        return (
            (DEVICE,),
            {
                "local_index": LOCAL,
                "bus_type": "1",
                "start_time": T0,
                "end_time": T1,
            },
        )
    if method == "task":
        return ((TASK_ID,), {})
    if method == "tasks":
        return ((7,), {})
    assert method == "recording_urls"
    return (
        (DEVICE,),
        {
            "local_index": LOCAL,
            "start_time": T0,
            "end_time": T1,
            "format": "HLS",
        },
    )


def execute(
    kind,
    method,
    *,
    args=None,
    kwargs=None,
    region=None,
    script=None,
    provider_script=None,
    recorder=None,
    transport=None,
    log=None,
):
    """Drive ``client.vod.<method>`` over scripted provider + transport."""
    args, kwargs = _call_args(method, args, kwargs)
    log = [] if log is None else log
    provider_script = [_token(TOKEN_1)] if provider_script is None else provider_script
    if transport is not None:
        factory = _fixed_factory(log, transport)
    else:
        factory = _scripted_factory(log, [_resp()] if script is None else script)
    options = dict(region=region or RegionConfig.china(), transport_factory=factory)
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    try:
        if kind == "sync":
            with Client(token_provider=_SyncProvider(log, provider_script), **options) as client:
                result = getattr(client.vod, method)(*args, **kwargs)
        else:

            async def run():
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, provider_script), **options
                ) as client:
                    bound = getattr(client.vod, method)(*args, **kwargs)
                    assert asyncio.iscoroutine(bound), "async facade must return a coroutine"
                    return await bound

            result = asyncio.run(run())
    except ValueError:
        # Invalid input must fail before provider, factory and HTTP.
        assert not log, log
        raise
    return result, log


def _expect_wire(request: httpx.Request, method: str, token: str, args, kwargs) -> None:
    """Full wire fingerprint for one attempt: path, headers, query/form."""
    header_keys = {key.lower() for key in request.headers.keys()}
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == token
    assert "accessToken" not in request.url.params
    if method == "create_remux_task":
        device = args[0]
        assert request.method == "POST"
        assert request.url.path == "/api/service/open/vod/media/trans/code"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert request.headers["deviceSerial"] == device
        assert request.headers["localIndex"] == kwargs["local_index"]
        form = dict(parse_qsl(request.content.decode("utf-8")))
        assert form["busType"] == kwargs["bus_type"]
        assert form["startTime"] == "2026-01-02 03:04:05"
        assert form["endTime"] == "2026-01-02 03:14:05"
        for name, wire in (
            ("format", "format"),
            ("space_id", "spaceId"),
            ("result_space_id", "resultSpaceId"),
            ("validate_code", "validateCode"),
            ("acceleration", "acceleration"),
        ):
            if kwargs.get(name) is not None:
                assert form[wire] == str(kwargs[name])
        assert not request.url.params
    elif method == "task":
        assert request.method == "GET"
        assert request.url.path == "/api/service/open/vod/task"
        assert request.url.params["taskId"] == args[0]
        assert not request.content
        assert "content-type" not in header_keys
    elif method == "tasks":
        assert request.method == "GET"
        assert request.url.path == "/api/service/open/vod/tasks"
        assert request.url.params["taskType"] == str(args[0])
        for name, wire in (
            ("page_start", "pageStart"),
            ("page_size", "pageSize"),
            ("start_time", "startTime"),
            ("end_time", "endTime"),
        ):
            if kwargs.get(name) is not None:
                assert request.url.params[wire] == str(kwargs[name])
        assert not request.content
        assert "content-type" not in header_keys
    else:
        assert method == "recording_urls"
        device = args[0]
        assert request.method == "POST"
        assert request.url.path == "/api/service/open/vod/file/address/hls"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert request.headers["deviceSerial"] == device
        assert request.headers["localIndex"] == kwargs["local_index"]
        form = dict(parse_qsl(request.content.decode("utf-8")))
        assert form["startTime"] == "2026-01-02 03:04:05"
        assert form["endTime"] == "2026-01-02 03:14:05"
        assert form["format"] == kwargs["format"]
        for name, wire in (("expire_seconds", "expireSeconds"), ("space_id", "spaceId")):
            if kwargs.get(name) is not None:
                assert form[wire] == str(kwargs[name])
        assert not request.url.params


def _assert_replay_once(method, result, log, recorder, mode):
    """Shared read-replay assertions: two tokens, two wires, one retry."""
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    args, kwargs = _call_args(method, None, None)
    requests = _http(log)
    _expect_wire(requests[0], method, TOKEN_1, args, kwargs)
    _expect_wire(requests[1], method, TOKEN_2, args, kwargs)
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.code == 200 and result.raw == BODY
    assert result.data is result.raw["data"]
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "succeeded"
        assert terminal["error_family"] == "none"
        assert terminal["attempt"] == 0
        assert terminal["mode"] == mode
        assert [event["attempt"] for event in _attempts(events, method)] == [1, 2]
        retries = _retries(events)
        assert len(retries) == 1 and retries[0]["outcome"] == "scheduled"


# ---------------------------------------------------------------------------
# code 10002: reads replay once, writes never replay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", READ_METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_read_replays_once_on_code_10002(kind, method, diagnostics):
    recorder = DiagnosticRecorder() if diagnostics else None
    result, log = execute(
        kind,
        method,
        script=[lambda request: _resp(STALE), lambda request: _resp()],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
        recorder=recorder,
    )
    _assert_replay_once(method, result, log, recorder, kind)


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", READ_METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_read_replay_at_most_once_on_repeated_10002(kind, method, diagnostics):
    """Second 10002 invalidates TOKEN_2 but never triggers a third call."""
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            method,
            script=[lambda request: _resp(STALE), lambda request: _resp(STALE_AGAIN)],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            recorder=recorder,
            log=log,
        )
    assert captured.value.invalid_token is True
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
        "invalidate",
    ]
    args, kwargs = _call_args(method, None, None)
    requests = _http(log)
    _expect_wire(requests[0], method, TOKEN_1, args, kwargs)
    _expect_wire(requests[1], method, TOKEN_2, args, kwargs)
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert invalidations == [("invalidate", TOKEN_1), ("invalidate", TOKEN_2)]
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "authentication"
        assert terminal["code_category"] == "invalid_token"
        assert terminal["mode"] == kind
        assert [event["attempt"] for event in _attempts(events, method)] == [1, 2]
        assert len(_retries(events)) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", WRITE_METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_write_never_replays_on_code_10002(kind, method, diagnostics):
    """Both POSTs treat a single 10002 as terminal (one invalidate allowed)."""
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            method,
            script=[lambda request: _resp(STALE), lambda request: _resp()],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            recorder=recorder,
            log=log,
        )
    assert captured.value.invalid_token is True
    assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
    args, kwargs = _call_args(method, None, None)
    requests = _http(log)
    assert len(requests) == 1
    _expect_wire(requests[0], method, TOKEN_1, args, kwargs)
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "authentication"
        assert terminal["code_category"] == "invalid_token"
        assert [event["attempt"] for event in _attempts(events, method)] == [1]
        assert not _retries(events)


# ---------------------------------------------------------------------------
# Non-10002 failures: no retry for any method
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("failure_kind", ["api", "http500", "connect", "read_timeout"])
def test_no_retry_on_other_failures(kind, method, diagnostics, failure_kind):
    if failure_kind == "api":
        script = [lambda request: _resp(FAILED)]
    elif failure_kind == "http500":
        script = [lambda request: _resp({"meta": {"code": 500, "message": "boom"}}, status=500)]
    elif failure_kind == "connect":
        script = [_connect_error]
    else:
        script = [_timeout]
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(Exception) as captured:
        execute(kind, method, script=script, recorder=recorder, log=log)
    error = captured.value
    if failure_kind in ("api", "http500"):
        assert type(error) is ApiError
        assert not isinstance(error, AuthenticationError)
        if failure_kind == "http500":
            assert error.http_status == 500
        else:
            assert error.code == 5000
    else:
        assert type(error) is TransportError
        assert error.reason == ("network" if failure_kind == "connect" else "timeout")
    assert _kinds(log) == ["get_token", "factory", "http"]
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["mode"] == kind
        if failure_kind == "api":
            assert terminal["error_family"] == "api"
            assert terminal["http_status_category"] == "success"
        elif failure_kind == "http500":
            assert terminal["error_family"] == "api"
            assert terminal["http_status_category"] == "server_error"
        else:
            assert terminal["error_family"] == "transport"
            assert terminal["reason"] == ("network" if failure_kind == "connect" else "timeout")
        assert [event["attempt"] for event in _attempts(events, method)] == [1]
        assert not _retries(events)


# ---------------------------------------------------------------------------
# Success wire/order, diagnostics parity, ApiResponse identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
def test_success_wire_order_and_response_identity(kind, method):
    args, kwargs = _call_args(method, None, None)
    results = []
    logs = []
    for diag in (False, True):
        recorder = DiagnosticRecorder() if diag else None
        result, log = execute(
            kind,
            method,
            script=[lambda request: _resp()],
            recorder=recorder,
        )
        results.append(result)
        logs.append(log)
        assert _kinds(log) == ["get_token", "factory", "http"]
        _expect_wire(_http(log)[0], method, TOKEN_1, args, kwargs)
        assert result.http_status == 200 and result.code == 200
        assert result.operation == "vod." + method
        assert result.raw == BODY and result.data is result.raw["data"]
        if recorder is not None:
            events = _events(recorder)
            terminal = _facade_span(events, method)
            assert terminal["outcome"] == "succeeded"
            assert terminal["error_family"] == "none"
            assert terminal["reason"] == "none"
            assert terminal["attempt"] == 0
            assert terminal["mode"] == kind
            assert [event["attempt"] for event in _attempts(events, method)] == [1]
            assert not _retries(events)
    # Diagnostics on/off: identical wire fingerprints and identical order.
    assert _kinds(logs[0]) == _kinds(logs[1])
    assert [_fingerprint(r) for r in _http(logs[0])] == [_fingerprint(r) for r in _http(logs[1])]
    assert results[0].raw == results[1].raw
    assert results[0].data == results[1].data
    assert results[0].http_status == results[1].http_status
    assert results[0].code == results[1].code
    assert results[0].operation == results[1].operation

    # Identity: the facade must return the exact ApiResponse client.request made.
    sentinel_recorder = DiagnosticRecorder()
    sentinel = ApiResponse(200, 200, operation="vod." + method, data={"marker": True})
    produced = []

    def record(operation):
        produced.append(operation.name)
        return sentinel

    if kind == "sync":
        with Client(
            region=RegionConfig.china(),
            token_provider=_SyncProvider([], []),
            transport_factory=_fixed_factory([], httpx.MockTransport(_timeout)),
            diagnostic_recorder=sentinel_recorder,
        ) as client:
            client.request = lambda operation, **_kw: record(operation)
            returned = getattr(client.vod, method)(*args, **kwargs)
    else:

        async def run():
            async with AsyncClient(
                region=RegionConfig.china(),
                token_provider=_AsyncProvider([], []),
                transport_factory=_fixed_factory([], httpx.MockTransport(_timeout)),
                diagnostic_recorder=sentinel_recorder,
            ) as client:

                async def stub(operation, **_kw):
                    return record(operation)

                client.request = stub
                return await getattr(client.vod, method)(*args, **kwargs)

        returned = asyncio.run(run())
    assert returned is sentinel
    assert produced == ["vod." + method]
    terminal = _facade_span(_events(sentinel_recorder), method)
    assert terminal["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# Validation failure precedes provider/factory/HTTP (behavioural spot cases)
# ---------------------------------------------------------------------------

_REMUX_BASE = {
    "local_index": LOCAL,
    "bus_type": "1",
    "start_time": T0,
    "end_time": T1,
}
_URLS_BASE = {
    "local_index": LOCAL,
    "start_time": T0,
    "end_time": T1,
    "format": "HLS",
}

_INVALID_MATRIX = [
    # create_remux_task: header rule, bus_type set, span, bus7/space, 71/HLS.
    ("create_remux_task", ("  ",), _REMUX_BASE),
    ("create_remux_task", (DEVICE,), {**_REMUX_BASE, "local_index": " x"}),
    ("create_remux_task", (DEVICE,), {**_REMUX_BASE, "bus_type": "2"}),
    ("create_remux_task", (DEVICE,), {**_REMUX_BASE, "end_time": T0}),
    (
        "create_remux_task",
        (DEVICE,),
        {**_REMUX_BASE, "bus_type": "7"},  # missing space_id
    ),
    (
        "create_remux_task",
        (DEVICE,),
        {**_REMUX_BASE, "bus_type": "71", "format": "HLS"},
    ),
    ("create_remux_task", (DEVICE,), {**_REMUX_BASE, "acceleration": 2}),
    ("create_remux_task", (DEVICE,), {**_REMUX_BASE, "space_id": True}),
    # task: task_id must be a non-blank UTF-8 str.
    ("task", ("  ",), {}),
    ("task", (None,), {}),
    ("task", (7,), {}),
    ("task", ("\ud800",), {}),
    # tasks: task_type int 7/8 only; optional fields typed, strings non-blank.
    ("tasks", (1,), {}),
    ("tasks", (True,), {}),
    ("tasks", ("7",), {}),
    ("tasks", (7,), {"page_size": "x"}),
    ("tasks", (7,), {"page_size": True}),
    ("tasks", (7,), {"start_time": "  "}),
    # recording_urls: header rule, aware/micro datetimes, order, format, bounds.
    ("recording_urls", ("dev\x7f",), _URLS_BASE),
    ("recording_urls", (DEVICE,), {**_URLS_BASE, "end_time": T0 - timedelta(seconds=1)}),
    ("recording_urls", (DEVICE,), {**_URLS_BASE, "format": "MP4 "}),
    ("recording_urls", (DEVICE,), {**_URLS_BASE, "expire_seconds": 0}),
    ("recording_urls", (DEVICE,), {**_URLS_BASE, "expire_seconds": 604801}),
    ("recording_urls", (DEVICE,), {**_URLS_BASE, "space_id": False}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("method,args,kwargs", _INVALID_MATRIX)
def test_validation_failure_no_io(kind, method, diagnostics, args, kwargs):
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(ValueError):
        execute(
            kind,
            method,
            args=args,
            kwargs=kwargs,
            recorder=recorder,
            log=log,
        )
    # Pre-auth failure: provider/factory/HTTP stay silent; the context manager
    # still closes the client legitimately after the failed call.
    assert log == []
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "validation"
        assert terminal["reason"] == "none"
        assert terminal["attempt"] == 0
        assert terminal["mode"] == kind
        assert not _attempts(events, method)
        assert not _retries(events)
        close_terminal = _close_span(events)
        assert close_terminal["outcome"] == "succeeded"
        assert close_terminal["span_id"] != terminal["span_id"]
        close_started = [
            event for event in events if event["kind"] == "close" and event["outcome"] == "started"
        ]
        assert len(close_started) == 1
        assert terminal["sequence"] < close_started[0]["sequence"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_vod_domestic_only(kind):
    for method in METHODS:
        with pytest.raises(ValueError):
            execute(kind, method, region=RegionConfig.overseas())


# ---------------------------------------------------------------------------
# Closed client: TransportError(reason='closed'), no provider/network
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_closed_client_raises_transport_closed(kind, method, diagnostics):
    args, kwargs = _call_args(method, None, None)
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [lambda request: _resp()]),
    )
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    if kind == "sync":
        client = Client(token_provider=_SyncProvider(log, [_token(TOKEN_1)]), **options)
        client.close()
        with pytest.raises(TransportError) as captured:
            getattr(client.vod, method)(*args, **kwargs)
        captured_value = captured.value
    else:

        async def run(options=options, log=log):
            client = AsyncClient(token_provider=_AsyncProvider(log, [_token(TOKEN_1)]), **options)
            await client.aclose()
            with pytest.raises(TransportError) as captured:
                await getattr(client.vod, method)(*args, **kwargs)
            return captured.value

        captured_value = asyncio.run(run())
    assert captured_value.reason == "closed"
    assert log == []
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "transport"
        assert terminal["reason"] == "closed"
        assert terminal["mode"] == kind
        assert not _attempts(events, method)
        assert _close_span(events)["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# Provider exception identity is preserved (is, not text)
# ---------------------------------------------------------------------------


class _MarkerError(RuntimeError):
    pass


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("marker_family", ["runtime", "auth"])
def test_provider_exception_identity_preserved(kind, method, diagnostics, marker_family):
    args, kwargs = _call_args(method, None, None)
    if marker_family == "auth":
        marker = AuthenticationError("provider auth marker", code=10002)
        expected_family = "authentication"
    else:
        marker = _MarkerError("provider runtime marker")
        expected_family = "unknown"
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [lambda request: _resp()]),
    )
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    if kind == "sync":
        with Client(token_provider=_SyncProvider(log, [marker]), **options) as client:
            with pytest.raises(Exception) as captured:
                getattr(client.vod, method)(*args, **kwargs)
        captured_value = captured.value
    else:

        async def run(options=options, log=log, marker=marker):
            async with AsyncClient(
                token_provider=_AsyncProvider(log, [marker]), **options
            ) as client:
                with pytest.raises(Exception) as captured:
                    await getattr(client.vod, method)(*args, **kwargs)
            return captured.value

        captured_value = asyncio.run(run())
    assert captured_value is marker
    # Provider failure precedes transport setup: one get_token, nothing else.
    assert _kinds(log) == ["get_token"]
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == expected_family
        assert terminal["mode"] == kind
        assert not _attempts(events, method)
        assert not _retries(events)


# ---------------------------------------------------------------------------
# Async cancellation: CancelledError propagates with identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_async_cancel_propagates_cancelled_error(method, diagnostics):
    args, kwargs = _call_args(method, None, None)
    log = []
    transport = _BlockingTransport(log)
    recorder = DiagnosticRecorder() if diagnostics else None
    options = dict(
        region=RegionConfig.china(),
        token_provider=_AsyncProvider(log, [_token(TOKEN_1)]),
        transport_factory=_fixed_factory(log, transport),
    )
    if recorder is not None:
        options["diagnostic_recorder"] = recorder

    async def run(options=options):
        async with AsyncClient(**options) as client:
            seen = {}

            async def call():
                try:
                    await getattr(client.vod, method)(*args, **kwargs)
                except asyncio.CancelledError as error:
                    seen["error"] = error
                    raise

            task = asyncio.create_task(call())
            await asyncio.wait_for(transport.started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return seen["error"]

    sdk_error = asyncio.run(run())
    # The CancelledError surfacing through the SDK inside the cancelled task
    # is the very instance raised at the transport's await point.
    assert sdk_error is transport.cancelled_error
    assert transport.request_count == 1
    assert _kinds(log) == ["get_token", "factory", "http"]
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "cancelled"
        assert terminal["error_family"] == "interrupted"
        assert terminal["reason"] == "none"
        assert terminal["mode"] == "async"
        assert not _retries(events)
        assert _close_span(events)["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# Diagnostics: close event separate from business terminal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_close_emits_close_event_not_business_terminal(kind, method, diagnostics):
    args, kwargs = _call_args(method, None, None)
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [lambda request: _resp()]),
    )
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    if kind == "sync":
        with Client(token_provider=_SyncProvider(log, [_token(TOKEN_1)]), **options) as client:
            result = getattr(client.vod, method)(*args, **kwargs)
    else:

        async def run(options=options, log=log):
            async with AsyncClient(
                token_provider=_AsyncProvider(log, [_token(TOKEN_1)]), **options
            ) as client:
                return await getattr(client.vod, method)(*args, **kwargs)

        result = asyncio.run(run())
    assert result.code == 200
    assert _kinds(log) == ["get_token", "factory", "http"]
    if recorder is None:
        return
    # One snapshot, exact span separation: facade and close each emit one
    # started/terminal pair under different span_ids, ordered terminal < close.
    events = _events(recorder)
    entrypoint = "vod." + method
    facade_events = [e for e in events if e["kind"] == "call" and e["entrypoint"] == entrypoint]
    facade_started = [e for e in facade_events if e["outcome"] == "started"]
    facade_terminal = [e for e in facade_events if e["outcome"] != "started"]
    close_events = [e for e in events if e["kind"] == "close"]
    close_started = [e for e in close_events if e["outcome"] == "started"]
    close_terminal = [e for e in close_events if e["outcome"] != "started"]
    assert len(facade_started) == 1 and len(facade_terminal) == 1
    assert len(close_started) == 1 and len(close_terminal) == 1
    assert facade_started[0]["span_id"] == facade_terminal[0]["span_id"]
    assert close_started[0]["span_id"] == close_terminal[0]["span_id"]
    assert facade_terminal[0]["span_id"] != close_terminal[0]["span_id"]
    assert facade_terminal[0]["outcome"] == "succeeded"
    assert close_terminal[0]["outcome"] == "succeeded"
    assert facade_terminal[0]["sequence"] < close_started[0]["sequence"]
    assert close_started[0]["entrypoint"] in ("client.close", "client.aclose")
    assert all(e["entrypoint"] != entrypoint for e in events if e["kind"] in ("close", "cleanup"))


# ---------------------------------------------------------------------------
# Privacy: sentinels must not leak into error/diagnostic surfaces
# ---------------------------------------------------------------------------


def _secret_args(method: str):
    """Per-method args/kwargs carrying long legal sentinels on the wire."""
    if method == "create_remux_task":
        return (
            (DEVICE,),
            {
                **_REMUX_BASE,
                "result_space_id": RESULT_SPACE_ID,
                "validate_code": VALIDATE,
            },
        )
    if method == "task":
        return ((TASK_ID,), {})
    if method == "tasks":
        return ((7,), {"page_size": 50})
    return ((DEVICE,), _URLS_BASE)


def _secrets_for(method: str) -> tuple:
    """Known request secrets only: values this method actually puts on the
    wire, plus the real access token. Values never sent must not be listed
    here just to have them "redacted" from surfaces."""
    if method == "create_remux_task":
        return (DEVICE, TOKEN_1, VALIDATE, str(RESULT_SPACE_ID))
    if method == "task":
        return (TASK_ID, TOKEN_1)
    if method == "tasks":
        # tasks only sends taskType/pageSize — neither is secret.
        return (TOKEN_1,)
    return (DEVICE, TOKEN_1)


def _unknown_echo_for(method: str) -> tuple:
    """Free-form upstream echo text never sent as request data.

    ``HLS_URL`` is a returned-URL sentinel; ``DEVICE`` is added for ``tasks``
    so the echo still resembles the other methods' messages even though
    tasks transmits no device field. These are *not* known request secrets:
    SP169 never promised scrubbing arbitrary unknown server text, so the
    ApiError str/repr boundary keeps them verbatim."""
    if method == "tasks":
        return (HLS_URL, DEVICE)
    return (HLS_URL,)


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_privacy_sentinels_not_leaked(kind, method, diagnostics, caplog):
    """Known request secrets echoed by upstream are redacted everywhere.

    Unknown free-text sentinels (HLS_URL; DEVICE for ``tasks``) are echoed
    too: they were never sent as request data, so SDK exception semantics
    keep them in ApiError str/repr — while logs and diagnostics must still
    never contain them or any known secret."""
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder() if diagnostics else None
    args, kwargs = _secret_args(method)
    known = _secrets_for(method)
    unknown = _unknown_echo_for(method)
    echo_body = {
        "meta": {
            "code": 400,
            "message": "echo: " + "; ".join(known + unknown),
            "requestId": "UPSTREAM-REQUEST-ID-42",
        },
        "data": {"hlsAddress": HLS_URL},
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(
            kind,
            method,
            args=args,
            kwargs=kwargs,
            script=[lambda request: _resp(echo_body)],
            recorder=recorder,
            log=log,
        )
    # Wire still carries the real values verbatim.
    _expect_wire(_http(log)[0], method, TOKEN_1, args, kwargs)

    # Surface 1: the rendered exception. Frozen boundary — ApiError str/repr
    # scrubs known request secrets but passes unknown upstream text through.
    rendered = str(captured.value) + repr(captured.value)
    for secret in known:
        assert secret not in rendered
    for text in unknown:
        assert text in rendered

    # Surface 2: automatic logs must contain neither known secrets nor the
    # unknown echo text.
    for text in known + unknown:
        assert text not in caplog.text

    # Surface 3: diagnostics (only when a real recorder is installed) are a
    # strict whitelist — no secret or unknown echo may appear in the dump.
    if recorder is not None:
        diag_text = _dump_events(recorder)
        for text in known + unknown:
            assert text not in diag_text

    assert captured.value.operation == "vod." + method


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("diagnostics", [False, True])
def test_response_hls_url_not_in_repr_or_diagnostics(kind, diagnostics, caplog):
    """``hlsAddress`` stays in ``data``/``raw`` but never in repr/log/diag."""
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder() if diagnostics else None
    body = {
        "meta": {"code": 200, "message": "ok"},
        "data": [{"hlsAddress": HLS_URL, "stopTime": "x", "expireTime": "y"}],
    }
    result, log = execute(
        kind,
        "recording_urls",
        script=[lambda request: _resp(body)],
        recorder=recorder,
    )
    assert result.data == body["data"]
    assert result.raw["data"][0]["hlsAddress"] == HLS_URL
    surface = repr(result) + str(result) + caplog.text
    if recorder is not None:
        surface += _dump_events(recorder)
    for secret in (HLS_URL, TOKEN_1):
        assert secret not in surface


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_privacy_10002_collision_guard(kind, method, diagnostics, caplog):
    """An actual field value of ``10002`` must not fake stale-token logic.

    A non-auth code mentioning 10002 stays a plain ApiError with no replay;
    a genuine meta.code=10002 with ``taskId='10002'`` / ``resultSpaceId=10002``
    on the wire still classifies and replays exactly as the auth rule says.
    ``tasks`` has no sensitive-ID selector that can carry ``10002`` — for it
    this test only exercises the auth-vs-text classification; every other
    method does put a literal ``10002`` ID field on the wire.
    """
    caplog.set_level(logging.DEBUG)
    recorder_a = DiagnosticRecorder() if diagnostics else None
    recorder_b = DiagnosticRecorder() if diagnostics else None
    args, kwargs = _call_args(method, None, None)
    if method == "create_remux_task":
        kwargs = {**kwargs, "result_space_id": 10002}
    elif method == "task":
        args = ("10002",)
    elif method == "recording_urls":
        kwargs = {**kwargs, "space_id": 10002}

    # A: non-auth business code + "10002" text mention -> ApiError, no replay.
    log_a = []
    with pytest.raises(ApiError) as not_auth:
        execute(
            kind,
            method,
            args=args,
            kwargs=kwargs,
            script=[lambda request: _resp(SHORT_CODE_COLLISION)],
            recorder=recorder_a,
            log=log_a,
        )
    assert type(not_auth.value) is ApiError
    assert not isinstance(not_auth.value, AuthenticationError)
    assert _kinds(log_a) == ["get_token", "factory", "http"]
    if recorder_a is not None:
        events_a = _events(recorder_a)
        terminal_a = _facade_span(events_a, method)
        assert terminal_a["outcome"] == "failed"
        assert terminal_a["error_family"] == "api"
        assert terminal_a["mode"] == kind
        # A bare "10002" text mention must never classify as invalid_token;
        # the category may be scrubbed to "other", so do not require the raw
        # numeric code to be exported.
        assert terminal_a["code_category"] != "invalid_token"
        assert [event["attempt"] for event in _attempts(events_a, method)] == [1]
        assert not _retries(events_a)

    # B: genuine meta.code=10002 with colliding field values still classifies.
    log_b = []
    if method in READ_METHODS:
        result_b, _ = execute(
            kind,
            method,
            args=args,
            kwargs=kwargs,
            script=[lambda request: _resp(STALE), lambda request: _resp()],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            recorder=recorder_b,
            log=log_b,
        )
        assert result_b.code == 200
        assert _kinds(log_b) == [
            "get_token",
            "factory",
            "http",
            "invalidate",
            "get_token",
            "http",
        ]
        requests = _http(log_b)
        _expect_wire(requests[0], method, TOKEN_1, args, kwargs)
        _expect_wire(requests[1], method, TOKEN_2, args, kwargs)
        invalidations = [entry for entry in log_b if entry[0] == "invalidate"]
        assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
        if recorder_b is not None:
            events_b = _events(recorder_b)
            terminal_b = _facade_span(events_b, method)
            assert terminal_b["outcome"] == "succeeded"
            assert terminal_b["error_family"] == "none"
            assert terminal_b["mode"] == kind
            assert [event["attempt"] for event in _attempts(events_b, method)] == [1, 2]
            retries_b = _retries(events_b)
            assert len(retries_b) == 1 and retries_b[0]["outcome"] == "scheduled"
    else:
        with pytest.raises(AuthenticationError) as stale_write:
            execute(
                kind,
                method,
                args=args,
                kwargs=kwargs,
                script=[lambda request: _resp(STALE), lambda request: _resp()],
                provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
                recorder=recorder_b,
                log=log_b,
            )
        assert stale_write.value.invalid_token is True
        assert _kinds(log_b) == ["get_token", "factory", "http", "invalidate"]
        if recorder_b is not None:
            events_b = _events(recorder_b)
            terminal_b = _facade_span(events_b, method)
            assert terminal_b["outcome"] == "failed"
            assert terminal_b["error_family"] == "authentication"
            assert terminal_b["code_category"] == "invalid_token"
            assert terminal_b["mode"] == kind
            assert [event["attempt"] for event in _attempts(events_b, method)] == [1]
            assert not _retries(events_b)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_validation_errors_do_not_echo_input_values(kind):
    marker = "HIDDEN_VALUE_" + "v" * 40
    # deviceSerial has no length cap: a long ASCII value is legal. Real CR/LF
    # control characters make the header illegal, with the marker embedded so
    # the no-echo assertion targets this very input. ValueError precedes any
    # provider/factory/HTTP activity (asserted inside execute()).
    with pytest.raises(ValueError) as header_case:
        execute(
            kind,
            "create_remux_task",
            args=(marker + "\r\nX-Test: value",),
            kwargs=_REMUX_BASE,
        )
    assert marker not in str(header_case.value)
    # Marker plus a real lone surrogate inside validate_code: illegal UTF-8,
    # and again the marker is part of the rejected input itself.
    with pytest.raises(ValueError) as code_case:
        execute(
            kind,
            "create_remux_task",
            args=(DEVICE,),
            kwargs={**_REMUX_BASE, "validate_code": marker + "\ud800"},
        )
    assert marker not in str(code_case.value)
