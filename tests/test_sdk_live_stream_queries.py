"""Offline wire-contract tests for ``client.live_streams`` queries.

Covers the list / records / address operations in sync and async mode.
Every transport is mocked with ``httpx.MockTransport``; no live endpoint is
contacted and every identifier is a synthetic sentinel.
"""

import asyncio
from datetime import datetime, timedelta
from urllib.parse import parse_qs

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
TOKEN_A = "TOKEN_A"
TOKEN_B = "TOKEN_B"
CHINA_HOST = "open.ys7.com"
AUTH_PATH = "/api/lapp/token/get"
LIST_PATH = "/api/service/media/streammanage/stream/list"
RECORDS_PATH = "/api/service/media/streammanage/stream/record/list"
ADDRESS_PATH = "/api/service/media/streammanage/stream/address"
MEDIA_URL = "rtmps://media.invalid.example/live/SYNTH_ADDR"

START = datetime(2024, 1, 2, 3, 4, 5)
END = datetime(2024, 1, 5, 6, 7, 8)

# ``list`` projects ``data`` as a bare array of row dicts. Pagination lives in
# ``meta.moreInfo``, not inside ``data``; mixed ``streamId`` types and unknown
# per-row extension fields are preserved verbatim.
LIST_BODY = {
    "meta": {
        "code": 200,
        "message": "ok",
        "moreInfo": {"pageStart": 0, "pageSize": 50, "total": 2, "quota": "synthetic"},
    },
    "data": [
        {"streamId": "SYN-1", "deviceSerial": "SER1", "status": 1, "extra": "raw"},
        {"streamId": 42, "deviceSerial": "SER2", "status": 0},
    ],
}

# ``records`` projects ``data`` as a dict carrying the ``recordList`` array.
# Pagination lives in ``meta.moreInfo``; unknown row fields are preserved.
RECORDS_BODY = {
    "meta": {
        "code": 200,
        "message": "ok",
        "moreInfo": {"pageStart": 2, "pageSize": 10, "total": 1},
    },
    "data": {
        "recordList": [{"recordId": "REC-1", "streamId": 123, "startTime": "2024-01-02 03:04:05"}],
    },
}

# ``address`` requires a non-blank ``data.address``; unknown siblings pass through.
ADDRESS_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {
        "address": MEDIA_URL,
        "extra": "kept",
        "expireTime": "2099-01-01 00:00:00",
    },
}


def _auth_body(token):
    return {
        "code": "200",
        "data": {
            "accessToken": token,
            "expireTime": 9999999999999,
            "areaDomain": "https://open.ys7.com",
        },
    }


def _auth_transport(log, tokens=(TOKEN_A,)):
    def handler(request):
        index = len(log)
        log.append(request)
        return httpx.Response(200, json=_auth_body(tokens[min(index, len(tokens) - 1)]))

    return httpx.MockTransport(handler)


def _business_transport(log, responder):
    def handler(request):
        log.append(request)
        if callable(responder):
            return responder(request)
        return httpx.Response(200, json=responder)

    return httpx.MockTransport(handler)


def _sync_client(auth, business, recorder, region=None):
    return Client(
        APP_KEY,
        APP_SECRET,
        region=region or RegionConfig.china(),
        auth_transport=auth,
        transport_factory=lambda origin: business,
        diagnostic_recorder=recorder,
    )


def _async_client(auth, business, recorder):
    return AsyncClient(
        APP_KEY,
        APP_SECRET,
        region=RegionConfig.china(),
        auth_transport=auth,
        transport_factory=lambda origin: business,
        diagnostic_recorder=recorder,
    )


def _form(request):
    return parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def outcomes(rec, kind, name, mode):
    key = "entrypoint" if kind == "call" else "operation"
    return [
        e["outcome"]
        for e in rec.report()["events"]
        if e["kind"] == kind and e[key] == name and e["mode"] == mode
    ]


