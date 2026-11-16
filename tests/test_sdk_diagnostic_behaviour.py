"""Behaviour-parity tests for the diagnostic recorder (DG006B: H04-H06).

Offline and synthetic only: scripted token providers, ``httpx.MockTransport``
and gated ``httpx.AsyncBaseTransport`` doubles; no environment, network or
credential access and no imports from other test modules. Companion
privacy/coverage hardening tests live in a different file.
"""

from __future__ import annotations

import asyncio
import traceback

import httpx
import pytest

from ezviz_unified.auth_models import RegionConfig, Token
from ezviz_unified.client import AsyncClient, Client
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.errors import (
    AuthenticationError,
    PermissionDeniedError,
    TransportError,
)
from ezviz_unified.operations import Operation

_ORIGIN = "https://open.ys7.com"
_REGION = RegionConfig.china()
_FAR_FUTURE = 9_999_999_999.0
_TIMEOUT = 5.0
_TOKEN_1 = "synthetic-token-alpha-0001"
_TOKEN_2 = "synthetic-token-beta-0002"

_OP = Operation(
    name="devices.list",
    method="POST",
    path="/api/lapp/device/list",
    token_location="form",
    read_only=True,
)

_FACTORY_FLOW = [
    ("call", "started"),
    ("authentication", "started"),
    ("authentication", "succeeded"),
    ("transport_setup", "started"),
    ("transport_setup", "failed"),
    ("call", "failed"),
]

_RETRY_ONCE_FLOW = [
    ("call", "started"),
    ("authentication", "started"),
    ("authentication", "succeeded"),
    ("transport_setup", "started"),
    ("transport_setup", "succeeded"),
    ("attempt", "started"),
    ("attempt", "failed"),
    ("retry", "scheduled"),
    ("authentication", "started"),
    ("authentication", "succeeded"),
    ("transport_setup", "started"),
    ("transport_setup", "succeeded"),
    ("attempt", "started"),
    ("attempt", "failed"),
    ("call", "failed"),
]

_PROVIDER_FAIL_FLOW = [
    ("call", "started"),
    ("authentication", "started"),
    ("authentication", "succeeded"),
    ("transport_setup", "started"),
    ("transport_setup", "succeeded"),
    ("attempt", "started"),
    ("attempt", "failed"),
    ("retry", "scheduled"),
    ("authentication", "started"),
    ("authentication", "failed"),
    ("call", "failed"),
]


def _token(value: str) -> Token:
    return Token(
        value=value,
        expires_at=_FAR_FUTURE,
        refresh_at=_FAR_FUTURE - 60.0,
        api_base_url=_ORIGIN,
    )


def _stale() -> httpx.Response:
    return httpx.Response(200, json={"code": "10002", "msg": "stale token"})


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("synthetic network failure", request=request)


def _fingerprint(request: httpx.Request) -> tuple:
    return (
        request.method,
        request.url.raw_path.decode("ascii"),
        request.content,
        tuple(sorted(request.headers.items())),
    )


def _flow(events: list) -> list:
    return [(event["kind"], event["outcome"]) for event in events]


def _kind(events: list, kind: str) -> list:
    return [event for event in events if event["kind"] == kind]


def _tb_names(exc: BaseException) -> list:
    return [frame.name for frame in traceback.extract_tb(exc.__traceback__)]


class _SyncProvider:
    """Scripted provider recording the full get_token/invalidate order."""

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
        if callable(item):
            return item()
        return item

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
        if callable(item):
            return item()
        return item

    def invalidate(self, expected_value: str | None = None) -> None:
        self._log.append(("invalidate", expected_value))


class _OddValueError(ValueError):
    """Untrusted ValueError subclass; must stay unknown, never validation."""


def _scripted_factory(log: list, responses: list):
    script = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(("http", _fingerprint(request)))
        if not script:
            raise AssertionError("unscripted HTTP request")
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(request)
        return item

    mock = httpx.MockTransport(handler)

    def factory(origin: str):
        log.append(("factory", origin))
        return mock

    return factory


