"""Opt-in read-only acceptance runner for domestic account traffic reads.

Executes the six frozen ``traffic.*`` profiles (sync and async) through the
real SDK while a shared per-profile guard admits at most one auth POST
followed by one business POST. Emits a single sanitized JSON receipt:
tokens, payloads, credentials, and raw errors are never retained, echoed,
or written to disk.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable

import httpx

from ezviz_unified import AsyncClient, Client, RegionConfig
from tools.live._cli_transport import _AsyncGuardedTransport, _SyncGuardedTransport
from tools.live.verify_diagnostics import (
    _BoundaryRejection,
    _parsed_form_fields,
    _url_origin_ok,
)
from tools.live.verify_diagnostics import (
    _new_delegate_transport as _sync_delegate,
)
from tools.live.verify_diagnostics_async import (
    _new_delegate_transport as _async_delegate,
)
from tools.live.verify_diagnostics_async import (
    _read_credentials,
    _request_shape_ok,
)

_SCOPE = "domestic_account_traffic"
_TIMEOUT = 10
_MAX_ADMISSIONS = 2
_AUTH_KEYS = frozenset({"appKey", "appSecret"})
_SUMMARY_KEYS = frozenset({"accessToken"})
_DETAIL_KEYS = frozenset({"accessToken", "pageStart", "pageSize"})
_SUMMARY_OPERATION = "traffic.account_summary"

# Exact single business path per frozen operation; a name outside this
# allowlist is rejected before any request can be attempted.
_OPERATION_PATHS = {
    "traffic.account_summary": "/api/lapp/traffic/user/total",
    "traffic.account_details": "/api/lapp/traffic/user/detail",
    "traffic.day_details": "/api/lapp/traffic/day/detail",
}
_AUTH_PATH = "/api/lapp/token/get"

# Frozen order: account summary, account details, day details; sync then async.
_PROFILES = (
    ("traffic.account_summary", "sync", "object"),
    ("traffic.account_summary", "async", "object"),
    ("traffic.account_details", "sync", "array"),
    ("traffic.account_details", "async", "array"),
    ("traffic.day_details", "sync", "array"),
    ("traffic.day_details", "async", "array"),
)

_USAGE = "usage: python -m tools.live.verify_traffic_usage [--help] --prompt\n"
_REFUSED = "verify_traffic_usage: refused (interactive --prompt required)\n"
_ABORTED = "verify_traffic_usage: aborted\n"
_PASSED = "verify_traffic_usage: outcome passed\n"
_FAILED = "verify_traffic_usage: outcome failed\n"


class _Guard:
    """Admits only the two allowed routes, in order, for one operation name."""

    def __init__(
        self, name: str, app_key: str | None = None, app_secret: str | None = None
    ) -> None:
        if name not in _OPERATION_PATHS:
            raise ValueError("operation is not allowlisted")
        self.name = name
        self.spent = 0
        self.denied = False
        self._key = app_key
        self._secret = app_secret

    @staticmethod
    def _only(fields: dict[str, list[str]], key: str) -> str | None:
        values = fields.get(key)
        if isinstance(values, list) and len(values) == 1:
            return values[0]
        return None

    def _auth_ok(self, fields: dict[str, list[str]]) -> bool:
        if set(fields) != _AUTH_KEYS:
            return False
        key = self._only(fields, "appKey")
        secret = self._only(fields, "appSecret")
        if not key or not key.strip() or not secret or not secret.strip():
            return False
        if self._key is not None and key != self._key:
            return False
        if self._secret is not None and secret != self._secret:
            return False
        return True

    def _business_ok(self, fields: dict[str, list[str]]) -> bool:
        token = self._only(fields, "accessToken")
        if not token or not token.strip():
            return False
        if self.name == _SUMMARY_OPERATION:
            return set(fields) == _SUMMARY_KEYS
        return (
            set(fields) == _DETAIL_KEYS
            and self._only(fields, "pageStart") == "0"
            and self._only(fields, "pageSize") == "1"
        )

    def admit(self, request: httpx.Request) -> None:
        """Latch and raise on any violation; count admitted before delegate.

        The expected path is fixed by position — the auth route while
        nothing is spent, this profile's single business route afterwards —
        so an identical form on a sibling same-host path is refused. The
        cheap envelope checks run before the body is ever read: an
        unbuffered or streaming request fails ``_request_shape_ok`` and
        never reaches ``_parsed_form_fields``.
        """
        allowed = False
        if not self.denied and self.spent < _MAX_ADMISSIONS:
            expected_path = _AUTH_PATH if self.spent == 0 else _OPERATION_PATHS[self.name]
            try:
                if (
                    _request_shape_ok(request)
                    and _url_origin_ok(request.url, root_only=False)
                    and request.url.path == expected_path
                ):
                    fields = _parsed_form_fields(request)
                    allowed = isinstance(fields, dict) and (
                        self._auth_ok(fields) if self.spent == 0 else self._business_ok(fields)
                    )
            except Exception:
                allowed = False
        if not allowed:
            self.denied = True
            raise _BoundaryRejection("request outside admitted route")
        self.spent += 1


def _factory(guard: _Guard, transport, delegate):
    """Guarded transport_factory; validate the bare origin before delegating.

    ``origin`` arrives as a plain string from the client; it must be a
    string on a non-latched guard and normalize to the allowlisted root
    origin before any delegate transport is constructed.
    """

    def build(origin):
        if guard.denied:
            raise _BoundaryRejection("transport origin outside admitted route")
        ok = False
        if isinstance(origin, str):
            try:
                ok = bool(_url_origin_ok(httpx.URL(origin), root_only=True))
            except Exception:
                ok = False
        if not ok:
            guard.denied = True
            raise _BoundaryRejection("transport origin outside admitted route")
        return transport(guard, delegate())

    return build


def _call(traffic, operation: str):
    method = getattr(traffic, operation.rpartition(".")[2])
    if operation == _SUMMARY_OPERATION:
        return method()
    return method(page_start=0, page_size=1)


def _judge(guard: _Guard, expected: str, response) -> tuple[int, bool]:
    data = response.data
    right = isinstance(data, dict) if expected == "object" else isinstance(data, list)
    passed = right and not guard.denied and guard.spent == _MAX_ADMISSIONS
    return guard.spent, bool(passed)


def _run_sync(guard: _Guard, operation: str, expected: str, app_key: str, app_secret: str):
    try:
        auth = _SyncGuardedTransport(guard, _sync_delegate())
        with Client(
            app_key,
            app_secret,
            region=RegionConfig.china(),
            timeout=_TIMEOUT,
            auth_transport=auth,
            transport_factory=_factory(guard, _SyncGuardedTransport, _sync_delegate),
        ) as client:
            response = _call(client.traffic, operation)
        return _judge(guard, expected, response)
    except Exception:
        return guard.spent, False


async def _run_async(guard: _Guard, operation: str, expected: str, app_key: str, app_secret: str):
    try:
        auth = _AsyncGuardedTransport(guard, _async_delegate())
        async with AsyncClient(
            app_key,
            app_secret,
            region=RegionConfig.china(),
            timeout=_TIMEOUT,
            auth_transport=auth,
            transport_factory=_factory(guard, _AsyncGuardedTransport, _async_delegate),
        ) as client:
            response = await _call(client.traffic, operation)
        return _judge(guard, expected, response)
    except Exception:
        return guard.spent, False


def _run_async_profile(guard: _Guard, operation: str, expected: str, app_key: str, app_secret: str):
    # A failure after requests keeps the real admitted count — never an
    # invented zero; interrupts and cancellations propagate to ``main``.
    try:
        return asyncio.run(_run_async(guard, operation, expected, app_key, app_secret))
    except Exception:
        return guard.spent, False


def _row(operation: str, mode: str, status: str, http_requests: int, data_type):
    return {
        "operation": operation,
        "mode": mode,
        "status": status,
        "http_requests": int(http_requests),
        "data_type": data_type,
    }


def _interrupted_receipt(rows: list[dict], total: int, guard: _Guard, index: int) -> dict:
    """Assemble the redacted failed receipt for an in-flight interruption.

    Completed rows and their real admitted counts are kept verbatim; the
    interrupted profile records ``failed`` with this guard's honest
    ``spent`` and ``data_type`` ``None``; every remaining configured
    profile is ``skipped`` with zero requests. No exception, payload, or
    credential data is ever included.
    """
    profiles = list(rows)
    operation, mode, _expected = _PROFILES[index]
    profiles.append(_row(operation, mode, "failed", guard.spent, None))
    total += guard.spent
    for operation, mode, _expected in _PROFILES[index + 1 :]:
        profiles.append(_row(operation, mode, "skipped", 0, None))
    return {
        "schema_version": 1,
        "scope": _SCOPE,
        "outcome": "failed",
        "http_requests": total,
        "profiles": profiles,
    }


def _run(
    config: dict[str, str],
    on_interrupt: Callable[[dict], None] | None = None,
) -> dict:
    """Profile loop shared by ``verify``; interrupts may emit one receipt.

    When ``on_interrupt`` is provided and a ``KeyboardInterrupt`` or
    ``asyncio.CancelledError`` escapes a profile worker, the redacted
    failed receipt built from the honest counts is handed to the callback
    exactly once before the original interruption is re-raised. Without a
    callback the interruption propagates untouched and nothing is built.
    """
    app_key = config["app_key"]
    app_secret = config["app_secret"]
    rows = []
    total = 0
    halted = False
    for index, (operation, mode, expected) in enumerate(_PROFILES):
        if halted:
            rows.append(_row(operation, mode, "skipped", 0, None))
            continue
        guard = _Guard(operation, app_key, app_secret)
        try:
            if mode == "sync":
                spent, passed = _run_sync(guard, operation, expected, app_key, app_secret)
            else:
                spent, passed = _run_async_profile(guard, operation, expected, app_key, app_secret)
        except (KeyboardInterrupt, asyncio.CancelledError):
            if on_interrupt is not None:
                on_interrupt(_interrupted_receipt(rows, total, guard, index))
            raise
        total += spent
        rows.append(
            _row(
                operation,
                mode,
                "passed" if passed else "failed",
                spent,
                expected if passed else None,
            )
        )
        halted = halted or not passed
    return {
        "schema_version": 1,
        "scope": _SCOPE,
        "outcome": "failed" if halted else "passed",
        "http_requests": total,
        "profiles": rows,
    }


def verify(config: dict[str, str]) -> dict:
    """Run all frozen profiles in order; stop at the first failure.

    One fresh ``_Guard`` per profile keeps the admitted-request count
    honest even when a worker aborts mid-run — the receipt records the
    real ``guard.spent``, never an invented number. An interruption raised
    inside a profile propagates unchanged; only ``main`` asks ``_run`` to
    emit the redacted partial receipt on its way out.
    """
    return _run(config)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        sys.stdout.write(_USAGE)
        return 0
    if args != ["--prompt"]:
        sys.stderr.write(_REFUSED)
        return 2
    try:
        receipt = _run(
            _read_credentials(),
            on_interrupt=lambda payload: sys.stdout.write(json.dumps(payload) + "\n"),
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        # The redacted partial receipt (if a profile guard existed) was
        # already written by the callback; stderr gets only the fixed line.
        sys.stderr.write(_ABORTED)
        return 130
    except Exception:
        sys.stderr.write(_REFUSED)
        return 2
    sys.stdout.write(json.dumps(receipt) + "\n")
    passed = receipt.get("outcome") == "passed"
    sys.stderr.write(_PASSED if passed else _FAILED)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
