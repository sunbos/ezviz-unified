"""Behavioural tests for the domestic ``cloud_capture`` facade.

Real ``Client``/``AsyncClient`` instances are exercised through scripted
token providers and ``httpx.MockTransport`` transport factories.  No network
access occurs and no ``tests.*`` helper modules are used, so this file is
self-contained for the SDK sdist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from datetime import datetime, timezone

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    RegionConfig,
    Token,
    TransportError,
)
from ezviz_unified.diagnostic_report import DiagnosticRecorder

ORIGIN = "https://open.ys7.com"
FAR_FUTURE = 4_102_444_800.0
TOKEN_A = "capture-token-alpha-1"
TOKEN_B = "capture-token-beta-2"
DEVICE = "C9SENTINEL_DEVICE_0001"
VCODE = "VCODE-SENTINEL-0123456789ABCDEF"
METHODS = ("create_interval_task", "create_timing_task", "task")
WRITE_METHODS = ("create_interval_task", "create_timing_task")


# ---------------------------------------------------------------------------
# local helpers (standalone: no tests.* imports)


def _token(value: str) -> Token:
    return Token(
        value=value,
        expires_at=FAR_FUTURE,
        refresh_at=FAR_FUTURE - 60.0,
        api_base_url=ORIGIN,
    )


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
    """Async get_token; ``invalidate`` stays synchronous like the real one."""

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


def _scripted_factory(log: list, script: list):
    """Return a transport factory serving the queued responses in order."""

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


def _fingerprint(request: httpx.Request) -> tuple:
    return (
        request.method,
        request.url.raw_path.decode("ascii"),
        request.content,
        tuple(sorted(request.headers.items())),
    )


def _kind_fp(entry) -> tuple:
    if entry[0] == "http":
        return ("http", _fingerprint(entry[1]))
    return entry


def _kinds(log: list) -> list:
    return [entry[0] for entry in log]


def _http(log: list) -> list:
    return [entry[1] for entry in log if entry[0] == "http"]


def _form(request: httpx.Request) -> dict:
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _facade_span(events: list, method: str) -> dict:
    """Exactly one started and one terminal event for the facade call."""
    entrypoint = "cloud_capture." + method
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
    return [
        event
        for event in events
        if event["kind"] == "attempt"
        and event["operation"] == "cloud_capture." + method
        and event["outcome"] == "started"
    ]


def _retries(events: list) -> list:
    return [event for event in events if event["kind"] == "retry"]


def _events(recorder: DiagnosticRecorder) -> list:
    """Snapshot of the recorder's events as a list of plain dicts."""
    return recorder.report()["events"]


def _exc_key(exc: BaseException) -> tuple:
    return (
        type(exc),
        getattr(exc, "code", None),
        getattr(exc, "http_status", None),
        getattr(exc, "reason", None),
        getattr(exc, "invalid_token", None),
    )


def _ok(data=None) -> httpx.Response:
    return httpx.Response(
        200,
        json={"meta": {"code": 200, "message": "ok"}, "data": {} if data is None else data},
    )


def _biz(code: int, message: str = "platform failure") -> httpx.Response:
    return httpx.Response(200, json={"meta": {"code": code, "message": message}, "data": None})


def _interval_kwargs(**overrides) -> dict:
    kwargs = {
        "channel": 1,
        "rec_type": "cloud",
        "start_time": datetime(2030, 1, 1, 0, 0, 0),
        "end_time": datetime(2030, 1, 1, 1, 0, 0),
        "frame_interval": 5,
        "project_id": "proj_capture",
    }
    kwargs.update(overrides)
    return kwargs


def _timing_kwargs(**overrides) -> dict:
    kwargs = {
        "channel": 1,
        "rec_type": "local",
        "timing_points": [datetime(2030, 2, 1, 8, 0, 0), datetime(2030, 2, 1, 9, 30, 0)],
        "result_space_id": 7,
    }
    kwargs.update(overrides)
    return kwargs


def _invoke(facade, method, task_id="task_alpha_1", device_serial=DEVICE, **overrides):
    """Dispatch one facade call; for async facades this yields a coroutine."""
    if method == "create_interval_task":
        return facade.create_interval_task(device_serial, **_interval_kwargs(**overrides))
    if method == "create_timing_task":
        return facade.create_timing_task(device_serial, **_timing_kwargs(**overrides))
    if method == "task":
        return facade.task(task_id)
    raise AssertionError(f"unknown facade method {method}")


def _sync_client(*, region=None, script=(), tokens=(), recorder=None):
    log: list = []
    client = Client(
        region=RegionConfig.china() if region is None else region,
        token_provider=_SyncProvider(log, list(tokens)),
        transport_factory=_scripted_factory(log, list(script)),
        diagnostic_recorder=recorder,
    )
    return client, log