def _sync_client(log: list, responses: list, provider_script: list, recorder) -> Client:
    return Client(
        region=_REGION,
        token_provider=_SyncProvider(log, provider_script),
        transport_factory=_scripted_factory(log, responses),
        diagnostic_recorder=recorder,
    )


def _async_client(log: list, responses: list, provider_script: list, recorder) -> AsyncClient:
    return AsyncClient(
        region=_REGION,
        token_provider=_AsyncProvider(log, provider_script),
        transport_factory=_scripted_factory(log, responses),
        diagnostic_recorder=recorder,
    )


class _BlockingRequestTransport(httpx.AsyncBaseTransport):
    """Holds the in-flight request open until the awaiting task is cancelled."""

    def __init__(self) -> None:
        self.request_count = 0
        self.aclose_count = 0
        self.started = asyncio.Event()
        self.cancel_exc: BaseException | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            self.cancel_exc = exc
            raise
        raise AssertionError("gate released unexpectedly")

    async def aclose(self) -> None:
        self.aclose_count += 1


class _GatedCloseTransport(httpx.AsyncBaseTransport):
    """Delays the real transport aclose behind an explicit release gate."""

    def __init__(self) -> None:
        self.aclose_count = 0
        self.aclose_started = asyncio.Event()
        self.aclose_release = asyncio.Event()
        self.aclose_finished = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "200", "data": {}})

    async def aclose(self) -> None:
        self.aclose_count += 1
        self.aclose_started.set()
        await self.aclose_release.wait()
        self.aclose_finished.set()


def _assert_factory_terminal(events: list, bad_code: int) -> None:
    assert _flow(events) == _FACTORY_FLOW
    failed = _kind(events, "transport_setup")[-1]
    assert failed["error_family"] == "authentication"
    assert failed["code_category"] == ("invalid_token" if bad_code == 10002 else "other")
    assert events[-1]["error_family"] == "authentication"
    assert not _kind(events, "attempt")
    assert not _kind(events, "retry")


# -- H04: factory AuthenticationError stays outside the retry try-block ------


