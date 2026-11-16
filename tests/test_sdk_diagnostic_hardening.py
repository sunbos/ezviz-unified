"""DG006 H01-H03: diagnostic hardening — hostile inputs, single internal
faults, and ContextVar/recorder/thread isolation.

All tests are offline: synthetic sentinel values, ``httpx.MockTransport``
and an inline ``httpx.AsyncBaseTransport``. No environment, network or
credential reads. Async scenarios are driven by ``asyncio.run`` with
bounded ``wait_for`` gates; threads use bounded barriers/joins.
"""

from __future__ import annotations

import asyncio
import collections
import inspect
import json
import threading
from unittest import mock

import httpx
import pytest

from ezviz_unified import diagnostic_report as _diag
from ezviz_unified.auth_models import RegionConfig, Token
from ezviz_unified.client import AsyncClient, Client
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.errors import ApiError, EzvizError, TransportError
from ezviz_unified.models import ApiResponse
from ezviz_unified.operations import Operation

_COUNTED_ATTRS = ("code", "http_status", "reason", "operation")
_PROBE_METHODS = ("__str__", "__repr__", "__hash__", "__eq__", "__lt__")
_API_ORIGIN = "https://open.ys7.com"
_FAR_FUTURE = 4_000_000_000.0


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"code": "200", "msg": "ok", "data": {"ok": True}})


def _sync_factory(origin: str) -> httpx.MockTransport:
    return httpx.MockTransport(_ok_response)


class _AsyncHandlerTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


class _ScriptedProvider:
    """Synchronous TokenProvider returning one scripted result."""

    def __init__(self, result):
        self._result = result
        self.invalidate_calls = []

    @property
    def can_refresh(self) -> bool:
        return False

    def get_token(self) -> Token:
        # Classify through the real type so this fixture never reads the
        # spoofed ``__class__`` that _HostileMixin counts.
        if issubclass(type(self._result), BaseException):
            raise self._result
        return self._result

    def invalidate(self, expected_value=None) -> None:
        self.invalidate_calls.append(expected_value)


_TOKEN = Token(
    value="synthetic-token",
    expires_at=_FAR_FUTURE,
    refresh_at=_FAR_FUTURE,
    api_base_url=_API_ORIGIN,
)
_OP_AUTH = Operation(name="devices.list", method="GET", path="/devices", read_only=True)
_OP_PUBLIC = Operation(
    name="devices.list", method="GET", path="/devices", token_location="none", read_only=True
)


def _public_op(name: str, path: str) -> Operation:
    return Operation(name=name, method="GET", path=path, token_location="none", read_only=True)


def _anon_sync_client(recorder: DiagnosticRecorder) -> Client:
    return Client(
        region=RegionConfig.china(),
        anonymous=True,
        transport_factory=_sync_factory,
        diagnostic_recorder=recorder,
    )


def _provider_sync_client(recorder: DiagnosticRecorder, provider) -> Client:
    return Client(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=_sync_factory,
        diagnostic_recorder=recorder,
    )


class _ClientShim:
    """Minimal object exposing only what _diagnostic_scope reads."""

    def __init__(self, recorder: DiagnosticRecorder) -> None:
        self._diagnostic_recorder = recorder
        self._region = RegionConfig.china()


# ------------------------------------------------------------------ H01 --


class _HostileMixin:
    """Counts diagnosed-attribute reads and spoofs ``__class__``.

    ``__class__`` is spoofed to an exact known SDK error type and its reads
    are counted like every other diagnosed attribute: diagnostics must
    classify through the real ``type()`` and must not consult the spoofed
    attribute or any counted getter on the live exception object.
    """

    def __getattribute__(self, name: str):
        if name == "__class__":
            object.__getattribute__(self, "_probe")["__class__"] += 1
            return ApiError
        if name in _COUNTED_ATTRS:
            object.__getattribute__(self, "_probe")[name] += 1
            return "SENTINEL-" + name
        return super().__getattribute__(name)


class _HostileEzviz(_HostileMixin, EzvizError):
    def __init__(self, counter):
        object.__setattr__(self, "_probe", counter)
        EzvizError.__init__(self, "hostile")


class _HostilePlain(_HostileMixin, Exception):
    def __init__(self, counter):
        object.__setattr__(self, "_probe", counter)
        Exception.__init__(self, "hostile")


class _HostileValueError(_HostileMixin, ValueError):
    def __init__(self, counter):
        object.__setattr__(self, "_probe", counter)
        ValueError.__init__(self, "hostile")


