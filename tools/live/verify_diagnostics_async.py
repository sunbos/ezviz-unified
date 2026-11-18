"""Explicit one-shot async read-only diagnostic acceptance for one account.

Standalone narrow live tool, not an SDK service. It authenticates once,
reads the first authorized ordinary device from a single ``devices.list``
call (strict ``pageStart=0`` / ``pageSize=1``), binds that device serial
in memory, then runs the fixed five-call ``inspect_device_async``
sequence on channel 1. Authentication and business requests share one
origin (``https://open.ys7.com``, default port only), an allowlist of
seven method/path routes each with an exact urlencoded field contract, a
fixed admission order, and a shared HTTP budget (at most seven requests,
at most one per route); anything outside that boundary is rejected
before it is sent. The tool returns only a bounded allowlisted report —
never credentials, device identifiers, raw objects, or free-form error
text.

Live use is only via an explicit interactive local ``--prompt`` run:

    python -m tools.live.verify_diagnostics_async --prompt [--output NEW_JSON_FILE]

Both secrets are read with hidden ``getpass`` from a real TTY; nothing
is taken from argv, stdin, files, or the environment. ``--output`` must
name a file that does not exist yet.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import stat
import sys
import warnings
from collections.abc import Callable

import httpx

from ezviz_unified import AsyncClient, RegionConfig
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.inspection import inspect_device_async
from tools.live.verify_diagnostics import (
    _ALLOWED_ROUTES,
    _FORM_CONTENT_TYPE,
    _BoundaryRejection,
    _credentials,
    _fields_allowed,
    _RequestBudget,
    _url_origin_ok,
)

_ROUTE_ORDER = (
    ("POST", "/api/lapp/token/get"),
    ("POST", "/api/lapp/device/list"),
    ("POST", "/api/lapp/device/info"),
    ("POST", "/api/lapp/device/camera/list"),
    ("POST", "/api/lapp/device/status/get"),
    ("POST", "/api/lapp/device/capacity"),
    ("POST", "/api/lapp/device/version/info"),
)
_ROUTE_LABELS = {
    ("POST", "/api/lapp/token/get"): "auth",
    ("POST", "/api/lapp/device/list"): "list",
    ("POST", "/api/lapp/device/info"): "info",
    ("POST", "/api/lapp/device/camera/list"): "channels",
    ("POST", "/api/lapp/device/status/get"): "status",
    ("POST", "/api/lapp/device/capacity"): "capabilities",
    ("POST", "/api/lapp/device/version/info"): "version",
}
_STEP_LABELS = (
    ("devices.list", "list"),
    ("devices.get", "info"),
    ("devices.channels", "channels"),
    ("devices.status", "status"),
    ("diagnostics.capabilities", "capabilities"),
    ("diagnostics.version", "version"),
)
_TERMINAL_OUTCOMES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
_VALID_OUTCOMES = frozenset({"passed", "failed", "not_verified"})
_EXPECTED_REPORT_SCOPE = "local_sdk_diagnostics"
_REPORT_SCHEMA = 1
_REPORT_SCOPE = "domestic_async_device_inspection"
_OVERALL_TIMEOUT_SECONDS = 90
_FATAL_MESSAGE = "verification stopped\n"
_ALLOWED_HEADER_HOSTS = frozenset({"open.ys7.com", "open.ys7.com:443"})
_FORBIDDEN_HEADERS = ("Authorization", "Proxy-Authorization", "accessToken")
_MAX_GUARD_BODY_BYTES = 8192
_ROUTE_SEQUENCE = tuple(label for _route, label in _ROUTE_LABELS.items())
_RESULT_KEYS = (
    "schema_version",
    "scope",
    "outcome",
    "mode",
    "read_only",
    "http_requests",
    "logical_calls",
    "attempted_routes",
    "successful_steps",
    "diagnostics_intact",
    "closed",
    "boundary_rejected",
)
_STEP_LABEL_LIST = tuple(label for _entrypoint, label in _STEP_LABELS)


def _new_delegate_transport() -> httpx.AsyncBaseTransport:
    """Real one-shot async delegate; tests may monkeypatch this factory."""
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False)


def _header_values(request: httpx.Request, name: str) -> list[str] | None:
    """All copies of one header; ``None`` means the guard must fail closed."""
    try:
        return request.headers.get_list(name)
    except Exception:
        return None


def _request_shape_ok(request: httpx.Request) -> bool:
    """Async-only envelope checks beyond the shared form/origin contract.

    Fail-closed: forwarded authorization/proxy/token headers, a duplicate
    or non-exact form Content-Type, a misleading Host header, transfer
    encoding, and any body the SDK did not already buffer (live
    streaming/chunked bodies are never consumed by this guard) all reject
    the request before it reaches the delegate.
    """
    if request.method != "POST":
        return False
    url = request.url
    port = url.port if url.port is not None else 443
    if url.scheme != "https" or url.host != "open.ys7.com" or port != 443:
        return False
    path = url.path or "/"
    try:
        raw_path = url.raw_path
    except Exception:
        return False
    if not isinstance(raw_path, bytes) or raw_path != path.encode("ascii"):
        return False
    for forbidden in _FORBIDDEN_HEADERS:
        values = _header_values(request, forbidden)
        # Fail closed: an unreadable or present forbidden header rejects.
        if values is None or values:
            return False
    content_types = _header_values(request, "Content-Type")
    if content_types is None or len(content_types) != 1 or content_types[0] != _FORM_CONTENT_TYPE:
        return False
    hosts = _header_values(request, "Host")
    if hosts is None or len(hosts) > 1 or (hosts and hosts[0] not in _ALLOWED_HEADER_HOSTS):
        return False
    # Any present Transfer-Encoding header — even an empty value — rejects.
    if _header_values(request, "Transfer-Encoding") or "Transfer-Encoding" in request.headers:
        return False
    try:
        body = request.content
    except Exception:
        return False
    return isinstance(body, (bytes, bytearray)) and 0 < len(body) <= _MAX_GUARD_BODY_BYTES


class _GuardedAsyncTransport(httpx.AsyncBaseTransport):
    """Admits only the fixed ordered domestic reads inside the shared budget.

    ``trace`` records the fixed route label of each admitted request in
    order; a rejection increments the shared budget's ``rejected`` counter
    and raises :class:`_BoundaryRejection` before the delegate ever runs.
    The guard never reads an unbuffered live body and never retries.
    """

    def __init__(
        self,
        delegate: httpx.AsyncBaseTransport,
        budget: _RequestBudget,
        trace: list[str],
    ) -> None:
        # The order cursor must be a genuine shared list: nothing else can
        # express the fixed admission order, so fail at construction.
        if type(trace) is not list:
            raise ValueError("trace must be the shared ordered list")
        self._delegate = delegate
        self._budget = budget
        self._trace = trace

    def _order_ok(self, route: tuple[str, str]) -> bool:
        # The shared ordered trace is the sole admission cursor across the
        # auth and business transports. Its length must mirror the budget's
        # admitted total exactly — any divergence means the cursor can no
        # longer prove the fixed order, so the request is refused.
        if len(self._trace) != self._budget.total:
            return False
        position = len(self._trace)
        return position < len(_ROUTE_ORDER) and route == _ROUTE_ORDER[position]

    def _reject(self) -> None:
        self._budget.rejected += 1
        raise _BoundaryRejection("request is outside the diagnostic acceptance boundary")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        route = (request.method, request.url.path)
        if (
            route not in _ALLOWED_ROUTES
            or not self._order_ok(route)
            or not _request_shape_ok(request)
            or not _url_origin_ok(request.url, root_only=False)
            or not _fields_allowed(route, request, self._budget)
        ):
            self._reject()
        # ``admit`` already counts its own refusal once; a rejected repeat
        # route must still total exactly one rejection, never two.
        if not self._budget.admit(route):
            raise _BoundaryRejection("request is outside the diagnostic acceptance boundary")
        self._trace.append(_ROUTE_LABELS[route])
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self._delegate.aclose()


def _guarded_factory(
    budget: _RequestBudget, trace: list[str]
) -> Callable[[str], httpx.AsyncBaseTransport]:
    def factory(origin: str) -> httpx.AsyncBaseTransport:
        if not _url_origin_ok(httpx.URL(origin), root_only=True):
            # An out-of-boundary origin is a boundary rejection too: it must
            # surface in the report as a refused attempt, not a silent abort.
            budget.rejected += 1
            raise _BoundaryRejection("origin is outside the diagnostic acceptance boundary")
        return _GuardedAsyncTransport(_new_delegate_transport(), budget, trace)

    return factory


def _business_terminal_events(report: object) -> dict[str, list[str]] | None:
    """Observed terminal outcomes per one of the six business entrypoints.

    Independent of any other completeness condition: every terminal
    ``call`` event whose ``entrypoint`` is one of the six logical business
    steps (and only those — never ``client.request``) is collected in
    order, including failed/cancelled outcomes. ``None`` is returned only
    when the snapshot itself is unusable (bad schema, non-dict events); a
    partially recorded run still yields the steps it actually observed
    rather than an invented zero.
    """
    if not isinstance(report, dict):
        return None
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        return None
    if type(report.get("scope")) is not str or report["scope"] != _EXPECTED_REPORT_SCOPE:
        return None
    events = report.get("events")
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        return None
    allowed = {entrypoint for entrypoint, _label in _STEP_LABELS}
    terminals: dict[str, list[str]] = {}
    for event in events:
        if event.get("kind") != "call":
            continue
        entrypoint = event.get("entrypoint")
        outcome = event.get("outcome")
        if (
            isinstance(entrypoint, str)
            and entrypoint in allowed
            and event.get("mode") == "async"
            and event.get("region") == "domestic"
            and isinstance(outcome, str)
            and outcome in _TERMINAL_OUTCOMES
        ):
            terminals.setdefault(entrypoint, []).append(outcome)
    return terminals


def _report_projection(report: object) -> dict[str, object] | None:
    """Project the recorder snapshot onto the fixed verification facts.

    ``None`` means the snapshot is compromised and a ``passed`` claim must
    be refused. A usable projection carries ``steps`` — the observed
    terminal outcome per business entrypoint — plus ``clean``: whether the
    run produced exactly one terminal per observed step, a single
    ``inspect_device_async`` inspection and a single ``client.aclose``
    close with clean counters and no foreign-mode/region call terminals,
    and ``corrupted``: whether the snapshot carries proof of tampering
    (unknown business terminals, duplicate or out-of-order steps, corrupt
    inspection/close terminals, bad counters) that must fail the run
    outright. Complete ``passed`` proof is enforced by the caller; this
    helper never fabricates a count or upgrades a partial run.
    """
    terminal_map = _business_terminal_events(report)
    if terminal_map is None:
        return None
    assert isinstance(report, dict)
    steps = {ep: outcomes[-1] for ep, outcomes in terminal_map.items()}
    clean = all(len(outcomes) == 1 for outcomes in terminal_map.values())
    events = report["events"]
    counters_ok = True
    for counter in ("recording_errors", "dropped_events"):
        value = report.get(counter)
        if type(value) is not int or value != 0:
            counters_ok = False
    inspections = [
        event
        for event in events
        if event.get("kind") == "inspection"
        and event.get("entrypoint") == "inspect_device_async"
        and event.get("mode") == "async"
        and event.get("region") == "domestic"
        and event.get("outcome") in _TERMINAL_OUTCOMES
    ]
    closes = [
        event
        for event in events
        if event.get("kind") == "close"
        and event.get("entrypoint") == "client.aclose"
        and event.get("mode") == "async"
        and event.get("region") == "domestic"
        and event.get("outcome") in _TERMINAL_OUTCOMES
    ]
    # Beyond the usable per-step terminals, the raw call events must not
    # carry corruption the filtered map would hide: a business entrypoint
    # terminal with an unknown or non-str outcome, a wrong mode/region, a
    # duplicated step, an out-of-order step, or a terminal ``call`` event
    # for any entrypoint that is neither a business step nor the inner
    # ``client.request`` scope. Inner ``client.request`` terminals are
    # allowed any outcome — they are transport plumbing, not steps. A
    # legitimate non-terminal ``started`` outcome is allowed verbatim only
    # on a business entrypoint or ``client.request`` with the correct
    # mode/region; it is never counted as a step terminal and never
    # corrupts the snapshot. Any other non-standard outcome or foreign
    # mode/region on a started event still fails. Such corruption is
    # distinct from an honest partial run: it downgrades the outcome to
    # ``failed`` outright rather than only blocking ``passed``.
    allowed = {entrypoint for entrypoint, _label in _STEP_LABELS}
    order_index = {entrypoint: index for index, (entrypoint, _l) in enumerate(_STEP_LABELS)}
    seen_order: list[int] = []
    corrupted = False
    for event in events:
        if event.get("kind") != "call":
            continue
        entrypoint = event.get("entrypoint")
        outcome = event.get("outcome")
        if outcome == "started":
            # A scope-open marker is legitimate only inside the run: known
            # business entrypoint or client.request, domestic async mode.
            if (
                not isinstance(entrypoint, str)
                or (entrypoint not in allowed and entrypoint != "client.request")
                or event.get("mode") != "async"
                or event.get("region") != "domestic"
            ):
                corrupted = True
            continue
        if entrypoint == "client.request":
            continue
        if not isinstance(entrypoint, str) or entrypoint not in allowed:
            if isinstance(outcome, str) and outcome in _TERMINAL_OUTCOMES:
                corrupted = True
            continue
        if not isinstance(outcome, str) or outcome not in _TERMINAL_OUTCOMES:
            corrupted = True
            continue
        if event.get("mode") != "async" or event.get("region") != "domestic":
            corrupted = True
            continue
        seen_order.append(order_index[entrypoint])
    if seen_order != sorted(seen_order) or any(
        len(outcomes) != 1 for outcomes in terminal_map.values()
    ):
        corrupted = True
    # A terminal inspection/close event with a corrupt shape (foreign
    # mode/region or unknown outcome) is corruption too; a legitimate
    # ``started`` marker with the correct shape is skipped outright, and
    # only the event's total absence can still be an honest partial run.
    for kind, entrypoint in (("inspection", "inspect_device_async"), ("close", "client.aclose")):
        for event in events:
            if event.get("kind") != kind or event.get("entrypoint") != entrypoint:
                continue
            outcome = event.get("outcome")
            if outcome == "started":
                if event.get("mode") != "async" or event.get("region") != "domestic":
                    corrupted = True
                continue
            if (
                event.get("mode") != "async"
                or event.get("region") != "domestic"
                or not isinstance(outcome, str)
                or outcome not in _TERMINAL_OUTCOMES
            ):
                corrupted = True
    if not counters_ok or len(inspections) > 1 or len(closes) > 1:
        corrupted = True
    clean = (
        not corrupted
        and len(inspections) == 1
        and len(closes) == 1
        and all(len(outcomes) == 1 for outcomes in terminal_map.values())
    )
    return {
        "steps": steps,
        "clean": clean,
        "corrupted": corrupted,
        "inspection_outcome": inspections[0]["outcome"] if inspections else None,
        "close_outcome": closes[0]["outcome"] if closes else None,
    }


async def verify(config: dict[str, str]) -> dict[str, object]:
    """Run the bounded one-shot async check; the result stays allowlisted.

    Returns the finite field projection only — never the raw diagnostic
    report, responses, identifiers, or error text. Any failure, rejection,
    malformed snapshot, or close fault ends the run as ``failed``; an
    empty account or a missing first serial yields ``not_verified``; a
    cancellation propagates and is never converted into ``passed``.
    """
    app_key, app_secret = _credentials(config)
    recorder = DiagnosticRecorder()
    budget = _RequestBudget()
    trace: list[str] = []
    closed = False
    outcome = "failed"
    client: AsyncClient | None = None
    try:
        client = AsyncClient(
            app_key,
            app_secret,
            region=RegionConfig.china(),
            timeout=10,
            auth_transport=_GuardedAsyncTransport(_new_delegate_transport(), budget, trace),
            transport_factory=_guarded_factory(budget, trace),
            diagnostic_recorder=recorder,
        )
        response = await client.devices.list(page_start=0, page_size=1)
        rows = response.data
        if not isinstance(rows, list):
            outcome = "failed"
        elif not rows:
            outcome = "not_verified"
        else:
            first = rows[0]
            serial = first.get("deviceSerial") if isinstance(first, dict) else None
            if not isinstance(serial, str) or not serial.strip():
                outcome = "not_verified"
            else:
                budget.bind_device(serial)
                inspection = await inspect_device_async(client, serial, channel=1)
                results = inspection.results
                outcome = (
                    "passed"
                    if len(results) == 5 and all(item.succeeded for item in results)
                    else "failed"
                )
    except Exception:
        outcome = "failed"
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                outcome = "failed"
            else:
                closed = True
    try:
        report = recorder.report()
    except Exception:
        report = None
        outcome = "failed"
    projection = _report_projection(report)
    if projection is None:
        # Compromised snapshot: keep only the honestly observed business
        # terminals (never an invented count) and refuse any passed claim.
        outcome = "failed"
        raw = _business_terminal_events(report)
        steps = {ep: outcomes[-1] for ep, outcomes in raw.items()} if raw else {}
        clean = False
        inspection_terminal = None
        close_terminal = None
    else:
        steps = projection["steps"]
        clean = bool(projection["clean"])
        inspection_terminal = projection["inspection_outcome"]
        close_terminal = projection["close_outcome"]
        if projection["corrupted"]:
            # Corrupt statistics or terminals can never masquerade as a
            # clean partial run — a corrupted snapshot fails outright.
            outcome = "failed"
    # ``successful_steps`` lists every fixed-sequence step that actually
    # recorded a real ``succeeded`` terminal, in frozen order. ``_collect``
    # keeps running after an ordinary per-step ApiError, so the list may
    # contain gaps; it never stops at the first failure and never invents
    # a success.
    successful_steps = [
        label for entrypoint, label in _STEP_LABELS if steps.get(entrypoint) == "succeeded"
    ]
    diagnostics_intact = clean
    if outcome == "passed" and (
        not diagnostics_intact
        or inspection_terminal != "succeeded"
        or close_terminal != "succeeded"
        or len(steps) != 6
        or any(steps.get(entrypoint) != "succeeded" for entrypoint, _l in _STEP_LABELS)
        or successful_steps != list(_STEP_LABEL_LIST)
        or trace != list(_ROUTE_SEQUENCE)
        or budget.total != 7
        or budget.rejected != 0
        or not closed
    ):
        outcome = "failed"
    return {
        "schema_version": _REPORT_SCHEMA,
        "scope": _REPORT_SCOPE,
        "outcome": outcome if outcome in _VALID_OUTCOMES else "failed",
        "mode": "async",
        "read_only": True,
        "http_requests": budget.total,
        "logical_calls": sum(1 for value in steps.values() if value in _TERMINAL_OUTCOMES),
        "attempted_routes": list(trace),
        "successful_steps": successful_steps,
        "diagnostics_intact": diagnostics_intact,
        "closed": closed,
        "boundary_rejected": budget.rejected > 0,
    }


def _report_allowed(result: object) -> bool:
    """Strict allowlist proof for the projection about to be serialized.

    Only the twelve fixed keys (exact set, no extras or missing), the
    fixed scope/mode/schema, bounded non-bool ints, deduplicated
    fixed-label lists, and internally consistent outcome/route/success
    bookkeeping are ever serialized. A ``passed`` payload must prove the full seven-route /
    six-step success with intact diagnostics and a real close and no
    boundary rejection; a partial ``failed``/``not_verified`` payload may
    keep its honest counts but can never masquerade as ``passed``.
    """
    if (
        not isinstance(result, dict)
        or len(result) != len(_RESULT_KEYS)
        or set(result.keys()) != set(_RESULT_KEYS)
    ):
        return False
    if type(result["schema_version"]) is not int or result["schema_version"] != _REPORT_SCHEMA:
        return False
    if result["scope"] != _REPORT_SCOPE or result["mode"] != "async":
        return False
    if result["outcome"] not in _VALID_OUTCOMES:
        return False
    if result["read_only"] is not True:
        return False
    for key, upper in (("http_requests", 7), ("logical_calls", 6)):
        value = result[key]
        if type(value) is not int or not 0 <= value <= upper:
            return False
    attempted = result["attempted_routes"]
    # Elements are compared with ``type() is str`` first so a non-str or
    # unhashable forged entry can never raise or slip through set().
    if not isinstance(attempted, list) or any(type(v) is not str for v in attempted):
        return False
    if len(attempted) != len(set(attempted)):
        return False
    if any(label not in _ROUTE_LABELS.values() for label in attempted):
        return False
    # The route counter is exactly the number of admitted routes, and a
    # real trace is always a strict prefix of the fixed sequence.
    if result["http_requests"] != len(attempted):
        return False
    if attempted != list(_ROUTE_SEQUENCE[: len(attempted)]):
        return False
    successful = result["successful_steps"]
    if not isinstance(successful, list) or any(type(v) is not str for v in successful):
        return False
    if len(successful) != len(set(successful)):
        return False
    if any(label not in _STEP_LABEL_LIST for label in successful):
        return False
    # Successful steps must be an ordered subsequence of the fixed step
    # sequence and may only name steps whose route was actually attempted.
    position = 0
    for label in successful:
        try:
            position = _STEP_LABEL_LIST.index(label, position) + 1
        except ValueError:
            return False
    step_routes = {"list", "info", "channels", "status", "capabilities", "version"}
    if any(label in step_routes and label not in attempted for label in successful):
        return False
    if len(successful) > result["logical_calls"]:
        return False
    for key in ("diagnostics_intact", "closed", "boundary_rejected"):
        if type(result[key]) is not bool:
            return False
    if result["outcome"] == "passed":
        return (
            attempted == list(_ROUTE_SEQUENCE)
            and successful == list(_STEP_LABEL_LIST)
            and result["http_requests"] == 7
            and result["logical_calls"] == 6
            and result["diagnostics_intact"] is True
            and result["closed"] is True
            and result["boundary_rejected"] is False
        )
    if result["outcome"] == "not_verified":
        # Only an honest safe stop may claim not_verified: the client was
        # closed cleanly, exactly the auth+list routes ran, the list step
        # succeeded, and nothing was refused at the boundary.
        return (
            attempted == ["auth", "list"]
            and result["http_requests"] == 2
            and result["logical_calls"] == 1
            and successful == ["list"]
            and result["closed"] is True
            and result["boundary_rejected"] is False
        )
    return True


def _unlink_if_same(path: str, owner: int) -> None:
    """Remove ``path`` only while it still names the inode we hold open.

    ``owner`` is the still-open descriptor ``_open_output_exclusive``
    returned; ``fstat`` on it names our inode even after the path was
    swapped, and keeping it open until this point means a freed inode
    number can never be recycled onto a replacement we would then delete.
    ``lstat`` is used deliberately: if the path was replaced by a symlink
    to our inode (or to anything else) after we opened it, the link itself
    is a different inode and is left untouched — never the target. The
    compare runs immediately before the unlink; it narrows but cannot
    remove the check/unlink gap on POSIX.
    """
    try:
        held = os.fstat(owner)
    except OSError:
        # The owner is not verifiable: never touch the path.
        return
    try:
        current = os.lstat(path)
    except OSError:
        return
    if (current.st_dev, current.st_ino) == (held.st_dev, held.st_ino):
        try:
            os.unlink(path)
        except OSError:
            pass


def _open_output_exclusive(path: str):
    """Create ``path`` as a brand-new 0600 regular file; never overwrite.

    Returns ``(write_stream, owner_fd)`` on two distinct live descriptors:
    the stream owns ``fd`` (fdopen default ``closefd=True``), while
    ``owner_fd`` is a separate ``os.dup`` of the verified descriptor that
    pins our inode for ``_unlink_if_same`` even after the stream closes.
    A failed ``fstat`` or rejected shape leaves the path in place rather
    than risking removal of a swapped-in foreign file.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
    except BaseException:
        # Identity was never confirmed: keep the (possibly swapped) path.
        os.close(fd)
        raise
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(fd)
        raise ValueError("output must be a new regular file")
    try:
        owner = os.dup(fd)
    except BaseException:
        _unlink_if_same(path, fd)
        os.close(fd)
        raise
    try:
        stream = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        # The owner still pins our inode: compare-and-unlink while it is
        # valid, then release both descriptors exactly once.
        _unlink_if_same(path, owner)
        try:
            os.close(owner)
        finally:
            os.close(fd)
        raise
    return stream, owner