@pytest.mark.parametrize("bad_code", [10002, 401])
def test_sync_factory_authentication_error_is_terminal(bad_code: int) -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    factory_exc = AuthenticationError("factory rejected", code=bad_code)

    def factory(origin: str):
        log.append(("factory", origin))
        raise factory_exc

    client = Client(
        region=_REGION,
        token_provider=_SyncProvider(log, [_token(_TOKEN_1)]),
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    try:
        with pytest.raises(AuthenticationError) as caught:
            client.request(_OP)
    finally:
        events = recorder.report()["events"]
        client.close()
    assert caught.value is factory_exc
    assert log == [("get_token",), ("factory", _ORIGIN)]
    _assert_factory_terminal(events, bad_code)
    assert all(
        event["mode"] == "sync"
        and event["entrypoint"] == "client.request"
        and event["operation"] == "devices.list"
        for event in events
    )


@pytest.mark.parametrize("bad_code", [10002, 401])
def test_async_factory_authentication_error_is_terminal(bad_code: int) -> None:
    asyncio.run(_async_factory_auth_error(bad_code))


async def _async_factory_auth_error(bad_code: int) -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    factory_exc = AuthenticationError("factory rejected", code=bad_code)

    def factory(origin: str):
        log.append(("factory", origin))
        raise factory_exc

    client = AsyncClient(
        region=_REGION,
        token_provider=_AsyncProvider(log, [_token(_TOKEN_1)]),
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    try:
        with pytest.raises(AuthenticationError) as caught:
            await client.request(_OP)
    finally:
        events = recorder.report()["events"]
        await client.aclose()
    assert caught.value is factory_exc
    assert log == [("get_token",), ("factory", _ORIGIN)]
    _assert_factory_terminal(events, bad_code)
    assert all(event["mode"] == "async" for event in events)


# -- H04: exactly one retry; the second failure keeps its real identity ------


def test_sync_second_attempt_business_error_is_terminal() -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    responses = [
        _stale(),
        httpx.Response(200, json={"code": "10031", "msg": "denied"}),
    ]
    client = _sync_client(log, responses, [_token(_TOKEN_1), _token(_TOKEN_2)], recorder)
    try:
        with pytest.raises(PermissionDeniedError) as caught:
            client.request(_OP, data={"limit": "1"})
    finally:
        events = recorder.report()["events"]
        client.close()
    assert caught.value.code == "10031"
    assert [item[0] for item in log] == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    assert log[3] == ("invalidate", _TOKEN_1)
    bodies = [item[1][2] for item in log if item[0] == "http"]
    assert _TOKEN_1.encode() in bodies[0]
    assert _TOKEN_2.encode() in bodies[1]
    assert _flow(events) == _RETRY_ONCE_FLOW
    attempts = _kind(events, "attempt")
    assert [e["attempt"] for e in attempts] == [1, 1, 2, 2]
    assert (attempts[1]["error_family"], attempts[1]["code_category"]) == (
        "authentication",
        "invalid_token",
    )
    assert (attempts[3]["error_family"], attempts[3]["code_category"]) == (
        "permission",
        "permission_denied",
    )
    assert events[-1]["error_family"] == "permission"
    assert len(_kind(events, "retry")) == 1


def _assert_second_invalid_token(events: list, log: list, exc: AuthenticationError) -> None:
    assert exc.invalid_token and exc.http_status == 401  # second attempt's object
    assert [item[0] for item in log] == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
        "invalidate",
    ]
    assert log[3] == ("invalidate", _TOKEN_1)
    assert log[6] == ("invalidate", _TOKEN_2)
    assert _flow(events) == _RETRY_ONCE_FLOW
    retries = _kind(events, "retry")
    assert len(retries) == 1
    assert retries[0]["outcome"] == "scheduled" and retries[0]["attempt"] == 2
    attempts = _kind(events, "attempt")
    assert [e["attempt"] for e in attempts] == [1, 1, 2, 2]
    assert attempts[3]["error_family"] == "authentication"
    assert attempts[3]["code_category"] == "invalid_token"
    assert attempts[3]["http_status_category"] == "client_error"
    assert events[-1]["error_family"] == "authentication"


def test_sync_second_attempt_invalid_token_retries_once() -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    responses = [
        _stale(),
        httpx.Response(401, json={"code": "10002", "msg": "still stale"}),
    ]
    client = _sync_client(log, responses, [_token(_TOKEN_1), _token(_TOKEN_2)], recorder)
    try:
        with pytest.raises(AuthenticationError) as caught:
            client.request(_OP)
    finally:
        events = recorder.report()["events"]
        client.close()
    _assert_second_invalid_token(events, log, caught.value)


def test_async_second_attempt_invalid_token_retries_once() -> None:
    asyncio.run(_async_second_invalid_token())


async def _async_second_invalid_token() -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    responses = [
        _stale(),
        httpx.Response(401, json={"code": "10002", "msg": "still stale"}),
    ]
    client = _async_client(log, responses, [_token(_TOKEN_1), _token(_TOKEN_2)], recorder)
    try:
        with pytest.raises(AuthenticationError) as caught:
            await client.request(_OP)
    finally:
        events = recorder.report()["events"]
        await client.aclose()
    _assert_second_invalid_token(events, log, caught.value)
    assert all(event["mode"] == "async" for event in events)


@pytest.mark.parametrize(
    "failure,family",
    [
        (RuntimeError("provider offline"), "unknown"),
        (AuthenticationError("stale provider token", code=10002), "authentication"),
        (_OddValueError("weird provider value"), "unknown"),
    ],
    ids=["runtime-error", "auth-error", "valueerror-subclass"],
)
def test_sync_second_provider_failure_is_terminal(failure, family) -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    client = _sync_client(log, [_stale()], [_token(_TOKEN_1), failure], recorder)
    try:
        with pytest.raises(type(failure)) as caught:
            client.request(_OP)
    finally:
        events = recorder.report()["events"]
        client.close()
    assert caught.value is failure
    # The provider's 10002 never reaches the transport retry handler either.
    assert [item[0] for item in log] == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
    ]
    assert log[3] == ("invalidate", _TOKEN_1)
    assert _flow(events) == _PROVIDER_FAIL_FLOW
    auth_events = _kind(events, "authentication")
    assert auth_events[-1]["error_family"] == family
    assert events[-1]["error_family"] == family


