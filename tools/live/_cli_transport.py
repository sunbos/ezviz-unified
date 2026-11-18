"""Bounded guarded transports for the frozen example-CLI acceptance.

Maintenance helper under ``tools.live`` (not SDK runtime). ``MODULES``
pins the six profiles in frozen order; each ``ProfileGuard`` is shared
by that module's auth and business transports so one ordered
single-shot budget applies. Every request goes through
``guard.admit(request)`` before it may reach the real delegate, and
the first rejection latches forever.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from tools.live.verify_diagnostics import (
    _BoundaryRejection,
    _fields_allowed,
    _RequestBudget,
    _require_private_tls,
    _url_origin_ok,
)
from tools.live.verify_diagnostics import (
    _new_delegate_transport as _sync_delegate,
)
from tools.live.verify_diagnostics_async import (
    _ROUTE_LABELS,
    _ROUTE_ORDER,
    _request_shape_ok,
)
from tools.live.verify_diagnostics_async import (
    _new_delegate_transport as _async_delegate,
)

MODULES = (
    "examples.devices_sync",
    "examples.devices_async",
    "examples.inspection_sync",
    "examples.inspection_async",
    "examples.diagnostics_sync",
    "examples.diagnostics_async",
)
_LIST = _ROUTE_ORDER[:2]
_DEVICE = (_ROUTE_ORDER[0],) + _ROUTE_ORDER[2:]
_PROFILE_ROUTES = {m: _DEVICE if ".inspection_" in m else _LIST for m in MODULES}
_TIMEOUT = 10


class ProfileGuard:
    """Ordered single-shot admission for one frozen profile.

    ``labels`` records each admitted route just ahead of the delegate;
    ``rejected`` latches on the first refusal; ``complete`` is true once
    every expected route was admitted exactly once, in order. ``serial``
    binds the device only for inspection modules — without it every
    device-scoped field check fails before delegating.
    """

    def __init__(self, module: str, serial: str | None = None) -> None:
        if module not in _PROFILE_ROUTES:
            raise ValueError("unsupported example module")
        self.module = module
        self.labels: list[str] = []
        self._routes = _PROFILE_ROUTES[module]
        self._budget = _RequestBudget()
        self._rejected = False
        self._async = module.endswith("_async")
        if serial is not None and ".inspection_" in module:
            self._budget.bind_device(serial)

    @property
    def rejected(self) -> bool:
        return self._rejected

    @property
    def complete(self) -> bool:
        return not self._rejected and len(self.labels) == len(self._routes)

    def client_kwargs(self) -> dict[str, Any]:
        factory = self._build_async if self._async else self._build_sync
        return {
            "auth_transport": self._wrap(),
            "transport_factory": factory,
            "timeout": _TIMEOUT,
        }

    def admit(self, request: httpx.Request) -> None:
        """Admit one request or raise ``_BoundaryRejection``; latch on refusal."""
        try:
            self._check(request)
        except _BoundaryRejection:
            self._rejected = True
            raise
        except Exception:
            # Malformed request shapes (e.g. UnicodeEncodeError while
            # inspecting fields) must also latch and reject.
            self._rejected = True
            raise _BoundaryRejection("request could not be admitted")

    def _check(self, request: httpx.Request) -> None:
        if self._rejected:
            raise _BoundaryRejection("guard latched rejected")
        route = (request.method, request.url.path)
        if not _request_shape_ok(request):
            raise _BoundaryRejection("request shape not allowlisted")
        if not _url_origin_ok(request.url, root_only=False):
            raise _BoundaryRejection("non-domestic origin")
        if route not in _ROUTE_LABELS:
            raise _BoundaryRejection("route not allowlisted")
        position = len(self.labels)
        if position >= len(self._routes) or route != self._routes[position]:
            raise _BoundaryRejection("route out of order")
        if not _fields_allowed(route, request, self._budget):
            raise _BoundaryRejection("form fields not allowlisted")
        if not self._budget.admit(route):
            raise _BoundaryRejection("request budget exhausted")
        self.labels.append(_ROUTE_LABELS[route])

    def _wrap(self) -> httpx.BaseTransport | httpx.AsyncBaseTransport:
        """Auth transport through the same guarded constructor path."""
        return self._guarded()

    def _check_origin(self, origin: str) -> None:
        """transport_factory gate: only an allowlisted root origin may spawn a delegate."""
        if self._rejected:
            raise _BoundaryRejection("guard latched rejected")
        if not isinstance(origin, str) or not origin:
            self._rejected = True
            raise _BoundaryRejection("transport origin not a non-empty str")
        try:
            url = httpx.URL(origin)
        except Exception:
            self._rejected = True
            raise _BoundaryRejection("transport origin not a valid URL")
        if not _url_origin_ok(url, root_only=True):
            self._rejected = True
            raise _BoundaryRejection("non-domestic transport origin")

    def _guarded(self) -> httpx.BaseTransport | httpx.AsyncBaseTransport:
        """The one delegate-construction path for auth and business transports.

        A latched guard never delegates again; an armed ``SSLKEYLOGFILE``
        latches the guard and refuses before any real transport is built.
        """
        if self._rejected:
            raise _BoundaryRejection("guard latched rejected")
        try:
            _require_private_tls()
        except _BoundaryRejection:
            self._rejected = True
            raise
        if self._async:
            return _AsyncGuardedTransport(self, _async_delegate())
        return _SyncGuardedTransport(self, _sync_delegate())

    def _build_sync(self, origin: str) -> httpx.BaseTransport:
        self._check_origin(origin)
        return self._guarded()

    def _build_async(self, origin: str) -> httpx.AsyncBaseTransport:
        self._check_origin(origin)
        return self._guarded()


class _RequestGuard(Protocol):
    def admit(self, request: httpx.Request) -> None: ...


class _SyncGuardedTransport(httpx.BaseTransport):
    """Admit through the shared guard, then delegate; nothing else."""

    def __init__(self, guard: _RequestGuard, delegate: httpx.BaseTransport) -> None:
        self._guard = guard
        self._delegate = delegate

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._guard.admit(request)
        return self._delegate.handle_request(request)

    def close(self) -> None:
        self._delegate.close()


class _AsyncGuardedTransport(httpx.AsyncBaseTransport):
    """Admit through the shared guard, then delegate; nothing else."""

    def __init__(self, guard: _RequestGuard, delegate: httpx.AsyncBaseTransport) -> None:
        self._guard = guard
        self._delegate = delegate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._guard.admit(request)
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self._delegate.aclose()
