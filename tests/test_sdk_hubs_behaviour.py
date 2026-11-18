"""Phase A+B+C acceptance for the domestic HUB facades (docs 1356/1357/1358).

Phase A covers the success wire shape, typed error mapping, single and
capped ``10002`` token recovery, and recorder-off/on wire parity.

Phase B covers argument validation before any I/O (region rejection
ordered ahead of argument checks, lowercase-ASCII/blank serials and a
non-str ``version``; non-ASCII UTF-8 serials stay accepted), provider
failure identity, request-boundary identity through the real client
lifecycle, calls on a closed client, and real async cancellation.

Phase C covers privacy of sent values in errors/logs/diagnostics,
verbatim ``ApiResponse`` payload retention on success, and the
``deviceSerial='10002'`` business-code collision, which must still be
treated as invalid-token with the documented invalidate/replay bounds.
"""

from __future__ import annotations

import asyncio
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
    DiagnosticRecorder,
    RegionConfig,
    Token,
    TransportError,
)

ORIGIN = "https://open.ys7.com"
PARENT, CHILD = "PRIVATE_HUB_123", "PRIVATE_SUB_456"
METHODS = ("list_subdevices", "subdevice_info", "channels")
_OPS = {
    "list_subdevices": ((PARENT,), {"version": ""}, "/api/lapp/hub/device/sub/list"),
    "subdevice_info": ((CHILD,), {}, "/api/lapp/hub/device/sub/info"),
    "channels": ((CHILD,), {}, "/api/lapp/hub/device/camera/list"),
}
_OK = {"code": "200", "msg": "ok", "data": {"items": [1, 2]}}
_BAD_TOKEN = {"code": "10002", "msg": "accessToken invalid", "data": None}
# Parametrize names only — never hold live Response/exception objects here.
# A consumed sync Response has a bound/closed stream and cannot be replayed
# by AsyncClient; _fresh_script builds a new instance for every call.
_ERRORS = ("http500", "biz5000", "timeout", "connect")
_ERROR_FAMILY = {"http500": "api", "biz5000": "api", "timeout": "transport", "connect": "transport"}
_OP_KINDS = ("get_token", "factory", "http", "invalidate")
# One invalid-argument case per facade method; serial cases are positional,
# the list_subdevices version case is a keyword override.
_BAD_ARGS = {
    "list_subdevices": ("lower_serial", "blank_serial", "version_list"),
    "subdevice_info": ("lower_serial", "blank_serial"),
    "channels": ("lower_serial", "blank_serial"),
}
_UTF8_SERIAL = "PARENT中枢HUB"


def _fresh_script(case):
    """Fresh response/exception instances so paired runs never share state."""
    if case == "success":
        return [httpx.Response(200, json=dict(_OK))]
    if case == "http500":
        return [httpx.Response(500, content=b"upstream failure")]
    if case == "biz5000":
        return [httpx.Response(200, json={"code": "5000", "msg": "x", "data": None})]
    if case == "timeout":
        return [httpx.ConnectTimeout("slow")]
    if case == "connect":
        return [httpx.ConnectError("refused")]
    raise KeyError(case)


def _token(value):
    return Token(value, expires_at=9999999999, refresh_at=9999999939, api_base_url=ORIGIN)


class _SyncProvider:
    can_refresh = True

    def get_token(self):
        raise AssertionError("provider script exhausted")


class _ScriptedSyncProvider(_SyncProvider):
    def __init__(self, log, script):
        self._log, self._script = log, list(script)

    def _next(self):
        item = self._script.pop(0)
        # A BaseException entry is raised verbatim so the caller observes the
        # identical provider error object — this is a test-double script, not
        # an SDK compatibility layer.
        if isinstance(item, BaseException):
            raise item
        return _token(item)

    def get_token(self):
        self._log.append(("get_token",))
        if not self._script:
            raise AssertionError("provider script exhausted")
        return self._next()

    def invalidate(self, expected_value=None):
        self._log.append(("invalidate", expected_value))


class _ScriptedAsyncProvider(_ScriptedSyncProvider):
    async def get_token(self):
        self._log.append(("get_token",))
        if not self._script:
            raise AssertionError("provider script exhausted")
        return self._next()


def _bad_args(method, case):
    """Map an invalid-argument case onto call args/kwargs for *method*."""
    if case == "lower_serial":
        return ("lowercase_serial_1",), {}
    if case == "blank_serial":
        return ("   ",), {}
    if case == "version_list":
        assert method == "list_subdevices"
        return (PARENT,), {"version": ["1.0"]}
    raise KeyError(case)