def _attempt_events(rec, operation):
    return [
        e for e in rec.report()["events"] if e["kind"] == "attempt" and e["operation"] == operation
    ]


def _retry_events(rec, operation):
    return [
        e for e in rec.report()["events"] if e["kind"] == "retry" and e["operation"] == operation
    ]


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_uses_post_form_serial_header_and_preserves_raw():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests)
    business = _business_transport(business_requests, LIST_BODY)
    with _sync_client(auth, business, rec) as client:
        result = client.live_streams.list(
            device_serial="SER1", stream_id="SYN-9", access_type=1, status=0
        )

    # Exactly one business request, issued as POST form — no GET, no follow-up.
    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "POST"
    assert request.url.path == LIST_PATH
    assert request.url.host == CHINA_HOST
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert dict(request.url.params) == {}

    # Serial travels ONLY as a header; paging defaults and non-empty optional
    # filters are form fields.
    assert request.headers["deviceSerial"] == "SER1"
    assert request.headers["accessToken"] == TOKEN_A
    assert _form(request) == {
        "streamId": ["SYN-9"],
        "pageStart": ["0"],
        "pageSize": ["50"],
        "accessType": ["1"],
        "status": ["0"],
    }

    # Raw projection: ``data`` is the bare row array; mixed streamId types,
    # unknown per-row fields and meta.moreInfo are all preserved untouched.
    assert result.data == LIST_BODY["data"]
    assert result.data[0]["streamId"] == "SYN-1"
    assert result.data[1]["streamId"] == 42
    assert result.data[0]["extra"] == "raw"
    assert result.raw == LIST_BODY
    assert result.raw["meta"]["moreInfo"] == {
        "pageStart": 0,
        "pageSize": 50,
        "total": 2,
        "quota": "synthetic",
    }
    assert result.message == "ok"
    assert result.code == 200
    assert result.operation == "live_streams.list"

    # Auth endpoint hit exactly once for the token.
    assert len(auth_requests) == 1
    assert auth_requests[0].method == "POST"
    assert auth_requests[0].url.path == AUTH_PATH
    assert auth_requests[0].url.host == CHINA_HOST

    assert outcomes(rec, "call", "live_streams.list", "sync") == ["started", "succeeded"]
    assert outcomes(rec, "attempt", "live_streams.list", "sync") == ["started", "succeeded"]
    assert all(
        e["entrypoint"] == "client.request" for e in _attempt_events(rec, "live_streams.list")
    )


def test_list_without_filters_sends_only_paging():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, LIST_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        result = client.live_streams.list()

    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "POST"
    assert _form(request) == {"pageStart": ["0"], "pageSize": ["50"]}
    assert "deviceSerial" not in request.headers
    assert result.data[1]["streamId"] == 42


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


def test_records_uses_exact_get_query_and_keeps_pagination_in_raw():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, RECORDS_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        result = client.live_streams.records(
            123, start_time=START, end_time=END, page_start=2, page_size=10
        )

    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "GET"
    assert request.url.path == RECORDS_PATH
    assert request.url.host == CHINA_HOST
    # streamId as a plain decimal; same-domain second-precision time format.
    assert dict(request.url.params) == {
        "streamId": "123",
        "startTime": "2024-01-02 03:04:05",
        "endTime": "2024-01-05 06:07:08",
        "pageStart": "2",
        "pageSize": "10",
    }
    # No body and no deviceSerial header for records.
    assert request.content == b""
    assert "deviceSerial" not in request.headers
    assert request.headers["accessToken"] == TOKEN_A

    # data.recordList plus pagination preserved verbatim in the raw payload.
    assert result.data == RECORDS_BODY["data"]
    assert result.data["recordList"][0]["recordId"] == "REC-1"
    assert result.raw["meta"]["moreInfo"] == {
        "pageStart": 2,
        "pageSize": 10,
        "total": 1,
    }
    assert result.raw == RECORDS_BODY
    assert result.operation == "live_streams.records"
    assert result.code == 200
    assert result.message == "ok"

    assert outcomes(rec, "call", "live_streams.records", "sync") == ["started", "succeeded"]
    assert outcomes(rec, "attempt", "live_streams.records", "sync") == [
        "started",
        "succeeded",
    ]