def test_async_second_provider_failure_is_terminal() -> None:
    asyncio.run(_async_second_provider_failure())


async def _async_second_provider_failure() -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    failure = RuntimeError("provider offline")
    client = _async_client(log, [_stale()], [_token(_TOKEN_1), failure], recorder)
    try:
        with pytest.raises(RuntimeError) as caught:
            await client.request(_OP)
    finally:
        events = recorder.report()["events"]
        await client.aclose()
    assert caught.value is failure
    assert [item[0] for item in log] == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
    ]
    assert _flow(events) == _PROVIDER_FAIL_FLOW
    assert _kind(events, "authentication")[-1]["error_family"] == "unknown"


# -- H05: identical wire sequence and responses with/without recorder --------


def _run_sync_retry_script(recorder):
    log: list = []
    responses = [
        _stale(),
        httpx.Response(200, json={"code": "200", "msg": "ok", "data": {"total": 1}}),
    ]
    client = _sync_client(log, responses, [_token(_TOKEN_1), _token(_TOKEN_2)], recorder)
    try:
        response = client.request(_OP, data={"limit": "1"}, headers={"X-Probe": "dg006b"})
    finally:
        client.close()
    return log, response


def test_sync_wire_identical_with_and_without_recorder() -> None:
    log_off, response_off = _run_sync_retry_script(None)
    recorder = DiagnosticRecorder()
    log_on, response_on = _run_sync_retry_script(recorder)
    # Full sequence and payload equality: order, method, path, body, headers.
    assert log_on == log_off
    assert [item[0] for item in log_on] == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    assert (
        response_on.http_status,
        response_on.code,
        response_on.operation,
        response_on.message,
        response_on.data,
        response_on.raw,
    ) == (
        response_off.http_status,
        response_off.code,
        response_off.operation,
        response_off.message,
        response_off.data,
        response_off.raw,
    )
    assert response_on.code == "200" and response_on.data == {"total": 1}
    assert ("retry", "scheduled") in _flow(recorder.report()["events"])


def test_async_wire_identical_with_and_without_recorder() -> None:
    log_off, response_off = asyncio.run(_run_async_retry_script(None))
    recorder = DiagnosticRecorder()
    log_on, response_on = asyncio.run(_run_async_retry_script(recorder))
    assert log_on == log_off
    assert [item[0] for item in log_on] == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    assert (
        response_on.http_status,
        response_on.code,
        response_on.operation,
        response_on.message,
        response_on.data,
        response_on.raw,
    ) == (
        response_off.http_status,
        response_off.code,
        response_off.operation,
        response_off.message,
        response_off.data,
        response_off.raw,
    )
    assert ("retry", "scheduled") in _flow(recorder.report()["events"])


async def _run_async_retry_script(recorder):
    log: list = []
    responses = [
        _stale(),
        httpx.Response(200, json={"code": "200", "msg": "ok", "data": {"total": 1}}),
    ]
    client = _async_client(log, responses, [_token(_TOKEN_1), _token(_TOKEN_2)], recorder)
    try:
        response = await client.request(_OP, data={"limit": "1"}, headers={"X-Probe": "dg006b"})
    finally:
        await client.aclose()
    return log, response