def _build_client(mode, *, recorder=None, log=None, region=None, provider_script=("T1",)):
    """One real client with logged provider/factory; no request ever runs.

    Async clients are created inside the caller's running loop. Sync clients
    bind ``httpx.MockTransport``; async clients bind ``_AsyncScriptedTransport``
    whose handler just logs ``("http",)`` — no request should ever run here.
    """
    log = [] if log is None else log

    def sync_handler(request):
        log.append(("http",))

    async def async_handler(request):
        log.append(("http",))

    if mode == "sync":
        transport = httpx.MockTransport(sync_handler)
    else:
        transport = _AsyncScriptedTransport(async_handler)

    def factory(origin):
        log.append(("factory", origin))
        return transport

    provider = (_ScriptedAsyncProvider if mode == "async" else _ScriptedSyncProvider)(
        log, provider_script
    )
    config = {
        "region": RegionConfig.china() if region is None else region,
        "token_provider": provider,
        "transport_factory": factory,
        "diagnostic_recorder": recorder,
    }
    return (Client(**config) if mode == "sync" else AsyncClient(**config)), log


def _call_args(method, *, args=None, kwargs=None):
    return (
        _OPS[method][0] if args is None else args,
        _OPS[method][1] if kwargs is None else kwargs,
    )


def _call(client, method, call_args, call_kwargs):
    return getattr(client.hubs, method)(*call_args, **call_kwargs)


async def _acall(client, method, call_args, call_kwargs):
    return await getattr(client.hubs, method)(*call_args, **call_kwargs)


class _AsyncScriptedTransport(httpx.AsyncBaseTransport):
    """Async transport delegating to a per-test coroutine handler.

    The handler is the transport's own await point: gates, logging and
    CancelledError observation all live inside it, so a cancellation
    propagates through the transport frame exactly as on a real socket.
    """

    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request):
        return await self._handler(request)


def _execute(
    mode,
    method,
    *,
    script,
    provider_script,
    recorder=None,
    log=None,
    args=None,
    kwargs=None,
    region=None,
):
    """Run one facade call with real client lifecycle; errors propagate.

    Sync clients are closed via ``with``; async clients run inside a single
    ``asyncio.run`` under ``async with``. Neither path catches the call's
    exception — context managers guarantee cleanup and the original error
    propagates unchanged to the caller. Leftover http script items are only
    reported after a call that returned successfully, so an AssertionError
    never masks the original exception.
    """
    log = [] if log is None else log
    items = list(script)

    def handler(request):
        log.append(
            (
                "http",
                request.method,
                request.url.path,
                request.headers.get("content-type"),
                sorted(parse_qsl(request.content.decode(), True)),
            )
        )
        log.append(
            (
                "wire",
                request.method,
                request.url.raw_path,
                request.content,
                tuple(sorted(request.headers.items())),
            )
        )
        if not items:
            raise AssertionError("http script exhausted")
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    transport = httpx.MockTransport(handler)

    def factory(origin):
        log.append(("factory", origin))
        return transport

    provider = (_ScriptedAsyncProvider if mode == "async" else _ScriptedSyncProvider)(
        log, provider_script
    )
    config = {
        # region=None keeps the existing domestic default unchanged.
        "region": RegionConfig.china() if region is None else region,
        "token_provider": provider,
        "transport_factory": factory,
        "diagnostic_recorder": recorder,
    }
    call_args, call_kwargs = _call_args(method, args=args, kwargs=kwargs)
    if mode == "sync":
        with Client(**config) as client:
            log.append(("region", client.region.scope))
            result = getattr(client.hubs, method)(*call_args, **call_kwargs)
    else:

        async def _run():
            async with AsyncClient(**config) as client:
                log.append(("region", client.region.scope))
                return await getattr(client.hubs, method)(*call_args, **call_kwargs)

        result = asyncio.run(_run())
    if items:
        raise AssertionError(f"uncalled http script items: {items!r}")
    return result


def _trace(log, kind):
    return [event for event in log if event[0] == kind]


def _diag(recorder, kind, ep=None):
    events = [e for e in recorder.report()["events"] if e["kind"] == kind]
    if ep is None:
        return events
    if kind == "call":
        # only the facade entrypoint — the inner client.request call shares
        # the operation name and must not be counted here.
        return [e for e in events if e.get("entrypoint") == ep]
    return [e for e in events if e.get("operation") == ep]


