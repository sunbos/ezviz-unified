"""Contract tests for the public country/region directory (sync and async).

``Settings.list_areas`` / ``AsyncSettings.list_areas`` send exactly one
GET to ``/api/lapp/area/list`` with no query, no body and no token —
even when the client holds a refreshable token provider. All fixtures are
synthetic; no real country rows, counts, harness modules or docs data are
used. Covered here:

* wire shape: method/path/empty query/empty body/no accessToken or
  Authorization anywhere on the wire, exactly one request per call;
* token provider never called, ``read_only=True``, ``token_location="none"``;
* ``ApiResponse`` preserves extra ``data`` fields and an empty array
  verbatim and hardcodes no item count;
* domestic region, missing ``api_base_url`` and a closed client fail
  before any request through the existing error semantics;
* business ``10002``/other business errors, HTTP errors and malformed
  payloads are never retried;
* async cancellation propagates and ``aclose`` closes the client;
* diagnostic on/off changes neither return values nor exception
  semantics across success, pre-request validation, business and
  transport failures, and the recorded two-layer event tree keeps the
  facade ``call`` on ``other`` with the nested
  ``client.request``/``transport_setup``/``attempt`` scopes projected to
  ``settings.list_areas``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    ProtocolError,
    RegionConfig,
    Token,
    TransportError,
)
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.operations import Operation
from ezviz_unified.providers import AsyncTokenProvider, TokenProvider

API_BASE = "https://open.ezvizlife.com"
AREA_PATH = "/api/lapp/area/list"
WAIT_TIMEOUT = 5

AREA_ITEMS = [
    {
        "id": 90001,
        "name": "评测地区甲",
        "region": "TEST-REGION-1",
        "telephoneCode": 9001,
    },
    {
        "id": 90002,
        "name": "Test Area Beta",
        "region": "TEST-REGION-2",
        "telephoneCode": 9002,
        "extraField": "preserved-verbatim",
    },
]
OK_BODY = {"code": "200", "msg": "ok", "data": AREA_ITEMS}
EMPTY_BODY = {"code": "200", "msg": "ok", "data": []}
EXPIRED_BODY = {"code": "10002", "msg": "expired", "data": None}
BUSINESS_ERROR_BODY = {"code": "10017", "msg": "denied", "data": None}

Handler = Callable[[httpx.Request], httpx.Response]


def _region() -> RegionConfig:
    return RegionConfig.overseas(api_base_url=API_BASE)


def _ok(handler_list: list) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        handler_list.append(request)
        return httpx.Response(200, json=OK_BODY)

    return handler


def _forbidden(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError("no network request was expected")


def _sync_client(handler, **kwargs) -> Client:
    return Client(
        region=_region(),
        transport_factory=lambda origin: httpx.MockTransport(handler),
        **kwargs,
    )


def _async_client(handler, **kwargs) -> AsyncClient:
    return AsyncClient(
        region=_region(),
        transport_factory=lambda origin: httpx.MockTransport(handler),
        **kwargs,
    )


class _CountingSyncProvider(TokenProvider):
    """Refreshable provider double counting every provider interaction."""

    can_refresh = True

    def __init__(self) -> None:
        self.calls = 0
        self.invalidations = 0

    def get_token(self):
        self.calls += 1
        return Token(value="TEST_TOKEN", expires_at=2000, refresh_at=1900, api_base_url=API_BASE)

    def invalidate(self, expected_value=None):
        self.invalidations += 1


class _CountingAsyncProvider(AsyncTokenProvider):
    """Async refreshable provider double; ``invalidate`` stays sync."""

    can_refresh = True

    def __init__(self) -> None:
        self.calls = 0
        self.invalidations = 0

    async def get_token(self):
        self.calls += 1
        return Token(value="TEST_TOKEN", expires_at=2000, refresh_at=1900, api_base_url=API_BASE)

    def invalidate(self, expected_value=None):
        self.invalidations += 1


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_sends_exactly_one_bare_get(mode):
    captured: list[httpx.Request] = []
    seen: list[Operation] = []
    if mode == "sync":
        client = _sync_client(_ok(captured), anonymous=True)
        original = client.request

        def spy(operation, **kw):
            seen.append(operation)
            return original(operation, **kw)

        client.request = spy
        try:
            response = client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_ok(captured), anonymous=True)
            original = client.request

            async def spy(operation, **kw):
                seen.append(operation)
                return await original(operation, **kw)

            client.request = spy
            try:
                return await client.settings.list_areas()
            finally:
                await client.aclose()

        response = _run(run())
    assert response.http_status == 200
    assert response.code == "200"
    assert response.data == AREA_ITEMS
    assert len(seen) == 1
    operation = seen[0]
    assert operation.name == "settings.list_areas"
    assert operation.method == "GET"
    assert operation.path == AREA_PATH
    assert operation.read_only is True
    assert operation.token_location == "none"
    assert operation.regions == frozenset({"overseas"})
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == AREA_PATH
    assert request.url.query == b""
    assert request.content in (b"", "")
    header_names = {key.lower() for key in request.headers}
    assert "accesstoken" not in header_names
    assert "authorization" not in header_names
    assert "content-type" not in header_names


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_never_calls_refreshable_provider(mode):
    captured: list[httpx.Request] = []
    provider = _CountingSyncProvider() if mode == "sync" else _CountingAsyncProvider()
    if mode == "sync":
        client = _sync_client(_ok(captured), token_provider=provider)
        try:
            response = client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_ok(captured), token_provider=provider)
            try:
                return await client.settings.list_areas()
            finally:
                await client.aclose()

        response = _run(run())
    assert response.data == AREA_ITEMS
    assert provider.calls == 0
    assert provider.invalidations == 0
    assert len(captured) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_preserves_extra_fields_and_empty_array(mode):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=EMPTY_BODY)

    if mode == "sync":
        client = _sync_client(handler, anonymous=True)
        try:
            rich = client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(handler, anonymous=True)
            try:
                return await client.settings.list_areas()
            finally:
                await client.aclose()

        rich = _run(run())
    # Empty array is a legitimate directory payload and stays verbatim.
    assert rich.data == []
    assert rich.raw == EMPTY_BODY

    captured.clear()
    if mode == "sync":
        client = _sync_client(_ok(captured), anonymous=True)
        try:
            response = client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_ok(captured), anonymous=True)
            try:
                return await client.settings.list_areas()
            finally:
                await client.aclose()

        response = _run(run())
    # Extra item fields are preserved verbatim, never stripped or mapped.
    assert response.data == AREA_ITEMS
    assert response.data[1]["extraField"] == "preserved-verbatim"
    assert response.raw == OK_BODY


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_domestic_region_rejected_before_request(mode):
    captured: list[httpx.Request] = []
    provider = _CountingSyncProvider()
    client = Client(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_ok(captured)),
    )
    try:
        with pytest.raises(ValueError):
            client.settings.list_areas()
    finally:
        client.close()
    assert captured == []
    assert provider.calls == 0

    if mode == "async":

        async def run():
            aprovider = _CountingAsyncProvider()
            client = AsyncClient(
                region=RegionConfig.china(),
                token_provider=aprovider,
                transport_factory=lambda origin: httpx.MockTransport(_ok(captured)),
            )
            try:
                with pytest.raises(ValueError):
                    await client.settings.list_areas()
            finally:
                await client.aclose()
            return aprovider

        aprovider = _run(run())
        assert aprovider.calls == 0
    assert captured == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_missing_api_base_url_fails_before_provider(mode):
    captured: list[httpx.Request] = []
    provider = _CountingSyncProvider() if mode == "sync" else _CountingAsyncProvider()
    if mode == "sync":
        client = Client(
            region=RegionConfig.overseas(),
            token_provider=provider,
            transport_factory=lambda origin: httpx.MockTransport(_ok(captured)),
        )
        try:
            with pytest.raises(ValueError):
                client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = AsyncClient(
                region=RegionConfig.overseas(),
                token_provider=provider,
                transport_factory=lambda origin: httpx.MockTransport(_ok(captured)),
            )
            try:
                with pytest.raises(ValueError):
                    await client.settings.list_areas()
            finally:
                await client.aclose()

        _run(run())
    assert captured == []
    assert provider.calls == 0
    assert provider.invalidations == 0


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_closed_client_fails_before_request(mode):
    captured: list[httpx.Request] = []
    if mode == "sync":
        client = _sync_client(_ok(captured), anonymous=True)
        client.close()
        with pytest.raises(TransportError) as excinfo:
            client.settings.list_areas()
        error = excinfo.value
    else:

        async def run():
            client = _async_client(_ok(captured), anonymous=True)
            await client.aclose()
            with pytest.raises(TransportError) as excinfo:
                await client.settings.list_areas()
            return excinfo.value

        error = _run(run())
    assert getattr(error, "reason", None) == "closed"
    assert captured == []


@pytest.mark.parametrize("body", [EXPIRED_BODY, BUSINESS_ERROR_BODY])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_business_errors_not_retried(mode, body):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=body)

    provider = _CountingSyncProvider() if mode == "sync" else _CountingAsyncProvider()
    expected = AuthenticationError if body["code"] == "10002" else ApiError
    if mode == "sync":
        client = _sync_client(handler, token_provider=provider)
        try:
            with pytest.raises(expected) as excinfo:
                client.settings.list_areas()
            assert excinfo.value.code == body["code"]
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(handler, token_provider=provider)
            try:
                with pytest.raises(expected) as excinfo:
                    await client.settings.list_areas()
                return excinfo.value.code
            finally:
                await client.aclose()

        assert _run(run()) == body["code"]
    assert len(captured) == 1
    assert provider.calls == 0
    assert provider.invalidations == 0


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_http_error_not_retried(mode):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(500, json=OK_BODY)

    if mode == "sync":
        client = _sync_client(handler, anonymous=True)
        try:
            with pytest.raises(ApiError) as excinfo:
                client.settings.list_areas()
            assert excinfo.value.http_status == 500
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(handler, anonymous=True)
            try:
                with pytest.raises(ApiError) as excinfo:
                    await client.settings.list_areas()
                return excinfo.value.http_status
            finally:
                await client.aclose()

        assert _run(run()) == 500
    assert len(captured) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_malformed_payload_not_retried(mode):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=b"not-json")

    if mode == "sync":
        client = _sync_client(handler, anonymous=True)
        try:
            with pytest.raises(ProtocolError):
                client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(handler, anonymous=True)
            try:
                with pytest.raises(ProtocolError):
                    await client.settings.list_areas()
            finally:
                await client.aclose()

        _run(run())
    assert len(captured) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_transport_error_not_retried(mode):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ConnectError("synthetic offline failure", request=request)

    if mode == "sync":
        client = _sync_client(handler, anonymous=True)
        try:
            with pytest.raises(TransportError):
                client.settings.list_areas()
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(handler, anonymous=True)
            try:
                with pytest.raises(TransportError):
                    await client.settings.list_areas()
            finally:
                await client.aclose()

        _run(run())
    assert len(captured) == 1


@pytest.mark.parametrize("recording", [False, True])
def test_list_areas_async_cancellation_propagates_and_aclose_completes(recording):
    """Cancellation reaches the caller with or without a recorder, and the
    client is really closed after ``aclose`` regardless of recording."""
    captured: list[httpx.Request] = []
    entered = asyncio.Event()
    release = asyncio.Event()
    recorder = DiagnosticRecorder() if recording else None

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=WAIT_TIMEOUT)
        return httpx.Response(200, json=OK_BODY)

    async def run():
        client = _async_client(handler, anonymous=True, diagnostic_recorder=recorder)
        try:
            task = asyncio.create_task(client.settings.list_areas())
            await asyncio.wait_for(entered.wait(), timeout=WAIT_TIMEOUT)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        await asyncio.wait_for(client.aclose(), timeout=WAIT_TIMEOUT)
        return client

    client = _run(run())
    assert len(captured) == 1
    assert client.is_closed
    if recorder is not None:
        events = recorder.report()["events"]
        attempt = [
            e for e in events if e["kind"] == "attempt" and e["entrypoint"] == "client.request"
        ]
        # The in-flight attempt scope always records started then cancelled.
        assert {e["outcome"] for e in attempt} == {"started", "cancelled"}
        assert all(e["mode"] == "async" for e in events)
        # The enclosing facade and client.request calls also end cancelled.
        for entrypoint in ("settings.list_areas", "client.request"):
            calls = [e for e in events if e["kind"] == "call" and e["entrypoint"] == entrypoint]
            assert {e["outcome"] for e in calls} == {
                "started",
                "cancelled",
            }, entrypoint


def _events_by(recorder, *, kind=None, entrypoint=None):
    return [
        e
        for e in recorder.report()["events"]
        if (kind is None or e["kind"] == kind)
        and (entrypoint is None or e["entrypoint"] == entrypoint)
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_diagnostic_on_off_parity(mode):
    """Recording on/off must not change the return value, and the recorded
    event tree keeps the facade ``call`` on ``other`` while the nested
    ``client.request``/``transport_setup``/``attempt`` scopes project to
    ``settings.list_areas`` — the two layers are asserted separately, never
    collapsed into one entrypoint filter."""
    for recorder in (None, DiagnosticRecorder()):
        captured: list[httpx.Request] = []
        if mode == "sync":
            client = _sync_client(_ok(captured), anonymous=True, diagnostic_recorder=recorder)
            try:
                response = client.settings.list_areas()
            finally:
                client.close()
        else:

            async def run():
                client = _async_client(_ok(captured), anonymous=True, diagnostic_recorder=recorder)
                try:
                    return await client.settings.list_areas()
                finally:
                    await client.aclose()

            response = _run(run())
        assert response.data == AREA_ITEMS
        assert response.http_status == 200
        assert len(captured) == 1
        if recorder is None:
            continue
        events = recorder.report()["events"]
        started = {e["span_id"] for e in events if e["outcome"] == "started"}

        facade = [
            e for e in events if e["kind"] == "call" and e["entrypoint"] == "settings.list_areas"
        ]
        # The facade never learns the Operation; it projects to "other".
        assert {e["outcome"] for e in facade} == {"started", "succeeded"}
        assert all(e["operation"] == "other" for e in facade)
        assert all(e["mode"] == mode for e in facade)
        assert all(e["region"] == "overseas" for e in facade)
        assert len({e["span_id"] for e in facade}) == 1
        facade_span = facade[0]["span_id"]
        assert facade[0]["parent_span_id"] is None

        request_call = [
            e for e in events if e["kind"] == "call" and e["entrypoint"] == "client.request"
        ]
        setup = _events_by(recorder, kind="transport_setup", entrypoint="client.request")
        attempt = _events_by(recorder, kind="attempt", entrypoint="client.request")
        # Inner scopes carry the real operation name on every event.
        for group in (request_call, setup, attempt):
            assert group, "missing nested client.request scopes"
            assert all(e["operation"] == "settings.list_areas" for e in group)
            assert all(e["mode"] == mode for e in group)
            assert all(e["region"] == "overseas" for e in group)
        assert {e["outcome"] for e in request_call} == {"started", "succeeded"}
        assert {e["outcome"] for e in setup} == {"started", "succeeded"}
        assert {e["outcome"] for e in attempt} == {"started", "succeeded"}
        assert {e["attempt"] for e in setup} == {0}
        assert {e["attempt"] for e in attempt} == {1}
        # Parent links: request scopes hang under the facade call span.
        assert all(e["parent_span_id"] == facade_span for e in request_call)
        request_span = request_call[0]["span_id"]
        assert all(e["parent_span_id"] == request_span for e in setup)
        assert all(e["parent_span_id"] == request_span for e in attempt)
        # Every scope that started reaches exactly one terminal event on the
        # same span in this single-call path.
        terminal_spans = {
            e["span_id"] for e in events if e["outcome"] in {"succeeded", "failed", "cancelled"}
        }
        assert set(started) == terminal_spans
        # A public operation never opens an authentication or retry scope.
        assert _events_by(recorder, kind="authentication") == []
        assert _events_by(recorder, kind="retry") == []
        assert API_BASE not in json.dumps(recorder.report())


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_diagnostic_failure_events(mode):
    """Pre-request validation failures record a failed facade call and never
    reach ``client.request``; provider stays untouched."""
    recorder = DiagnosticRecorder()
    provider = _CountingSyncProvider()
    client = Client(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_forbidden),
        diagnostic_recorder=recorder,
    )
    try:
        with pytest.raises(ValueError):
            client.settings.list_areas()
    finally:
        client.close()
    events = recorder.report()["events"]
    facade = [e for e in events if e["kind"] == "call" and e["entrypoint"] == "settings.list_areas"]
    assert {e["outcome"] for e in facade} == {"started", "failed"}
    assert all(e["mode"] == "sync" for e in facade)
    assert all(e["region"] == "domestic" for e in facade)
    assert all(e["operation"] == "other" for e in facade)
    # The region guard fires before any client.request scope exists.
    assert _events_by(recorder, entrypoint="client.request") == []
    assert provider.calls == 0

    if mode == "async":
        recorder2 = DiagnosticRecorder()

        async def run():
            aprovider = _CountingAsyncProvider()
            client = AsyncClient(
                region=RegionConfig.china(),
                token_provider=aprovider,
                transport_factory=lambda origin: httpx.MockTransport(_forbidden),
                diagnostic_recorder=recorder2,
            )
            try:
                with pytest.raises(ValueError):
                    await client.settings.list_areas()
            finally:
                await client.aclose()
            return aprovider

        aprovider = _run(run())
        facade = [e for e in _events_by(recorder2, kind="call", entrypoint="settings.list_areas")]
        assert {e["outcome"] for e in facade} == {"started", "failed"}
        assert all(e["mode"] == "async" for e in facade)
        assert all(e["region"] == "domestic" for e in facade)
        assert _events_by(recorder2, entrypoint="client.request") == []
        assert aprovider.calls == 0


# Recorder-off/Recorder-on parity across the real failure surface. Each row
# drives the real ``settings.list_areas`` entry against a MockTransport and
# checks: identical request count, provider untouched, identical exception
# type/public attributes, and the same recorded terminal outcomes when a
# recorder is attached. ``kind`` selects the failure the row exercises.


def _business_denied(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=BUSINESS_ERROR_BODY)


def _expired(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=EXPIRED_BODY)


def _http_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json=OK_BODY)


def _malformed(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"not-json")


def _offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("synthetic offline failure", request=request)


@pytest.mark.parametrize("recording", [False, True])
@pytest.mark.parametrize(
    "handler, expected_exc, expected_attrs",
    [
        (_business_denied, ApiError, {"code": "10017"}),
        (
            _expired,
            AuthenticationError,
            {"code": "10002", "invalid_token": True},
        ),
        (_http_500, ApiError, {"http_status": 500}),
        (_malformed, ProtocolError, {}),
        (_offline, TransportError, {"reason": "network"}),
    ],
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_areas_failure_semantics_parity(
    mode, handler, expected_exc, expected_attrs, recording
):
    """Every failure row surfaces the same exception type with the same
    public attributes whether or not a recorder is attached, exactly one
    request is sent, and a refreshable provider is never touched."""
    captured: list[httpx.Request] = []

    def tracked(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    recorder = DiagnosticRecorder() if recording else None
    provider = _CountingSyncProvider() if mode == "sync" else _CountingAsyncProvider()
    if mode == "sync":
        client = _sync_client(tracked, token_provider=provider, diagnostic_recorder=recorder)
        try:
            with pytest.raises(expected_exc) as excinfo:
                client.settings.list_areas()
            error = excinfo.value
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(tracked, token_provider=provider, diagnostic_recorder=recorder)
            try:
                with pytest.raises(expected_exc) as excinfo:
                    await client.settings.list_areas()
                return excinfo.value
            finally:
                await client.aclose()

        error = _run(run())
    assert len(captured) == 1
    assert provider.calls == 0
    assert provider.invalidations == 0
    # Public attributes are stable and identical across recorder on/off.
    assert error.operation == "settings.list_areas"
    for name, value in expected_attrs.items():
        assert getattr(error, name) == value

    if recorder is None:
        return
    events = recorder.report()["events"]
    facade = [e for e in events if e["kind"] == "call" and e["entrypoint"] == "settings.list_areas"]
    assert {e["outcome"] for e in facade} == {"started", "failed"}
    assert all(e["operation"] == "other" for e in facade)
    assert all(e["mode"] == mode for e in facade)
    assert all(e["region"] == "overseas" for e in facade)
    facade_span = facade[0]["span_id"]

    request_call = [
        e for e in events if e["kind"] == "call" and e["entrypoint"] == "client.request"
    ]
    attempt = _events_by(recorder, kind="attempt", entrypoint="client.request")
    # The wire attempt ran once and ended in failure; request call fails too.
    assert {e["outcome"] for e in request_call} == {"started", "failed"}
    assert {e["outcome"] for e in attempt} == {"started", "failed"}
    assert {e["attempt"] for e in attempt} == {1}
    assert all(e["operation"] == "settings.list_areas" for e in (*request_call, *attempt))
    assert all(e["parent_span_id"] == facade_span for e in request_call)
    assert all(e["parent_span_id"] == request_call[0]["span_id"] for e in attempt)
    # No authentication scope ran and no retry was ever scheduled for a
    # public operation.
    assert _events_by(recorder, kind="authentication") == []
    assert _events_by(recorder, kind="retry") == []
    assert API_BASE not in json.dumps(recorder.report())


def test_list_areas_return_and_error_identity_not_wrapped():
    """The facade forwards the ``client.request`` result and its failures
    verbatim — the same ``ApiResponse`` object comes back, and the same
    raised exception object reaches the caller (never re-wrapped)."""
    captured: list[httpx.Request] = []
    seen: list = []
    client = _sync_client(_ok(captured), anonymous=True)
    original = client.request

    def spy(operation, **kw):
        result = original(operation, **kw)
        seen.append(result)
        return result

    client.request = spy
    try:
        response = client.settings.list_areas()
    finally:
        client.close()
    assert len(seen) == 1
    assert response is seen[0]

    sentinel = ApiError("synthetic", code="10017", operation="settings.list_areas")

    def boom(operation, **kw):
        raise sentinel

    client = _sync_client(_forbidden, anonymous=True)
    client.request = boom
    try:
        with pytest.raises(ApiError) as excinfo:
            client.settings.list_areas()
    finally:
        client.close()
    assert excinfo.value is sentinel