def _async_client(*, region=None, script=(), tokens=(), recorder=None, transport=None, log=None):
    if log is None:
        log = []
    factory = (
        _fixed_factory(log, transport)
        if transport is not None
        else _scripted_factory(log, list(script))
    )
    client = AsyncClient(
        region=RegionConfig.china() if region is None else region,
        token_provider=_AsyncProvider(log, list(tokens)),
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    return client, log


_INTERVAL_INVALID = [
    {"channel": True},
    {"channel": "1"},
    {"rec_type": "tape"},
    {"start_time": datetime(2030, 1, 1, tzinfo=timezone.utc)},
    {"end_time": datetime(2029, 12, 31, 23, 59, 59)},
    {"frame_interval": 0},
    {"frame_interval": True},
    {"project_id": None},
    {"project_id": ""},
    {"project_id": "has space"},
    {"project_id": "p" * 33},
    {"result_space_id": 0},
    {"frame_model": 3},
    {"frame_model": True},
    {"dev_proto": "rtsp"},
    {"stream_type": "3"},
    {"validate_code": " "},
]

_TIMING_INVALID = [
    {"channel": False},
    {"rec_type": "tape"},
    {"timing_points": []},
    {"timing_points": "20300201080000"},
    {"timing_points": [datetime(2030, 2, 1, tzinfo=timezone.utc)]},
    {"project_id": None, "result_space_id": None},
    {"project_id": "   "},
    {"frame_model": 3},
    {"dev_proto": "jt808"},
    {"stream_type": "0"},
    {"validate_code": ""},
]

_TASK_INVALID = ["", " ", "x" * 33, "has space", "dash-not-allowed", "任务一"]

# one representative invalid input per facade method for recorder evidence
_VALIDATION_PROBE = {
    "create_interval_task": {"frame_interval": 0},
    "create_timing_task": {"timing_points": []},
    "task": {"task_id": "bad id"},
}


# ---------------------------------------------------------------------------
# A: recorder off/on parity of wire fingerprints and call order


@pytest.mark.parametrize("method", METHODS)
def test_success_wire_and_response_recorder_parity(method):
    runs = []
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_ok({"echo": "value-" + method})],
            tokens=[_token(TOKEN_A)],
            recorder=recorder,
        )
        try:
            response = _invoke(client.cloud_capture, method)
            runs.append((response, [_kind_fp(e) for e in log], recorder))
        finally:
            client.close()
    first, second = runs
    assert first[0].data == second[0].data == {"echo": "value-" + method}
    assert first[0].raw == second[0].raw
    assert first[1] == second[1]
    events = _events(second[2])
    terminal = _facade_span(events, method)
    assert terminal["outcome"] == "succeeded"
    assert terminal["mode"] == "sync"
    assert terminal["region"] == "domestic"
    assert [e["attempt"] for e in _attempts(events, method)] == [1]
    assert _retries(events) == []
    assert _close_span(events)["outcome"] == "succeeded"


@pytest.mark.parametrize("method", METHODS)
def test_business_error_parity_across_recorders(method):
    keys, logs = [], []
    events = None
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_biz(5000, "server refused")],
            tokens=[_token(TOKEN_A)],
            recorder=recorder,
        )
        try:
            with pytest.raises(ApiError) as exc_info:
                _invoke(client.cloud_capture, method)
            keys.append(_exc_key(exc_info.value))
            logs.append([_kind_fp(e) for e in log])
        finally:
            client.close()
        if recorder is not None:
            events = _events(recorder)
    assert keys[0] == keys[1] == (ApiError, 5000, 200, None, None)
    assert logs[0] == logs[1]
    assert [e[0] for e in logs[0]].count("http") == 1
    terminal = _facade_span(events, method)
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "api"


# ---------------------------------------------------------------------------
# A: request-level identity (stub, not HTTP evidence)


@pytest.mark.parametrize("method", METHODS)
def test_request_stub_response_and_exception_identity_sync(method):
    """Instance-level ``request`` stub identity (boundary proof, not HTTP)."""
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_ok({"seed": True})], tokens=[_token(TOKEN_A)], recorder=recorder
        )
        try:
            real = _invoke(client.cloud_capture, method)
            assert isinstance(real, ApiResponse)
            prebuilt = ApiError("prebuilt failure", code="5000")
            original = client.request
            try:
                client.request = lambda *a, **k: real
                assert _invoke(client.cloud_capture, method) is real

                def _boom(*a, **k):
                    raise prebuilt

                client.request = _boom
                with pytest.raises(ApiError) as exc_info:
                    _invoke(client.cloud_capture, method)
                assert exc_info.value is prebuilt
            finally:
                del client.request
                assert client.request == original
        finally:
            client.close()
        if recorder is not None:
            assert _close_span(_events(recorder))["outcome"] == "succeeded"