# ---------------------------------------------------------------------------
# address
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address_type", [1, 2])
def test_address_posts_form_with_explicit_type(address_type):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, ADDRESS_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        result = client.live_streams.address(
            7,
            2,
            address_type=address_type,
            quality=1,
            support_h265=1,
            mute=0,
            expire_time=3600,
        )

    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "POST"
    assert request.url.path == ADDRESS_PATH
    assert dict(request.url.params) == {}
    assert request.headers["accessToken"] == TOKEN_A
    assert "deviceSerial" not in request.headers
    assert _form(request) == {
        "streamId": ["7"],
        "protocol": ["2"],
        "type": [str(address_type)],
        "quality": ["1"],
        "supportH265": ["1"],
        "mute": ["0"],
        "expireTime": ["3600"],
    }
    assert result.data == ADDRESS_BODY["data"]
    assert result.operation == "live_streams.address"
    assert outcomes(rec, "call", "live_streams.address", "sync") == [
        "started",
        "succeeded",
    ]
    assert outcomes(rec, "attempt", "live_streams.address", "sync") == [
        "started",
        "succeeded",
    ]


def test_address_defaults_and_never_opens_media_url():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, ADDRESS_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        result = client.live_streams.address(7, 3)

    request = business_requests[0]
    assert _form(request) == {
        "streamId": ["7"],
        "protocol": ["3"],
        "type": ["1"],
    }
    assert result.data["address"] == MEDIA_URL
    # Unknown sibling fields travel through untouched.
    assert result.data["extra"] == "kept"

    # The SDK must not dereference the issued media URL: the only requests on
    # either transport are the token call and the address POST.
    assert len(business_requests) == 1
    assert len(auth_requests) == 1
    seen = [str(r.url) for r in auth_requests + business_requests]
    assert all("media.invalid.example" not in url for url in seen)


def test_address_does_not_infer_ingress_from_protocol():
    # type=2 / expireTime describe an RTMP ingest window, yet the caller is
    # still free to request a pull protocol: the SDK submits protocol=3 (FLV)
    # verbatim and never rewrites it to match the ingest type.
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, ADDRESS_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        result = client.live_streams.address(9, 3, address_type=2, expire_time=600)

    assert _form(business_requests[0]) == {
        "streamId": ["9"],
        "protocol": ["3"],
        "type": ["2"],
        "expireTime": ["600"],
    }
    assert result.code == 200


def test_address_rejects_unknown_protocol_before_auth():
    # protocol is a closed enum: 1=HLS, 2=RTMP, 3=FLV. Anything else is a
    # client-side validation failure before any credential or wire traffic.
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, ADDRESS_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        with pytest.raises(ValueError):
            client.live_streams.address(9, 4)

    assert auth_requests == []
    assert business_requests == []
    assert outcomes(rec, "call", "live_streams.address", "sync") == [
        "started",
        "failed",
    ]
    assert outcomes(rec, "attempt", "live_streams.address", "sync") == []


# ---------------------------------------------------------------------------
# pre-auth validation
# ---------------------------------------------------------------------------


