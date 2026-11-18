"""Phase A+B+C acceptance: domestic message_subscriptions create_consumer/consume.

Phase A scenarios run with ``diagnostic_recorder`` both off and on; the paired
test then proves the two runs produce identical wire fingerprints, call
ordering and outcomes. Diagnostics are read only through
``DiagnosticRecorder.report()["events"]`` dict snapshots taken after close.

Phase B covers validation ordering (the region guard runs before parameter
validation, which runs before provider/factory/HTTP), provider- and
request-boundary object identity, closed-client rejection and true async
cancellation.

Phase C covers privacy of sent request values in errors/logs/diagnostics,
verbatim ``ApiResponse`` payload retention on success, and the business-code
``10002``/short ``consumer_id`` collision, which must still be treated as an
invalid-token result with exactly one invalidate and no replay.
"""

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

_ORIGIN = "https://open.ys7.com"
_TOKEN = Token(
    value="phase-a-token", expires_at=9999999999, refresh_at=9999999939, api_base_url=_ORIGIN
)
_METHODS = ("create_consumer", "consume")


class _SyncProvider:
    can_refresh = True

    def __init__(self, script, log):
        self._script, self._log = script, log

    def _next(self):
        if not self._script:
            raise AssertionError("provider script exhausted")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get_token(self):
        self._log.append(("get_token",))
        return self._next()

    def invalidate(self, expected_value=None):
        self._log.append(("invalidate", expected_value))


class _AsyncProvider(_SyncProvider):
    async def get_token(self):
        self._log.append(("get_token",))
        return self._next()


def _handler(script, log):
    def send(request):
        headers = tuple(sorted((k.lower(), v) for k, v in request.headers.items()))
        log.append(
            (
                "http",
                request.method,
                request.url.raw_path.decode("utf-8"),
                request.content.decode("utf-8"),
                headers,
            )
        )
        if not script:
            raise AssertionError("http script exhausted")
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return send


def _default_args(method):
    if method == "create_consumer":
        return ("group1",), {}
    return ("PRIVATE_CONSUMER_123",), {"offset": 0}


def _entries(log, kind):
    return [entry for entry in log if entry[0] == kind]


def _assert_wire(entry, method):
    """Full wire check: verb, raw path, form fields, safe headers."""
    tag, verb, raw_path, content, headers = entry
    assert tag == "http"
    assert verb == "POST"
    assert "?" not in raw_path
    fields = dict(parse_qsl(content, keep_blank_values=True))
    if method == "create_consumer":
        assert raw_path == "/api/lapp/mq/v2/consumer/group1"
        assert fields == {"accessToken": _TOKEN.value}
    else:
        assert raw_path == "/api/lapp/mq/v2/consumer/messages"
        assert fields == {
            "accessToken": _TOKEN.value,
            "consumerId": "PRIVATE_CONSUMER_123",
            "offset": "0",
        }
    header_map = dict(headers)
    assert header_map.get("content-type") == "application/x-www-form-urlencoded"
    assert "accesstoken" not in header_map
    assert "authorization" not in header_map


def _payload(method):
    if method == "create_consumer":
        return {
            "code": "200",
            "msg": "OK",
            "data": {"consumerId": "PRIVATE_CONSUMER_123"},
            "unknownField": "preserved",
        }
    return {"code": "200", "msg": "OK", "data": [{"messageId": "m1"}], "unknownField": "preserved"}


def _error_script(case):
    if case == "http500":
        return [httpx.Response(500, text="upstream exploded")]
    if case == "biz5000":
        return [httpx.Response(200, json={"code": "5000", "msg": "bad", "data": None})]
    if case == "timeout":
        return [httpx.TimeoutException("timed out")]
    return [httpx.ConnectError("refused")]


def _call_events(recorder, method):
    entrypoint = f"message_subscriptions.{method}"
    return [
        e
        for e in recorder.report()["events"]
        if e["kind"] == "call" and e["entrypoint"] == entrypoint
    ]


def _operation_events(recorder, method, kind):
    operation = f"message_subscriptions.{method}"
    return [
        e for e in recorder.report()["events"] if e["kind"] == kind and e["operation"] == operation
    ]