@pytest.mark.parametrize("method", METHODS)
def test_request_stub_response_and_exception_identity_async(method):
    """Instance-level ``request`` stub identity (boundary proof, not HTTP)."""

    async def run() -> None:
        for recorder in (None, DiagnosticRecorder()):
            client, log = _async_client(
                script=[_ok({"seed": True})],
                tokens=[_token(TOKEN_A)],
                recorder=recorder,
            )
            try:
                real = await _invoke(client.cloud_capture, method)
                assert isinstance(real, ApiResponse)
                prebuilt = TransportError("prebuilt closed", reason="closed")
                original = client.request
                try:

                    async def _ret(*a, **k):
                        return real

                    client.request = _ret
                    assert await _invoke(client.cloud_capture, method) is real

                    async def _boom(*a, **k):
                        raise prebuilt

                    client.request = _boom
                    with pytest.raises(TransportError) as exc_info:
                        await _invoke(client.cloud_capture, method)
                    assert exc_info.value is prebuilt
                finally:
                    del client.request
                    assert client.request == original
            finally:
                await client.aclose()
            if recorder is not None:
                assert _close_span(_events(recorder))["outcome"] == "succeeded"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# B: per-operation read_only policy on platform code 10002


def test_read_10002_recovers_once_and_swaps_token_sync():
    parity_logs = []
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_biz(10002, "access token invalid"), _ok({"taskId": "task_alpha_1"})],
            tokens=[_token(TOKEN_A), _token(TOKEN_B)],
            recorder=recorder,
        )
        try:
            response = client.cloud_capture.task("task_alpha_1")
            assert response.data == {"taskId": "task_alpha_1"}
        finally:
            client.close()
        assert _kinds(log) == [
            "get_token",
            "factory",
            "http",
            "invalidate",
            "get_token",
            "http",
        ]
        assert log[1] == ("factory", ORIGIN)
        assert log[3] == ("invalidate", TOKEN_A)
        requests = _http(log)
        assert TOKEN_A.encode() in requests[0].url.raw_path
        assert TOKEN_B.encode() in requests[1].url.raw_path
        parity_logs.append([_kind_fp(e) for e in log])
        if recorder is not None:
            events = _events(recorder)
            assert _facade_span(events, "task")["outcome"] == "succeeded"
            assert [e["attempt"] for e in _attempts(events, "task")] == [1, 2]
            assert len(_retries(events)) == 1
            assert _close_span(events)["outcome"] == "succeeded"
    assert parity_logs[0] == parity_logs[1]


def test_read_second_10002_gives_up_sync():
    parity = []
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_biz(10002, "invalid"), _biz(10002, "still invalid")],
            tokens=[_token(TOKEN_A), _token(TOKEN_B)],
            recorder=recorder,
        )
        try:
            with pytest.raises(AuthenticationError) as exc_info:
                client.cloud_capture.task("task_alpha_1")
            assert exc_info.value.invalid_token is True
            assert _kinds(log) == [
                "get_token",
                "factory",
                "http",
                "invalidate",
                "get_token",
                "http",
                "invalidate",
            ]
            assert log[3] == ("invalidate", TOKEN_A)
            assert log[6] == ("invalidate", TOKEN_B)
        finally:
            client.close()
        parity.append([_kind_fp(e) for e in log])
        if recorder is not None:
            events = _events(recorder)
            terminal = _facade_span(events, "task")
            assert terminal["outcome"] == "failed"
            assert terminal["error_family"] == "authentication"
            assert [e["attempt"] for e in _attempts(events, "task")] == [1, 2]
            assert len(_retries(events)) == 1
            assert _close_span(events)["outcome"] == "succeeded"
    assert parity[0] == parity[1]


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_write_10002_not_replayed_sync(method):
    parity = []
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_biz(10002, "token expired")],
            tokens=[_token(TOKEN_A)],
            recorder=recorder,
        )
        try:
            with pytest.raises(AuthenticationError) as exc_info:
                _invoke(client.cloud_capture, method)
            assert exc_info.value.invalid_token is True
            # a confirmed 10002 invalidates first even though writes never replay
            assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
            assert log[3] == ("invalidate", TOKEN_A)
        finally:
            client.close()
        parity.append([_kind_fp(e) for e in log])
        if recorder is not None:
            events = _events(recorder)
            terminal = _facade_span(events, method)
            assert terminal["outcome"] == "failed"
            assert terminal["error_family"] == "authentication"
            assert [e["attempt"] for e in _attempts(events, method)] == [1]
            assert _retries(events) == []
            assert _close_span(events)["outcome"] == "succeeded"
    assert parity[0] == parity[1]


# ---------------------------------------------------------------------------
# C: precise error mapping