class _HostileTypeError(_HostileMixin, TypeError):
    def __init__(self, counter):
        object.__setattr__(self, "_probe", counter)
        TypeError.__init__(self, "hostile")


_HOSTILE_EXCS = (_HostileEzviz, _HostilePlain, _HostileValueError, _HostileTypeError)


class _HostileStr(str):
    def __new__(cls, value, counter):
        self = str.__new__(cls, value)
        self._probe = counter
        return self

    def __str__(self):
        self._probe["__str__"] += 1
        return str.__str__(self)

    def __repr__(self):
        self._probe["__repr__"] += 1
        return str.__repr__(self)

    def __hash__(self):
        self._probe["__hash__"] += 1
        return str.__hash__(self)

    def __eq__(self, other):
        self._probe["__eq__"] += 1
        return str.__eq__(self, other)


class _HostileInt(int):
    def __new__(cls, value, counter):
        self = int.__new__(cls, value)
        self._probe = counter
        return self

    def __str__(self):
        self._probe["__str__"] += 1
        return int.__str__(self)

    def __repr__(self):
        self._probe["__repr__"] += 1
        return int.__repr__(self)

    def __hash__(self):
        self._probe["__hash__"] += 1
        return int.__hash__(self)

    def __eq__(self, other):
        self._probe["__eq__"] += 1
        return int.__eq__(self, other)

    def __lt__(self, other):
        self._probe["__lt__"] += 1
        return int.__lt__(self, other)


class _HostileOperation(Operation):
    """Operation subclass whose ``name`` read is counted."""

    def __init__(self, counter):
        object.__setattr__(self, "_probe", counter)
        super().__init__(name="devices.list", method="GET", path="/op/x", token_location="none")

    def __getattribute__(self, name: str):
        if name == "name":
            object.__getattribute__(self, "_probe")["name"] += 1
            return "devices.list"
        return super().__getattribute__(name)


def _probe_zero(counter, names=_COUNTED_ATTRS) -> bool:
    # ``__class__`` reads are counted on the hostile exception mixin; scalar
    # probe counters never register the key, so it stays 0 for them.
    return counter["__class__"] == 0 and all(counter[name] == 0 for name in names)


@pytest.mark.parametrize("route", ["provider", "capture"])
@pytest.mark.parametrize("exc_cls", _HOSTILE_EXCS, ids=lambda c: c.__name__)
def test_h01_hostile_exception_subclass_never_inspected(exc_cls, route):
    counter = collections.Counter()
    exc = exc_cls(counter)
    rec = DiagnosticRecorder()
    caught = None
    if route == "provider":
        client = _provider_sync_client(rec, _ScriptedProvider(exc))
        try:
            try:
                client.request(_OP_AUTH)
            except BaseException as err:
                caught = err
            # Snapshot before close() so lifecycle events cannot leak into
            # the failed-event and recording-error assertions below.
            report = rec.report()
            report_json = rec.to_json()
        finally:
            client.close()
    else:
        try:
            with rec.capture():
                raise exc
        except BaseException as err:
            caught = err
        report = rec.report()
        report_json = rec.to_json()
    assert caught is exc
    blob = json.dumps(report)
    assert "SENTINEL" not in blob
    assert "Hostile" not in blob
    assert "Hostile" not in report_json
    assert report["recording_errors"] == 0
    assert _probe_zero(counter)
    failed = [e for e in report["events"] if e["outcome"] == "failed"]
    if route == "provider":
        # authentication and enclosing call scopes both classify unknown.
        assert len(failed) == 2
        assert {e["error_family"] for e in failed} == {"unknown"}
    else:
        # Unknown exceptions inside capture classify as application.
        assert len(failed) == 1
        assert failed[0]["kind"] == "application"
        assert failed[0]["error_family"] == "application"


def _scalar_case(kind):
    """Build a real SDK error, then swap diagnosed fields for hostile scalars."""
    counter = collections.Counter()
    if kind == "api":
        exc = ApiError("plain")
        exc.code = _HostileStr("10002", counter)
        exc.http_status = _HostileInt(503, counter)
        return (
            exc,
            counter,
            {
                "error_family": "api",
                "reason": "none",
                "code_category": "other",
                "http_status_category": "other",
            },
        )
    exc = TransportError("plain", reason="timeout")
    exc.code = _HostileStr("10031", counter)
    exc.http_status = _HostileInt(500, counter)
    exc.reason = _HostileStr("timeout", counter)
    return (
        exc,
        counter,
        {
            "error_family": "transport",
            "reason": "other",
            "code_category": "other",
            "http_status_category": "other",
        },
    )