def _check_diag(
    recorder,
    ep,
    mode,
    outcomes,
    attempts,
    retries,
    error_family=None,
    expected_region="domestic",
):
    calls = _diag(recorder, "call", ep)
    assert [(e["outcome"], e["mode"], e["region"]) for e in calls] == [
        (o, mode, expected_region) for o in outcomes
    ]
    assert len(calls) == len(outcomes)
    if error_family is not None:
        # error_family is a fixed string ("authentication"/"api"/"transport"),
        # so compare the exact value — never a substring match.
        assert calls[-1].get("error_family") == error_family
    started = [e for e in _diag(recorder, "attempt", ep) if e["outcome"] == "started"]
    assert [e["attempt"] for e in started] == attempts
    assert [e["attempt"] for e in started] == sorted(
        set(e["attempt"] for e in _diag(recorder, "attempt", ep))
    )
    assert len(_diag(recorder, "retry")) == retries


def _http(method, token, *, serial=None, version=None):
    call_args, call_kwargs, path = _OPS[method]
    if serial is None:
        serial = call_args[0]
    params = [("accessToken", token), ("deviceSerial", serial)]
    if method == "list_subdevices":
        # blank version must survive parsing -> keep_blank_values=True.
        if version is None:
            version = call_kwargs.get("version", "")
        params.append(("version", version))
    return ("http", "POST", path, "application/x-www-form-urlencoded", sorted(params))


def _ops_trace(log):
    """Operation-level order only: get_token/factory/http/invalidate.

    The verbose ``wire`` events are excluded on purpose — they are compared
    wholesale by the recorder-off/on parity test instead of being folded
    into this sequence.
    """
    return [event for event in log if event[0] in _OP_KINDS]


def _ops(method, tokens, invalidated, *, serial=None, version=None):
    """Expected ordered op log for an n-request exchange.

    get_token before factory (first transport only) before http; every
    confirmed 10002 invalidates, even when no retry follows.
    """
    seq = []
    for index, token in enumerate(tokens):
        seq.append(("get_token",))
        if index == 0:
            seq.append(("factory", ORIGIN))
        seq.append(_http(method, token, serial=serial, version=version))
        if index < len(invalidated):
            seq.append(("invalidate", invalidated[index]))
    return seq


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("diag", (False, True))
def test_lapp_success(mode, method, diag):
    recorder, log = DiagnosticRecorder() if diag else None, []
    resp = _execute(
        mode,
        method,
        script=[httpx.Response(200, json=_OK)],
        provider_script=["T1"],
        recorder=recorder,
        log=log,
    )
    assert resp.raw["code"] == "200"
    assert resp.data == {"items": [1, 2]}
    assert _ops_trace(log) == _ops(method, ("T1",), ())
    assert ("region", "domestic") in log
    if diag:
        _check_diag(recorder, f"hubs.{method}", mode, ("started", "succeeded"), [1], 0)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("diag", (False, True))
@pytest.mark.parametrize("case", tuple(_ERRORS))
def test_typed_failures(mode, method, diag, case):
    recorder, log, error = DiagnosticRecorder() if diag else None, [], None
    try:
        _execute(
            mode,
            method,
            script=_fresh_script(case),
            provider_script=["T1"],
            recorder=recorder,
            log=log,
        )
    except (ApiError, TransportError) as exc:
        error = exc
    assert error is not None, "expected a typed SDK error"
    if case in ("http500", "biz5000"):
        assert type(error) is ApiError
        if case == "http500":
            assert error.http_status == 500
            assert error.code is None
        else:
            # keep the original code type — never str(error.code)
            assert error.code == "5000"
            assert error.http_status == 200
    else:
        assert type(error) is TransportError
        assert error.reason == ("timeout" if case == "timeout" else "network")
    assert _ops_trace(log) == _ops(method, ("T1",), ())
    if diag:
        _check_diag(
            recorder,
            f"hubs.{method}",
            mode,
            ("started", "failed"),
            [1],
            0,
            error_family=_ERROR_FAMILY[case],
        )


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
def test_10002_single_recovery(mode, method):
    wires = []
    for diag in (False, True):
        recorder, log = DiagnosticRecorder() if diag else None, []
        resp = _execute(
            mode,
            method,
            script=[httpx.Response(200, json=_BAD_TOKEN), httpx.Response(200, json=_OK)],
            provider_script=["T1", "T2"],
            recorder=recorder,
            log=log,
        )
        assert resp.data == {"items": [1, 2]}
        wires.append(_trace(log, "http"))
        assert wires[-1] == [_http(method, "T1"), _http(method, "T2")]
        assert _ops_trace(log) == _ops(method, ("T1", "T2"), ("T1",))
        if diag:
            _check_diag(recorder, f"hubs.{method}", mode, ("started", "succeeded"), [1, 2], 1)
    assert wires[0] == wires[1]


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
def test_10002_repeat_capped(mode, method):
    wires = []
    for diag in (False, True):
        recorder, log, error = DiagnosticRecorder() if diag else None, [], None
        try:
            _execute(
                mode,
                method,
                script=[httpx.Response(200, json=_BAD_TOKEN), httpx.Response(200, json=_BAD_TOKEN)],
                provider_script=["T1", "T2"],
                recorder=recorder,
                log=log,
            )
        except AuthenticationError as exc:
            error = exc
        assert type(error) is AuthenticationError
        assert error.invalid_token is True
        assert error.code == "10002"
        wires.append(_trace(log, "http"))
        assert wires[-1] == [_http(method, "T1"), _http(method, "T2")]
        # every confirmed 10002 invalidates — full (T1, T2) list, not [:1]
        assert _ops_trace(log) == _ops(method, ("T1", "T2"), ("T1", "T2"))
        if diag:
            _check_diag(
                recorder,
                f"hubs.{method}",
                mode,
                ("started", "failed"),
                [1, 2],
                1,
                error_family="authentication",
            )
    assert wires[0] == wires[1]