def test_validation_rejects_before_any_auth():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, LIST_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        with pytest.raises(ValueError):
            client.live_streams.list(page_size=0)
        assert outcomes(rec, "call", "live_streams.list", "sync") == [
            "started",
            "failed",
        ]
        assert outcomes(rec, "attempt", "live_streams.list", "sync") == []

        invalid_calls = [
            lambda c: c.live_streams.list(page_size=101),
            lambda c: c.live_streams.list(page_size=True),
            lambda c: c.live_streams.list(page_size="10"),
            lambda c: c.live_streams.list(page_start=-1),
            lambda c: c.live_streams.list(device_serial="   "),
            lambda c: c.live_streams.list(device_serial=42),
            lambda c: c.live_streams.list(stream_id=""),
            lambda c: c.live_streams.list(status=7),
            lambda c: c.live_streams.list(access_type=9),
            lambda c: c.live_streams.records("abc", start_time=START, end_time=END),
            lambda c: c.live_streams.records(True, start_time=START, end_time=END),
            lambda c: c.live_streams.records(1, start_time="now", end_time=END),
            lambda c: c.live_streams.address("7", 1),
            lambda c: c.live_streams.address(7, "x"),
            lambda c: c.live_streams.address(7, True),
            lambda c: c.live_streams.address(7, 0),
            lambda c: c.live_streams.address(7, 1, address_type=3),
            lambda c: c.live_streams.address(7, 1, quality="high"),
            lambda c: c.live_streams.address(7, 1, expire_time=True),
        ]
        for call in invalid_calls:
            with pytest.raises(ValueError):
                call(client)

    # Every rejection happens before credential retrieval or business traffic.
    assert auth_requests == []
    assert business_requests == []


def test_records_window_validation_is_pre_auth():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, RECORDS_BODY)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        # Inverted window.
        with pytest.raises(ValueError):
            client.live_streams.records(1, start_time=END, end_time=START)
        # Interval beyond the documented 30-day cap.
        with pytest.raises(ValueError):
            client.live_streams.records(1, start_time=START, end_time=START + timedelta(days=31))
    assert auth_requests == []
    assert business_requests == []


# ---------------------------------------------------------------------------
# response-shape and error mapping
# ---------------------------------------------------------------------------


def test_string_meta_code_is_not_success():
    # ``meta.code`` is declared an Int for this envelope; the string '200' is a
    # scalar but not the documented success value, so it maps to a business
    # ApiError carrying the verbatim code, not a ProtocolError.
    body = {"meta": {"code": "200", "message": "ok"}, "data": []}
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, body)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        with pytest.raises(ApiError) as excinfo:
            client.live_streams.list()
    assert excinfo.value.code == "200"
    assert not isinstance(
        excinfo.value,
        (AuthenticationError, PermissionDeniedError, ProtocolError),
    )
    assert outcomes(rec, "call", "live_streams.list", "sync") == ["started", "failed"]
    assert outcomes(rec, "attempt", "live_streams.list", "sync") == [
        "started",
        "failed",
    ]


def test_malformed_data_raises_protocol_error_after_successful_attempt():
    body = {"meta": {"code": 200, "message": "ok"}, "data": "oops"}
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, body)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        with pytest.raises(ProtocolError):
            client.live_streams.records(1, start_time=START, end_time=END)

    # The business request itself succeeded; the postcondition failed.
    assert len(business_requests) == 1
    assert outcomes(rec, "attempt", "live_streams.records", "sync") == [
        "started",
        "succeeded",
    ]
    assert outcomes(rec, "call", "live_streams.records", "sync") == ["started", "failed"]


@pytest.mark.parametrize(
    "meta_code, exc_type",
    [(10031, PermissionDeniedError), (10001, ApiError)],
)
def test_business_error_mapping(meta_code, exc_type):
    body = {"meta": {"code": meta_code, "message": "synthetic failure"}}
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, body)
    with _sync_client(_auth_transport(auth_requests), business, rec) as client:
        with pytest.raises(exc_type) as excinfo:
            client.live_streams.address(7, 1)
    assert excinfo.value.code == meta_code
    if exc_type is ApiError:
        assert not isinstance(excinfo.value, (AuthenticationError, PermissionDeniedError))
    assert outcomes(rec, "call", "live_streams.address", "sync") == ["started", "failed"]


