"""Contract tests for the domestic ``devices.charging_status`` endpoint.

Only the outermost HTTP boundary is faked with ``httpx.MockTransport``; the
SDK facade, auth/retry pipeline and diagnostic hooks run for real.
"""

import asyncio
import contextlib
import logging

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

APP_KEY = "SYNTH_APP"
APP_SECRET = "SYNTH_SECRET"
HOST = "open.ys7.com"
AUTH_PATH = "/api/lapp/token/get"
BIZ_PATH = "/api/v3/device/power/status/get"
ENTRYPOINT = "devices.charging_status"
SERIAL = "AbC123XyZ"
SPACED_SERIAL = "has space"
TOKEN_A = "SYNTH_TOKEN_A"
TOKEN_B = "SYNTH_TOKEN_B"
MODES = ("sync", "async")


def _token_payload(token):
    return {
        "code": "200",
        "data": {
            "accessToken": token,
            "expireTime": 9999999999999,
            "areaDomain": "https://open.ys7.com",
        },
    }


def _ok_payload(power="0"):
    return {
        "code": "200",
        "msg": "ok",
        "extraRoot": {"z": 0},
        "data": {
            "valueInfo": {"powerStatus": power, "deep": {"k": 1}},
            "futureField": [1, 2],
        },
    }


def _kwargs(region, auth, biz, rec):
    return {
        "region": region,
        "auth_transport": httpx.MockTransport(auth),
        "transport_factory": lambda origin: httpx.MockTransport(biz),
        "diagnostic_recorder": rec,
    }


@contextlib.contextmanager
def _session(mode, auth, biz, region=None):
    """Yield ``(invoke(serial), recorder)`` for one fresh configured client."""
    rec = DiagnosticRecorder()
    kwargs = _kwargs(region or RegionConfig.china(), auth, biz, rec)
    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **kwargs) as client:
            yield client.devices.charging_status, rec
        return

    def invoke(serial):
        async def call():
            async with AsyncClient(APP_KEY, APP_SECRET, **kwargs) as client:
                return await client.devices.charging_status(serial)

        return asyncio.run(call())

    yield invoke, rec


def _auth(tokens, seen):
    seq = iter(tokens)

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=_token_payload(next(seq)))

    return handle


def _biz(steps, seen):
    seq = iter(steps)

    def handle(request):
        seen.append(request)
        step = next(seq)
        if callable(step):
            return step(request)
        return httpx.Response(200, json=step)

    return handle


def _timeout(request):
    raise httpx.ReadTimeout("synthetic timeout", request=request)


def _check_biz_request(request, serial, token):
    assert request.method == "GET"
    assert request.url.scheme == "https" and request.url.host == HOST
    assert request.url.path == BIZ_PATH and request.url.query == b""
    assert request.headers.get_list("accessToken") == [token]
    assert request.headers.get_list("deviceSerial") == [serial]
    names = {key.lower() for key in request.headers.keys()}
    assert "content-type" not in names and "channel" not in names
    # MockTransport already read the body before dispatching to the handler.
    assert request.content == b""


def _events(rec, kind):
    return [e for e in rec.report()["events"] if e.get("kind") == kind]


def _check_call(rec, mode, terminal):
    """Assert one call scope with the expected started/terminal pair."""
    calls = [
        e
        for e in _events(rec, "call")
        if e.get("entrypoint") == ENTRYPOINT and e.get("mode") == mode
    ]
    assert [e.get("outcome") for e in calls] == ["started", terminal]


def _check_attempts(rec, mode, outcomes):
    """Assert the per-attempt started/terminal outcome sequence.

    ``outcomes`` is the flat ordered list, e.g. a single success is
    ``["started", "succeeded"]`` and a refresh retry is
    ``["started", "failed", "started", "succeeded"]``.
    """
    attempts = [
        e
        for e in _events(rec, "attempt")
        if e.get("entrypoint") == "client.request"
        and e.get("operation") == ENTRYPOINT
        and e.get("mode") == mode
    ]
    assert [e.get("outcome") for e in attempts] == outcomes