def test_http_500_maps_to_plain_api_error():
    recorder = DiagnosticRecorder()
    client, log = _sync_client(
        script=[httpx.Response(500, content=b"upstream exploded")],
        tokens=[_token(TOKEN_A)],
        recorder=recorder,
    )
    try:
        with pytest.raises(ApiError) as exc_info:
            client.cloud_capture.task("task_alpha_1")
        err = exc_info.value
        assert type(err) is ApiError
        assert err.http_status == 500
        assert _kinds(log).count("http") == 1
    finally:
        client.close()
    terminal = _facade_span(_events(recorder), "task")
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "api"
    assert terminal["http_status_category"] == "server_error"


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    "transport_error,reason",
    [
        (httpx.ReadTimeout("slow backend"), "timeout"),
        (httpx.ConnectError("refused"), "network"),
    ],
)
def test_transport_failures_map_precisely_without_replay(method, transport_error, reason):
    client, log = _sync_client(script=[transport_error], tokens=[_token(TOKEN_A)])
    try:
        with pytest.raises(TransportError) as exc_info:
            _invoke(client.cloud_capture, method)
        assert exc_info.value.reason == reason
        assert _kinds(log).count("http") == 1
        assert _kinds(log).count("get_token") == 1
    finally:
        client.close()


@pytest.mark.parametrize("method", METHODS)
def test_closed_client_raises_transport_closed_sync(method):
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(recorder=recorder)
        client.close()
        with pytest.raises(TransportError) as exc_info:
            _invoke(client.cloud_capture, method)
        err = exc_info.value
        assert type(err) is TransportError
        assert err.reason == "closed"
        assert log == []
        if recorder is not None:
            events = _events(recorder)
            terminal = _facade_span(events, method)
            assert terminal["outcome"] == "failed"
            assert terminal["error_family"] == "transport"
            assert terminal["reason"] == "closed"
            assert _close_span(events)["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# B: ordering guarantees for region guard, provider, validation


@pytest.mark.parametrize("method", METHODS)
def test_overseas_region_rejected_before_any_io(method):
    client, log = _sync_client(region=RegionConfig.overseas())
    with pytest.raises(ValueError):
        _invoke(client.cloud_capture, method)
    assert log == []
    client.close()


def test_provider_failure_precedes_factory_and_http_and_keeps_identity():
    for error in (
        AuthenticationError("provider denied", code=10002),
        LookupError("provider exploded"),
    ):
        client, log = _sync_client(tokens=[error])
        try:
            with pytest.raises(type(error)) as exc_info:
                client.cloud_capture.task("task_alpha_1")
            assert exc_info.value is error
            assert _kinds(log) == ["get_token"]
        finally:
            client.close()


def test_provider_auth_error_diagnostic_family():
    recorder = DiagnosticRecorder()
    client, log = _sync_client(
        tokens=[AuthenticationError("provider denied", code=10002)], recorder=recorder
    )
    try:
        with pytest.raises(AuthenticationError) as exc_info:
            client.cloud_capture.task("task_alpha_1")
        assert exc_info.value.invalid_token is True
        assert _kinds(log) == ["get_token"]
    finally:
        client.close()
    terminal = _facade_span(_events(recorder), "task")
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "authentication"


@pytest.mark.parametrize("overrides", _INTERVAL_INVALID)
def test_interval_validation_precedes_provider(overrides):
    client, log = _sync_client()
    with pytest.raises(ValueError):
        _invoke(client.cloud_capture, "create_interval_task", **overrides)
    assert log == []
    client.close()


@pytest.mark.parametrize("overrides", _TIMING_INVALID)
def test_timing_validation_precedes_provider(overrides):
    client, log = _sync_client()
    with pytest.raises(ValueError):
        _invoke(client.cloud_capture, "create_timing_task", **overrides)
    assert log == []
    client.close()


@pytest.mark.parametrize("task_id", _TASK_INVALID)
def test_task_id_validation_precedes_provider(task_id):
    client, log = _sync_client()
    with pytest.raises(ValueError):
        client.cloud_capture.task(task_id)
    assert log == []
    client.close()


@pytest.mark.parametrize("method", METHODS)
def test_validation_failure_recorded_by_recorder(method):
    """One invalid input per facade method lands in a validation span.

    Both modes run with a real recorder: the call fails inside facade
    validation before any provider/factory/http entry is written.
    """
    overrides = _VALIDATION_PROBE[method]

    recorder = DiagnosticRecorder()
    client, log = _sync_client(recorder=recorder)
    try:
        with pytest.raises(ValueError):
            _invoke(client.cloud_capture, method, **overrides)
        assert log == []
    finally:
        client.close()
    events = _events(recorder)
    terminal = _facade_span(events, method)
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "validation"
    assert terminal["mode"] == "sync"
    assert _attempts(events, method) == []
    assert _close_span(events)["outcome"] == "succeeded"

    async def run() -> None:
        recorder = DiagnosticRecorder()
        client, log = _async_client(recorder=recorder)
        try:
            with pytest.raises(ValueError):
                await _invoke(client.cloud_capture, method, **overrides)
            assert log == []
        finally:
            await client.aclose()
        events = _events(recorder)
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "failed"
        assert terminal["error_family"] == "validation"
        assert terminal["mode"] == "async"
        assert _attempts(events, method) == []
        assert _close_span(events)["outcome"] == "succeeded"

    asyncio.run(run())


@pytest.mark.parametrize("serial", ["", "   ", "C9\ud800"])
def test_device_serial_rejected_before_provider(serial):
    client, log = _sync_client()
    for method in WRITE_METHODS:
        with pytest.raises(ValueError):
            _invoke(client.cloud_capture, method, device_serial=serial)
    assert log == []
    client.close()


# ---------------------------------------------------------------------------
# wire-level behaviour of the real client


def test_interval_task_wire_form_fields():
    client, log = _sync_client(script=[_ok({"jobId": "job-1"})], tokens=[_token(TOKEN_A)])
    try:
        _invoke(
            client.cloud_capture,
            "create_interval_task",
            frame_model=2,
            dev_proto="jt808",
            stream_type="2",
            validate_code=VCODE,
            result_space_id=42,
        )
    finally:
        client.close()
    request = _http(log)[0]
    assert request.method == "POST"
    assert request.url.raw_path == b"/api/v3/open/cloud/video/frame/interval/start"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    form = _form(request)
    assert form["deviceSerial"] == [DEVICE]
    assert form["channelNo"] == ["1"]
    assert form["recType"] == ["cloud"]
    assert form["startTime"] == ["20300101000000"]
    assert form["endTime"] == ["20300101010000"]
    assert form["frameInterval"] == ["5"]
    assert form["projectId"] == ["proj_capture"]
    assert form["resultSpaceId"] == ["42"]
    assert form["frameModel"] == ["2"]
    assert form["devProto"] == ["jt808"]
    assert form["streamType"] == ["2"]
    assert form["validateCode"] == [VCODE]
    assert TOKEN_A.encode() in request.content


def test_interval_task_omits_unset_optional_fields():
    client, log = _sync_client(script=[_ok({})], tokens=[_token(TOKEN_A)])
    try:
        _invoke(client.cloud_capture, "create_interval_task")
    finally:
        client.close()
    form = _form(_http(log)[0])
    for key in ("frameModel", "devProto", "streamType", "validateCode", "resultSpaceId"):
        assert key not in form
    assert form["projectId"] == ["proj_capture"]


def test_interval_task_equal_times_and_max_project_id_pass_through():
    client, log = _sync_client(script=[_ok({})], tokens=[_token(TOKEN_A)])
    try:
        _invoke(
            client.cloud_capture,
            "create_interval_task",
            start_time=datetime(2030, 3, 4, 5, 6, 7),
            end_time=datetime(2030, 3, 4, 5, 6, 7),
            project_id="P" * 32,
        )
    finally:
        client.close()
    form = _form(_http(log)[0])
    assert form["startTime"] == form["endTime"] == ["20300304050607"]
    assert form["projectId"] == ["P" * 32]


def test_timing_task_wire_preserves_order_duplicates_and_loose_selectors():
    project = "项目_Task_Project_允许超过三十二字符_ABCDEF"
    client, log = _sync_client(script=[_ok({})], tokens=[_token(TOKEN_A)])
    try:
        _invoke(
            client.cloud_capture,
            "create_timing_task",
            timing_points=[
                datetime(2030, 2, 1, 9, 0, 0),
                datetime(2030, 2, 1, 8, 0, 0),
                datetime(2030, 2, 1, 8, 0, 0),
            ],
            project_id=project,
            result_space_id=-5,
            frame_model=2,
        )
    finally:
        client.close()
    request = _http(log)[0]
    assert request.url.raw_path == b"/api/v3/open/cloud/video/frame/timing/start"
    form = _form(request)
    assert form["projectId"] == [project]
    assert form["resultSpaceId"] == ["-5"]
    assert form["frameModel"] == ["2"]
    assert form["timingPoints"] == ["20300201090000,20300201080000,20300201080000"]
    assert form["recType"] == ["local"]


def test_task_wire_uses_path_param_and_query_token():
    task_id = "task_" + "A" * 27
    client, log = _sync_client(script=[_ok({"taskId": task_id})], tokens=[_token(TOKEN_A)])
    try:
        response = client.cloud_capture.task(task_id)
        assert response.data == {"taskId": task_id}
    finally:
        client.close()
    request = _http(log)[0]
    assert request.method == "GET"
    raw_path = request.url.raw_path.decode("ascii")
    assert raw_path.startswith("/api/v3/open/cloud/task/" + task_id + "?")
    assert TOKEN_A in raw_path
    assert request.content == b""


@pytest.mark.parametrize("task_id", ["a" * 32, "Z9_" + "b" * 29])
def test_task_id_boundary_lengths_accepted(task_id):
    client, log = _sync_client(script=[_ok({"ok": True})], tokens=[_token(TOKEN_A)])
    try:
        client.cloud_capture.task(task_id)
    finally:
        client.close()
    assert _kinds(log).count("http") == 1


# ---------------------------------------------------------------------------
# F: literal "10002" payload values must not break invalid-token semantics


def test_literal_10002_values_do_not_break_semantics():
    for recorder in (None, DiagnosticRecorder()):
        client, log = _sync_client(
            script=[_ok({"taskId": "10002"})], tokens=[_token(TOKEN_A)], recorder=recorder
        )
        try:
            response = client.cloud_capture.task("10002")
            assert response.data["taskId"] == "10002"
        finally:
            client.close()
        assert _kinds(log).count("http") == 1
        assert b"10002" in _http(log)[0].url.raw_path

        client, log = _sync_client(
            script=[_biz(10002), _ok({"taskId": "10002"})],
            tokens=[_token(TOKEN_A), _token(TOKEN_B)],
            recorder=recorder,
        )
        try:
            response = client.cloud_capture.task("10002")
            assert response.data["taskId"] == "10002"
        finally:
            client.close()
        assert _kinds(log).count("http") == 2

        client, log = _sync_client(
            script=[_biz(10002, "token invalid")],
            tokens=[_token(TOKEN_A)],
            recorder=recorder,
        )
        try:
            with pytest.raises(AuthenticationError) as exc_info:
                _invoke(client.cloud_capture, "create_interval_task", project_id="10002")
            assert exc_info.value.invalid_token is True
        finally:
            client.close()
        assert _kinds(log).count("http") == 1


# ---------------------------------------------------------------------------
# F: privacy of sent request values


_PRIVACY_CASES = {
    "create_interval_task": {
        # ASCII project (<=32) + validate_code + result_space_id form ints
        "device_serial": "C9PRIVACYINTERVAL00000000001",
        "overrides": {
            "project_id": "proj_PRIVINT_0123456789ABCDEF",
            "validate_code": "VCODE_PRIVACY_INT_0123456789AB",
            "result_space_id": 4242424242,
        },
        "sentinel_map": {
            "serial": "C9PRIVACYINTERVAL00000000001",
            "project": "proj_PRIVINT_0123456789ABCDEF",
            "vcode": "VCODE_PRIVACY_INT_0123456789AB",
            "space": "4242424242",
        },
        "form_sentinels": ("serial", "project", "vcode", "space"),
        "query_sentinels": (),
        "path_sentinels": (),
    },
    "create_timing_task": {
        # non-ASCII project may exceed 32 chars and stays legal on this path
        "device_serial": "C9PRIVACYTIMING000000000001",
        "overrides": {
            "project_id": "项目_TIMING_隐私_SENTINEL_超三十二字符边界_ABCDEF",
            "result_space_id": 5555555555,
        },
        "sentinel_map": {
            "serial": "C9PRIVACYTIMING000000000001",
            "project": "项目_TIMING_隐私_SENTINEL_超三十二字符边界_ABCDEF",
            "space": "5555555555",
        },
        "form_sentinels": ("serial", "project", "space"),
        "query_sentinels": (),
        "path_sentinels": (),
    },
    "task": {
        # real path taskId + query token only; nothing else is sent
        "task_id": "task_PRIVACY_sentinel_0123456789",
        "sentinel_map": {},
        "form_sentinels": (),
        "query_sentinels": ("token",),
        "path_sentinels": ("task_id",),
    },
}

_PRIVACY_FOREIGN = "https://unrelated.invalid/never-sent?q=42"


def _privacy_case(method):
    case = dict(_PRIVACY_CASES[method])
    sentinels = dict(case["sentinel_map"])
    if method == "task":
        sentinels["task_id"] = case["task_id"]
    sentinels["token"] = TOKEN_A
    case["sentinels"] = sentinels
    # flat SDK kwargs: project_id/result_space_id/validate_code sit beside
    # device_serial — a nested "overrides" dict is not a real facade kwarg.
    invoke_kwargs = {key: case[key] for key in ("device_serial", "task_id") if key in case}
    invoke_kwargs.update(case.get("overrides", {}))
    case["invoke_kwargs"] = invoke_kwargs
    return case


def _check_privacy(method, case, request, err, events, caplog_text):
    sentinels = case["sentinels"]
    known = list(sentinels.values())
    form = _form(request)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url.raw_path.decode("utf-8")).query)
    path = urllib.parse.unquote(urllib.parse.urlsplit(request.url.raw_path.decode("utf-8")).path)
    for name in case["form_sentinels"]:
        values = [value for pair in form.items() for value in pair[1]]
        assert sentinels[name] in values  # sent wire form value unchanged
    for name in case["query_sentinels"]:
        values = [value for pair in query.items() for value in pair[1]]
        assert sentinels[name] in values  # sent query value unchanged
    for name in case["path_sentinels"]:
        assert sentinels[name] in path  # sent path value unchanged
    for text in (str(err), repr(err)):
        for value in known:
            assert value not in text
        # never sent: free error text is still allowed in the error itself
        assert _PRIVACY_FOREIGN in text
    blob = json.dumps(events, default=str)
    for value in known + [_PRIVACY_FOREIGN]:
        assert value not in blob
        assert value not in caplog_text


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("use_recorder", (False, True))
def test_sent_request_values_masked_in_errors_logs_and_diagnostics_sync(
    caplog, method, use_recorder
):
    case = _privacy_case(method)
    recorder = DiagnosticRecorder() if use_recorder else None
    message = "backend echo {0} {1}".format(" ".join(case["sentinels"].values()), _PRIVACY_FOREIGN)
    client, log = _sync_client(
        script=[_biz(5000, message)], tokens=[_token(TOKEN_A)], recorder=recorder
    )
    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ApiError) as exc_info:
                _invoke(client.cloud_capture, method, **case["invoke_kwargs"])
    finally:
        client.close()
    _check_privacy(
        method,
        case,
        _http(log)[0],
        exc_info.value,
        _events(recorder) if recorder is not None else [],
        caplog.text,
    )


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("use_recorder", (False, True))
def test_sent_request_values_masked_in_errors_logs_and_diagnostics_async(
    caplog, method, use_recorder
):
    async def run() -> None:
        case = _privacy_case(method)
        recorder = DiagnosticRecorder() if use_recorder else None
        message = "backend echo {0} {1}".format(
            " ".join(case["sentinels"].values()), _PRIVACY_FOREIGN
        )
        client, log = _async_client(
            script=[_biz(5000, message)], tokens=[_token(TOKEN_A)], recorder=recorder
        )
        try:
            with caplog.at_level(logging.DEBUG):
                with pytest.raises(ApiError) as exc_info:
                    await _invoke(client.cloud_capture, method, **case["invoke_kwargs"])
        finally:
            await client.aclose()
        _check_privacy(
            method,
            case,
            _http(log)[0],
            exc_info.value,
            _events(recorder) if recorder is not None else [],
            caplog.text,
        )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# async surface