# ---------------------------------------------------------------------------
# both-mode driver
# ---------------------------------------------------------------------------

# The three query operations exercised uniformly below. ``op`` is the public
# facade method name; ``args``/``kwargs`` build one valid request per operation.
LIST_CALL = ("list", (), {})
RECORDS_CALL = ("records", (5,), {"start_time": START, "end_time": END})
ADDRESS_CALL = ("address", (7, 1), {})


def _invoke(mode, call, recorder, auth_transport, biz_transport, region=None):
    """Run ``client.live_streams.<op>(*args, **kwargs)`` in the chosen mode."""
    op, args, kwargs = call
    options = dict(
        region=region or RegionConfig.china(),
        auth_transport=auth_transport,
        transport_factory=lambda origin: biz_transport,
        diagnostic_recorder=recorder,
    )
    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **options) as client:
            return getattr(client.live_streams, op)(*args, **kwargs)

    async def _run():
        async with AsyncClient(APP_KEY, APP_SECRET, **options) as client:
            return await getattr(client.live_streams, op)(*args, **kwargs)

    return asyncio.run(_run())


def _op_name(call):
    return "live_streams." + call[0]


# ---------------------------------------------------------------------------
# wire shape, both modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_posts_form(mode):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests)
    business = _business_transport(business_requests, LIST_BODY)
    result = _invoke(mode, LIST_CALL, rec, auth, business)

    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "POST"
    assert request.url.path == LIST_PATH
    assert _form(request) == {"pageStart": ["0"], "pageSize": ["50"]}
    assert "deviceSerial" not in request.headers
    assert result.data == LIST_BODY["data"]
    assert result.operation == "live_streams.list"
    assert outcomes(rec, "call", "live_streams.list", mode) == ["started", "succeeded"]
    assert outcomes(rec, "attempt", "live_streams.list", mode) == [
        "started",
        "succeeded",
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_records_exact_query(mode):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, RECORDS_BODY)
    result = _invoke(mode, RECORDS_CALL, rec, _auth_transport(auth_requests), business)

    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "GET"
    assert request.url.path == RECORDS_PATH
    assert dict(request.url.params) == {
        "streamId": "5",
        "startTime": "2024-01-02 03:04:05",
        "endTime": "2024-01-05 06:07:08",
        "pageStart": "0",
        "pageSize": "50",
    }
    assert "deviceSerial" not in request.headers
    assert result.data == RECORDS_BODY["data"]
    assert outcomes(rec, "call", "live_streams.records", mode) == [
        "started",
        "succeeded",
    ]
    assert outcomes(rec, "attempt", "live_streams.records", mode) == [
        "started",
        "succeeded",
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_address_post_form(mode):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, ADDRESS_BODY)
    result = _invoke(mode, ADDRESS_CALL, rec, _auth_transport(auth_requests), business)

    assert len(business_requests) == 1
    request = business_requests[0]
    assert request.method == "POST"
    assert request.url.path == ADDRESS_PATH
    assert _form(request) == {
        "streamId": ["7"],
        "protocol": ["1"],
        "type": ["1"],
    }
    assert result.data == ADDRESS_BODY["data"]
    assert result.data["address"] == MEDIA_URL
    assert outcomes(rec, "call", "live_streams.address", mode) == [
        "started",
        "succeeded",
    ]
    assert outcomes(rec, "attempt", "live_streams.address", mode) == [
        "started",
        "succeeded",
    ]


# ---------------------------------------------------------------------------
# empty pages, both modes: an empty result set is a valid first page and
# never triggers a follow-up request.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_empty_page_accepted(mode):
    body = {"meta": {"code": 200, "message": "ok"}, "data": []}
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, body)
    result = _invoke(mode, LIST_CALL, rec, _auth_transport(auth_requests), business)

    assert result.data == []
    assert len(auth_requests) == 1
    assert len(business_requests) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_records_empty_page_accepted(mode):
    body = {
        "meta": {"code": 200, "message": "ok"},
        "data": {"recordList": []},
    }
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, body)
    result = _invoke(mode, RECORDS_CALL, rec, _auth_transport(auth_requests), business)

    assert result.data == {"recordList": []}
    assert len(auth_requests) == 1
    assert len(business_requests) == 1