def _check_retries(rec, count):
    retries = [
        e
        for e in _events(rec, "retry")
        if e.get("entrypoint") == "client.request" and e.get("operation") == ENTRYPOINT
    ]
    assert len(retries) == count
    assert all(e.get("outcome") == "scheduled" for e in retries)
    assert all(e.get("attempt") == 2 for e in retries)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("serial", (SERIAL, SPACED_SERIAL))
@pytest.mark.parametrize(
    "power", ("0", "1", "2", "3", "4", "9", "https://future.example/power?x=1")
)
def test_success(mode, serial, power):
    auth_seen, biz_seen = [], []
    with _session(mode, _auth([TOKEN_A], auth_seen), _biz([_ok_payload(power)], biz_seen)) as (
        invoke,
        rec,
    ):
        resp = invoke(serial)
    assert resp.code == "200"
    assert resp.message == "ok"
    assert resp.operation == ENTRYPOINT
    assert resp.http_status == 200
    assert resp.data["valueInfo"]["powerStatus"] == power
    assert resp.data["valueInfo"]["deep"] == {"k": 1}
    assert resp.data["futureField"] == [1, 2]
    assert resp.raw["extraRoot"] == {"z": 0}
    assert resp.raw["data"]["valueInfo"]["powerStatus"] == power
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    # The serial header value travels verbatim, including interior spaces.
    _check_biz_request(biz_seen[0], serial, TOKEN_A)
    req = auth_seen[0]
    assert req.method == "POST" and req.url.path == AUTH_PATH
    assert req.url.scheme == "https" and req.url.host == HOST
    _check_call(rec, mode, "succeeded")
    _check_attempts(rec, mode, ["started", "succeeded"])
    _check_retries(rec, 0)


_BAD_DATA = [
    {"code": "200"},
    {"code": "200", "data": None},
    {"code": "200", "data": [1]},
    {"code": "200", "data": {}},
    {"code": "200", "data": {"valueInfo": None}},
    {"code": "200", "data": {"valueInfo": "oops"}},
    {"code": "200", "data": {"valueInfo": {}}},
] + [{"code": "200", "data": {"valueInfo": {"powerStatus": v}}} for v in (3, True, None)]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("payload", _BAD_DATA)
def test_malformed_data(mode, payload):
    auth_seen, biz_seen = [], []
    with _session(mode, _auth([TOKEN_A], auth_seen), _biz([payload], biz_seen)) as (invoke, rec):
        with pytest.raises(ProtocolError) as err:
            invoke(SERIAL)
    assert err.value.operation == ENTRYPOINT
    assert err.value.http_status == 200
    # The postcondition never copies the business code onto the error.
    assert err.value.code is None
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    _check_call(rec, mode, "failed")
    # Transport succeeded; the failure is postcondition payload parsing.
    _check_attempts(rec, mode, ["started", "succeeded"])
    _check_retries(rec, 0)


# Scalar codes outside the success set decode to a typed business ApiError
# carrying the original scalar verbatim (int 200 is not the success "200").
_BAD_CODE = [
    {"code": 200, "data": {"valueInfo": {"powerStatus": "0"}}},
    {"code": "500", "data": {"valueInfo": {"powerStatus": "0"}}},
    {"code": "0", "data": {"valueInfo": {"powerStatus": "0"}}},
]

# Missing or non-scalar business-code fields are protocol violations (no .code).
_BAD_META = [
    {"data": {"valueInfo": {"powerStatus": "0"}}},
    {"code": None, "data": {"valueInfo": {"powerStatus": "0"}}},
    {"code": True, "data": {"valueInfo": {"powerStatus": "0"}}},
    {"code": {"x": 1}, "data": {"valueInfo": {"powerStatus": "0"}}},
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("payload", _BAD_CODE)
def test_non_success_code_typed_error(mode, payload):
    auth_seen, biz_seen = [], []
    with _session(mode, _auth([TOKEN_A], auth_seen), _biz([payload], biz_seen)) as (invoke, rec):
        with pytest.raises(ApiError) as err:
            invoke(SERIAL)
    # A non-success scalar is a typed business error, not a protocol fault.
    assert not isinstance(err.value, ProtocolError)
    assert err.value.code == payload["code"]
    assert err.value.operation == ENTRYPOINT
    assert err.value.http_status == 200
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, ["started", "failed"])
    _check_retries(rec, 0)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("payload", _BAD_META)