def _outcome(case, result, error):
    """Exact outcome fingerprint for recorder-off/on parity.

    Success compares the real ApiResponse fields (raw/data/code/http_status);
    failures compare the exact exception type plus the original code value
    and type, http_status, message, and the documented optional attributes
    reason/invalid_token via getattr — never alias names like ``msg``.
    """
    if case == "success":
        assert error is None
        assert type(result).__name__ == "ApiResponse"
        return ("ok", result.raw, result.data, result.code, result.http_status)
    assert result is None
    if case in ("http500", "biz5000"):
        assert type(error) is ApiError
        assert error.code == ("5000" if case == "biz5000" else None)
        assert type(error.code) is (str if case == "biz5000" else type(None))
        assert error.http_status == (200 if case == "biz5000" else 500)
        return ("api", error.code, error.message, error.http_status)
    if case in ("timeout", "connect"):
        assert type(error) is TransportError
        assert error.reason == ("timeout" if case == "timeout" else "network")
        return ("transport", error.reason, error.message, getattr(error, "invalid_token", None))
    raise KeyError(case)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("case", ("success", "biz5000", "http500", "timeout", "connect"))
def test_parity_recorder_off_on(mode, method, case):
    """diag None vs on must produce identical wires, op order, outcomes."""
    observed = []
    for diag in (None, DiagnosticRecorder()):
        log, result, error = [], None, None
        try:
            result = _execute(
                mode,
                method,
                script=_fresh_script(case),
                provider_script=["T1"],
                recorder=diag,
                log=log,
            )
        except (ApiError, TransportError) as exc:
            error = exc
        observed.append(
            {
                "ops": _ops_trace(log),  # full order incl. factory position
                "region": _trace(log, "region"),  # ("region", "domestic") once
                # full wire fingerprint: method, raw path+query, body, headers —
                # compared as a whole, not asserted field-by-field.
                "wires": _trace(log, "wire"),
                "outcome": _outcome(case, result, error),
            }
        )
        assert observed[-1]["ops"] == _ops(method, ("T1",), ())
    assert observed[0] == observed[1]


# --------------------------------------------------------------------------
# Phase B: validation, provider/request identity, closed clients, cancel.
# --------------------------------------------------------------------------


# Only the real (method, case) pairs are parametrized — version_list only
# exists on list_subdevices, so no combination is ever skipped.
_BAD_ARG_PAIRS = tuple((method, case) for method, cases in _BAD_ARGS.items() for case in cases)


