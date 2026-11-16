"""Acceptance checks for the frozen local diagnostic report (A01-A09).

Only the interfaces frozen in docs/plans/2026-11-16-local-diagnostics-v1.md
are exercised. Everything stays offline through ``httpx.MockTransport`` and
scripted token providers; sentinel payloads must never reach a report.
"""

from __future__ import annotations

import asyncio
import json
import threading

import httpx
import pytest

from ezviz_unified import ApiResponse, AsyncClient, Client, RegionConfig
from ezviz_unified._diagnostic_registry import ENTRYPOINTS, OPERATIONS
from ezviz_unified.auth_models import Token
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.errors import (
    ApiError,
    AuthenticationError,
    DeviceError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)
from ezviz_unified.inspection import inspect_device, inspect_device_async
from ezviz_unified.operations import Operation

SENTINEL = "DG003-NO-LEAK-5e1f"
ORIGIN = "https://open.ys7.com"
MODES = ("sync", "async")
_ALLOWED_KEYS = frozenset(
    {
        "sequence",
        "span_id",
        "parent_span_id",
        "kind",
        "entrypoint",
        "operation",
        "mode",
        "region",
        "outcome",
        "error_family",
        "reason",
        "code_category",
        "http_status_category",
        "attempt",
    }
)
_OUTCOMES = frozenset({"started", "succeeded", "failed", "cancelled", "interrupted", "scheduled"})
_FAMILIES = frozenset(
    {
        "none",
        "validation",
        "authentication",
        "permission",
        "device",
        "protocol",
        "transport",
        "application",
        "unknown",
        "interrupted",
        "api",
    }
)
_KINDS = frozenset(
    {
        "call",
        "authentication",
        "attempt",
        "retry",
        "close",
        "cleanup",
        "inspection",
        "application",
        "transport_setup",
    }
)
_REASONS = frozenset({"none", "timeout", "network", "closed", "other"})
_CODECATS = frozenset({"none", "invalid_token", "permission_denied", "other"})
_HTTPCATS = frozenset(
    {
        "none",
        "informational",
        "success",
        "redirect",
        "client_error",
        "server_error",
        "other",
    }
)


class _Boom:
    """Raises if the recorder ever stringifies it."""

    def __str__(self):  # pragma: no cover - must never run
        raise AssertionError("error attributes must not be stringified")

    def __repr__(self):  # pragma: no cover - must never run
        raise AssertionError("error attributes must not be stringified")


def _refuse(request):  # pragma: no cover - safety net
    raise AssertionError(f"unexpected network use: {request.method} {request.url}")


def _token(value="dg-token-1"):
    return Token(value=value, expires_at=2000.0, refresh_at=1500.0, api_base_url=ORIGIN)


class _SyncProvider:
    """Scripted provider satisfying the TokenProvider protocol."""

    def __init__(self, *effects, refreshable=True):
        self._effects = list(effects)
        self._refreshable = refreshable
        self.calls = 0
        self.invalidated = []

    @property
    def can_refresh(self):
        return self._refreshable

    def get_token(self):
        self.calls += 1
        effect = self._effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    def invalidate(self, expected_value=None):
        self.invalidated.append(expected_value)


class _AsyncProvider(_SyncProvider):
    async def get_token(self):
        return _SyncProvider.get_token(self)


def _client(mode, recorder=None, handler=None, *, provider=None, region=None, anonymous=False):
    seen = []

    def _handler(request):
        seen.append(request)
        if handler is None:
            return httpx.Response(200, json={"code": "200", "data": {}})
        return handler(request)

    kwargs = {
        "region": region or RegionConfig.china(),
        "clock": lambda: 1000.0,
        "transport_factory": lambda origin: httpx.MockTransport(_handler),
    }
    if recorder is not None:
        kwargs["diagnostic_recorder"] = recorder
    if anonymous:
        kwargs["anonymous"] = True
    elif provider is not None:
        kwargs["token_provider"] = provider
    elif mode == "async":
        kwargs["token_provider"] = _AsyncProvider(_token())
    else:
        kwargs["token_provider"] = _SyncProvider(_token())
    cls = Client if mode == "sync" else AsyncClient
    return cls(**kwargs), seen


async def _drive(mode, client, operation, **kwargs):
    if mode == "sync":
        return client.request(operation, **kwargs)
    return await client.request(operation, **kwargs)