def _attempts_started(recorder, method):
    # Only started attempt events count; each scope also emits a terminal one.
    return [
        e["attempt"]
        for e in _operation_events(recorder, method, "attempt")
        if e["outcome"] == "started"
    ]


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
    log = [] if log is None else log
    script, provider_script = list(script), list(provider_script)
    provider = (
        _SyncProvider(provider_script, log)
        if mode == "sync"
        else _AsyncProvider(provider_script, log)
    )
    transport = httpx.MockTransport(_handler(script, log))

    def factory(origin):
        log.append(("factory", origin))
        return transport

    if args is None and kwargs is None:
        args, kwargs = _default_args(method)
    args, kwargs = args or (), kwargs or {}
    region = RegionConfig.china() if region is None else region
    options = dict(
        region=region,
        token_provider=provider,
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )

    def check_drained():
        # Call only on expected-consumed paths; never inside a finally so it
        # cannot mask the exception under test with AssertionError.
        assert not script and not provider_script, (
            f"uncalled leftovers http={script!r} provider={provider_script!r}"
        )

    if mode == "sync":
        with Client(**options) as client:
            assert client.region.scope == region.scope
            result = getattr(client.message_subscriptions, method)(*args, **kwargs)
        check_drained()
        return result

    async def run():
        # Create, invoke and aclose all inside this one event loop.
        async with AsyncClient(**options) as client:
            assert client.region.scope == region.scope
            return await getattr(client.message_subscriptions, method)(*args, **kwargs)

    result = asyncio.run(run())
    check_drained()
    return result


def _assert_outcomes(
    recorder, method, outcomes, mode, *, attempts, retries, expected_region="domestic"
):
    calls = _call_events(recorder, method)
    assert [e["outcome"] for e in calls] == outcomes
    assert all(e["mode"] == mode for e in calls)
    assert all(e["region"] == expected_region for e in calls)
    assert _attempts_started(recorder, method) == attempts
    scheduled = [
        e for e in _operation_events(recorder, method, "retry") if e["outcome"] == "scheduled"
    ]
    assert len(scheduled) == retries
    assert all(e["mode"] == mode for e in scheduled)
    assert all(e["region"] == expected_region for e in scheduled)
    return calls


def _run(mode, method, *, script, provider_script, recorded):
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    try:
        return (
            (
                "ok",
                _execute(
                    mode,
                    method,
                    script=script,
                    provider_script=provider_script,
                    recorder=recorder,
                    log=log,
                ),
            ),
            log,
            recorder,
        )
    except (ApiError, TransportError) as error:
        return ("error", error), log, recorder


def _outcome_pair(result):
    status, payload = result
    if status == "ok":
        return (status, payload.data, payload.raw, payload.code, payload.http_status)
    return (
        status,
        type(payload),
        payload.code,
        payload.http_status,
        getattr(payload, "reason", None),
    )


