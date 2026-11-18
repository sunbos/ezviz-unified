"""Behavioural tests for ``client.cloud_recording_v1`` (sync and async).

Fully offline and synthetic: scripted token providers plus
``httpx.MockTransport`` / gated ``httpx.AsyncBaseTransport`` doubles only.
No environment, network, or credential access. Every business call goes
through the ``cloud_recording_v1`` facade; diagnostics off means no
``diagnostic_recorder`` is passed at all (a real ``None``), never a fresh
unused recorder.
"""

from __future__ import annotations

import asyncio
import json
import logging
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

METHODS = ("unfreeze_file", "file_info", "download_urls")
READ_METHODS = ("file_info", "download_urls")

PROJECT = "PROJ_0001"
FILE = "FILE0001"
# Long-but-legal sentinels actually sent as request parameters: project ids
# are 1..32 ASCII word chars; E1-prefixed file ids may run up to 128 chars.
LONG_PROJECT = "PROJ_" + "p" * 24
LONG_FILE = "E1" + "f" * 100
TOKEN_1 = "CRV1TOKEN_" + "x" * 56
TOKEN_2 = "CRV1TOKEN_" + "y" * 56
MEDIA_URL = "https://media.example.invalid/crv1/" + "m" * 64

BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {"fileName": "a", "size": 7, "unknownField": {"nested": [1]}},
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
    """Holds the in-flight request open until the awaiting task is cancelled.

    Appends ``("http", request)`` to the shared scenario log at the real
    entry point of ``handle_async_request`` so the observed order stays
    honest; the cancellation itself is captured on ``cancelled_error``.
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
    """Assert exactly one started and one terminal event for the facade call."""
    entrypoint = "cloud_recording_v1." + method
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
    """Assert exactly one started and one terminal close-scope event."""
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
        and event["operation"] == "cloud_recording_v1." + method
        and event["outcome"] == "started"
    ]


def _retries(events: list) -> list:
    return [event for event in events if event["kind"] == "retry"]


def _dump_events(recorder: DiagnosticRecorder) -> str:
    return json.dumps(_events(recorder), default=repr, ensure_ascii=False) + recorder.to_json()


def execute(
    kind,
    method,
    *,
    args=None,
    kwargs=None,
    script=None,
    provider_script=None,
    recorder=None,
    transport=None,
    log=None,
):
    """Drive ``client.cloud_recording_v1.<method>`` over scripted provider + transport."""
    args = (PROJECT, FILE) if args is None else args
    kwargs = {} if kwargs is None else kwargs
    log = [] if log is None else log
    provider_script = [_token(TOKEN_1)] if provider_script is None else provider_script
    if transport is not None:
        factory = _fixed_factory(log, transport)
    else:
        factory = _scripted_factory(log, [_resp()] if script is None else script)
    options = dict(region=RegionConfig.china(), transport_factory=factory)
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    try:
        if kind == "sync":
            with Client(token_provider=_SyncProvider(log, provider_script), **options) as client:
                result = getattr(client.cloud_recording_v1, method)(*args, **kwargs)
        else:

            async def run():
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, provider_script), **options
                ) as client:
                    bound = getattr(client.cloud_recording_v1, method)(*args, **kwargs)
                    assert asyncio.iscoroutine(bound), "async facade must return a coroutine"
                    return await bound

            result = asyncio.run(run())
    except ValueError:
        # Invalid input must fail before provider, factory and HTTP.
        assert not log, log
        raise
    return result, log


_DOWNLOAD_KEYS = {"content_type": "contentType", "expire_seconds": "expireSeconds"}


def _expect_wire(request: httpx.Request, method: str, token: str, args, kwargs) -> None:
    """Full wire fingerprint for one attempt: path, token location, fields."""
    project_id, file_id = args[0], args[1]
    header_keys = {key.lower() for key in request.headers.keys()}
    assert request.url.host == "open.ys7.com"
    if method == "unfreeze_file":
        assert request.method == "POST"
        assert request.url.path == "/api/open/cloud/v1/file/unfreeze"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        form = dict(parse_qsl(request.content.decode("utf-8")))
        assert form["accessToken"] == token
        assert form["projectId"] == project_id
        assert form["fileId"] == file_id
        assert "accessToken" not in request.url.params
        assert "accesstoken" not in header_keys
    elif method == "file_info":
        assert request.method == "GET"
        assert request.url.path == "/api/open/cloud/v1/file"
        assert request.url.params["accessToken"] == token
        assert request.url.params["projectId"] == project_id
        assert request.url.params["fileId"] == file_id
        assert not request.content
        assert "content-type" not in header_keys
        assert "accesstoken" not in header_keys
    else:
        assert method == "download_urls"
        assert request.method == "GET"
        assert request.url.path == "/api/service/cloudrecord/file/official/download"
        assert request.headers["accessToken"] == token
        assert request.url.params["projectId"] == project_id
        assert request.url.params["fileId"] == file_id
        assert "accessToken" not in request.url.params
        assert not request.content
        assert "content-type" not in header_keys
        for name, value in kwargs.items():
            assert request.url.params[_DOWNLOAD_KEYS[name]] == str(value)


def _assert_replay_once(method, result, log, recorder, mode):
    """Shared read-replay assertions: two tokens, two wires, exactly one retry."""
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    requests = _http(log)
    _expect_wire(requests[0], method, TOKEN_1, (PROJECT, FILE), {})
    _expect_wire(requests[1], method, TOKEN_2, (PROJECT, FILE), {})
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
# code 10002 replay: reads replay once, writes never
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", READ_METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_read_replays_once_on_code_10002(method, diagnostics):
    recorder = DiagnosticRecorder() if diagnostics else None
    result, log = execute(
        "sync",
        method,
        script=[lambda request: _resp(STALE), lambda request: _resp()],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
        recorder=recorder,
    )
    _assert_replay_once(method, result, log, recorder, "sync")


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", READ_METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_read_replay_at_most_once_on_repeated_10002(kind, method, diagnostics):
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
    # Every 10002 invalidates the token that produced it before the
    # retried/read_only gate runs: TOKEN_1 first, then TOKEN_2 — but never a
    # third get_token or HTTP request. Two business calls, two tokens, one
    # replay retry.
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
        "invalidate",
    ]
    requests = _http(log)
    _expect_wire(requests[0], method, TOKEN_1, (PROJECT, FILE), {})
    _expect_wire(requests[1], method, TOKEN_2, (PROJECT, FILE), {})
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


@pytest.mark.parametrize("diagnostics", [False, True])
def test_write_never_replays_on_code_10002(diagnostics):
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(AuthenticationError) as captured:
        execute(
            "sync",
            "unfreeze_file",
            script=[lambda request: _resp(STALE), lambda request: _resp()],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            recorder=recorder,
            log=log,
        )
    assert captured.value.invalid_token is True
    # The write is not read-only: the single 10002 is terminal, the scripted
    # second token/response is never consumed. One invalidate is still allowed.
    assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
    requests = _http(log)
    assert len(requests) == 1
    _expect_wire(requests[0], "unfreeze_file", TOKEN_1, (PROJECT, FILE), {})
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, "unfreeze_file")
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "authentication"
        assert terminal["code_category"] == "invalid_token"
        assert [event["attempt"] for event in _attempts(events, "unfreeze_file")] == [1]
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
    # A non-10002 failure is terminal: no invalidate, no retry, no second call.
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
# Ordering and wire shape on success, diagnostics on/off parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
def test_success_wire_order_and_response_identity(kind, method):
    kwargs = (
        {"content_type": "video/mp4", "expire_seconds": 120} if method == "download_urls" else {}
    )
    results = []
    logs = []
    for diag in (False, True):
        recorder = DiagnosticRecorder() if diag else None
        result, log = execute(
            kind,
            method,
            kwargs=kwargs,
            script=[lambda request: _resp()],
            recorder=recorder,
        )
        results.append(result)
        logs.append(log)
        assert _kinds(log) == ["get_token", "factory", "http"]
        _expect_wire(_http(log)[0], method, TOKEN_1, (PROJECT, FILE), kwargs)
        assert result.http_status == 200 and result.code == 200
        assert result.operation == "cloud_recording_v1." + method
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
    # Diagnostics on/off must not change the wire or the provider/factory/HTTP
    # order: identical fingerprints and identical event sequences.
    assert _kinds(logs[0]) == _kinds(logs[1])
    assert [_fingerprint(r) for r in _http(logs[0])] == [_fingerprint(r) for r in _http(logs[1])]
    assert results[0].raw == results[1].raw
    assert results[0].data == results[1].data
    assert results[0].http_status == results[1].http_status
    assert results[0].code == results[1].code
    assert results[0].operation == results[1].operation

    # Identity, not equality: the facade must return the very ApiResponse
    # object produced by ``client.request``.
    sentinel_recorder = DiagnosticRecorder()
    sentinel = ApiResponse(
        200, 200, operation="cloud_recording_v1." + method, data={"marker": True}
    )
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
            returned = getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
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
                return await getattr(client.cloud_recording_v1, method)(PROJECT, FILE)

        returned = asyncio.run(run())
    assert returned is sentinel
    assert produced == ["cloud_recording_v1." + method]
    terminal = _facade_span(_events(sentinel_recorder), method)
    assert terminal["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# Validation failure precedes provider/factory/HTTP
# ---------------------------------------------------------------------------


# (args, kwargs) pairs that every method must reject with built-in ValueError
# before provider/factory/HTTP. Blank project_id is the original scenario.
_INVALID_COMMON = [
    (("  ", FILE), {}),
    (("P1", "  "), {}),
    (("P1", "bad file!"), {}),
    (("P1", "x" * 33), {}),
    ((None, FILE), {}),
    (("P1", None), {}),
    ((7, FILE), {}),
]
# Extra strict project_id rules for unfreeze_file/download_urls (docs
# 5341/1377): file_info's loose rule intentionally accepts these shapes.
_INVALID_STRICT_PROJECT = [
    (("p" * 33, FILE), {}),
    (("项目1", FILE), {}),
]

_INVALID_MATRIX = [
    (method, args, kwargs)
    for method, cases in (
        (
            "unfreeze_file",
            _INVALID_COMMON + _INVALID_STRICT_PROJECT,
        ),
        ("file_info", _INVALID_COMMON),
        (
            "download_urls",
            _INVALID_COMMON + _INVALID_STRICT_PROJECT,
        ),
    )
    for args, kwargs in cases
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
    # Validation is a pre-auth failure: provider, factory and HTTP stay
    # silent — no-I/O means the shared log stays empty. The with/async with
    # client still closes normally, so a close span is expected; what must be
    # absent is any network-preparation stage.
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
        # No authentication/transport-setup stage ran before validation.
        network_stages = {
            "authentication",
            "transport_setup",
            "token_acquire",
            "request",
        }
        assert not [event for event in events if event["kind"] in network_stages]
        # Client teardown still happens and must succeed: its span is distinct
        # from the business span and starts only after the facade terminal.
        close_terminal = _close_span(events)
        assert close_terminal["outcome"] == "succeeded"
        assert close_terminal["span_id"] != terminal["span_id"]
        close_started = [
            event for event in events if event["kind"] == "close" and event["outcome"] == "started"
        ]
        assert len(close_started) == 1
        assert terminal["sequence"] < close_started[0]["sequence"]
        # Async cleanup events are allowed only inside the close span, never
        # mingled with the business span or any network stage.
        cleanup_events = [event for event in events if event["kind"] == "cleanup"]
        assert all(
            event["span_id"] == close_started[0]["span_id"]
            or event["parent_span_id"] == close_started[0]["span_id"]
            for event in cleanup_events
        )


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("project", ["p" * 33, "项目1"])
def test_file_info_loose_project_rules(kind, diagnostics, project):
    """file_info's loose project rule accepts ids its siblings must reject."""
    recorder = DiagnosticRecorder() if diagnostics else None
    result, log = execute(
        kind,
        "file_info",
        args=(project, FILE),
        recorder=recorder,
    )
    assert _kinds(log) == ["get_token", "factory", "http"]
    assert _http(log)[0].url.params["projectId"] == project
    assert result.code == 200
    if recorder is not None:
        terminal = _facade_span(_events(recorder), "file_info")
        assert terminal["outcome"] == "succeeded"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"expire_seconds": 30},
        {"expire_seconds": 604801},
        {"expire_seconds": True},
        {"expire_seconds": "x"},
        {"content_type": "   "},
        {"content_type": None, "expire_seconds": 59},
    ],
)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_validation_failure_no_io_download_kwargs(kind, diagnostics, kwargs):
    """``download_urls`` optional-parameter rules reject before any I/O."""
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(ValueError):
        execute(
            kind,
            "download_urls",
            kwargs=kwargs,
            recorder=recorder,
            log=log,
        )
    assert log == []
    if recorder is not None:
        terminal = _facade_span(_events(recorder), "download_urls")
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "validation"