async def _close(mode, client):
    if client.is_closed:
        return
    if mode == "sync":
        client.close()
    else:
        await client.aclose()


def _field(event, name, default="none"):
    return event.get(name, default)


def _events(recorder):
    return recorder.report()["events"]


def _assert_well_formed(events):
    seqs = [e["sequence"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    spans = {e["span_id"] for e in events}
    for e in events:
        assert set(e) <= _ALLOWED_KEYS
        assert isinstance(e["sequence"], int) and isinstance(e["span_id"], int)
        parent = e["parent_span_id"]
        assert parent is None or (isinstance(parent, int) and parent in spans)
        assert e["kind"] in _KINDS and e["outcome"] in _OUTCOMES
        assert e["mode"] in {"sync", "async", "application"}
        assert e["region"] in {"domestic", "overseas", "unknown"}
        assert e.get("attempt", 0) in (0, 1, 2)
        assert e["entrypoint"] in ENTRYPOINTS or e["entrypoint"] == "other"
        assert e["operation"] in OPERATIONS or e["operation"] == "other"
        assert _field(e, "error_family") in _FAMILIES
        assert _field(e, "reason") in _REASONS
        assert _field(e, "code_category") in _CODECATS
        assert _field(e, "http_status_category") in _HTTPCATS


def _phase_error(mode, marker, name="devices.list"):
    """Run a request whose provider raises *marker*; return the recorder."""

    async def scenario():
        recorder = DiagnosticRecorder()
        provider_cls = _SyncProvider if mode == "sync" else _AsyncProvider
        client, _ = _client(mode, recorder, provider=provider_cls(marker))
        try:
            with pytest.raises(Exception):
                await _drive(mode, client, Operation(name, "GET", "/dg"))
        finally:
            await _close(mode, client)
        return recorder

    return asyncio.run(scenario())


# A02 - capacity validation, report shape, detached snapshots


@pytest.mark.parametrize("bad", [0, -1, 4097, True, False, "8", 1.5, None, _Boom()])
def test_recorder_capacity_validation(bad):
    with pytest.raises((TypeError, ValueError)):
        DiagnosticRecorder(max_events=bad)


@pytest.mark.parametrize("size", [1, 256, 4096])
def test_recorder_report_shape(size):
    recorder = DiagnosticRecorder(max_events=size)
    report = recorder.report()
    assert set(report) == {
        "schema_version",
        "scope",
        "max_events",
        "dropped_events",
        "recording_errors",
        "events",
    }
    assert report["schema_version"] == 1
    assert report["scope"] == "local_sdk_diagnostics"
    assert report["max_events"] == size
    assert report["dropped_events"] == 0
    assert report["recording_errors"] == 0
    assert report["events"] == []
    payload = recorder.to_json()
    assert isinstance(payload, str)
    assert json.loads(payload) == report


def test_snapshot_detached_and_truncation():
    recorder = DiagnosticRecorder()
    with recorder.capture():
        pass
    first = recorder.report()
    first["events"].append({"corrupt": True})
    first["events"][0]["outcome"] = "corrupt"
    first["dropped_events"] = 99
    second = recorder.report()
    assert second["dropped_events"] == 0
    assert [e["outcome"] for e in second["events"]] == ["started", "succeeded"]
    json.dumps(second)  # stays JSON compatible

    small = DiagnosticRecorder(max_events=4)
    for _ in range(5):
        with small.capture():
            pass
    report = small.report()
    assert report["dropped_events"] == 6
    assert len(report["events"]) == 4
    seqs = [e["sequence"] for e in report["events"]]
    assert seqs == sorted(seqs)


@pytest.mark.parametrize("mode", MODES)
def test_client_rejects_foreign_recorder(mode):
    cls = Client if mode == "sync" else AsyncClient

    async def _make():
        cls("dg-key", "dg-secret", region=RegionConfig.china(), diagnostic_recorder=object())

    with pytest.raises((TypeError, ValueError)):
        asyncio.run(_make())


# A01 - default off


@pytest.mark.parametrize("mode", MODES)
def test_default_disabled(mode):
    async def scenario():
        recorder = DiagnosticRecorder()  # unrelated recorder must stay empty
        client, seen = _client(mode)
        try:
            resp = await _drive(mode, client, Operation("devices.list", "GET", "/dg"))
            assert isinstance(resp, ApiResponse)
            assert len(seen) == 1
            assert recorder.report()["events"] == []
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


# A04/A06 - scope phases, operation projection, identical wire behaviour


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "name,projected", [("devices.list", "devices.list"), ("dg.missing", "other")]
)
def test_request_phases_and_operation_projection(mode, name, projected):
    async def scenario():
        recorder = DiagnosticRecorder()
        client, seen = _client(mode, recorder)
        try:
            resp = await _drive(mode, client, Operation(name, "GET", "/dg"))
            assert isinstance(resp, ApiResponse)
            events = _events(recorder)
            _assert_well_formed(events)
            call = [
                e for e in events if e["kind"] == "call" and e["entrypoint"] == "client.request"
            ]
            assert {e["outcome"] for e in call} == {"started", "succeeded"}
            assert all(e["operation"] == projected for e in call)
            assert all(e["mode"] == mode and e["region"] == "domestic" for e in call)
            root = next(e for e in call if e["outcome"] == "started")
            assert root["parent_span_id"] is None
            for kind in ("authentication", "attempt"):
                kids = [e for e in events if e["kind"] == kind]
                assert {e["outcome"] for e in kids} == {"started", "succeeded"}
                started = [e for e in kids if e["outcome"] == "started"]
                assert all(e["parent_span_id"] is not None for e in started)
            auth_seq = min(e["sequence"] for e in events if e["kind"] == "authentication")
            att_seq = min(e["sequence"] for e in events if e["kind"] == "attempt")
            assert auth_seq < att_seq
            assert max(e["attempt"] for e in events if e["kind"] == "attempt") == 1
            assert len(seen) == 1
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
def test_retry_scheduled_then_second_attempt(mode):
    async def scenario():
        recorder = DiagnosticRecorder()
        state = {"calls": 0}

        def handler(request):
            state["calls"] += 1
            code = "10002" if state["calls"] == 1 else "200"
            return httpx.Response(200, json={"code": code, "data": {}})

        provider_cls = _SyncProvider if mode == "sync" else _AsyncProvider
        provider = provider_cls(_token("tk-1"), _token("tk-2"))
        client, seen = _client(mode, recorder, handler, provider=provider)
        try:
            op = Operation("devices.list", "GET", "/dg", read_only=True)
            await _drive(mode, client, op)
            assert len(seen) == 2 and provider.calls == 2
            assert provider.invalidated == ["tk-1"]
            events = _events(recorder)
            _assert_well_formed(events)
            retries = [e for e in events if e["kind"] == "retry"]
            assert len(retries) == 1 and retries[0]["outcome"] == "scheduled"
            first = [e for e in events if e["kind"] == "attempt" and e["attempt"] == 1]
            second = [e for e in events if e["kind"] == "attempt" and e["attempt"] == 2]
            assert {e["outcome"] for e in first} == {"started", "failed"}
            assert {e["outcome"] for e in second} == {"started", "succeeded"}
            first_failed = [e for e in first if e["outcome"] == "failed"]
            assert len(first_failed) == 1
            assert _field(first_failed[0], "error_family") == "authentication"
            assert _field(first_failed[0], "code_category") == "invalid_token"
            assert max(e["sequence"] for e in first) < retries[0]["sequence"]
            assert retries[0]["sequence"] < min(e["sequence"] for e in second)
            auth_spans = {e["span_id"] for e in events if e["kind"] == "authentication"}
            assert len(auth_spans) == 2
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("read_only,refreshable", [(False, True), (True, False)])
def test_no_retry_without_permission(mode, read_only, refreshable):
    async def scenario():
        recorder = DiagnosticRecorder()

        def handler(request):
            return httpx.Response(200, json={"code": "10002", "data": {}})

        provider_cls = _SyncProvider if mode == "sync" else _AsyncProvider
        provider = provider_cls(_token("tk-1"), _token("tk-2"), refreshable=refreshable)
        client, seen = _client(mode, recorder, handler, provider=provider)
        try:
            op = Operation("devices.list", "GET", "/dg", read_only=read_only)
            with pytest.raises(AuthenticationError):
                await _drive(mode, client, op)
            assert len(seen) == 1 and provider.calls == 1
            assert provider.invalidated == ["tk-1"]
            events = _events(recorder)
            assert not [e for e in events if e["kind"] == "retry"]
            assert not [e for e in events if e["kind"] == "attempt" and e["attempt"] == 2]
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


# A04/A05 - validation inside scopes, closed rejection


@pytest.mark.parametrize("mode", MODES)
def test_validation_and_closed_phases(mode):
    async def scenario():
        recorder = DiagnosticRecorder()
        client, seen = _client(mode, recorder)
        try:
            with pytest.raises(ValueError):
                if mode == "sync":
                    client.devices.get("   ")
                else:
                    await client.devices.get("   ")
            for kwargs in (
                {"params": {"accessToken": SENTINEL}},
                {"data": {}, "json": {}},
                {"headers": {"Host": "evil.example"}},
            ):
                with pytest.raises(ValueError):
                    await _drive(mode, client, Operation("devices.list", "GET", "/dg"), **kwargs)
            events = _events(recorder)
            facade = [e for e in events if e["entrypoint"] == "devices.get"]
            assert {e["outcome"] for e in facade} == {"started", "failed"}
            req = [e for e in events if e["entrypoint"] == "client.request"]
            assert len([e for e in req if e["outcome"] == "started"]) == 3
            failed = [e for e in facade + req if e["outcome"] == "failed"]
            assert all(_field(e, "error_family") == "validation" for e in failed)
            assert not [e for e in events if e["kind"] in ("authentication", "attempt")]
            assert not seen
            await _close(mode, client)
            with pytest.raises(TransportError):
                await _drive(mode, client, Operation("devices.list", "GET", "/dg"))
            closed = [
                e
                for e in _events(recorder)
                if e["entrypoint"] == "client.request"
                and e["outcome"] == "failed"
                and _field(e, "reason") == "closed"
            ]
            assert closed
            assert all(_field(e, "error_family") == "transport" for e in closed)
            assert SENTINEL not in json.dumps(recorder.report())
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


# A05 - classification: provider-injected errors stay inside their phase


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "error,family",
    [
        (AuthenticationError("denied"), "authentication"),
        (PermissionDeniedError("denied"), "permission"),
        (DeviceError("device"), "device"),
        (ProtocolError("schema"), "protocol"),
        (TransportError("down", reason="network"), "transport"),
        (ApiError("failed"), "api"),
        (RuntimeError("weird"), "unknown"),
    ],
)
def test_error_family_classification(mode, error, family):
    recorder = _phase_error(mode, error)
    failed = [e for e in _events(recorder) if e["outcome"] == "failed"]
    assert failed
    assert {_field(e, "error_family") for e in failed} == {family}