# ---------------------------------------------------------------------------
# token refresh / retry semantics, both modes.
#
# ``list`` and ``records`` are read-only: a 10002 reply triggers exactly one
# token re-issue and one replayed business attempt. ``address`` issues a new
# locator (read_only=False): it is never replayed on 10002 and never retried
# on a transport timeout.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "call, success_body",
    [
        (LIST_CALL, LIST_BODY),
        (RECORDS_CALL, RECORDS_BODY),
    ],
    ids=["list", "records"],
)
def test_read_refreshes_token_once_on_10002(mode, call, success_body):
    responses = iter(
        [
            {"meta": {"code": 10002, "message": "token expired"}},
            success_body,
        ]
    )
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests, tokens=(TOKEN_A, TOKEN_B))
    business = _business_transport(
        business_requests, lambda request: httpx.Response(200, json=next(responses))
    )
    result = _invoke(mode, call, rec, auth, business)

    name = _op_name(call)
    # One refresh, one business retry, and the retry carries the new token.
    assert len(auth_requests) == 2
    assert len(business_requests) == 2
    assert business_requests[0].headers["accessToken"] == TOKEN_A
    assert business_requests[1].headers["accessToken"] == TOKEN_B
    assert result.data == success_body["data"]

    assert outcomes(rec, "call", name, mode) == ["started", "succeeded"]
    assert outcomes(rec, "attempt", name, mode) == [
        "started",
        "failed",
        "started",
        "succeeded",
    ]
    # Exactly one scheduled retry, always targeting the second attempt.
    retries = _retry_events(rec, name)
    assert len(retries) == 1
    assert retries[0]["outcome"] == "scheduled"
    assert retries[0]["attempt"] == 2
    # Inner credential traffic must not leak into this operation's attempts.
    assert all(e["entrypoint"] == "client.request" for e in _attempt_events(rec, name))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("call", [LIST_CALL, RECORDS_CALL], ids=["list", "records"])
def test_read_refresh_is_bounded(mode, call):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests, tokens=(TOKEN_A, TOKEN_B, "TOKEN_C"))
    business = _business_transport(
        business_requests,
        {"meta": {"code": 10002, "message": "token expired"}},
    )
    with pytest.raises(AuthenticationError) as excinfo:
        _invoke(mode, call, rec, auth, business)

    name = _op_name(call)
    assert excinfo.value.code == 10002
    # Single refresh: no credential retrieval beyond one re-issue, no extra
    # business attempts after the retried request also fails.
    assert len(auth_requests) == 2
    assert len(business_requests) == 2
    assert outcomes(rec, "call", name, mode) == ["started", "failed"]
    assert outcomes(rec, "attempt", name, mode) == [
        "started",
        "failed",
        "started",
        "failed",
    ]
    assert len(_retry_events(rec, name)) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_address_never_retries_on_10002(mode):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests, tokens=(TOKEN_A, TOKEN_B))
    business = _business_transport(
        business_requests,
        {"meta": {"code": 10002, "message": "token expired"}},
    )
    with pytest.raises(AuthenticationError) as excinfo:
        _invoke(mode, ADDRESS_CALL, rec, auth, business)

    assert excinfo.value.code == 10002
    # Issuance is not replay-safe: no refresh, no retry.
    assert len(auth_requests) == 1
    assert len(business_requests) == 1
    assert outcomes(rec, "call", "live_streams.address", mode) == ["started", "failed"]
    assert outcomes(rec, "attempt", "live_streams.address", mode) == [
        "started",
        "failed",
    ]
    assert _retry_events(rec, "live_streams.address") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_address_read_timeout_maps_transport_error_no_retry(mode):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests)
    business = _business_transport(business_requests, timeout)
    with pytest.raises(TransportError) as excinfo:
        _invoke(mode, ADDRESS_CALL, rec, auth, business)

    assert excinfo.value.reason == "timeout"
    assert len(auth_requests) == 1
    assert len(business_requests) == 1
    assert outcomes(rec, "call", "live_streams.address", mode) == ["started", "failed"]
    assert outcomes(rec, "attempt", "live_streams.address", mode) == [
        "started",
        "failed",
    ]
    assert _retry_events(rec, "live_streams.address") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_token_is_reused_across_calls(mode):
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, LIST_BODY)
    auth = _auth_transport(auth_requests)
    options = dict(
        region=RegionConfig.china(),
        auth_transport=auth,
        transport_factory=lambda origin: business,
        diagnostic_recorder=rec,
    )
    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **options) as client:
            client.live_streams.list()
            client.live_streams.list()
    else:

        async def _run():
            async with AsyncClient(APP_KEY, APP_SECRET, **options) as client:
                await client.live_streams.list()
                await client.live_streams.list()

        asyncio.run(_run())

    assert len(auth_requests) == 1
    assert len(business_requests) == 2