@pytest.mark.parametrize("kind", ["api", "transport"])
def test_h01_hostile_scalar_fields_never_invoked(kind):
    exc, counter, expected = _scalar_case(kind)
    rec = DiagnosticRecorder()
    caught = None
    try:
        with rec.capture():
            raise exc
    except BaseException as err:
        caught = err
    assert caught is exc
    report = rec.report()
    failed = [e for e in report["events"] if e["outcome"] == "failed"]
    assert len(failed) == 1
    for key, value in expected.items():
        assert failed[0][key] == value
    assert _probe_zero(counter, _PROBE_METHODS)
    assert report["recording_errors"] == 0
    assert "SENTINEL" not in json.dumps(report)


def test_h01_operation_name_hostile_str_never_invoked():
    counter = collections.Counter()
    op = Operation(name="devices.list", method="GET", path="/devices", token_location="none")
    # Assigned after construction: SDK business code legitimately reads
    # ``operation.name`` itself, so the projection is exercised directly at
    # the controlled _diagnostic_scope boundary instead of via client.request.
    object.__setattr__(op, "name", _HostileStr("devices.list", counter))
    rec = DiagnosticRecorder()
    with _diag._diagnostic_scope(
        _ClientShim(rec), entrypoint="client.request", mode="sync", operation=op
    ):
        pass
    assert _probe_zero(counter, _PROBE_METHODS)
    report = rec.report()
    assert [e["operation"] for e in report["events"]] == ["other", "other"]
    assert report["recording_errors"] == 0


def test_h01_operation_subclass_name_getter_never_read():
    counter = collections.Counter()
    op = _HostileOperation(counter)
    counter.clear()  # discount Operation.__post_init__'s own reads
    rec = DiagnosticRecorder()
    with _diag._diagnostic_scope(
        _ClientShim(rec), entrypoint="client.request", mode="sync", operation=op
    ):
        pass
    assert counter["name"] == 0
    events = rec.report()["events"]
    assert [e["operation"] for e in events] == ["other", "other"]
    assert rec.report()["recording_errors"] == 0


# ------------------------------------------------------------------ H02 --


class _Fault(Exception):
    """Synthetic internal recording fault; must never reach SDK callers."""


_ORIG_PUSH = DiagnosticRecorder._push_scope


def _raise_scope_init(self, *args, **kwargs):
    raise _Fault("scope init")


def _raise_projection(operation):
    raise _Fault("operation projection")


def _raise_append(self, event):
    raise _Fault("append")


def _push_then_raise(self, scope):
    _ORIG_PUSH(self, scope)
    raise _Fault("push")


def _raise_pop(self, token):
    raise _Fault("pop")


_SEAM_PATCHERS = {
    "scope_init": lambda: mock.patch.object(_diag._Scope, "__init__", _raise_scope_init),
    "operation_name": lambda: mock.patch.object(_diag, "_operation_name", _raise_projection),
    "append": lambda: mock.patch.object(DiagnosticRecorder, "_append", _raise_append),
    "push_scope": lambda: mock.patch.object(DiagnosticRecorder, "_push_scope", _push_then_raise),
    "pop_scope": lambda: mock.patch.object(DiagnosticRecorder, "_pop_scope", _raise_pop),
}

# Buffered events expected while the seam is broken: 0 = nothing recorded,
# 2 = only the application scope pair, None = recording still works (the
# _pop_scope fallback restores the parent and emitting proceeds).
_SEAM_EVENTS_WHILE_BROKEN = {
    "scope_init": 0,
    "operation_name": 2,
    "append": 0,
    "push_scope": 0,
    "pop_scope": None,
}


def _make_body_exc(name):
    return {
        "runtime": RuntimeError("body failure"),
        "cancelled": asyncio.CancelledError(),
        "interrupt": KeyboardInterrupt(),
        "exit": SystemExit(3),
    }[name]