@pytest.mark.parametrize("mode", MODES)
def test_provider_value_error_is_not_validation(mode):
    recorder = _phase_error(mode, ValueError("provider bug"))
    failed = [e for e in _events(recorder) if e["outcome"] == "failed"]
    assert any(e["kind"] == "authentication" for e in failed)
    assert all(_field(e, "error_family") != "validation" for e in failed)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "code,expected",
    [
        (10002, "invalid_token"),
        ("10002", "invalid_token"),
        (10031, "permission_denied"),
        ("10031", "permission_denied"),
        ("204", "other"),
        (0, "other"),
        (True, "other"),
        ("10002x", "other"),
        (_Boom(), "other"),
        (None, "none"),
    ],
)
def test_code_category_whitelist(mode, code, expected):
    recorder = _phase_error(mode, AuthenticationError("denied", code=code))
    events = _events(recorder)
    if expected == "none":
        assert all(_field(e, "code_category") == "none" for e in events)
    else:
        assert any(
            _field(e, "code_category") == expected for e in events if e["outcome"] == "failed"
        )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "status,expected",
    [
        (102, "informational"),
        (200, "success"),
        (301, "redirect"),
        (404, "client_error"),
        (503, "server_error"),
        ("200", "other"),
        (True, "other"),
        (_Boom(), "other"),
        (None, "none"),
    ],
)
def test_http_status_category(mode, status, expected):
    recorder = _phase_error(mode, ApiError("failed", http_status=status))
    events = _events(recorder)
    if expected == "none":
        assert all(_field(e, "http_status_category") == "none" for e in events)
    else:
        assert any(
            _field(e, "http_status_category") == expected
            for e in events
            if e["outcome"] == "failed"
        )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "reason,expected",
    [
        ("timeout", "timeout"),
        ("network", "network"),
        ("closed", "closed"),
        ("weird", "other"),
        (_Boom(), "other"),
    ],
)
def test_transport_reason_whitelist(mode, reason, expected):
    recorder = _phase_error(mode, TransportError("down", reason=reason))
    failed = [e for e in _events(recorder) if e["outcome"] == "failed"]
    assert failed
    assert any(_field(e, "reason") == expected for e in failed)
    assert all(_field(e, "error_family") == "transport" for e in failed)


