"""Local, opt-in, bounded diagnostic event recording for the unified SDK.

A ``DiagnosticRecorder`` instance stores at most ``max_events`` whitelisted
scalar events describing SDK call outcomes. It never stores credentials,
tokens, URLs, device identifiers, request/response payloads, exception
messages, exception class names or chains, user labels or timestamps, and it
never performs I/O, network access or implicit background work.

``report()`` returns an independent JSON-compatible snapshot; ``to_json()``
only serializes that snapshot to a string. Recording is strictly per
instance: there is no global recorder, ambient logging or exporter.
Internal recording failures are counted under ``recording_errors`` and never
replace SDK return values or exceptions — but only ordinary ``Exception``
faults are isolated this way. ``KeyboardInterrupt``, ``SystemExit``,
``asyncio.CancelledError`` and other non-``Exception`` ``BaseException``
instances reaching an internal position propagate unchanged after any
already-installed context push is unwound, so user interrupts and
cancellation are never swallowed; such an interruption may leave the report
missing events without any ``recording_errors`` increment.

Classification never invokes ``str``/``repr``/``isinstance``/equality/hash
on unknown objects and never trusts a spoofable ``__class__`` attribute:
every decision uses the real ``type(exc)`` identity, or ``issubclass`` of
that real type against fixed trusted exception bases. Base attributes are
read only when ``type(exc)`` is exactly a known SDK error type and
``type(operation)`` is exactly ``Operation``; untrusted exception
subclasses are reported only as ``unknown`` or ``application`` with no
attribute access. Whitelisted candidate values are compared only after a
strict builtin ``type()`` check.

``_diagnostic_scope`` and ``_diagnostic_retry`` are internal hooks used by
instrumented client code only. They are side-effect-free no-ops when the
client does not carry an exact ``DiagnosticRecorder`` instance and never
change call signatures, request ordering or exception identity.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Literal

from ._diagnostic_registry import ENTRYPOINTS, OPERATIONS
from .auth_models import RegionConfig
from .errors import (
    ApiError,
    AuthenticationError,
    DeviceError,
    EzvizError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)
from .operations import Operation

__all__ = ["DiagnosticRecorder"]

_SCHEMA_VERSION = 1
_REPORT_SCOPE = "local_sdk_diagnostics"
_MIN_EVENTS = 1
_MAX_EVENTS_LIMIT = 4096

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
_MODES = frozenset({"sync", "async"})
_REGION_SCOPES = frozenset({"domestic", "overseas"})
_TRANSPORT_REASONS = frozenset({"timeout", "network", "closed"})
_ATTEMPT_VALUES = frozenset({0, 1, 2})
# Only these kinds may ever claim "validation"; authentication, attempt and
# transport_setup (the Client factory/origin/injection stage) must not, and
# a claim is honest only while no child scope was ever entered.
_VALIDATION_KINDS = frozenset({"call", "inspection"})
_CODE_CATEGORIES = {
    10002: "invalid_token",
    10031: "permission_denied",
    "10002": "invalid_token",
    "10031": "permission_denied",
}

_MISSING = object()


class DiagnosticRecorder:
    """Bounded, thread-safe recorder of whitelisted SDK diagnostic events.

    The recorder is explicitly passed to a client; nothing is recorded
    globally. Events keep only fixed scalar fields and are dropped
    oldest-first beyond ``max_events`` while ``dropped_events`` counts the
    truncations. Spans propagate through a per-instance ContextVar holding
    the current scope reference for the running context, so parent links
    are isolated across recorders, threads and async tasks. A parent scope
    is only flagged one-way through ``had_child``; no shared mutable list
    or dict stack exists.
    """

    def __init__(self, *, max_events: int = 256) -> None:
        # Only a genuine int in [1, 4096] is accepted; bool is rejected.
        if (
            type(max_events) is not int
            or max_events < _MIN_EVENTS
            or max_events > _MAX_EVENTS_LIMIT
        ):
            raise ValueError("max_events must be an int between 1 and 4096")
        self._max_events = max_events
        self._events: deque[dict[str, object]] = deque()
        self._dropped_events = 0
        self._recording_errors = 0
        self._next_sequence = 0
        self._next_span_id = 0
        self._lock = threading.Lock()
        self._scope_var: ContextVar[_Scope | None] = ContextVar(
            "ezviz_unified_diagnostic_scope", default=None
        )

    def __repr__(self) -> str:
        return f"DiagnosticRecorder(max_events={self._max_events})"

    def capture(self) -> contextlib.AbstractContextManager[None]:
        """Explicit application scope; works in sync and async code.

        Records a ``kind="application"`` started/terminal pair, propagates
        exceptions unchanged, and may wrap construction, Python argument
        binding and arbitrary application code. Unknown exceptions raised
        inside are classified as ``application``. A construction failure of
        the scope itself degrades to a no-op instead of raising.
        """
        try:
            return _Scope(
                self,
                kind="application",
                entrypoint=_project_str("application", ENTRYPOINTS, "other"),
                operation="other",
                mode="application",
                region="unknown",
                attempt=0,
            )
        except Exception:
            self._note_recording_error()
            return contextlib.nullcontext()

    def report(self) -> dict[str, object]:
        """Return an independent JSON-compatible snapshot of the buffer."""
        with self._lock:
            events = [dict(event) for event in self._events]
            snapshot: dict[str, object] = {
                "schema_version": _SCHEMA_VERSION,
                "scope": _REPORT_SCOPE,
                "max_events": self._max_events,
                "dropped_events": self._dropped_events,
                "recording_errors": self._recording_errors,
                "events": events,
            }
        return snapshot

    def to_json(self) -> str:
        """Serialize ``report()`` to a string; never writes or uploads."""
        return json.dumps(self.report(), ensure_ascii=True, separators=(",", ":"))

    # -- internal recording primitives, used only by instrumented SDK code --

    def _current_scope(self) -> _Scope | None:
        scope = self._scope_var.get()
        return scope if type(scope) is _Scope else None

    def _current_span(self) -> int | None:
        scope = self._current_scope()
        return scope._span_id if scope is not None else None

    def _push_scope(self, scope: _Scope) -> Token[_Scope | None]:
        return self._scope_var.set(scope)

    def _pop_scope(self, token: Token[_Scope | None]) -> None:
        self._scope_var.reset(token)

    def _restore_scope(self, scope: _Scope | None) -> None:
        # Explicit parent restoration fallback: only ever reinstalls the
        # scope object that was current at enter time (or None), never a
        # fabricated parent.
        self._scope_var.set(scope)

    def _alloc_span(self) -> int:
        with self._lock:
            self._next_span_id += 1
            return self._next_span_id

    def _append(self, event: dict[str, object]) -> None:
        with self._lock:
            self._next_sequence += 1
            event["sequence"] = self._next_sequence
            if len(self._events) >= self._max_events:
                self._events.popleft()
                self._dropped_events += 1
            self._events.append(event)

    def _note_recording_error(self) -> None:
        try:
            with self._lock:
                self._recording_errors += 1
        except Exception:
            # Ordinary accounting faults must never raise into SDK code and
            # are only ever taken outside other lock-held sections; process
            # control arriving during the count still propagates.
            return


def _read_field(exc: BaseException, name: str) -> object:
    """Read an existing attribute without str/repr; never raises.

    Only invoked for ``type(exc)`` values that are exactly known SDK error
    types, so no user-defined property/descriptor can run. ``_MISSING`` marks
    a hostile read so the caller counts a recording fault.
    """
    try:
        return getattr(exc, name)
    except AttributeError:
        return None
    except Exception:
        return _MISSING


def _code_category(code: object) -> str:
    # None -> "none"; only genuine str/int map 10002/10031; every other
    # present value (including bool and foreign types) -> "other".
    if code is None:
        return "none"
    if type(code) is int or type(code) is str:
        return _CODE_CATEGORIES.get(code, "other")
    return "other"


def _http_status_category(status: object) -> str:
    # None -> "none"; only a genuine int is bucketed; any other non-None
    # value (including bool and out-of-range ints) -> "other".
    if status is None:
        return "none"
    if type(status) is not int:
        return "other"
    if status < 100 or status > 599:
        return "other"
    if status < 200:
        return "informational"
    if status < 300:
        return "success"
    if status < 400:
        return "redirect"
    if status < 500:
        return "client_error"
    return "server_error"


def _classify(
    exc: BaseException, kind: str, had_child: bool
) -> tuple[str, str, str, str, str, bool]:
    """Classify *exc* into fixed event fields without reading payloads.

    Returns ``(outcome, error_family, reason, code_category,
    http_status_category, internal_fault)``. The fault flag marks hostile
    attribute reads so the caller can bump ``recording_errors``. No
    ``str``/``repr``/``isinstance`` is invoked on the exception: every
    decision uses the real ``type(exc)`` identity, or ``issubclass`` of
    that real type against fixed trusted bases, so a spoofed ``__class__``
    attribute or hostile metaclass ``__eq__``/``__hash__`` cannot run.

    ``had_child`` records whether any child scope was entered inside this
    scope. ``call``/``inspection`` may claim ``validation`` only while no
    child scope ran; once a provider/factory/inner scope executed, the same
    ValueError/TypeError is honest only as ``unknown``.
    """
    exc_type = type(exc)
    # Subclass checks on the real type keep interrupt/cancel semantics for
    # genuine subclasses while ignoring any spoofed ``__class__``.
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit, GeneratorExit)):
        return ("interrupted", "interrupted", "none", "none", "none", False)
    if issubclass(exc_type, asyncio.CancelledError):
        return ("cancelled", "interrupted", "none", "none", "none", False)

    fault = False
    reason = "none"
    code_category = "none"
    http_status_category = "none"

    if issubclass(exc_type, EzvizError):
        read_context = False
        if exc_type is TransportError:
            family = "transport"
            read_context = True
            raw_reason = _read_field(exc, "reason")
            if raw_reason is _MISSING:
                fault = True
                reason = "other"
            elif type(raw_reason) is str and raw_reason in _TRANSPORT_REASONS:
                reason = raw_reason
            else:
                reason = "other"
        elif exc_type is AuthenticationError:
            family = "authentication"
            read_context = True
        elif exc_type is PermissionDeniedError:
            family = "permission"
            read_context = True
        elif exc_type is DeviceError:
            family = "device"
            read_context = True
        elif exc_type is ApiError:
            family = "api"
            read_context = True
        elif exc_type is ProtocolError:
            family = "protocol"
            read_context = True
        elif exc_type is EzvizError:
            family = "unknown"
            read_context = True
        else:
            # Untrusted EzvizError subclass: no getters, no str/repr/eq/hash;
            # it may only surface as unknown, or application in capture.
            family = "application" if kind == "application" else "unknown"
        if read_context:
            raw_code = _read_field(exc, "code")
            raw_status = _read_field(exc, "http_status")
            if raw_code is _MISSING or raw_status is _MISSING:
                fault = True
            code_category = _code_category(None if raw_code is _MISSING else raw_code)
            http_status_category = _http_status_category(
                None if raw_status is _MISSING else raw_status
            )
    elif exc_type is ValueError or exc_type is TypeError:
        # Only an exact builtin ValueError/TypeError may claim validation,
        # and only inside call/inspection scopes that never entered a child
        # scope; provider, factory or HTTPX errors of the same builtin type
        # must stay ``unknown``, and untrusted subclasses of either fall
        # through to unknown/application. authentication/attempt/
        # transport_setup never claim validation.
        if kind == "application":
            family = "application"
        elif kind in _VALIDATION_KINDS and not had_child:
            family = "validation"
        else:
            family = "unknown"
    elif kind == "application":
        family = "application"
    else:
        family = "unknown"

    return ("failed", family, reason, code_category, http_status_category, fault)


def _operation_name(operation: object) -> tuple[str, bool]:
    """Project an ``Operation`` to its whitelisted name; ``(name, fault)``.

    Only ``type(operation) is Operation`` is accepted, so subclassed or
    duck-typed objects cannot run user code here.
    """
    if type(operation) is not Operation:
        return ("other", False)
    try:
        name = operation.name
    except Exception:
        return ("other", True)
    if type(name) is str and name in OPERATIONS:
        return (name, False)
    return ("other", False)


def _project_str(value: object, allowed: frozenset[str], default: str) -> str:
    # Strict builtin str check before the whitelist membership test so no
    # foreign __hash__/__eq__ can run.
    if type(value) is str and value in allowed:
        return value
    return default


def _recorder_of(client: object) -> DiagnosticRecorder | None:
    try:
        recorder: object = getattr(client, "_diagnostic_recorder", None)
    except Exception:
        return None
    if type(recorder) is DiagnosticRecorder:
        return recorder
    return None


def _region_scope_of(client: object) -> tuple[str, bool]:
    """Read ``client._region.scope`` safely; ``(scope, fault)``.

    ``client._region`` is still read under the fault guard, but ``scope``
    is touched only when ``type(region)`` is exactly ``RegionConfig``: the
    client may carry an untrusted subclass whose ``scope`` getter could run
    or raise user code, so such regions report ``unknown`` with no
    attribute access. This does not narrow what ``Client`` accepts.
    """
    try:
        region = getattr(client, "_region", None)
    except Exception:
        return ("unknown", True)
    if type(region) is not RegionConfig:
        return ("unknown", False)
    try:
        scope = region.scope
    except Exception:
        return ("unknown", True)
    if type(scope) is str and scope in _REGION_SCOPES:
        return (scope, False)
    return ("unknown", False)


class _Scope(contextlib.AbstractContextManager[None]):
    """Internal scope emitting one started event and one terminal event.

    Tracks ``_had_child`` so a parent ``call``/``inspection`` scope knows a
    nested scope ran and must not relabel outer ValueError/TypeError as
    input validation. No exception objects or identities are stored.
    """

    __slots__ = (
        "_recorder",
        "_kind",
        "_entrypoint",
        "_operation",
        "_mode",
        "_region",
        "_attempt",
        "_span_id",
        "_parent_span_id",
        "_parent_scope",
        "_had_child",
        "_entered",
        "_token",
    )

    def __init__(
        self,
        recorder: DiagnosticRecorder,
        *,
        kind: str,
        entrypoint: str,
        operation: str,
        mode: str,
        region: str,
        attempt: int,
    ) -> None:
        self._recorder = recorder
        self._kind = kind
        self._entrypoint = entrypoint
        self._operation = operation
        self._mode = mode
        self._region = region
        self._attempt = attempt
        self._span_id = 0
        self._parent_span_id: int | None = None
        self._parent_scope: _Scope | None = None
        self._had_child = False
        self._entered = False
        self._token: Token[_Scope | None] | None = None

    def __enter__(self) -> None:
        recorder = self._recorder
        may_have_pushed = False
        try:
            parent = recorder._current_scope()
            if parent is not None:
                # Mark before pushing: over-marking is the safe direction and
                # only ever downgrades validation to unknown.
                parent._had_child = True
                self._parent_scope = parent
                self._parent_span_id = parent._span_id
            self._span_id = recorder._alloc_span()
            # The push may already have installed this scope if the call was
            # interrupted mid-flight; the except paths then restore the
            # pre-enter parent (None at root) so no later call sees this
            # never-started scope as parent.
            may_have_pushed = True
            self._token = recorder._push_scope(self)
            self._entered = True
            self._emit("started", "none", "none", "none", "none")
        except Exception:
            # Ordinary recorder fault: unwind any push first, then count, so
            # the with-body still runs untracked (fail-open).
            if may_have_pushed:
                self._abort_enter()
            recorder._note_recording_error()
        except BaseException:
            # Process control must propagate, but only after unwinding any
            # push this enter may already have installed; a fresh control
            # exception inside _abort_enter is itself never swallowed.
            if may_have_pushed:
                self._abort_enter()
            raise
        return None

    def _restore_context(self) -> None:
        # Best-effort reinstall of the scope that was current before this
        # enter (or None at the root). Only ordinary faults are swallowed;
        # a new process-control exception inside the restore still
        # propagates instead of being caught again.
        try:
            self._recorder._restore_scope(self._parent_scope)
        except Exception:
            pass

    def _abort_enter(self) -> None:
        # Unwind a failed or interrupted enter: drop the token and put the
        # pre-enter context back, so the with-body either runs untracked
        # (ordinary fault) or a propagating control exception does not
        # leave this never-started scope installed as current parent.
        self._token = None
        self._restore_context()
        self._entered = False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        recorder = self._recorder
        token = self._token
        self._token = None
        if token is not None:
            try:
                recorder._pop_scope(token)
            except Exception:
                self._restore_context()
                recorder._note_recording_error()
            except BaseException:
                # A stop instruction during pop: restore the context, then
                # let it propagate — losing the terminal event must not
                # shield the stop instruction.
                self._restore_context()
                raise
        if self._entered:
            try:
                if exc_value is None:
                    self._emit("succeeded", "none", "none", "none", "none")
                else:
                    outcome, family, reason, code_cat, http_cat, fault = _classify(
                        exc_value, self._kind, self._had_child
                    )
                    self._emit(outcome, family, reason, code_cat, http_cat)
                    if fault:
                        recorder._note_recording_error()
            except Exception:
                recorder._note_recording_error()
        # Never suppress or replace the caller's exception or traceback; a
        # process-control exception raised while recording propagates on its
        # own because only ordinary Exception is isolated above.
        return False

    def _emit(
        self,
        outcome: str,
        error_family: str,
        reason: str,
        code_category: str,
        http_status_category: str,
    ) -> None:
        self._recorder._append(
            {
                "sequence": 0,
                "span_id": self._span_id,
                "parent_span_id": self._parent_span_id,
                "kind": self._kind,
                "entrypoint": self._entrypoint,
                "operation": self._operation,
                "mode": self._mode,
                "region": self._region,
                "outcome": outcome,
                "error_family": error_family,
                "reason": reason,
                "code_category": code_category,
                "http_status_category": http_status_category,
                "attempt": self._attempt,
            }
        )


def _diagnostic_scope(
    client: object,
    *,
    entrypoint: str,
    mode: str,
    kind: str = "call",
    operation: object = None,
    attempt: int = 0,
) -> contextlib.AbstractContextManager[None]:
    """Return a started/terminal scope for one instrumented SDK stage.

    Side-effect-free no-op when ``client`` carries no exact
    ``DiagnosticRecorder`` or fields are missing; performs no validation of
    its own. Any failure during projection or scope construction degrades to
    ``nullcontext`` and is counted, never overriding the SDK call.
    """
    recorder = _recorder_of(client)
    if recorder is None:
        return contextlib.nullcontext()
    try:
        kind_v = _project_str(kind, _KINDS, "call")
        entrypoint_v = _project_str(entrypoint, ENTRYPOINTS, "other")
        operation_v, operation_fault = _operation_name(operation)
        region_v, region_fault = _region_scope_of(client)
        if operation_fault or region_fault:
            recorder._note_recording_error()
        if kind_v == "application":
            mode_v = "application"
        else:
            mode_v = _project_str(mode, _MODES, "sync")
        attempt_v = attempt if type(attempt) is int and attempt in _ATTEMPT_VALUES else 0
        return _Scope(
            recorder,
            kind=kind_v,
            entrypoint=entrypoint_v,
            operation=operation_v,
            mode=mode_v,
            region=region_v,
            attempt=attempt_v,
        )
    except Exception:
        recorder._note_recording_error()
        return contextlib.nullcontext()


def _diagnostic_retry(client: object, *, mode: str, operation: object) -> None:
    """Record a scheduled second attempt inside ``client.request``.

    Emitted only after the existing invalidate/retry decision already chose
    to retry; it never adds retries. No-op without a recorder, and any
    internal failure is counted instead of propagated.
    """
    recorder = _recorder_of(client)
    if recorder is None:
        return
    try:
        operation_v, operation_fault = _operation_name(operation)
        region_v, region_fault = _region_scope_of(client)
        if operation_fault or region_fault:
            recorder._note_recording_error()
        parent = recorder._current_span()
        span_id = recorder._alloc_span()
        recorder._append(
            {
                "sequence": 0,
                "span_id": span_id,
                "parent_span_id": parent,
                "kind": "retry",
                "entrypoint": _project_str("client.request", ENTRYPOINTS, "other"),
                "operation": operation_v,
                "mode": _project_str(mode, _MODES, "sync"),
                "region": region_v,
                "outcome": "scheduled",
                "error_family": "none",
                "reason": "none",
                "code_category": "none",
                "http_status_category": "none",
                "attempt": 2,
            }
        )
    except Exception:
        recorder._note_recording_error()