# ---------------------------------------------------------------------------
# Closed client: TransportError(reason='closed'), no provider/network
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_closed_client_raises_transport_closed(method, diagnostics):
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [lambda request: _resp()]),
    )
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    client = Client(token_provider=_SyncProvider(log, [_token(TOKEN_1)]), **options)
    client.close()
    with pytest.raises(TransportError) as captured:
        getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
    assert captured.value.reason == "closed"
    assert log == []
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "transport"
        assert terminal["reason"] == "closed"
        assert not _attempts(events, method)
        assert _close_span(events)["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# Provider exception identity is preserved
# ---------------------------------------------------------------------------


class _MarkerError(RuntimeError):
    pass


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize(
    "marker_family",
    ["runtime", "auth"],
)
def test_provider_exception_identity_preserved(kind, method, diagnostics, marker_family):
    """A provider-raised marker object must propagate unchanged (`is`, not text)."""
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
                getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
        captured_value = captured.value
    else:

        async def run(options=options, log=log, marker=marker):
            async with AsyncClient(
                token_provider=_AsyncProvider(log, [marker]), **options
            ) as client:
                with pytest.raises(Exception) as captured:
                    await getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
            return captured.value

        captured_value = asyncio.run(run())
    assert captured_value is marker
    # The provider failure precedes transport setup: one get_token, nothing else.
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
# Async parity: 10002 replay, write no-replay, closed, cancel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", READ_METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_async_read_replays_once_on_code_10002(method, diagnostics):
    recorder = DiagnosticRecorder() if diagnostics else None
    result, log = execute(
        "async",
        method,
        script=[lambda request: _resp(STALE), lambda request: _resp()],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
        recorder=recorder,
    )
    _assert_replay_once(method, result, log, recorder, "async")


@pytest.mark.parametrize("diagnostics", [False, True])
def test_async_write_never_replays_on_code_10002(diagnostics):
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    with pytest.raises(AuthenticationError) as captured:
        execute(
            "async",
            "unfreeze_file",
            script=[lambda request: _resp(STALE), lambda request: _resp()],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            recorder=recorder,
            log=log,
        )
    assert captured.value.invalid_token is True
    assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
    requests = _http(log)
    assert len(requests) == 1
    _expect_wire(requests[0], "unfreeze_file", TOKEN_1, (PROJECT, FILE), {})
    if recorder is not None:
        events = _events(recorder)
        terminal = _facade_span(events, "unfreeze_file")
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "authentication"
        assert terminal["code_category"] == "invalid_token"
        assert terminal["mode"] == "async"
        assert [event["attempt"] for event in _attempts(events, "unfreeze_file")] == [1]
        assert not _retries(events)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_async_closed_client_transport_closed(method, diagnostics):
    log = []
    recorder = DiagnosticRecorder() if diagnostics else None
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [lambda request: _resp()]),
    )
    if recorder is not None:
        options["diagnostic_recorder"] = recorder

    async def run(options=options, log=log):
        client = AsyncClient(token_provider=_AsyncProvider(log, [_token(TOKEN_1)]), **options)
        await client.aclose()
        with pytest.raises(TransportError) as captured:
            await getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
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
        assert terminal["mode"] == "async"
        assert not _attempts(events, method)
        assert _close_span(events)["outcome"] == "succeeded"


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_async_cancel_propagates_cancelled_error(method, diagnostics):
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
                    await getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
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
    # The exception surfacing through the SDK inside the cancelled task is the
    # very instance raised at the transport's await point — not a re-created
    # cancellation scheduled from another task.
    assert sdk_error is transport.cancelled_error
    # One in-flight request was cancelled; nothing retried afterwards.
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
            result = getattr(client.cloud_recording_v1, method)(PROJECT, FILE)
    else:

        async def run(options=options, log=log):
            async with AsyncClient(
                token_provider=_AsyncProvider(log, [_token(TOKEN_1)]), **options
            ) as client:
                return await getattr(client.cloud_recording_v1, method)(PROJECT, FILE)

        result = asyncio.run(run())
    assert result.code == 200
    assert _kinds(log) == ["get_token", "factory", "http"]
    if recorder is None:
        return
    # One snapshot, exact span separation: facade and close each emit exactly
    # one started/terminal pair under different span_ids, ordered business
    # terminal < close started.
    events = _events(recorder)
    entrypoint = "cloud_recording_v1." + method
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
    # Close and cleanup are not facade business events and vice versa.
    assert all(e["entrypoint"] != entrypoint for e in events if e["kind"] in ("close", "cleanup"))