# A03 - no free text, sentinel values or class names may leak


@pytest.mark.parametrize("mode", MODES)
def test_no_secret_or_class_names_leak(mode):
    class Tricky(Exception):
        code = _Boom()
        http_status = _Boom()
        reason = _Boom()

    recorder = _phase_error(mode, Tricky(SENTINEL), name=f"dg.{SENTINEL}")
    events = _events(recorder)
    _assert_well_formed(events)
    failed = [e for e in events if e["outcome"] == "failed"]
    assert {_field(e, "error_family") for e in failed} == {"unknown"}
    text = json.dumps(recorder.report())
    assert SENTINEL not in text and "Tricky" not in text
    ops = {e["operation"] for e in events if e["entrypoint"] == "client.request"}
    assert ops == {"other"}


# A06 - exception identity is preserved with or without a recorder


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("wired", [False, True])
def test_exception_identity(mode, wired):
    async def scenario():
        marker = ApiError("marker-dg003")
        provider_cls = _SyncProvider if mode == "sync" else _AsyncProvider
        recorder = DiagnosticRecorder() if wired else None
        client, _ = _client(mode, recorder, provider=provider_cls(marker))
        try:
            with pytest.raises(ApiError) as caught:
                await _drive(mode, client, Operation("devices.list", "GET", "/dg"))
            assert caught.value is marker
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