# -- H05: exception identity, traceback function chain and cause/context -----


def _chained_failure() -> tuple:
    """Fresh marker plus a raiser that throws it while a context exception is
    in flight, so the marker always crosses the SDK boundary carrying a real,
    referenced cause/context/suppress_context chain regardless of entrypoint."""
    cause = KeyError("synthetic-cause")
    context = IndexError("synthetic-context")
    try:
        raise RuntimeError("synthetic-marker") from cause
    except RuntimeError as exc:
        marker = exc

    def raise_marker(*_args):
        try:
            raise context
        except IndexError:
            raise marker
        raise AssertionError("context exception not raised")

    return marker, cause, context, raise_marker


def _sync_transport_raise(recorder):
    client = _sync_client([], [_connect_error], [_token(_TOKEN_1)], recorder)
    try:
        client.request(_OP)
    except TransportError as exc:
        return exc
    finally:
        client.close()
    raise AssertionError("transport failure did not propagate")


async def _async_transport_raise(recorder):
    client = _async_client([], [_connect_error], [_token(_TOKEN_1)], recorder)
    try:
        await client.request(_OP)
    except TransportError as exc:
        return exc
    finally:
        await client.aclose()
    raise AssertionError("transport failure did not propagate")


def _sync_plain_exception_drive(entrypoint: str, recorder):
    # One driver for both entrypoints: the same chained marker is raised by
    # the scripted provider or by the MockTransport handler. A plain
    # RuntimeError is not an httpx.HTTPError, so the SDK must let the very
    # same object pass through untouched instead of converting it.
    log: list = []
    marker, cause, context, raise_marker = _chained_failure()
    provider_script = [raise_marker] if entrypoint == "provider" else [_token(_TOKEN_1)]
    responses = [] if entrypoint == "provider" else [raise_marker]
    client = _sync_client(log, responses, provider_script, recorder)
    try:
        client.request(_OP)
    except RuntimeError as exc:
        return log, marker, cause, context, exc
    finally:
        client.close()
    raise AssertionError("marker did not propagate")


async def _async_plain_exception_drive(entrypoint: str, recorder):
    log: list = []
    marker, cause, context, raise_marker = _chained_failure()
    provider_script = [raise_marker] if entrypoint == "provider" else [_token(_TOKEN_1)]
    responses = [] if entrypoint == "provider" else [raise_marker]
    client = _async_client(log, responses, provider_script, recorder)
    try:
        await client.request(_OP)
    except RuntimeError as exc:
        return log, marker, cause, context, exc
    finally:
        await client.aclose()
    raise AssertionError("marker did not propagate")


def _assert_marker_round_trip(exc, marker, cause, context) -> None:
    # Identity plus the pre-existing chain: the cause and context objects
    # and the suppress_context flag all arrive unchanged at the catch site.
    assert exc is marker
    assert exc.__cause__ is cause
    assert exc.__context__ is context
    assert exc.__suppress_context__ is True


def _assert_same_chain(exc_off: BaseException, exc_on: BaseException) -> None:
    # Both runs go through the same driver, so the full name chain must be
    # identical: any diagnostic re-raise frame would surface here. The
    # cause/context links must match structurally as well, so the recorder
    # cannot silently rewrite or drop them.
    assert _tb_names(exc_on) == _tb_names(exc_off)
    assert type(exc_on.__cause__) is type(exc_off.__cause__)
    assert type(exc_on.__context__) is type(exc_off.__context__)
    assert exc_on.__suppress_context__ == exc_off.__suppress_context__