@pytest.mark.parametrize("method,case", _BAD_ARG_PAIRS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("diag", (False, True))
@pytest.mark.parametrize("region_kind", ("china", "overseas"))
def test_invalid_args_before_io(mode, method, diag, region_kind, case):
    """Bad args raise one ValueError before provider/factory/http/invalidate.

    Overseas is checked before argument validation, so even a bad argument
    surfaces the region rejection first; neither path performs any I/O. The
    recorder events carry region metadata, so a zero-I/O run asserts the
    operation trace is empty instead of requiring an empty raw log.
    """
    recorder, log, error = DiagnosticRecorder() if diag else None, [], None
    region = RegionConfig.china() if region_kind == "china" else RegionConfig.overseas()
    try:
        args, kwargs = _bad_args(method, case)
        _execute(
            mode,
            method,
            script=[],
            provider_script=["T1"],
            recorder=recorder,
            log=log,
            args=args,
            kwargs=kwargs,
            region=region,
        )
    except ValueError as exc:
        error = exc
    assert type(error) is ValueError
    assert _ops_trace(log) == []
    if diag:
        _check_diag(
            recorder,
            f"hubs.{method}",
            mode,
            ("started", "failed"),
            [],
            0,
            error_family="validation",
            expected_region=region.scope,
        )


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
def test_utf8_serial_accepted(mode, method):
    """Non-ASCII UTF-8 serials are valid input; validation is not ASCII-only."""
    log = []
    resp = _execute(
        mode,
        method,
        script=[httpx.Response(200, json=dict(_OK))],
        provider_script=["T1"],
        log=log,
        args=(_UTF8_SERIAL,),
    )
    assert resp.raw["code"] == "200"
    assert resp.data == {"items": [1, 2]}
    # The serial reaches the wire unmodified — never ASCII-folded or dropped.
    wires = _trace(log, "wire")
    assert len(wires) == 1
    body = wires[0][3]
    params = dict(parse_qsl(body.decode("utf-8"), True))
    assert params["deviceSerial"] == _UTF8_SERIAL


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("diag", (False, True))
@pytest.mark.parametrize("marker", ("auth_error", "runtime_error"))
def test_provider_error_identity(mode, method, diag, marker):
    """A provider failure propagates as the identical object, once.

    One ``get_token`` and no factory/http/retry/invalidate: provider errors
    are never retried and never wrapped. The scripted provider raises the
    script item itself, so ``is`` identity is observable end to end.
    """
    raised = (
        AuthenticationError("token rejected", code=10002)
        if marker == "auth_error"
        else RuntimeError("provider exploded")
    )
    recorder, log, error = DiagnosticRecorder() if diag else None, [], None
    try:
        _execute(mode, method, script=[], provider_script=[raised], recorder=recorder, log=log)
    except (AuthenticationError, RuntimeError) as exc:
        error = exc
    assert error is raised
    assert _ops_trace(log) == [("get_token",)]
    if diag:
        _check_diag(
            recorder,
            f"hubs.{method}",
            mode,
            ("started", "failed"),
            [],
            0,
            error_family="authentication" if marker == "auth_error" else "unknown",
        )


def _prebuilt_response():
    return ApiResponse(
        http_status=200, code="200", operation="hubs.test", message="ok", data={"k": 1}, raw=_OK
    )


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("diag", (False, True))
@pytest.mark.parametrize("kind", ("response", "sdk_error"))
def test_request_boundary_identity(mode, method, diag, kind):
    """An instance-level request stub is returned/raised with ``is`` identity.

    The stub replaces ``client.request`` on one instance only, so this is a
    boundary-identity check — not HTTP evidence. The facade's own diagnostic
    scope still runs around the stub and is restored/closed via the normal
    client context manager.
    """
    prebuilt = (
        _prebuilt_response()
        if kind == "response"
        else TransportError("boundary failure", reason="network", operation="hubs.test")
    )
    recorder = DiagnosticRecorder() if diag else None
    if mode == "sync":
        client, _ = _build_client("sync", recorder=recorder)
        try:
            original = client.request

            def stub(operation, **kwargs):
                if kind == "response":
                    return prebuilt
                raise prebuilt

            client.request = stub
            try:
                result, error = None, None
                try:
                    result = _call(client, method, *_call_args(method))
                except TransportError as exc:
                    error = exc
            finally:
                client.request = original
        finally:
            client.close()
    else:

        async def _run():
            client, _ = _build_client("async", recorder=recorder)
            try:
                original = client.request

                async def stub(operation, **kwargs):
                    if kind == "response":
                        return prebuilt
                    raise prebuilt

                client.request = stub
                try:
                    result, error = None, None
                    try:
                        result = await _acall(client, method, *_call_args(method))
                    except TransportError as exc:
                        error = exc
                finally:
                    client.request = original
                return result, error
            finally:
                await client.aclose()

        result, error = asyncio.run(_run())
    if kind == "response":
        assert result is prebuilt
    else:
        assert error is prebuilt
    if diag:
        calls = _diag(recorder, "call", f"hubs.{method}")
        assert [(e["outcome"], e["mode"]) for e in calls] == [
            ("started", mode),
            ("succeeded" if kind == "response" else "failed", mode),
        ]


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("diag", (False, True))
def test_closed_client_rejects_call(mode, method, diag):
    """A call after close/aclose fails with TransportError(reason="closed").

    No provider/factory/http runs; the close diagnostic scope is a separate
    succeeded span, and the facade call reports failed/transport/closed —
    never a bare RuntimeError.
    """
    recorder, log, error = DiagnosticRecorder() if diag else None, [], None
    if mode == "sync":
        client, log = _build_client("sync", recorder=recorder, log=log)
        client.close()
        try:
            _call(client, method, *_call_args(method))
        except TransportError as exc:
            error = exc
    else:

        async def _run():
            client, _ = _build_client("async", recorder=recorder, log=log)
            await client.aclose()
            try:
                await _acall(client, method, *_call_args(method))
            except TransportError as exc:
                return exc
            return None

        error = asyncio.run(_run())
    assert type(error) is TransportError
    assert error.reason == "closed"
    assert _ops_trace(log) == []
    if diag:
        calls = _diag(recorder, "call", f"hubs.{method}")
        assert [(e["outcome"], e["mode"], e["error_family"], e["reason"]) for e in calls] == [
            ("started", mode, "none", "none"),
            ("failed", mode, "transport", "closed"),
        ]
        closes = [
            e
            for e in recorder.report()["events"]
            if e["kind"] == "close" and e["entrypoint"] in ("client.close", "client.aclose")
        ]
        assert [(e["outcome"], e["mode"]) for e in closes] == [
            ("started", mode),
            ("succeeded", mode),
        ]


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("diag", (False, True))
def test_async_cancel_propagates(method, diag):
    """A real task cancel reaches the transport await and propagates.

    The transport's gate blocks on ``release``; ``started`` proves the one
    HTTP request began within 5s before ``task.cancel()``. The transport
    and the SDK-call boundary each record the CancelledError that flowed
    through them into a dedicated list; ``pytest.raises`` outside observes
    propagation (``await`` on a cancelled task raises a fresh
    CancelledError, so identity is checked between the two internal
    records, never against the raised one). Exactly one HTTP attempt runs,
    with no retry and no invalidate. Finally cancels+awaits the task under
    a timeout if needed and always acloses — close performs no business
    request.
    """
    recorder, log = DiagnosticRecorder() if diag else None, []

    async def _run():
        started, release = asyncio.Event(), asyncio.Event()
        # Captured CancelledError objects: captured[0] = transport await
        # point, sdk_boundary[0] = SDK-call boundary. ``task.exception()``
        # on a cancelled task raises CancelledError itself, so the SDK-side
        # record is the only observable boundary object.
        captured, sdk_boundary = [], []

        async def handler(request):
            # The transport-side gate: log the attempt, prove it began, then
            # park on ``release`` (never set) so task.cancel() lands inside
            # this very await.
            log.append(("http",))
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError as exc:
                captured.append(exc)
                raise
            return httpx.Response(200, json=dict(_OK))

        transport = _AsyncScriptedTransport(handler)

        def factory(origin):
            log.append(("factory", origin))
            return transport

        provider = _ScriptedAsyncProvider(log, ["T1"])
        client = AsyncClient(
            region=RegionConfig.china(),
            token_provider=provider,
            transport_factory=factory,
            diagnostic_recorder=recorder,
        )
        call_args, call_kwargs = _call_args(method)

        async def call():
            try:
                await _acall(client, method, call_args, call_kwargs)
            except asyncio.CancelledError as exc:
                sdk_boundary.append(exc)
                raise

        task = asyncio.ensure_future(call())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
            # The transport-caught object is the same object that propagated
            # through the SDK call boundary — no wrap, no substitute.
            assert len(captured) == 1
            assert len(sdk_boundary) == 1
            assert captured[0] is sdk_boundary[0]
        finally:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            await client.aclose()

    asyncio.run(_run())
    # One HTTP request only: no retry scheduled and no invalidate fired.
    assert _ops_trace(log) == [("get_token",), ("factory", ORIGIN), ("http",)]
    if diag:
        calls = _diag(recorder, "call", f"hubs.{method}")
        assert [(e["outcome"], e["mode"], e["error_family"]) for e in calls] == [
            ("started", "async", "none"),
            ("cancelled", "async", "interrupted"),
        ]
        closes = [
            e
            for e in recorder.report()["events"]
            if e["kind"] == "close" and e["entrypoint"] == "client.aclose"
        ]
        assert [(e["outcome"], e["mode"]) for e in closes] == [
            ("started", "async"),
            ("succeeded", "async"),
        ]


# --------------------------------------------------------------------------
# Phase C: privacy of sent values, verbatim success payload, 10002 collision.
# --------------------------------------------------------------------------

# Long ASCII-uppercase synthetic sentinels actually sent on the wire; every
# serial satisfies the deviceSerial contract (non-blank UTF-8, no a-z).
_PRIV_SERIAL = {
    "list_subdevices": "HUBPARENTSENT0A1B2C3D4E5F60718293A4B5C6D7E8F9",
    "subdevice_info": "HUBCHILDSENT0F1E2D3C4B5A69718293A4B5C6D7E8F9",
    "channels": "HUBCHANNELSENT09Z8Y7X6W5V4U3T2S1R0Q9P8O7N6M5L4",
}
# Long synthetic access tokens — real provider values, never short markers.
_PRIV_TOKEN = "{{SECRET_hubspriv01}}"
_PRIV_TOKEN2 = "{{SECRET_hubspriv02}}"
# A plain caller string sent as ``version``: not a sensitive key, so it is a
# normal free-text value in errors yet must still stay out of logs/diag.
_PRIV_VERSION = "UNSENT_SENSITIVE_NOT_A_KEY"
# A URL never sent, never configured; echoed by the server as free text only.
_PRIV_FOREIGN = "https://unrelated.invalid/never-sent?q=42"
# A marker never sent anywhere; the SDK must not reproduce it.
_PRIV_RAW = "RAW_PAYLOAD_MARKER_NEVER_SENT_zz00"


def _privacy_call(method):
    """Return ``(args, kwargs, serial, version)`` for one privacy probe.

    ``version`` is only sent by ``list_subdevices``; it stays ``None`` for the
    other two methods.
    """
    serial = _PRIV_SERIAL[method]
    version = _PRIV_VERSION if method == "list_subdevices" else None
    kwargs = {"version": version} if version is not None else {}
    return (serial,), kwargs, serial, version


def _wire_fields(entry):
    """Parsed form fields of one ``http`` log entry (the real wire body)."""
    tag, verb, _path, _ctype, params = entry
    assert tag == "http" and verb == "POST"
    return dict(params)


def _privacy_wire(log, method, serial, version, token):
    """Prove the sent serial/token/version reach the wire verbatim, once."""
    http = _trace(log, "http")
    assert len(http) == 1
    fields = _wire_fields(http[0])
    assert fields["accessToken"] == token
    assert fields["deviceSerial"] == serial
    if version is None:
        assert "version" not in fields
    else:
        assert fields["version"] == version


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("recorded", (False, True))
def test_privacy_error_message_scrubs_sent_values(mode, method, recorded, caplog):
    """Sent serial/token are masked in error str/repr, logs and diagnostics.

    The server msg echoes the sent token and serial plus a never-sent URL and
    the (non-sensitive) sent version. Exact secrets are masked while the
    unknown URL and the plain version string stay as free error text.
    """
    recorder = DiagnosticRecorder() if recorded else None
    args, kwargs, serial, version = _privacy_call(method)
    known = [_PRIV_TOKEN, serial]
    msg = "denied accessToken={0} deviceSerial={1}".format(_PRIV_TOKEN, serial)
    if version is not None:
        msg += " version={0}".format(version)
    msg += " " + _PRIV_FOREIGN
    script = [httpx.Response(200, json={"code": "5000", "msg": msg, "data": None})]
    log = []
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ApiError) as caught:
            _execute(
                mode,
                method,
                script=script,
                provider_script=[_PRIV_TOKEN],
                recorder=recorder,
                log=log,
                args=args,
                kwargs=kwargs,
            )
    err = caught.value
    assert type(err) is ApiError
    _privacy_wire(log, method, serial, version, _PRIV_TOKEN)
    assert _ops_trace(log) == _ops(method, (_PRIV_TOKEN,), (), serial=serial, version=version)
    for text in (str(err), repr(err)):
        # Values actually sent never surface verbatim in the typed error.
        for value in known:
            assert value not in text
        # The never-sent URL is free server text and stays in the error.
        assert _PRIV_FOREIGN in text
        # Sent values may only appear in their masked key form.
        assert "accessToken=***" in text
        assert "deviceSerial=***" in text
        # ``version`` is not a sensitive key: it is not required to be erased
        # from free error text (it deterministically stays verbatim).
        if version is not None:
            assert version in text
    # Automatic log records and the diagnostic JSON carry neither the sent
    # values nor the never-sent URL nor the raw-payload marker. ``version`` is
    # a sent form value and must not leak into logs/diag either.
    forbidden = known + [_PRIV_FOREIGN, _PRIV_RAW]
    if version is not None:
        forbidden.append(version)
    for value in forbidden:
        assert value not in caplog.text
    if recorded:
        blob = recorder.to_json()
        for value in forbidden:
            assert value not in blob
        _check_diag(
            recorder,
            f"hubs.{method}",
            mode,
            ("started", "failed"),
            [1],
            0,
            error_family="api",
        )


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("recorded", (False, True))
def test_privacy_success_response_not_a_leak(mode, method, recorded, caplog):
    """``data``/``raw`` keep sent serial, foreign URL and unknown fields.

    An SDK return value is the caller's own data, not a diagnostic leak: the
    SDK does not delete the returned payload. The repr, automatic body logs
    and the diagnostic JSON still exclude every sensitive value, and unknown
    response fields are preserved.
    """
    recorder = DiagnosticRecorder() if recorded else None
    args, kwargs, serial, version = _privacy_call(method)
    payload = {
        "code": "200",
        "msg": "ok",
        "data": {"deviceSerial": serial, "url": _PRIV_FOREIGN, "items": [1, 2]},
        "unknownField": "preserved",
    }
    log = []
    with caplog.at_level(logging.DEBUG):
        resp = _execute(
            mode,
            method,
            script=[httpx.Response(200, json=payload)],
            provider_script=[_PRIV_TOKEN],
            recorder=recorder,
            log=log,
            args=args,
            kwargs=kwargs,
        )
    assert resp.data == payload["data"]
    assert resp.raw == payload
    assert resp.raw["unknownField"] == "preserved"
    _privacy_wire(log, method, serial, version, _PRIV_TOKEN)
    sensitive = [serial, _PRIV_FOREIGN, _PRIV_TOKEN]
    shown = str(resp) + repr(resp)
    for value in sensitive + [_PRIV_RAW]:
        assert value not in shown
        assert value not in caplog.text
    if version is not None:
        assert version not in caplog.text
    if recorded:
        blob = recorder.to_json()
        for value in sensitive + [_PRIV_RAW]:
            assert value not in blob
        if version is not None:
            assert version not in blob
        _check_diag(recorder, f"hubs.{method}", mode, ("started", "succeeded"), [1], 0)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("scenario", ("recover", "give_up"))