def test_bad_meta_rejected(mode, payload):
    auth_seen, biz_seen = [], []
    with _session(mode, _auth([TOKEN_A], auth_seen), _biz([payload], biz_seen)) as (invoke, rec):
        with pytest.raises(ProtocolError) as err:
            invoke(SERIAL)
    assert err.value.code is None
    assert err.value.http_status == 200
    assert err.value.operation == ENTRYPOINT
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, ["started", "failed"])
    _check_retries(rec, 0)


# Only leading/trailing whitespace, C0 controls, DEL, non-ASCII, empty and
# non-strings are rejected; an interior space ("has space") is a legal header.
_BAD_SERIALS = [
    " lead",
    "trail ",
    "tab\tchar",
    "nl\nchar",
    "cr\rchar",
    "del\x7fchar",
    "ctl\x01char",
    "batt電",
    "café",
    "   ",
    "",
    None,
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "serial,region", [(s, "china") for s in _BAD_SERIALS] + [(SERIAL, "overseas")]
)
def test_rejected_before_send(mode, serial, region):
    auth_seen, biz_seen = [], []
    cfg = RegionConfig.overseas() if region == "overseas" else RegionConfig.china()
    with _session(
        mode, _auth([TOKEN_A], auth_seen), _biz([_ok_payload()], biz_seen), region=cfg
    ) as (invoke, rec):
        with pytest.raises(ValueError):
            invoke(serial)
    # Validation happens before any authentication or HTTP request.
    assert auth_seen == [] and biz_seen == []
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, [])
    _check_retries(rec, 0)


@pytest.mark.parametrize("mode", MODES)
def test_closed_client_rejected(mode):
    rec = DiagnosticRecorder()
    seen = []
    kwargs = _kwargs(RegionConfig.china(), _auth([TOKEN_A], seen), _biz([_ok_payload()], seen), rec)
    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **kwargs) as client:
            pass
        with pytest.raises(TransportError) as err:
            client.devices.charging_status(SERIAL)
        assert err.value.reason == "closed"
        assert err.value.operation == ENTRYPOINT
    else:

        async def make_closed():
            async with AsyncClient(APP_KEY, APP_SECRET, **kwargs) as client:
                return client

        client = asyncio.run(make_closed())
        with pytest.raises(TransportError) as err:
            asyncio.run(client.devices.charging_status(SERIAL))
        assert err.value.reason == "closed"
        assert err.value.operation == ENTRYPOINT
    # A closed client never reaches auth or the business transport.
    assert seen == []
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, [])
    _check_retries(rec, 0)


@pytest.mark.parametrize("mode", MODES)
def test_token_refresh_replays_once(mode):
    auth_seen, biz_seen = [], []

    def expired_once(request):
        if request.headers.get_list("accessToken") != [TOKEN_A]:
            return httpx.Response(500, text="unexpected token")
        return httpx.Response(200, json={"code": "10002"})

    with _session(
        mode, _auth([TOKEN_A, TOKEN_B], auth_seen), _biz([expired_once, _ok_payload("1")], biz_seen)
    ) as (invoke, rec):
        resp = invoke(SERIAL)
    assert resp.code == "200" and resp.message == "ok"
    assert resp.data["valueInfo"]["powerStatus"] == "1"
    assert len(auth_seen) == 2 and len(biz_seen) == 2
    assert [r.headers.get_list("accessToken") for r in biz_seen] == [[TOKEN_A], [TOKEN_B]]
    _check_biz_request(biz_seen[1], SERIAL, TOKEN_B)
    _check_call(rec, mode, "succeeded")
    _check_attempts(rec, mode, ["started", "failed", "started", "succeeded"])
    _check_retries(rec, 1)