@pytest.mark.parametrize("entrypoint", ["provider", "transport"])
def test_sync_plain_exception_identity_and_chain_preserved(entrypoint) -> None:
    log_off, marker_off, cause_off, ctx_off, exc_off = _sync_plain_exception_drive(entrypoint, None)
    recorder = DiagnosticRecorder()
    log_on, marker_on, cause_on, ctx_on, exc_on = _sync_plain_exception_drive(entrypoint, recorder)
    _assert_marker_round_trip(exc_off, marker_off, cause_off, ctx_off)
    _assert_marker_round_trip(exc_on, marker_on, cause_on, ctx_on)
    _assert_same_chain(exc_off, exc_on)
    # The marker is raised exactly once: close() must not replay the provider
    # or the transport handler during cleanup.
    assert log_on == log_off
    if entrypoint == "provider":
        assert log_on == [("get_token",)]
        assert "get_token" in _tb_names(exc_on)
    else:
        assert [item[0] for item in log_on] == ["get_token", "factory", "http"]
        assert "handler" in _tb_names(exc_on)
    terminal = [
        event
        for event in recorder.report()["events"]
        if event["kind"] == "call"
        and event["entrypoint"] == "client.request"
        and event["outcome"] != "started"
    ]
    assert len(terminal) == 1
    assert terminal[0]["outcome"] == "failed"
    assert terminal[0]["error_family"] == "unknown"


@pytest.mark.parametrize("entrypoint", ["provider", "transport"])
def test_async_plain_exception_identity_and_chain_preserved(entrypoint) -> None:
    log_off, marker_off, cause_off, ctx_off, exc_off = asyncio.run(
        _async_plain_exception_drive(entrypoint, None)
    )
    recorder = DiagnosticRecorder()
    log_on, marker_on, cause_on, ctx_on, exc_on = asyncio.run(
        _async_plain_exception_drive(entrypoint, recorder)
    )
    _assert_marker_round_trip(exc_off, marker_off, cause_off, ctx_off)
    _assert_marker_round_trip(exc_on, marker_on, cause_on, ctx_on)
    _assert_same_chain(exc_off, exc_on)
    assert log_on == log_off
    if entrypoint == "provider":
        assert log_on == [("get_token",)]
        assert "get_token" in _tb_names(exc_on)
    else:
        assert [item[0] for item in log_on] == ["get_token", "factory", "http"]
        assert "handler" in _tb_names(exc_on)
    terminal = [
        event
        for event in recorder.report()["events"]
        if event["kind"] == "call"
        and event["entrypoint"] == "client.request"
        and event["outcome"] != "started"
    ]
    assert len(terminal) == 1
    assert terminal[0]["outcome"] == "failed"
    assert terminal[0]["error_family"] == "unknown"


def test_sync_transport_exception_traceback_unchanged() -> None:
    exc_off = _sync_transport_raise(None)
    exc_on = _sync_transport_raise(DiagnosticRecorder())
    assert type(exc_on) is TransportError and type(exc_off) is TransportError
    assert exc_on.reason == exc_off.reason == "network"
    _assert_same_chain(exc_off, exc_on)


def test_async_transport_exception_traceback_unchanged() -> None:
    exc_off = asyncio.run(_async_transport_raise(None))
    exc_on = asyncio.run(_async_transport_raise(DiagnosticRecorder()))
    assert type(exc_on) is TransportError and type(exc_off) is TransportError
    assert exc_on.reason == exc_off.reason == "network"
    _assert_same_chain(exc_off, exc_on)


# -- H06: async cancellation inside the attempt and aclose waiter cancel -----


def test_async_attempt_cancellation_preserves_identity_and_outcome() -> None:
    asyncio.run(_attempt_cancellation())