# A07 - cancellation, interrupted outcomes, cleanup independence


def test_cancel_during_authentication():
    async def scenario():
        recorder = DiagnosticRecorder()
        gate = asyncio.Event()

        class Slow(_AsyncProvider):
            async def get_token(self):
                self.calls += 1
                await gate.wait()
                return _token()

        client, seen = _client("async", recorder, provider=Slow(_token()))
        try:
            task = asyncio.create_task(client.request(Operation("devices.list", "GET", "/dg")))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            events = _events(recorder)
            _assert_well_formed(events)
            auth = [e for e in events if e["kind"] == "authentication"]
            assert any(e["outcome"] == "cancelled" for e in auth)
            assert not seen
        finally:
            gate.set()
            await client.aclose()

    asyncio.run(scenario())


def test_aclose_waiter_cancel_leaves_cleanup_running():
    async def scenario():
        recorder = DiagnosticRecorder()
        gate = asyncio.Event()

        class Slow(_AsyncProvider):
            async def get_token(self):
                self.calls += 1
                await gate.wait()
                return _token()

        client, _ = _client("async", recorder, provider=Slow(_token()))
        request_task = asyncio.create_task(client.request(Operation("devices.list", "GET", "/dg")))
        await asyncio.sleep(0)
        close_task = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task
        gate.set()
        await request_task
        for _ in range(100):
            if any(
                e["kind"] == "cleanup" and e["outcome"] == "succeeded" for e in _events(recorder)
            ):
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("cleanup did not finish after waiter cancellation")
        assert client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("exc", [KeyboardInterrupt("k"), SystemExit(2)])
def test_capture_interrupted_outcome(exc):
    recorder = DiagnosticRecorder()
    with pytest.raises(type(exc)):
        with recorder.capture():
            raise exc
    app = [e for e in _events(recorder) if e["entrypoint"] == "application"]
    assert any(e["outcome"] == "interrupted" for e in app)


# A06/A09 - boundaries: unawaited coroutines, binding errors, capture scope


def test_unawaited_coroutine_records_nothing():
    async def scenario():
        recorder = DiagnosticRecorder()
        client, _ = _client("async", recorder)
        try:
            coro = client.devices.list()
            coro.close()
            assert recorder.report()["events"] == []
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
def test_capture_boundaries(mode):
    async def scenario():
        recorder = DiagnosticRecorder()
        client, _ = _client(mode, recorder)
        try:
            with pytest.raises(TypeError):
                client.devices.get()  # binding fails before the body runs
            assert not [e for e in _events(recorder) if e["entrypoint"] == "devices.get"]
            with pytest.raises(TypeError):
                with recorder.capture():
                    client.devices.get()
            app = [e for e in _events(recorder) if e["entrypoint"] == "application"]
            assert {e["outcome"] for e in app} == {"started", "failed"}
            assert all(e["mode"] == "application" for e in app)
            failed = [e for e in app if e["outcome"] == "failed"]
            assert {_field(e, "error_family") for e in failed} == {"application"}
        finally:
            await _close(mode, client)

    asyncio.run(scenario())

    # constructor binding and arbitrary application code follow the same rule
    recorder = DiagnosticRecorder()
    with pytest.raises(TypeError):
        with recorder.capture():
            Client()  # missing required region argument
    app = [e for e in _events(recorder) if e["entrypoint"] == "application"]
    assert {e["outcome"] for e in app} == {"started", "failed"}

    quiet = DiagnosticRecorder()

    def _explode():
        raise ValueError("application bug")

    with pytest.raises(ValueError):
        _explode()
    assert _events(quiet) == []