@pytest.mark.parametrize("method", METHODS)
def test_async_success_wire_and_response_recorder_parity(method):
    async def run() -> None:
        runs = []
        for recorder in (None, DiagnosticRecorder()):
            client, log = _async_client(
                script=[_ok({"m": method})], tokens=[_token(TOKEN_A)], recorder=recorder
            )
            try:
                response = await _invoke(client.cloud_capture, method)
                runs.append((response, [_kind_fp(e) for e in log], recorder))
            finally:
                await client.aclose()
        first, second = runs
        assert first[0].data == second[0].data == {"m": method}
        assert first[0].raw == second[0].raw
        assert first[1] == second[1]
        events = _events(second[2])
        terminal = _facade_span(events, method)
        assert terminal["outcome"] == "succeeded"
        assert terminal["mode"] == "async"
        assert terminal["region"] == "domestic"
        assert [e["attempt"] for e in _attempts(events, method)] == [1]
        assert _close_span(events)["outcome"] == "succeeded"

    asyncio.run(run())


def test_async_read_10002_recovers_once():
    async def run() -> None:
        parity = []
        for recorder in (None, DiagnosticRecorder()):
            client, log = _async_client(
                script=[_biz(10002), _ok({"taskId": "task_alpha_1"})],
                tokens=[_token(TOKEN_A), _token(TOKEN_B)],
                recorder=recorder,
            )
            try:
                response = await client.cloud_capture.task("task_alpha_1")
                assert response.data == {"taskId": "task_alpha_1"}
                assert _kinds(log) == [
                    "get_token",
                    "factory",
                    "http",
                    "invalidate",
                    "get_token",
                    "http",
                ]
                assert log[3] == ("invalidate", TOKEN_A)
                requests = _http(log)
                assert TOKEN_A.encode() in requests[0].url.raw_path
                assert TOKEN_B.encode() in requests[1].url.raw_path
            finally:
                await client.aclose()
            parity.append([_kind_fp(e) for e in log])
            if recorder is not None:
                events = _events(recorder)
                terminal = _facade_span(events, "task")
                assert terminal["outcome"] == "succeeded"
                assert terminal["mode"] == "async"
                assert [e["attempt"] for e in _attempts(events, "task")] == [1, 2]
                assert len(_retries(events)) == 1
                assert _close_span(events)["outcome"] == "succeeded"
        assert parity[0] == parity[1]

    asyncio.run(run())