@pytest.mark.parametrize("mode", MODES)
def test_token_refresh_gives_up(mode):
    auth_seen, biz_seen = [], []
    with _session(
        mode, _auth([TOKEN_A, TOKEN_B], auth_seen), _biz([{"code": "10002"}] * 2, biz_seen)
    ) as (invoke, rec):
        with pytest.raises(AuthenticationError) as err:
            invoke(SERIAL)
    assert err.value.code == "10002"
    assert err.value.operation == ENTRYPOINT
    assert len(auth_seen) == 2 and len(biz_seen) == 2
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, ["started", "failed", "started", "failed"])
    _check_retries(rec, 1)


@pytest.mark.parametrize("mode", MODES)
def test_permission_denied_not_retried(mode):
    auth_seen, biz_seen = [], []
    step = {"code": "10031", "msg": "denied"}
    with _session(mode, _auth([TOKEN_A], auth_seen), _biz([step], biz_seen)) as (invoke, rec):
        with pytest.raises(PermissionDeniedError) as err:
            invoke(SERIAL)
    assert err.value.code == "10031"
    assert err.value.operation == ENTRYPOINT
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, ["started", "failed"])
    _check_retries(rec, 0)


@pytest.mark.parametrize("mode", MODES)
def test_transport_timeout_not_retried(mode):
    auth_seen, biz_seen = [], []
    with _session(mode, _auth([TOKEN_A], auth_seen), _biz([_timeout], biz_seen)) as (invoke, rec):
        with pytest.raises(TransportError) as err:
            invoke(SERIAL)
    assert err.value.reason == "timeout"
    assert err.value.operation == ENTRYPOINT
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, ["started", "failed"])
    _check_retries(rec, 0)


@pytest.mark.parametrize("mode", MODES)
def test_error_details_redacted(mode, caplog):
    token, serial = "TKN-CANARY-7fQa", "SER-CANARY-Zz19"
    marker = "free-marker"
    locator = f"https://locator.invalid/{marker}"
    auth_seen, biz_seen = [], []
    payload = {"code": "10031", "msg": f"deny {locator} echo {serial} {token}"}
    with caplog.at_level(logging.DEBUG):
        with _session(mode, _auth([token], auth_seen), _biz([payload], biz_seen)) as (invoke, rec):
            with pytest.raises(PermissionDeniedError) as err:
                invoke(serial)
    assert len(auth_seen) == 1 and len(biz_seen) == 1
    info_records = [
        r
        for r in caplog.records
        if r.name.startswith(("httpx", "httpcore")) and r.levelno == logging.INFO
    ]
    assert info_records
    for text in (
        str(err.value),
        repr(err.value),
        str(rec.report()),
        caplog.text,
        *(r.getMessage() for r in info_records),
    ):
        assert token not in text
        assert serial not in text
        assert locator not in text
        assert marker not in text
    _check_call(rec, mode, "failed")
    _check_attempts(rec, mode, ["started", "failed"])
    _check_retries(rec, 0)


def test_async_attempt_cancellation_propagates():
    """A cancelled async attempt stays cancelled: no retry, no swallow."""
    rec = DiagnosticRecorder()
    auth_seen, biz_seen = [], []

    def _cancel(request):
        raise asyncio.CancelledError

    kwargs = _kwargs(
        RegionConfig.china(),
        _auth([TOKEN_A], auth_seen),
        _biz([_cancel], biz_seen),
        rec,
    )

    async def cancelled_call():
        async with AsyncClient(APP_KEY, APP_SECRET, **kwargs) as client:
            return await client.devices.charging_status(SERIAL)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_call())
    # The auth request and exactly one business attempt each ran once.
    assert len(auth_seen) == 1
    assert len(biz_seen) == 1
    _check_call(rec, "async", "cancelled")
    _check_attempts(rec, "async", ["started", "cancelled"])
    _check_retries(rec, 0)