@pytest.mark.parametrize("exc", [ValueError("v"), TypeError("t")], ids=["value", "type"])
def test_capture_typed_errors_are_application(exc):
    recorder = DiagnosticRecorder()
    with pytest.raises(type(exc)):
        with recorder.capture():
            raise exc
    app = [
        e
        for e in _events(recorder)
        if e["entrypoint"] == "application" and e["outcome"] == "failed"
    ]
    assert {_field(e, "error_family") for e in app} == {"application"}


def test_capture_unknown_error_has_no_identity():
    class Weird(Exception):
        pass

    recorder = DiagnosticRecorder()
    with pytest.raises(Weird):
        with recorder.capture():
            raise Weird(SENTINEL)
    app = [e for e in _events(recorder) if e["outcome"] == "failed"]
    assert app and all(e["kind"] == "application" for e in app)
    text = json.dumps(recorder.report())
    assert SENTINEL not in text and "Weird" not in text


def test_nested_capture_parenting_across_await():
    async def scenario():
        recorder = DiagnosticRecorder()
        client, _ = _client("async", recorder)
        try:
            with recorder.capture():
                await asyncio.sleep(0)
                with recorder.capture():
                    await _drive("async", client, Operation("devices.list", "GET", "/dg"))
            events = _events(recorder)
            _assert_well_formed(events)
            apps = [
                e for e in events if e["entrypoint"] == "application" and e["outcome"] == "started"
            ]
            assert len(apps) == 2
            assert apps[0]["parent_span_id"] is None
            assert apps[1]["parent_span_id"] == apps[0]["span_id"]
            call_root = next(
                e
                for e in events
                if e["entrypoint"] == "client.request" and e["outcome"] == "started"
            )
            assert call_root["parent_span_id"] == apps[1]["span_id"]
        finally:
            await _close("async", client)

    asyncio.run(scenario())


# A02 - parent isolation across threads and async tasks


def test_thread_parent_isolation():
    recorder = DiagnosticRecorder()
    client, _ = _client("sync", recorder, provider=_SyncProvider(_token(), _token()))
    inside = threading.Event()
    done = threading.Event()
    errors = []

    def worker():
        try:
            inside.wait(5)
            client.request(Operation("dg.thread", "GET", "/dg"))
        except BaseException as exc:  # reported to parent thread
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        with recorder.capture():
            inside.set()
            assert done.wait(5)
            client.request(Operation("devices.list", "GET", "/dg"))
    finally:
        thread.join(5)
        client.close()
    assert not errors
    events = _events(recorder)
    calls = [e for e in events if e["entrypoint"] == "client.request" and e["outcome"] == "started"]
    thread_root = next(e for e in calls if e["operation"] == "other")
    main_root = next(e for e in calls if e["operation"] == "devices.list")
    app_span = next(
        e["span_id"]
        for e in events
        if e["entrypoint"] == "application" and e["outcome"] == "started"
    )
    assert thread_root["parent_span_id"] is None
    assert main_root["parent_span_id"] == app_span


def test_task_parent_isolation():
    async def scenario():
        recorder = DiagnosticRecorder()
        first, _ = _client("async", recorder)
        second, _ = _client("async", recorder)
        started = asyncio.Event()
        release = asyncio.Event()

        async def held():
            with recorder.capture():
                started.set()
                await release.wait()
                await first.request(Operation("devices.list", "GET", "/dg"))

        async def bare():
            await started.wait()
            await second.request(Operation("dg.bare", "GET", "/dg"))
            release.set()

        try:
            await asyncio.gather(held(), bare())
        finally:
            await first.aclose()
            await second.aclose()
        events = _events(recorder)
        calls = [
            e for e in events if e["entrypoint"] == "client.request" and e["outcome"] == "started"
        ]
        bare_root = next(e for e in calls if e["operation"] == "other")
        held_root = next(e for e in calls if e["operation"] == "devices.list")
        app_span = next(
            e["span_id"]
            for e in events
            if e["entrypoint"] == "application" and e["outcome"] == "started"
        )
        assert bare_root["parent_span_id"] is None
        assert held_root["parent_span_id"] == app_span

    asyncio.run(scenario())