def test_async_read_second_10002_gives_up():
    async def run() -> None:
        parity = []
        for recorder in (None, DiagnosticRecorder()):
            client, log = _async_client(
                script=[_biz(10002, "invalid"), _biz(10002, "still invalid")],
                tokens=[_token(TOKEN_A), _token(TOKEN_B)],
                recorder=recorder,
            )
            try:
                with pytest.raises(AuthenticationError) as exc_info:
                    await client.cloud_capture.task("task_alpha_1")
                assert exc_info.value.invalid_token is True
                assert _kinds(log) == [
                    "get_token",
                    "factory",
                    "http",
                    "invalidate",
                    "get_token",
                    "http",
                    "invalidate",
                ]
                assert log[3] == ("invalidate", TOKEN_A)
                assert log[6] == ("invalidate", TOKEN_B)
            finally:
                await client.aclose()
            parity.append([_kind_fp(e) for e in log])
            if recorder is not None:
                events = _events(recorder)
                terminal = _facade_span(events, "task")
                assert terminal["outcome"] == "failed"
                assert terminal["error_family"] == "authentication"
                assert [e["attempt"] for e in _attempts(events, "task")] == [1, 2]
                assert len(_retries(events)) == 1
                assert _close_span(events)["outcome"] == "succeeded"
        assert parity[0] == parity[1]

    asyncio.run(run())


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_async_write_10002_not_replayed(method):
    async def run() -> None:
        parity = []
        for recorder in (None, DiagnosticRecorder()):
            client, log = _async_client(
                script=[_biz(10002)],
                tokens=[_token(TOKEN_A)],
                recorder=recorder,
            )
            try:
                with pytest.raises(AuthenticationError) as exc_info:
                    await _invoke(client.cloud_capture, method)
                assert exc_info.value.invalid_token is True
                # writes never replay, but a confirmed 10002 still invalidates
                assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
                assert log[3] == ("invalidate", TOKEN_A)
            finally:
                await client.aclose()
            parity.append([_kind_fp(e) for e in log])
            if recorder is not None:
                events = _events(recorder)
                terminal = _facade_span(events, method)
                assert terminal["outcome"] == "failed"
                assert terminal["error_family"] == "authentication"
                assert [e["attempt"] for e in _attempts(events, method)] == [1]
                assert _retries(events) == []
                assert _close_span(events)["outcome"] == "succeeded"
        assert parity[0] == parity[1]

    asyncio.run(run())