@pytest.mark.parametrize("body_exc", [None, "runtime", "cancelled", "interrupt", "exit"])
@pytest.mark.parametrize("seam", sorted(_SEAM_PATCHERS))
def test_h02_single_internal_fault_isolated(seam, body_exc):
    exc = None if body_exc is None else _make_body_exc(body_exc)
    rec = DiagnosticRecorder()
    # The real client is built before the seam is broken.
    client = _provider_sync_client(rec, _ScriptedProvider(_TOKEN))
    try:
        resp = None
        caught = None
        with _SEAM_PATCHERS[seam]():
            try:
                with rec.capture():
                    resp = client.request(_OP_AUTH)
                    assert isinstance(resp, ApiResponse)
                    if exc is not None:
                        raise exc
            except BaseException as err:
                caught = err
            mid_report = rec.report()
        if exc is None:
            assert caught is None
        else:
            assert caught is exc  # original object, never swallowed or replaced
        assert isinstance(resp, ApiResponse)
        assert resp.code == "200"
        assert mid_report["recording_errors"] >= 1
        events = mid_report["events"]
        expected = _SEAM_EVENTS_WHILE_BROKEN[seam]
        if expected == 0:
            assert events == []
        elif expected == 2:
            assert [e["kind"] for e in events] == ["application", "application"]
        else:
            assert any(e["kind"] == "call" for e in events)
        # With the patch removed, a fresh root scope must see no stale parent.
        with rec.capture():
            pass
        starts = [
            e
            for e in rec.report()["events"]
            if e["kind"] == "application" and e["outcome"] == "started"
        ]
        assert starts[-1]["parent_span_id"] is None
    finally:
        client.close()


# ------------------------------------------------------------------ H03 --


class _Gate:
    """Blocks the /a request inside its attempt scope until released."""

    def __init__(self):
        self.a_in_attempt = asyncio.Event()
        self.release_a = asyncio.Event()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/a"):
            self.a_in_attempt.set()
            await asyncio.wait_for(self.release_a.wait(), 10)
        return httpx.Response(200, json={"code": "200", "data": {"ok": True}})


async def _interleaved_tasks() -> dict:
    gate = _Gate()
    rec = DiagnosticRecorder()
    client = AsyncClient(
        region=RegionConfig.china(),
        anonymous=True,
        transport_factory=lambda origin: _AsyncHandlerTransport(gate.handler),
        diagnostic_recorder=rec,
    )
    op_a = _public_op("devices.list", "/gate/a")
    op_b = _public_op("devices.get", "/gate/b")
    task_a = None
    task_b = None
    try:
        with rec.capture():
            task_a = asyncio.create_task(client.request(op_a))
            task_b = asyncio.create_task(client.request(op_b))
            try:
                await asyncio.wait_for(gate.a_in_attempt.wait(), 5)
                resp_b = await asyncio.wait_for(task_b, 5)
            finally:
                # Whatever happened to B, release A from its attempt scope
                # so no request is still in flight when the client closes.
                gate.release_a.set()
            resp_a = await asyncio.wait_for(task_a, 5)
        assert isinstance(resp_a, ApiResponse)
        assert isinstance(resp_b, ApiResponse)
        # Snapshot before close: lifecycle events must not enter the
        # business-event assertions.
        return rec.report()
    finally:
        pending = [task for task in (task_a, task_b) if task is not None and not task.done()]
        if pending:
            gate.release_a.set()
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 5)
        closer = getattr(client, "aclose", None) or getattr(client, "close")
        closed = closer()
        if inspect.isawaitable(closed):
            await closed


def test_h03_tasks_in_one_capture_are_siblings():
    report = asyncio.run(asyncio.wait_for(_interleaved_tasks(), 15))
    events = report["events"]
    assert report["recording_errors"] == 0
    capture_span = next(
        e["span_id"] for e in events if e["kind"] == "application" and e["outcome"] == "started"
    )
    call_starts = [e for e in events if e["kind"] == "call" and e["outcome"] == "started"]
    assert len(call_starts) == 2
    a_call = next(e for e in call_starts if e["operation"] == "devices.list")
    b_call = next(e for e in call_starts if e["operation"] == "devices.get")
    assert a_call["span_id"] != b_call["span_id"]
    # Both tasks copied the live capture context; neither parents the other.
    assert a_call["parent_span_id"] == capture_span
    assert b_call["parent_span_id"] == capture_span
    a_end = next(
        e
        for e in events
        if e["kind"] == "call" and e["span_id"] == a_call["span_id"] and e["outcome"] != "started"
    )
    # B's whole request ran while A was still blocked inside its attempt.
    assert a_call["sequence"] < b_call["sequence"] < a_end["sequence"]
    for span, op_name in (
        (a_call["span_id"], "devices.list"),
        (b_call["span_id"], "devices.get"),
    ):
        children = [
            e
            for e in events
            if e["kind"] in {"transport_setup", "attempt"}
            and e["outcome"] == "started"
            and e["parent_span_id"] == span
        ]
        assert len(children) == 2
        assert {e["operation"] for e in children} == {op_name}
    # No B-side event may ever point at A's span.
    assert not [
        e
        for e in events
        if e["operation"] == "devices.get" and e["parent_span_id"] == a_call["span_id"]
    ]