# anonymous operations produce no authentication events


@pytest.mark.parametrize("mode", MODES)
def test_anonymous_has_no_authentication_events(mode):
    async def scenario():
        recorder = DiagnosticRecorder()
        region = RegionConfig.overseas(api_base_url="https://isgpopen.ezviz.com")
        client, seen = _client(mode, recorder, region=region, anonymous=True)
        try:
            op = Operation("global.settings.list_timezones", "GET", "/dg", token_location="none")
            await _drive(mode, client, op)
            events = _events(recorder)
            assert not [e for e in events if e["kind"] == "authentication"]
            assert any(e["kind"] == "attempt" and e["outcome"] == "succeeded" for e in events)
            assert all(e["region"] == "overseas" for e in events)
            assert len(seen) == 1
        finally:
            await _close(mode, client)

    asyncio.run(scenario())


# A08 - inspection orchestration scopes and partial failure


_INSPECT_OPS = [
    "devices.get",
    "devices.channels",
    "devices.status",
    "diagnostics.capabilities",
    "diagnostics.version",
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("failure", ["none", "partial", "auth"])
def test_inspection_scopes(mode, failure):
    auth_marker = AuthenticationError("stop")
    api_marker = ApiError("step failed")

    async def scenario():
        recorder = DiagnosticRecorder()
        client, _ = _client(mode, recorder)
        produced = []
        calls = []

        def _effect(operation, **_kw):
            calls.append(operation.name)
            if failure == "auth" and len(calls) == 1:
                raise auth_marker
            if failure == "partial" and len(calls) == 3:
                raise api_marker
            resp = ApiResponse(200, "200", operation=operation.name, data={})
            produced.append(resp)
            return resp

        if mode == "sync":
            client.request = _effect
        else:

            async def _aeffect(operation, **kw):
                return _effect(operation, **kw)

            client.request = _aeffect
        try:
            if failure == "auth":
                with pytest.raises(AuthenticationError) as caught:
                    if mode == "sync":
                        inspect_device(client, "DEV123")
                    else:
                        await inspect_device_async(client, "DEV123")
                assert caught.value is auth_marker
                events = _events(recorder)
                insp = [e for e in events if e["kind"] == "inspection"]
                assert any(e["outcome"] == "failed" for e in insp)
                started = [
                    e["entrypoint"]
                    for e in events
                    if e["kind"] == "call"
                    and e["outcome"] == "started"
                    and e["entrypoint"] in _INSPECT_OPS
                ]
                assert started == ["devices.get"]
                return
            if mode == "sync":
                result = inspect_device(client, "DEV123")
            else:
                result = await inspect_device_async(client, "DEV123")
            events = _events(recorder)
            insp = [e for e in events if e["kind"] == "inspection"]
            assert {e["outcome"] for e in insp} == {"started", "succeeded"}
            expected = "inspect_device" if mode == "sync" else "inspect_device_async"
            assert all(e["entrypoint"] == expected for e in insp)
            insp_span = next(e["span_id"] for e in insp if e["outcome"] == "started")
            children = [
                e for e in events if e["kind"] == "call" and e["entrypoint"] in _INSPECT_OPS
            ]
            started = [e for e in children if e["outcome"] == "started"]
            assert [e["entrypoint"] for e in started] == _INSPECT_OPS
            assert all(e["parent_span_id"] == insp_span for e in started)
            responses = [r.response for r in result.results if r.response is not None]
            assert len(responses) == len(produced)
            assert all(resp is out for resp, out in zip(responses, produced))
            if failure == "partial":
                assert not result.all_succeeded
                assert result.results[2].error is api_marker
                failed = [e for e in children if e["outcome"] == "failed"]
                assert [e["entrypoint"] for e in failed] == ["devices.status"]
            else:
                assert result.all_succeeded
            assert "DEV123" not in json.dumps(recorder.report())
        finally:
            await _close(mode, client)

    asyncio.run(scenario())