def test_async_timeout_maps_to_transport_timeout():
    async def run() -> None:
        client, log = _async_client(
            script=[httpx.ReadTimeout("slow backend")], tokens=[_token(TOKEN_A)]
        )
        try:
            with pytest.raises(TransportError) as exc_info:
                await client.cloud_capture.task("task_alpha_1")
            assert exc_info.value.reason == "timeout"
            assert _kinds(log).count("http") == 1
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("method", METHODS)
def test_async_closed_client_raises_transport_closed(method):
    async def run() -> None:
        for recorder in (None, DiagnosticRecorder()):
            client, log = _async_client(recorder=recorder)
            await client.aclose()
            with pytest.raises(TransportError) as exc_info:
                await _invoke(client.cloud_capture, method)
            err = exc_info.value
            assert type(err) is TransportError
            assert err.reason == "closed"
            assert log == []
            if recorder is not None:
                events = _events(recorder)
                terminal = _facade_span(events, method)
                assert terminal["outcome"] == "failed"
                assert terminal["error_family"] == "transport"
                assert terminal["reason"] == "closed"
                assert _close_span(events)["outcome"] == "succeeded"

    asyncio.run(run())


def test_async_validation_precedes_provider():
    async def run() -> None:
        client, log = _async_client()
        try:
            with pytest.raises(ValueError):
                await _invoke(client.cloud_capture, "create_interval_task", frame_interval=0)
            with pytest.raises(ValueError):
                await _invoke(client.cloud_capture, "task", task_id="bad id")
            assert log == []
        finally:
            await client.aclose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# D: true task cancellation with instance identity


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("use_recorder", (False, True))
def test_async_cancellation_propagates_same_instance(method, use_recorder):
    async def run() -> None:
        log: list = []
        transport = _BlockingTransport(log)
        recorder = DiagnosticRecorder() if use_recorder else None
        client, client_log = _async_client(
            tokens=[_token(TOKEN_A)],
            transport=transport,
            recorder=recorder,
            log=log,
        )
        assert client_log is log
        try:
            inner: list = []

            async def invoke() -> None:
                try:
                    await _invoke(client.cloud_capture, method)
                except asyncio.CancelledError as error:
                    inner.append(error)
                    raise

            job = asyncio.create_task(invoke())
            await asyncio.wait_for(transport.started.wait(), timeout=5.0)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            assert job.cancelled()
            assert len(inner) == 1
            assert transport.cancelled_error is not None
            assert inner[0] is transport.cancelled_error
            assert transport.request_count == 1
            # exactly one get_token/factory/http entry, no retry
            assert _kinds(log) == ["get_token", "factory", "http"]
            assert log[1] == ("factory", ORIGIN)
            if recorder is not None:
                events = _events(recorder)
                terminal = _facade_span(events, method)
                assert terminal["outcome"] == "cancelled"
                assert terminal["error_family"] == "interrupted"
                assert len(_attempts(events, method)) == 1
                assert _retries(events) == []
        finally:
            await client.aclose()
        assert transport.request_count == 1
        assert _kinds(log) == ["get_token", "factory", "http"]
        if recorder is not None:
            assert _close_span(_events(recorder))["outcome"] == "succeeded"

    asyncio.run(run())