async def _to_thread_two_recorders():
    rec1, rec2 = DiagnosticRecorder(), DiagnosticRecorder()
    client1 = _anon_sync_client(rec1)
    client2 = _anon_sync_client(rec2)
    try:
        with rec1.capture():
            resp1 = await asyncio.to_thread(client1.request, _public_op("devices.list", "/t/1"))
            resp2 = await asyncio.to_thread(client2.request, _public_op("devices.get", "/t/2"))
        assert isinstance(resp1, ApiResponse)
        assert isinstance(resp2, ApiResponse)
        # Return report snapshots, not live clients or recorders: close()
        # may append lifecycle events after the business events.
        return rec1.report(), rec2.report()
    finally:
        try:
            client1.close()
        finally:
            client2.close()


def test_h03_to_thread_inherits_capture_per_recorder():
    report1, report2 = asyncio.run(asyncio.wait_for(_to_thread_two_recorders(), 15))
    e1 = report1["events"]
    e2 = report2["events"]
    cap = next(e["span_id"] for e in e1 if e["kind"] == "application" and e["outcome"] == "started")
    call1 = next(e for e in e1 if e["kind"] == "call" and e["outcome"] == "started")
    # to_thread copies the live context: the threaded request nests under it.
    assert call1["parent_span_id"] == cap
    assert call1["mode"] == "sync"
    # The second recorder owns a separate ContextVar: no capture, root parent.
    assert not any(e["kind"] == "application" for e in e2)
    call2 = next(e for e in e2 if e["kind"] == "call" and e["outcome"] == "started")
    assert call2["parent_span_id"] is None
    # Every parent link stays inside its own recorder's span ids.
    for events in (e1, e2):
        span_ids = {e["span_id"] for e in events}
        assert all(e["parent_span_id"] is None or e["parent_span_id"] in span_ids for e in events)


def test_h03_concurrent_threads_unique_sequences():
    rec = DiagnosticRecorder()
    client = _anon_sync_client(rec)
    barrier = threading.Barrier(4)
    errors = []

    def worker():
        try:
            barrier.wait(timeout=10)
            with rec.capture():
                assert isinstance(client.request(_OP_PUBLIC), ApiResponse)
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        # Snapshot before close() so lifecycle events cannot change the
        # business-event counts asserted below.
        events = rec.report()["events"]
    finally:
        # Only close when every worker has exited. If a recorder-lock
        # regression leaves a worker alive, the is_alive assertion above
        # already fails the test; calling close() here could block again
        # on the diagnostic scope or active-call wait and defeat the
        # bounded join. The daemon threads keep the process from hanging
        # on that exceptional failure path, so close() is skipped there.
        if all(not thread.is_alive() for thread in threads):
            client.close()
    sequences = [e["sequence"] for e in events]
    assert len(sequences) == len(set(sequences)) == 32
    assert sorted(sequences) == list(range(1, 33))
    span_ids = {e["span_id"] for e in events}
    assert len(span_ids) == 16
    assert all(e["parent_span_id"] is None or e["parent_span_id"] in span_ids for e in events)
    app_starts = [e for e in events if e["kind"] == "application" and e["outcome"] == "started"]
    assert len(app_starts) == 4
    assert {e["parent_span_id"] for e in app_starts} == {None}


def test_h03_report_snapshots_are_independent():
    rec = DiagnosticRecorder()
    with rec.capture():
        pass
    snap = rec.report()
    event_count = len(snap["events"])
    snap["events"][0]["kind"] = "MUTATED"
    snap["events"][0]["parent_span_id"] = 424242
    snap["events"].append({"sequence": -1, "kind": "injected"})
    snap["max_events"] = 0
    snap["dropped_events"] = -5
    fresh = rec.report()
    assert fresh["max_events"] == 256
    assert fresh["dropped_events"] == 0
    assert len(fresh["events"]) == event_count
    blob = json.dumps(fresh)
    assert "MUTATED" not in blob
    assert "injected" not in blob
    assert all(e["parent_span_id"] != 424242 for e in fresh["events"])
    assert json.loads(rec.to_json()) == fresh