def _read_credentials() -> dict[str, str]:
    """Hidden interactive prompts only; aborts before any networking."""
    # Interactive input only: getpass itself raises GetPassWarning (treated
    # as an error) if it must fall back to visible input, so a real TTY for
    # stdin is the hard precondition.
    if not sys.stdin.isatty():
        raise ValueError("interactive terminal required")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        app_key = getpass.getpass("AppKey: ")
        app_secret = getpass.getpass("AppSecret: ")
    # Reject blank input but keep the entered secret verbatim — stripping
    # would silently change a credential whose whitespace may be meaningful.
    if not app_key.strip() or not app_secret.strip():
        raise ValueError("credentials must be non-empty")
    return {"app_key": app_key, "app_secret": app_secret}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    class _Parser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            # Never echo a mistyped flag (which may be a pasted secret) back
            # to stderr; help/usage stays available via ``--help``.
            raise ValueError("verification stopped")

    parser = _Parser(
        prog="verify_diagnostics_async",
        description=(
            "One-shot interactive read-only async diagnostic acceptance for one "
            "domestic account. --prompt reads both secrets with hidden getpass; "
            "--output names a file that must not exist yet."
        ),
    )
    parser.add_argument(
        "--prompt",
        action="store_true",
        help="read app_key/app_secret interactively with hidden getpass",
    )
    parser.add_argument(
        "--output",
        metavar="NEW_JSON_FILE",
        default=None,
        help="write the allowlisted report to a file that does not exist yet",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: only ``--prompt`` performs I/O; errors print fixed text."""
    try:
        args = _parse_args(argv)
    except SystemExit:
        # ``--help`` (and only that) reaches stdout normally.
        raise
    except BaseException:
        sys.stderr.write(_FATAL_MESSAGE)
        return 1
    if not args.prompt:
        # Missing --prompt is a usage error (not --help): fixed message,
        # explicit non-zero, never a raw echo of argv.
        sys.stderr.write(_FATAL_MESSAGE)
        return 1
    stream = None
    written = False
    output_fd: int | None = None
    if args.output is not None:
        try:
            stream, output_fd = _open_output_exclusive(args.output)
        except BaseException:
            sys.stderr.write(_FATAL_MESSAGE)
            return 1
    config: dict[str, str] | None = None
    close_error = False
    exit_code = 1
    try:
        config = _read_credentials()
        try:
            result = asyncio.run(asyncio.wait_for(verify(config), timeout=_OVERALL_TIMEOUT_SECONDS))
        except asyncio.TimeoutError:
            raise ValueError("verification timed out") from None
        if not _report_allowed(result):
            sys.stderr.write(_FATAL_MESSAGE)
            return 1
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if stream is None:
            sys.stdout.write(payload)
            written = True
        else:
            # write/flush/close each run inside the guarded block: any
            # flush or close fault propagates to the fixed error exit and
            # ``written`` stays False so the fresh artifact is removed —
            # a partial file is never mistaken for a completed report.
            stream.write(payload)
            stream.flush()
            try:
                stream.close()
            except BaseException:
                close_error = True
            stream = None
            if close_error:
                raise ValueError("verification stopped") from None
            written = True
        exit_code = 0 if result["outcome"] == "passed" else 1
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        sys.stderr.write(_FATAL_MESSAGE)
        return 1
    finally:
        if config is not None:
            config.clear()
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if output_fd is not None:
            # The distinct owner descriptor stayed open the whole run, so
            # its inode was never freed for a swapped-in replacement to
            # inherit — independent of when the stream closed its own fd.
            # On an unfinished run, compare the live owner against the path
            # right before unlinking — a file the user put in our place is
            # left untouched — then release the owner exactly once.
            try:
                if not written and args.output is not None:
                    _unlink_if_same(args.output, output_fd)
            finally:
                try:
                    os.close(output_fd)
                except OSError:
                    # A failed owner release must not leave the run
                    # reporting success; only an otherwise-clean run gets
                    # the fixed error line, and the close is never retried.
                    if exit_code == 0:
                        sys.stderr.write(_FATAL_MESSAGE)
                    exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