async def _attempt_cancellation() -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    transport = _BlockingRequestTransport()

    def factory(origin: str):
        log.append(("factory", origin))
        return transport

    client = AsyncClient(
        region=_REGION,
        token_provider=_AsyncProvider(log, [_token(_TOKEN_1)]),
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    captured: dict[str, BaseException] = {}

    async def run() -> None:
        try:
            await client.request(_OP)
        except asyncio.CancelledError as exc:
            captured["exc"] = exc
            raise

    task = asyncio.create_task(run())
    events: list = []
    try:
        await asyncio.wait_for(transport.started.wait(), timeout=_TIMEOUT)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=_TIMEOUT)
        assert task.cancelled()
        # The CancelledError object raised inside the transport travelled all
        # the way out unchanged: no wrapping, no substitution, no retry.
        assert captured["exc"] is transport.cancel_exc
        assert transport.request_count == 1
        assert log == [("get_token",), ("factory", _ORIGIN)]
        # Business snapshot before teardown: the idempotent cleanup below
        # records close/cleanup events of its own and must not pollute the
        # attempt/call assertions after this block.
        events = list(recorder.report()["events"])
    finally:
        # Bounded teardown: if a mid-test failure left the request task
        # running, cancel it and join it within a timeout, then close the
        # client through the public API, still bounded. asyncio.wait only
        # drains the task's expected cancellation; a real hang or other
        # failure still surfaces instead of hanging forever.
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=_TIMEOUT)
        await asyncio.wait_for(client.aclose(), timeout=_TIMEOUT)
    attempts = _kind(events, "attempt")
    assert [e["outcome"] for e in attempts] == ["started", "cancelled"]
    assert attempts[1]["error_family"] == "interrupted"
    assert _kind(events, "call")[-1]["outcome"] == "cancelled"
    assert not _kind(events, "retry")


def test_async_aclose_waiter_cancel_keeps_cleanup_gated() -> None:
    asyncio.run(_aclose_waiter_cancel())


async def _aclose_waiter_cancel() -> None:
    log: list = []
    recorder = DiagnosticRecorder()
    transport = _GatedCloseTransport()

    def factory(origin: str):
        log.append(("factory", origin))
        return transport

    client = AsyncClient(
        region=_REGION,
        token_provider=_AsyncProvider(log, [_token(_TOKEN_1)]),
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    waiter: asyncio.Task | None = None
    events: list = []
    try:
        await asyncio.wait_for(client.request(_OP), timeout=_TIMEOUT)

        waiter = asyncio.create_task(client.aclose())
        # The real transport aclose is now executing and blocked on the gate.
        await asyncio.wait_for(transport.aclose_started.wait(), timeout=_TIMEOUT)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, timeout=_TIMEOUT)
        assert waiter.cancelled()

        # Before release the shielded cleanup cannot have completed the real close.
        assert transport.aclose_count == 1
        assert not transport.aclose_finished.is_set()
        mid = list(recorder.report()["events"])
        assert [e["outcome"] for e in _kind(mid, "cleanup")] == ["started"]
        assert [e["outcome"] for e in _kind(mid, "close") if e["outcome"] != "started"] == [
            "cancelled"
        ]

        transport.aclose_release.set()
        await asyncio.wait_for(transport.aclose_finished.wait(), timeout=_TIMEOUT)
        assert transport.aclose_count == 1
        # A second aclose joins the same cleanup task through the public API.
        await asyncio.wait_for(client.aclose(), timeout=_TIMEOUT)

        # Business snapshot taken before the finally-block teardown so the
        # extra idempotent aclose below cannot pollute these assertions.
        events = list(recorder.report()["events"])
    finally:
        # Bounded teardown: always release the close gate, cancel and drain a
        # waiter that is still running, then close through the public API
        # within a timeout. asyncio.wait only drains the waiter's expected
        # cancellation; other failures keep surfacing and nothing can hang
        # forever or be left running in the background.
        transport.aclose_release.set()
        if waiter is not None and not waiter.done():
            waiter.cancel()
            await asyncio.wait({waiter}, timeout=_TIMEOUT)
        await asyncio.wait_for(client.aclose(), timeout=_TIMEOUT)
    assert [e["outcome"] for e in _kind(events, "cleanup")] == [
        "started",
        "succeeded",
    ]
    close_terminal = [e["outcome"] for e in _kind(events, "close") if e["outcome"] != "started"]
    assert close_terminal == ["cancelled", "succeeded"]