def _assert_paired(mode, method, *, script, provider_script):
    outcomes, logs = [], []
    for recorded in (False, True):
        result, log, recorder = _run(
            mode, method, script=script, provider_script=provider_script, recorded=recorded
        )
        for entry in _entries(log, "http"):
            _assert_wire(entry, method)
        outcomes.append(_outcome_pair(result))
        logs.append(log)
    assert outcomes[0] == outcomes[1]
    # Full ordered traces are identical: get_token/factory/http/invalidate.
    assert logs[0] == logs[1]


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_lapp_success(mode, method, recorded):
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    resp = _execute(
        mode,
        method,
        script=[httpx.Response(200, json=_payload(method))],
        provider_script=[_TOKEN],
        recorder=recorder,
        log=log,
    )
    assert resp.data == _payload(method)["data"]
    assert resp.raw == _payload(method)
    assert resp.raw["unknownField"] == "preserved"
    assert resp.code == "200" and type(resp.code) is str
    assert resp.http_status == 200
    # get_token (authentication) precedes transport_setup; the factory runs
    # only when the transport for this origin is first established.
    assert log == [("get_token",), ("factory", _ORIGIN), log[2]]
    assert len(_entries(log, "http")) == 1
    _assert_wire(log[2], method)
    if recorded:
        _assert_outcomes(recorder, method, ["started", "succeeded"], mode, attempts=[1], retries=0)


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("case", ("http500", "biz5000", "timeout", "connect"))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_lapp_failures(mode, method, case, recorded):
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    expected = TransportError if case in ("timeout", "connect") else ApiError
    with pytest.raises(expected) as caught:
        _execute(
            mode,
            method,
            script=_error_script(case),
            provider_script=[_TOKEN],
            recorder=recorder,
            log=log,
        )
    err = caught.value
    if case in ("http500", "biz5000"):
        assert type(err) is ApiError  # not AuthenticationError/subclasses
        if case == "http500":
            assert err.http_status == 500
            assert err.code is None
            expected_family, expected_reason = "api", "none"
        else:
            assert err.code == "5000" and type(err.code) is str
            expected_family, expected_reason = "api", "none"
    else:
        assert type(err) is TransportError
        assert err.reason == ("timeout" if case == "timeout" else "network")
        expected_family, expected_reason = "transport", err.reason
    assert log == [("get_token",), ("factory", _ORIGIN), log[2]]
    assert len(_entries(log, "http")) == 1
    _assert_wire(log[2], method)
    assert _entries(log, "invalidate") == []
    if recorded:
        calls = _assert_outcomes(
            recorder, method, ["started", "failed"], mode, attempts=[1], retries=0
        )
        assert calls[-1]["error_family"] == expected_family
        assert calls[-1]["reason"] == expected_reason


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_lapp_invalid_token_no_replay(mode, method, recorded):
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    script = [httpx.Response(200, json={"code": "10002", "msg": "expired", "data": None})]
    with pytest.raises(AuthenticationError) as caught:
        _execute(mode, method, script=script, provider_script=[_TOKEN], recorder=recorder, log=log)
    err = caught.value
    assert type(err) is AuthenticationError
    assert err.invalid_token is True
    assert err.code == "10002" and type(err.code) is str
    # These operations are not read_only: one HTTP attempt, a confirmed 10002
    # still invalidates T1 once, and no retry/re-authentication happens.
    assert log == [("get_token",), ("factory", _ORIGIN), log[2], ("invalidate", _TOKEN.value)]
    assert len(_entries(log, "http")) == 1
    _assert_wire(log[2], method)
    if recorded:
        calls = _assert_outcomes(
            recorder, method, ["started", "failed"], mode, attempts=[1], retries=0
        )
        assert calls[-1]["error_family"] == "authentication"
        assert calls[-1]["code_category"] == "invalid_token"


@pytest.mark.parametrize("case", ("success", "biz5000", "http500", "timeout", "connect"))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_paired_recorder_off_on(mode, method, case):
    """diagnostic_recorder None vs on: identical wires, order and outcome."""
    script = (
        [httpx.Response(200, json=_payload(method))] if case == "success" else _error_script(case)
    )
    _assert_paired(mode, method, script=script, provider_script=[_TOKEN])


# ---------------------------------------------------------------------------
# Phase B: validation ordering, object identity, closed client, cancellation.


def _invalid_args(method):
    """One illegal argument set per facade method."""
    if method == "create_consumer":
        return ("group9",), {}
    return ("",), {}


def _precheck(case, method):
    """(region, args, kwargs) for a pre-provider rejection scenario."""
    region = RegionConfig.china() if case == "domestic_invalid" else RegionConfig.overseas()
    args, kwargs = _invalid_args(method) if case.endswith("_invalid") else _default_args(method)
    return region, args, kwargs