# ---------------------------------------------------------------------------
# malformed data / region guard, both modes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("call", [LIST_CALL, RECORDS_CALL], ids=["list", "records"])
def test_malformed_data_is_protocol_error_after_attempt(mode, call):
    body = {"meta": {"code": 200, "message": "ok"}, "data": "oops"}
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    business = _business_transport(business_requests, body)
    with pytest.raises(ProtocolError):
        _invoke(mode, call, rec, _auth_transport(auth_requests), business)

    name = _op_name(call)
    # The business request itself succeeded; the postcondition failed.
    assert len(business_requests) == 1
    assert outcomes(rec, "attempt", name, mode) == ["started", "succeeded"]
    assert outcomes(rec, "call", name, mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "call", [LIST_CALL, RECORDS_CALL, ADDRESS_CALL], ids=["list", "records", "address"]
)
def test_overseas_region_rejected_before_any_call(mode, call):
    # Every live_streams operation is domestic-only: an overseas region fails
    # inside the facade scope before any credential or wire traffic.
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()
    auth = _auth_transport(auth_requests)
    business = _business_transport(business_requests, LIST_BODY)
    with pytest.raises(ValueError):
        _invoke(mode, call, rec, auth, business, region=RegionConfig.overseas())

    assert auth_requests == []
    assert business_requests == []
    name = _op_name(call)
    assert outcomes(rec, "call", name, mode) == ["started", "failed"]
    assert outcomes(rec, "attempt", name, mode) == []


# ---------------------------------------------------------------------------
# async cancellation: a cancelled in-flight request propagates CancelledError
# unchanged, is never retried, and closes both the attempt and the outer call
# scopes with a ``cancelled`` outcome.
# ---------------------------------------------------------------------------


def test_async_list_cancelled_request_propagates():
    auth_requests, business_requests, rec = [], [], DiagnosticRecorder()

    def cancel(request):
        business_requests.append(request)
        raise asyncio.CancelledError()

    auth = _auth_transport(auth_requests)
    business = httpx.MockTransport(cancel)

    async def run():
        async with _async_client(auth, business, rec) as client:
            await client.live_streams.list()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())

    # The token was fetched and the single attempt was made, then cancelled.
    assert len(auth_requests) == 1
    assert len(business_requests) == 1
    assert _retry_events(rec, "live_streams.list") == []
    assert outcomes(rec, "attempt", "live_streams.list", "async") == [
        "started",
        "cancelled",
    ]
    assert outcomes(rec, "call", "live_streams.list", "async") == [
        "started",
        "cancelled",
    ]