# ---------------------------------------------------------------------------
# Privacy: sentinels must not leak into error/diagnostic surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_privacy_sentinels_not_leaked(kind, method, diagnostics, caplog):
    """Long legal project/file ids plus the real token go on the wire; error
    echoes of them must be redacted on every rendered surface."""
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder() if diagnostics else None
    echo_body = {
        "meta": {
            "code": 400,
            "message": f"{LONG_PROJECT} missing; {LONG_FILE} denied; {TOKEN_1}",
            "requestId": "UPSTREAM-REQUEST-ID-42",
        },
        "data": {"mediaURL": MEDIA_URL},
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(
            kind,
            method,
            args=(LONG_PROJECT, LONG_FILE),
            script=[lambda request: _resp(echo_body)],
            recorder=recorder,
            log=log,
        )
    request = _http(log)[0]
    # Wire still carries the real values verbatim.
    _expect_wire(request, method, TOKEN_1, (LONG_PROJECT, LONG_FILE), {})
    surface = str(captured.value) + repr(captured.value) + caplog.text
    if recorder is not None:
        surface += _dump_events(recorder)
    for secret in (LONG_PROJECT, LONG_FILE, TOKEN_1, MEDIA_URL):
        assert secret not in surface
    # The upstream requestId is a response field, not one of our request ids;
    # it is never merged into the SDK's error identity.
    assert captured.value.operation == "cloud_recording_v1." + method


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("diagnostics", [False, True])
def test_response_media_url_not_in_repr_or_diagnostics(kind, diagnostics, caplog):
    """A media URL stays available in ``data``/``raw`` but never in repr,
    SDK log output, or the diagnostic dump."""
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder() if diagnostics else None
    body = {
        "meta": {"code": 200, "message": "ok"},
        "data": {"mediaURL": MEDIA_URL, "fileId": FILE},
    }
    result, log = execute(
        kind,
        "download_urls",
        script=[lambda request: _resp(body)],
        recorder=recorder,
    )
    assert result.data["mediaURL"] == MEDIA_URL
    assert result.raw["data"]["mediaURL"] == MEDIA_URL
    surface = repr(result) + str(result) + caplog.text
    if recorder is not None:
        surface += _dump_events(recorder)
    for secret in (MEDIA_URL, TOKEN_1):
        assert secret not in surface


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diagnostics", [False, True])
def test_privacy_long_sentinel_collision_guard(kind, method, diagnostics, caplog):
    """Sub-scenario A: a genuinely long legal file id is sent and still redacted.
    Sub-scenario B: an actual id equal to "10002" never fakes a stale-token
    classification, while a real meta.code=10002 still drives read replay /
    write no-replay correctly."""
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder() if diagnostics else None

    # --- A: long legal ids on the wire, echoed upstream, redacted everywhere.
    echo_body = {
        "meta": {
            "code": 400,
            "message": f"{LONG_PROJECT} / {LONG_FILE} rejected; token {TOKEN_1}",
        },
        "data": None,
    }
    log = []
    with pytest.raises(ApiError):
        execute(
            kind,
            method,
            args=(LONG_PROJECT, LONG_FILE),
            script=[lambda request: _resp(echo_body)],
            recorder=recorder,
            log=log,
        )
    wire_request = _http(log)[0]
    _expect_wire(wire_request, method, TOKEN_1, (LONG_PROJECT, LONG_FILE), {})
    surface = caplog.text
    if recorder is not None:
        surface += _dump_events(recorder)
    for secret in (LONG_PROJECT, LONG_FILE, TOKEN_1):
        assert secret not in surface

    # --- B: ids literally equal to "10002" must not confuse classification.
    if method == "file_info":
        project_id = "10002"  # loose rule: legal
        file_id = FILE
    else:
        project_id = PROJECT
        file_id = "10002"  # ordinary word id, legal
    # B1: a non-auth business code plus a short "10002" mention in the message
    # and the actual id value -> no invalidate, no replay, plain ApiError.
    log_b = []
    with pytest.raises(ApiError) as not_auth:
        execute(
            kind,
            method,
            args=(project_id, file_id),
            script=[lambda request: _resp(SHORT_CODE_COLLISION)],
            log=log_b,
        )
    assert type(not_auth.value) is ApiError
    assert not isinstance(not_auth.value, AuthenticationError)
    assert _kinds(log_b) == ["get_token", "factory", "http"]
    # B2: a genuine meta.code=10002 with an id value of "10002" on the wire
    # still classifies as AuthenticationError; reads replay once, writes never.
    log_c = []
    if method in READ_METHODS:
        result_c, _ = execute(
            kind,
            method,
            args=(project_id, file_id),
            script=[lambda request: _resp(STALE), lambda request: _resp()],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log_c,
        )
        assert result_c.code == 200
        assert _kinds(log_c) == [
            "get_token",
            "factory",
            "http",
            "invalidate",
            "get_token",
            "http",
        ]
        replay_requests = _http(log_c)
        _expect_wire(replay_requests[0], method, TOKEN_1, (project_id, file_id), {})
        _expect_wire(replay_requests[1], method, TOKEN_2, (project_id, file_id), {})
    else:
        with pytest.raises(AuthenticationError) as stale_write:
            execute(
                kind,
                method,
                args=(project_id, file_id),
                script=[lambda request: _resp(STALE), lambda request: _resp()],
                provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
                log=log_c,
            )
        assert stale_write.value.invalid_token is True
        assert _kinds(log_c) == ["get_token", "factory", "http", "invalidate"]