def _build(mode, *, recorder=None, log=None, script=None, provider_script=None, transport=None):
    """Construct a real client on the phase-A provider/handler helpers."""
    log = [] if log is None else log
    script, provider_script = list(script or ()), list(provider_script or ())
    provider = (
        _SyncProvider(provider_script, log)
        if mode == "sync"
        else _AsyncProvider(provider_script, log)
    )
    if transport is None:
        transport = httpx.MockTransport(_handler(script, log))

    def factory(origin):
        log.append(("factory", origin))
        return transport

    options = dict(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    return Client(**options) if mode == "sync" else AsyncClient(**options)


class _GateTransport(httpx.AsyncBaseTransport):
    """Holds the real async request open on the shared log until cancelled."""

    def __init__(self, log):
        self._log = log
        self.started = asyncio.Event()
        self.cancelled = None
        self.request_count = 0

    async def handle_async_request(self, request):
        self._log.append(("http", request.method, request.url.raw_path.decode("utf-8")))
        self.request_count += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            self.cancelled = error
            raise
        raise AssertionError("gate released unexpectedly")

    async def aclose(self):
        return None


@pytest.mark.parametrize("case", ("domestic_invalid", "overseas_valid", "overseas_invalid"))
@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_prevalidation_ordering(mode, method, case, recorded):
    """Region guard then parameter checks; both precede provider/factory/HTTP."""
    recorder = DiagnosticRecorder() if recorded else None
    region, args, kwargs = _precheck(case, method)
    log = []
    # Empty scripts: a leftover item could never mask the ValueError here.
    with pytest.raises(ValueError) as caught:
        _execute(
            mode,
            method,
            script=[],
            provider_script=[],
            recorder=recorder,
            log=log,
            args=args,
            kwargs=kwargs,
            region=region,
        )
    if case != "domestic_invalid":
        # The region guard's own message proves it fired before parameter
        # validation even when the arguments are also illegal.
        assert "domestic region" in str(caught.value)
    assert log == []
    if recorded:
        calls = _assert_outcomes(
            recorder,
            method,
            ["started", "failed"],
            mode,
            attempts=[],
            retries=0,
            expected_region=region.scope,
        )
        assert calls[-1]["error_family"] == "validation"
        assert calls[-1]["reason"] == "none"
        assert calls[-1]["region"] == region.scope


@pytest.mark.parametrize("error", ("auth", "runtime"))
@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_provider_error_identity(mode, method, error, recorded):
    """Provider exceptions reach the caller as the very same object."""
    recorder = DiagnosticRecorder() if recorded else None
    marker = (
        AuthenticationError("provider denied", code=10002)
        if error == "auth"
        else RuntimeError("provider exploded")
    )
    log = []
    with pytest.raises(type(marker)) as caught:
        _execute(
            mode,
            method,
            script=[],
            provider_script=[marker],
            recorder=recorder,
            log=log,
        )
    assert caught.value is marker
    # Provider failure is not the HTTP 10002 wire path: no factory call, no
    # request, no invalidate, no retry or re-authentication.
    assert log == [("get_token",)]
    if recorded:
        calls = _assert_outcomes(
            recorder, method, ["started", "failed"], mode, attempts=[], retries=0
        )
        if error == "auth":
            assert calls[-1]["error_family"] == "authentication"
            assert calls[-1]["code_category"] == "invalid_token"
        else:
            assert calls[-1]["error_family"] == "unknown"


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_request_boundary_identity(mode, method, recorded):
    """Instance-level ``client.request`` stub: same object in and out.

    The facade scope still runs (region guard, validation, diagnostics), but
    this is a boundary-identity proof only — not evidence of an HTTP exchange.
    """
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    args, kwargs = _default_args(method)
    prebuilt = ApiError("prebuilt boundary failure", code="5999")
    script = [httpx.Response(200, json=_payload(method))]

    if mode == "sync":
        client = _build(
            "sync",
            recorder=recorder,
            log=log,
            script=script,
            provider_script=[_TOKEN],
        )
        try:
            real = getattr(client.message_subscriptions, method)(*args, **kwargs)
            assert isinstance(real, ApiResponse)
            mark = len(log)
            original = client.request
            try:
                client.request = lambda *a, **k: real
                assert getattr(client.message_subscriptions, method)(*args, **kwargs) is real

                def _boom(*a, **k):
                    raise prebuilt

                client.request = _boom
                with pytest.raises(ApiError) as caught:
                    getattr(client.message_subscriptions, method)(*args, **kwargs)
                assert caught.value is prebuilt
            finally:
                del client.request
                assert client.request == original
            assert len(log) == mark
        finally:
            client.close()
    else:

        async def run():
            client = _build(
                "async",
                recorder=recorder,
                log=log,
                script=script,
                provider_script=[_TOKEN],
            )
            try:
                real = await getattr(client.message_subscriptions, method)(*args, **kwargs)
                assert isinstance(real, ApiResponse)
                mark = len(log)
                original = client.request
                try:

                    async def _ret(*a, **k):
                        return real

                    client.request = _ret
                    assert (
                        await getattr(client.message_subscriptions, method)(*args, **kwargs) is real
                    )

                    async def _boom(*a, **k):
                        raise prebuilt

                    client.request = _boom
                    with pytest.raises(ApiError) as caught:
                        await getattr(client.message_subscriptions, method)(*args, **kwargs)
                    assert caught.value is prebuilt
                finally:
                    del client.request
                    assert client.request == original
                assert len(log) == mark
            finally:
                await client.aclose()

        asyncio.run(run())
    if recorded:
        calls = _call_events(recorder, method)
        assert [e["outcome"] for e in calls] == [
            "started",
            "succeeded",
            "started",
            "succeeded",
            "started",
            "failed",
        ]
        assert calls[-1]["error_family"] == "api"


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_closed_client_transport_closed(mode, method, recorded):
    """Valid calls on a closed client raise TransportError(reason=closed)."""
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    args, kwargs = _default_args(method)
    if mode == "sync":
        client = _build("sync", recorder=recorder, log=log)
        client.close()
        with pytest.raises(TransportError) as caught:
            getattr(client.message_subscriptions, method)(*args, **kwargs)
        err = caught.value
    else:

        async def run():
            client = _build("async", recorder=recorder, log=log)
            await client.aclose()
            with pytest.raises(TransportError) as caught:
                await getattr(client.message_subscriptions, method)(*args, **kwargs)
            return caught.value

        err = asyncio.run(run())
    assert type(err) is TransportError
    assert err.reason == "closed"
    assert err.operation == f"message_subscriptions.{method}"
    assert log == []
    if recorded:
        calls = _assert_outcomes(
            recorder, method, ["started", "failed"], mode, attempts=[], retries=0
        )
        assert calls[-1]["error_family"] == "transport"
        assert calls[-1]["reason"] == "closed"
        closes = [e for e in recorder.report()["events"] if e["kind"] == "close"]
        assert [e["outcome"] for e in closes] == ["started", "succeeded"]


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("method", _METHODS)
def test_async_cancellation_identity(method, recorded):
    """Real task cancellation inside the HTTP attempt; same object in/out."""
    recorder = DiagnosticRecorder() if recorded else None
    log = []
    transport = _GateTransport(log)
    args, kwargs = _default_args(method)

    async def run():
        client = _build(
            "async",
            recorder=recorder,
            log=log,
            provider_script=[_TOKEN],
            transport=transport,
        )
        inner = []

        async def invoke():
            try:
                return await getattr(client.message_subscriptions, method)(*args, **kwargs)
            except asyncio.CancelledError as error:
                inner.append(error)
                raise

        task = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(transport.started.wait(), timeout=5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            # Always await an unfinished task until its cancellation lands
            # before closing; a never-awaited task's ``finally`` decrement is
            # unscheduled and aclose would then hang on the idle gate.
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.aclose()
        return task, inner

    task, inner = asyncio.run(run())
    assert task.cancelled()
    assert len(inner) == 1
    assert transport.cancelled is not None
    assert inner[0] is transport.cancelled
    # Exactly one authentication/transport-setup/HTTP hit; closing the client
    # consumes nothing further and there is no retry or invalidate.
    assert transport.request_count == 1
    assert [entry[0] for entry in log] == ["get_token", "factory", "http"]
    assert _entries(log, "invalidate") == []
    if recorded:
        calls = _assert_outcomes(
            recorder, method, ["started", "cancelled"], "async", attempts=[1], retries=0
        )
        assert calls[-1]["error_family"] == "interrupted"
        assert calls[-1]["reason"] == "none"
        closes = [e for e in recorder.report()["events"] if e["kind"] == "close"]
        assert [e["outcome"] for e in closes] == ["started", "succeeded"]


# ---------------------------------------------------------------------------
# Phase C: privacy of sent values, verbatim success payload, 10002 collision.

# Opaque caller-owned consumer id actually sent on the consume wire.
_PRIVACY_CONSUMER = "PRIVATE_CONSUMER_ABC123XYZ_opaque_0099887766"
# A raw-payload marker never sent anywhere; the SDK must not reproduce it.
_RAW_PAYLOAD_MARKER = "RAW_PAYLOAD_MARKER_NEVER_SENT_zz00"
# A URL that is never sent, never configured and never embedded in a request.
_PRIVACY_FOREIGN_URL = "https://unrelated.invalid/never-sent?q=42"


def _privacy_args(method):
    if method == "create_consumer":
        return ("group1",), {}
    return (_PRIVACY_CONSUMER,), {"offset": 0}


def _privacy_known(method):
    # Only values that really left this process: the fixed group1 path label
    # (create), the opaque consumerId (consume) and the form accessToken.
    known = [_TOKEN.value]
    if method == "consume":
        known.append(_PRIVACY_CONSUMER)
    else:
        known.append("group1")
    return known


def _privacy_wire(entry, method):
    """Request fingerprint: parsed form/path prove the sent values verbatim."""
    tag, verb, raw_path, content, _headers = entry
    assert tag == "http" and verb == "POST" and "?" not in raw_path
    fields = dict(parse_qsl(content, keep_blank_values=True))
    if method == "create_consumer":
        assert raw_path == "/api/lapp/mq/v2/consumer/group1"
        assert set(fields) == {"accessToken"}
        assert fields["accessToken"] == _TOKEN.value
    else:
        assert raw_path == "/api/lapp/mq/v2/consumer/messages"
        assert set(fields) == {"accessToken", "consumerId", "offset"}
        assert fields["accessToken"] == _TOKEN.value
        assert fields["consumerId"] == _PRIVACY_CONSUMER
        assert fields["offset"] == "0"


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_privacy_error_message_scrubs_sent_values(mode, method, recorded, caplog):
    """Server msg echoes sent values plus an unsent URL and a raw marker.

    The typed error keeps the unknown URL but must mask every value the SDK
    actually sent (key-scoped ``consumerId=``/``accessToken=`` and the exact
    token string). Automatic httpx log records and the diagnostic JSON must
    carry none of the sent values, the unsent URL or the raw marker.
    """
    recorder = DiagnosticRecorder() if recorded else None
    args, kwargs = _privacy_args(method)
    known = _privacy_known(method)
    msg = "denied accessToken={0} consumerId={1} {2}".format(
        _TOKEN.value,
        _PRIVACY_CONSUMER,
        _PRIVACY_FOREIGN_URL,
    )
    script = [httpx.Response(200, json={"code": "5000", "msg": msg, "data": None})]
    log = []
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ApiError) as caught:
            _execute(
                mode,
                method,
                script=script,
                provider_script=[_TOKEN],
                recorder=recorder,
                log=log,
                args=args,
                kwargs=kwargs,
            )
    err = caught.value
    assert type(err) is ApiError
    _privacy_wire(log[2], method)
    assert _entries(log, "invalidate") == []
    for text in (str(err), repr(err)):
        # Values that were actually sent may never be echoed back verbatim.
        for value in known:
            assert value not in text
        # The never-sent URL is free server text and stays in the error.
        assert _PRIVACY_FOREIGN_URL in text
        # Sent values may only surface in their masked key form.
        assert "accessToken=***" in text
        assert "consumerId=***" in text
    # Automatic log records and the diagnostic JSON show neither the sent
    # values nor the never-sent URL nor the raw-payload marker.
    forbidden = known + [_PRIVACY_FOREIGN_URL, _RAW_PAYLOAD_MARKER]
    for value in forbidden:
        assert value not in caplog.text
    if recorded:
        blob = recorder.to_json()
        for value in forbidden:
            assert value not in blob
        calls = _assert_outcomes(
            recorder, method, ["started", "failed"], mode, attempts=[1], retries=0
        )
        assert calls[-1]["error_family"] == "api"


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_privacy_success_response_not_a_leak(mode, method, recorded, caplog):
    """Sensitive response data stays verbatim on the returned ApiResponse.

    ``data``/``raw`` keep the synthetic consumerId, message body and URL — an
    SDK return value is the caller's own data, not a diagnostic leak. The
    repr, the diagnostic JSON and automatic log records still exclude them.
    """
    recorder = DiagnosticRecorder() if recorded else None
    args, kwargs = _privacy_args(method)
    payload = (
        {
            "code": "200",
            "msg": "OK",
            "data": {"consumerId": _PRIVACY_CONSUMER},
            "unknownField": "preserved",
        }
        if method == "create_consumer"
        else {
            "code": "200",
            "msg": "OK",
            "data": [
                {
                    "messageId": "m1",
                    "body": "body-" + _PRIVACY_CONSUMER,
                    "url": _PRIVACY_FOREIGN_URL,
                }
            ],
            "unknownField": "preserved",
        }
    )
    sensitive = [_PRIVACY_CONSUMER, _PRIVACY_FOREIGN_URL]
    log = []
    with caplog.at_level(logging.DEBUG):
        resp = _execute(
            mode,
            method,
            script=[httpx.Response(200, json=payload)],
            provider_script=[_TOKEN],
            recorder=recorder,
            log=log,
            args=args,
            kwargs=kwargs,
        )
    assert resp.data == payload["data"]
    assert resp.raw == payload
    assert resp.raw["unknownField"] == "preserved"
    _privacy_wire(log[2], method)
    shown = str(resp) + repr(resp)
    for value in sensitive + [_TOKEN.value, _RAW_PAYLOAD_MARKER]:
        assert value not in shown
        assert value not in caplog.text
    if recorded:
        blob = recorder.to_json()
        for value in sensitive + [_TOKEN.value, _RAW_PAYLOAD_MARKER]:
            assert value not in blob
        _assert_outcomes(recorder, method, ["started", "succeeded"], mode, attempts=[1], retries=0)


@pytest.mark.parametrize("recorded", (False, True))
@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("method", _METHODS)
def test_consumer_id_10002_business_code_collision(mode, method, recorded):
    """``consumer_id='10002'`` sent verbatim; raw code stays invalid-token.

    The colliding value ``"10002"`` is observed by two different layers:

    * ``AuthenticationError.invalid_token`` is derived from the raw upstream
      business code before any sent-value masking, so both methods raise
      ``AuthenticationError`` with ``invalid_token is True``.
    * The public ``err.code`` — and the diagnostic ``code_category``, which
      only enumerates the public code — reflects request-value masking.
      ``create_consumer`` sends just ``group1`` and the token, so
      ``err.code == "10002"`` and the category is ``"invalid_token"``.
      ``consume`` sends the caller-owned ``consumerId=10002`` verbatim; that
      sent value is masked, so ``err.code == "***"`` and the category is
      ``"other"`` (the family stays ``"authentication"`` from the type).

    In all cases the wire carries the literal consumer id, the sent token is
    invalidated exactly once and the write is never replayed.
    """
    recorder = DiagnosticRecorder() if recorded else None
    if method == "create_consumer":
        args, kwargs = ("group1",), {}
        expected_code, expected_category = "10002", "invalid_token"
    else:
        args, kwargs = ("10002",), {}
        expected_code, expected_category = "***", "other"
    script = [httpx.Response(200, json={"code": "10002", "msg": "expired", "data": None})]
    log = []
    with pytest.raises(AuthenticationError) as caught:
        _execute(
            mode,
            method,
            script=script,
            provider_script=[_TOKEN],
            recorder=recorder,
            log=log,
            args=args,
            kwargs=kwargs,
        )
    err = caught.value
    assert type(err) is AuthenticationError
    assert err.invalid_token is True
    # invalid_token comes from the raw business code; the public code is the
    # masked view and differs per method (see docstring) — exact, not either/or.
    assert err.code == expected_code
    # Exactly one get_token, one transport setup, one HTTP attempt, then one
    # invalidate of the sent token — never a retry or second get_token.
    assert log == [("get_token",), ("factory", _ORIGIN), log[2], ("invalidate", _TOKEN.value)]
    tag, verb, raw_path, content, _headers = log[2]
    assert tag == "http" and verb == "POST" and "?" not in raw_path
    fields = dict(parse_qsl(content, keep_blank_values=True))
    if method == "create_consumer":
        assert raw_path == "/api/lapp/mq/v2/consumer/group1"
        assert set(fields) == {"accessToken"}
        assert fields["accessToken"] == _TOKEN.value
    else:
        assert raw_path == "/api/lapp/mq/v2/consumer/messages"
        assert set(fields) == {"accessToken", "consumerId"}
        assert fields["accessToken"] == _TOKEN.value
        # The collision value is sent verbatim on the wire.
        assert fields["consumerId"] == "10002"
    if recorded:
        calls = _assert_outcomes(
            recorder, method, ["started", "failed"], mode, attempts=[1], retries=0
        )
        assert calls[-1]["error_family"] == "authentication"
        assert calls[-1]["code_category"] == expected_category