def test_serial_10002_business_code_collision(mode, method, recorded, scenario):
    """``deviceSerial='10002'`` sent verbatim; code ``'10002'`` is invalid-token.

    The caller's serial collides with the platform invalid-token code. The
    wire carries the literal value on every attempt, each confirmed ``10002``
    invalidates the sent token, and the read-only operation retries at most
    once — so ``get_token`` is read at most twice and ``invalidate`` runs at
    most twice. ``err.code`` may already be masked ``***`` because the sent
    serial is itself a registered secret; the test never branches on the raw
    business code and never sweeps short-``10002`` output.
    """
    recorder = DiagnosticRecorder() if recorded else None
    tokens = (_PRIV_TOKEN, _PRIV_TOKEN2)
    bad = {"code": "10002", "msg": "expired", "data": None}
    if scenario == "recover":
        script = [
            httpx.Response(200, json=dict(bad)),
            httpx.Response(200, json=dict(_OK)),
        ]
        invalidated = (tokens[0],)
    else:
        script = [
            httpx.Response(200, json=dict(bad)),
            httpx.Response(200, json=dict(bad)),
        ]
        invalidated = tokens
    log = []
    result, error = None, None
    try:
        result = _execute(
            mode,
            method,
            script=script,
            provider_script=list(tokens),
            recorder=recorder,
            log=log,
            args=("10002",),
        )
    except AuthenticationError as exc:
        error = exc
    if scenario == "recover":
        # First 10002 then success recovers once through the normal path.
        assert error is None
        assert result.data == {"items": [1, 2]}
    else:
        assert result is None
        assert type(error) is AuthenticationError
        assert error.invalid_token is True
        # The public code may already be masked because the sent serial is a
        # registered secret; never drive a decision off the masked value.
        assert error.code in ("10002", "***")
    assert _ops_trace(log) == _ops(method, tokens, invalidated, serial="10002")
    # The collision serial is sent verbatim on every wire attempt.
    for entry in _trace(log, "http"):
        assert _wire_fields(entry)["deviceSerial"] == "10002"
    if recorded:
        if scenario == "recover":
            _check_diag(recorder, f"hubs.{method}", mode, ("started", "succeeded"), [1, 2], 1)
        else:
            _check_diag(
                recorder,
                f"hubs.{method}",
                mode,
                ("started", "failed"),
                [1, 2],
                1,
                error_family="authentication",
            )
