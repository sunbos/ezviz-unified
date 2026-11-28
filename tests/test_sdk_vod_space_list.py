"""Offline contracts for ``client.vod.space_list`` (domestic doc 4473).

Fully offline and synthetic: scripted token providers plus
``httpx.MockTransport`` doubles only. No environment, network, or credential
access.

Contract facts under test (evidence/4473.json):

* ``GET /api/service/open/vod/space/list`` — token in the ``accessToken``
  request header (E14); ``data`` is an object containing ``result[]`` plus
  ``total``/``pageStart``/``pageSize`` — never flattened to a bare array.
* Query: required ``pageSize``/``pageStart`` (int); optional ``sortField``
  (createTime), ``sortRule`` (asc/desc), ``spaceIdName`` (E30).
* Page header notes the call is unsupported for managed/sub-accounts; the SDK
  records the constraint without guessing the account role.
* E163 error table lists 200/500/400; shared default classification applies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote, quote_plus

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)
from ezviz_unified.auth_models import Token

ORIGIN = "https://open.ys7.com"
FAR_FUTURE = 9_999_999_999.0
PATH = "/api/service/open/vod/space/list"

TOKEN_1 = "{{SECRET_vodspacelist1}}" + "x" * 48
TOKEN_2 = "{{SECRET_vodspacelist1}}" + "y" * 48
MSG = "BACKEND_PRIVATE"
SPACE_NAME = "录像空间-私有" + "名" * 20

SPACE_ROW = {
    "spaceId": 123456,
    "totalSize": 1024,
    "spaceName": SPACE_NAME,
    "storageType": 1,
    "expireDays": 30,
    "createTime": "2026-01-01 00:00:00",
    "updateTime": "2026-01-02 00:00:00",
    "unknownField": {"nested": [None]},
}
DATA = {
    "result": [SPACE_ROW, {"spaceId": 0}, {}],
    "total": 3,
    "pageStart": 0,
    "pageSize": 10,
    "unknownPageField": "EXT_PRIVATE",
}
BODY = {
    "meta": {"code": 200, "message": MSG, "moreInfo": None},
    "data": DATA,
    "unrelatedTopLevel": "preserved",
}
STALE = {"meta": {"code": 10002, "message": "stale token"}, "data": None}
MISS = object()


def _token(value: str) -> Token:
    return Token(
        value=value,
        expires_at=FAR_FUTURE,
        refresh_at=FAR_FUTURE - 60.0,
        api_base_url=ORIGIN,
    )


def _resp(body=None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=BODY if body is None else body)


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("synthetic timeout", request=request)


class _SyncProvider:
    can_refresh = True

    def __init__(self, log: list, script: list) -> None:
        self._log = log
        self._script = list(script)

    def get_token(self) -> Token:
        self._log.append(("get_token",))
        if not self._script:
            raise AssertionError("unscripted get_token call")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item

    def invalidate(self, expected_value: str | None = None) -> None:
        self._log.append(("invalidate", expected_value))


class _AsyncProvider:
    can_refresh = True

    def __init__(self, log: list, script: list) -> None:
        self._log = log
        self._script = list(script)

    async def get_token(self) -> Token:
        self._log.append(("get_token",))
        if not self._script:
            raise AssertionError("unscripted get_token call")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item

    def invalidate(self, expected_value: str | None = None) -> None:
        self._log.append(("invalidate", expected_value))


def _scripted_factory(log: list, script: list):
    items = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(("http", request))
        if not items:
            raise AssertionError("unscripted HTTP request")
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item(request) if callable(item) else item

    transport = httpx.MockTransport(handler)

    def factory(origin: str):
        log.append(("factory", origin))
        return transport

    return factory


def _fixed_factory(log: list, transport):
    def factory(origin: str):
        log.append(("factory", origin))
        return transport

    return factory


def _http(log: list) -> list:
    return [entry[1] for entry in log if entry[0] == "http"]


def _kinds(log: list) -> list:
    return [entry[0] for entry in log]


_EVENT_KEYS = frozenset(
    {
        "kind",
        "entrypoint",
        "span_id",
        "parent_span_id",
        "outcome",
        "error_family",
        "reason",
        "code_category",
        "http_status_category",
        "attempt",
        "mode",
    }
)


def _events(recorder: DiagnosticRecorder) -> list:
    events = recorder.report()["events"]
    assert isinstance(events, list)
    for event in events:
        assert isinstance(event, dict)
        assert _EVENT_KEYS <= frozenset(event)
    return events


def _facade_span(events: list) -> dict:
    calls = [
        event for event in events if event["kind"] == "call" and event["parent_span_id"] is None
    ]
    started = [event for event in calls if event["outcome"] == "started"]
    terminal = [event for event in calls if event["outcome"] != "started"]
    assert len(started) == 1 and len(terminal) == 1
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _retries(events: list) -> list:
    return [event for event in events if event["kind"] == "retry"]


def _dump_events(recorder: DiagnosticRecorder) -> str:
    return json.dumps(_events(recorder), default=repr, ensure_ascii=False) + recorder.to_json()


def execute(
    kind,
    *,
    kwargs=None,
    region=None,
    script=None,
    provider_script=None,
    recorder=None,
    transport=None,
    log=None,
):
    """Drive ``client.vod.space_list`` over scripted provider + transport."""
    kwargs = {"page_start": 0, "page_size": 10} if kwargs is None else kwargs
    log = [] if log is None else log
    provider_script = [_token(TOKEN_1)] if provider_script is None else provider_script
    if transport is not None:
        factory = _fixed_factory(log, transport)
    else:
        factory = _scripted_factory(log, [_resp()] if script is None else script)
    options = dict(region=region or RegionConfig.china(), transport_factory=factory)
    if recorder is not None:
        options["diagnostic_recorder"] = recorder
    try:
        if kind == "sync":
            with Client(token_provider=_SyncProvider(log, provider_script), **options) as client:
                result = client.vod.space_list(**kwargs)
        else:

            async def run():
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, provider_script), **options
                ) as client:
                    bound = client.vod.space_list(**kwargs)
                    assert asyncio.iscoroutine(bound)
                    return await bound

            result = asyncio.run(run())
    except ValueError:
        assert not log, log
        raise
    return result, log


def _expect_wire(request: httpx.Request, token: str) -> None:
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == PATH
    assert request.headers["accessToken"] == token
    assert not request.content
    assert "content-type" not in {k.lower() for k in request.headers}
    assert "accessToken" not in request.url.params


@pytest.fixture(params=("sync", "async"))
def kind(request):
    return request.param


# ---------------------------------------------------------------------------
# Wire shape and response passthrough
# ---------------------------------------------------------------------------


def test_exact_wire_minimal(kind):
    result, log = execute(kind, kwargs={"page_start": 0, "page_size": 10})
    assert _kinds(log) == ["get_token", "factory", "http"]
    request = _http(log)[0]
    _expect_wire(request, TOKEN_1)
    assert sorted(request.url.params.multi_items()) == [
        ("pageSize", "10"),
        ("pageStart", "0"),
    ]
    assert result.code == 200 and type(result.code) is int
    assert result.operation == "vod.space_list"
    assert result.raw == BODY and result.data is result.raw["data"]


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (
            {"page_start": 3, "page_size": 50},
            [("pageSize", "50"), ("pageStart", "3")],
        ),
        (
            {"page_start": 0, "page_size": 1, "sort_field": "createTime"},
            [("pageSize", "1"), ("pageStart", "0"), ("sortField", "createTime")],
        ),
        (
            {"page_start": 0, "page_size": 1, "sort_rule": "desc"},
            [("pageSize", "1"), ("pageStart", "0"), ("sortRule", "desc")],
        ),
        (
            {"page_start": 0, "page_size": 1, "space_id_name": SPACE_NAME},
            [("pageSize", "1"), ("pageStart", "0"), ("spaceIdName", SPACE_NAME)],
        ),
        (
            {
                "page_start": 9,
                "page_size": 20,
                "sort_field": "createTime",
                "sort_rule": "asc",
                "space_id_name": "77",
            },
            [
                ("pageSize", "20"),
                ("pageStart", "9"),
                ("sortField", "createTime"),
                ("sortRule", "asc"),
                ("spaceIdName", "77"),
            ],
        ),
    ],
)
def test_exact_query_and_passthrough(kind, kwargs, expected):
    result, log = execute(kind, kwargs=kwargs)
    request = _http(log)[0]
    _expect_wire(request, TOKEN_1)
    assert sorted(request.url.params.multi_items()) == sorted(expected)
    # data is the documented object; result stays a nested list, not flattened.
    assert result.data == DATA
    assert result.data["result"] == DATA["result"]
    assert result.data["total"] == 3
    assert result.data["pageStart"] == 0 and result.data["pageSize"] == 10
    assert result.data["unknownPageField"] == "EXT_PRIVATE"


def test_data_object_never_flattened(kind):
    """``data.result`` rows and page counters stay nested under ``data``."""
    result, _ = execute(kind)
    assert isinstance(result.data, dict)
    assert result.data["result"][0]["spaceName"] == SPACE_NAME
    assert result.data["result"][1] == {"spaceId": 0}
    assert result.data["result"][2] == {}


def test_empty_result_list_is_success(kind):
    payload = {
        "meta": {"code": 200, "message": MSG},
        "data": {"result": [], "total": 0, "pageStart": 0, "pageSize": 10},
    }
    result, log = execute(kind, script=[_resp(payload)])
    assert result.data["result"] == [] and result.data["total"] == 0
    assert len(_http(log)) == 1


# ---------------------------------------------------------------------------
# Response-shape enforcement: data.result must be a list
# ---------------------------------------------------------------------------


BAD_DATA = [
    MISS,  # data key absent
    None,
    [],
    "text",
    7,
    {},  # no result key
    {"result": None},
    {"result": {}},
    {"result": "x"},
    {"result": 3},
    # E71 types result as array<object>: non-dict rows are violations.
    {"result": [42]},
    {"result": [None]},
    {"result": ["row"]},
    {"result": [[], {}]},
    {"result": [{"ok": 1}, "bad"]},
    {"result": [{"ok": 1}, None]},
]


@pytest.mark.parametrize("data", BAD_DATA)
def test_reject_bad_result_container(kind, data):
    payload = {"meta": {"code": 200, "message": MSG}}
    if data is not MISS:
        payload["data"] = data
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ProtocolError):
        execute(kind, script=[_resp(payload)], log=log, recorder=recorder)
    assert _kinds(log) == ["get_token", "factory", "http"]
    assert not _retries(_events(recorder))
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "protocol"


@pytest.mark.parametrize(
    "envelope,exc",
    [
        ({"code": "200", "data": DATA}, ProtocolError),
        ({"meta": {"code": "200"}, "data": DATA}, ApiError),
        ({"meta": {"code": True}, "data": DATA}, ProtocolError),
        ({"meta": {"code": 200.0}, "data": DATA}, ProtocolError),
    ],
)
def test_bad_envelope(kind, envelope, exc):
    log = []
    with pytest.raises(exc):
        execute(kind, script=[_resp(envelope)], log=log)
    assert len(_http(log)) == 1


# ---------------------------------------------------------------------------
# Validation before any I/O
# ---------------------------------------------------------------------------


BAD_KW = [
    {"page_start": True, "page_size": 10},
    {"page_start": "0", "page_size": 10},
    {"page_start": 1.5, "page_size": 10},
    {"page_start": None, "page_size": 10},
    {"page_start": -1, "page_size": 10},
    {"page_start": -100, "page_size": 10},
    {"page_start": 0, "page_size": True},
    {"page_start": 0, "page_size": "10"},
    {"page_start": 0, "page_size": 1.5},
    {"page_start": 0, "page_size": 0},
    {"page_start": 0, "page_size": -1},
    {"page_start": 0, "page_size": 10, "sort_field": "updateTime"},
    {"page_start": 0, "page_size": 10, "sort_field": "name"},
    {"page_start": 0, "page_size": 10, "sort_field": 7},
    {"page_start": 0, "page_size": 10, "sort_rule": "ASC"},
    {"page_start": 0, "page_size": 10, "sort_rule": "up"},
    {"page_start": 0, "page_size": 10, "sort_rule": 1},
    {"page_start": 0, "page_size": 10, "space_id_name": ""},
    {"page_start": 0, "page_size": 10, "space_id_name": "   "},
    {"page_start": 0, "page_size": 10, "space_id_name": b"x"},
    {"page_start": 0, "page_size": 10, "space_id_name": "\ud800"},
    {"page_start": 0, "page_size": 10, "space_id_name": 5},
]


@pytest.mark.parametrize("kwargs", BAD_KW)
def test_invalid_params_rejected_before_any_io(kind, kwargs):
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError):
        execute(kind, kwargs=kwargs, script=[], log=log, recorder=recorder)
    assert log == []
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "validation"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_start": -1, "page_size": 10},
        {"page_start": 0, "page_size": 0},
        {"page_start": 0, "page_size": -1},
        {"page_start": True, "page_size": 10},
        {"page_start": 0, "page_size": True},
    ],
)
def test_invalid_pagination_sends_no_auth_or_business_request(kind, kwargs):
    """Illegal paging fails before authentication: zero provider and HTTP calls.

    The provider script is deliberately empty — a single ``get_token`` would
    already fail the run; the shared ``log`` then proves neither authentication
    nor the business request left the process.
    """
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError):
        execute(kind, kwargs=kwargs, script=[], provider_script=[], log=log, recorder=recorder)
    assert log == []
    assert not _http(log)
    events = _events(recorder)
    assert not _attempts_like(events)
    terminal = _facade_span(events)
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "validation"


def _attempts_like(events: list) -> list:
    return [event for event in events if event["kind"] == "attempt"]


def test_overseas_region_rejected_before_any_io(kind):
    log = []
    with pytest.raises(ValueError) as error:
        execute(kind, region=RegionConfig.overseas(), script=[], log=log)
    assert log == []
    assert "domestic" in str(error.value)


# ---------------------------------------------------------------------------
# 10002 stale-token: read-only replay at most once
# ---------------------------------------------------------------------------


def test_refresh_replays_once(kind):
    log = []
    result, _ = execute(
        kind,
        script=[_resp(STALE), _resp()],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
        log=log,
    )
    calls = _http(log)
    assert len(calls) == 2
    _expect_wire(calls[0], TOKEN_1)
    _expect_wire(calls[1], TOKEN_2)
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.code == 200 and result.data == DATA


def test_refresh_at_most_once_on_repeated_stale(kind):
    log = []
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            script=[_resp(STALE), _resp(STALE)],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
        )
    assert captured.value.invalid_token is True
    assert len(_http(log)) == 2


# ---------------------------------------------------------------------------
# Error classification (E163: 200/500/400)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (200, 10031, PermissionDeniedError),
        (403, MISS, PermissionDeniedError),
        (200, 400, ApiError),
        (200, "400", ApiError),
        (200, 403, ApiError),
        (200, 404, ApiError),
        (200, 500, ApiError),
        (200, "500", ApiError),
        (503, 404, ApiError),
        (401, MISS, AuthenticationError),
    ],
)
def test_visible_codes_and_permissions(kind, status, code, expected):
    meta = {"message": MSG}
    if code is not MISS:
        meta["code"] = code
    log = []
    with pytest.raises(expected) as error:
        execute(kind, script=[_resp({"meta": meta}, status)], log=log)
    assert type(error.value) is expected and error.value.http_status == status
    if code is MISS:
        assert error.value.code is None
    else:
        assert error.value.code == code and type(error.value.code) is type(code)
    assert _kinds(log) == ["get_token", "factory", "http"]


def test_transport_failure_is_final(kind):
    log = []
    with pytest.raises(TransportError):
        execute(kind, script=[_timeout], log=log)
    assert len(_http(log)) == 1


# ---------------------------------------------------------------------------
# Privacy: space_name is a sent secret, masked on error surfaces
# ---------------------------------------------------------------------------


def test_sensitive_values_not_leaked_on_error(kind, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    err_body = {
        "meta": {
            "code": 400,
            "message": f"{SPACE_NAME} denied; token {TOKEN_1}",
        },
        "data": DATA,
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(
            kind,
            kwargs={"page_start": 0, "page_size": 10, "space_id_name": SPACE_NAME},
            script=[_resp(err_body)],
            recorder=recorder,
            log=log,
        )
    request = _http(log)[0]
    assert request.url.params["spaceIdName"] == SPACE_NAME
    surface = str(captured.value) + repr(captured.value) + caplog.text + _dump_events(recorder)
    for secret in (SPACE_NAME, TOKEN_1):
        assert secret not in surface


def test_returns_the_client_request_result_unchanged(kind):
    recorder = DiagnosticRecorder()
    sentinel = ApiResponse(200, 200, operation="vod.space_list", data={"result": []})
    produced = []

    def record(operation):
        produced.append(operation.name)
        return sentinel

    if kind == "sync":
        with Client(
            region=RegionConfig.china(),
            token_provider=_SyncProvider([], []),
            transport_factory=_fixed_factory([], httpx.MockTransport(_timeout)),
            diagnostic_recorder=recorder,
        ) as client:
            client.request = lambda operation, **_kw: record(operation)
            result = client.vod.space_list(page_start=0, page_size=1)
    else:

        async def run():
            async with AsyncClient(
                region=RegionConfig.china(),
                token_provider=_AsyncProvider([], []),
                transport_factory=_fixed_factory([], httpx.MockTransport(_timeout)),
                diagnostic_recorder=recorder,
            ) as client:

                async def stub(operation, **_kw):
                    return record(operation)

                client.request = stub
                return await client.vod.space_list(page_start=0, page_size=1)

        result = asyncio.run(run())
    assert result is sentinel
    assert produced == ["vod.space_list"]
    assert _facade_span(_events(recorder))["outcome"] == "succeeded"


def test_httpx_request_line_has_token_in_header_not_url(kind, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    _, log = execute(kind, kwargs={"page_start": 0, "page_size": 10})
    lines = [
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    ]
    assert len(lines) == 1 and TOKEN_1 not in lines[0]
    assert _http(log)


# ---------------------------------------------------------------------------
# Validation failures never carry the caller's value on the exception chain
# ---------------------------------------------------------------------------


def test_space_id_name_error_chain_never_echoes_value(kind):
    """A surrogate ``space_id_name`` fails before I/O and leaks nothing.

    ``UnicodeEncodeError.args`` embeds the offending string; if it stayed on
    ``__context__`` the caller's selector would be recoverable from the chain.
    """
    marker = "HIDDEN_SPACE_" + "s" * 40 + "\ud800"
    log = []
    with pytest.raises(ValueError) as captured:
        execute(
            kind,
            kwargs={"page_start": 0, "page_size": 10, "space_id_name": marker},
            script=[],
            log=log,
        )
    assert log == []
    exc = captured.value
    assert marker not in str(exc) and marker not in repr(exc)
    assert all(marker not in str(arg) for arg in exc.args)
    chain = []
    current = exc
    while current is not None:
        chain.append(current)
        current = current.__cause__ or current.__context__
    for link in chain:
        assert marker not in str(link)
        assert all(marker not in str(arg) for arg in getattr(link, "args", ()))


# ---------------------------------------------------------------------------
# Controlled httpx INFO record: server-controlled fields are masked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("http_version", "expected"),
    [
        (
            b"HTTP/1.1 UNTRUSTED_PRIVATE_VERSION",
            'HTTP Request: GET *** "HTTP 200 ***"',
        ),
        (
            b"HTTP/9 UNTRUSTED_PRIVATE_VERSION",
            'HTTP Request: GET *** "HTTP 200 ***"',
        ),
        (b"HTTP/1.1", 'HTTP Request: GET *** "HTTP/1.1 200 ***"'),
    ],
    ids=["appended-version", "bogus-version", "allowlisted-version"],
)
def test_controlled_log_masks_server_supplied_fields(kind, caplog, http_version, expected):
    """Server-controlled http_version/reason must not reach the owned INFO line.

    Under the media-URL policy the ENTIRE SDK request URL renders as ``***``
    -- origin and path do not survive on the owned record. An unrecognized
    http_version collapses to ``HTTP`` (allowlisted versions pass through)
    and the free-text reason phrase is blanked. The selector is synthetic
    with a space, a slash and non-ASCII so raw, ``quote(..., safe="")`` and
    ``quote_plus(..., safe="")`` forms are three different strings; an
    unstructured probe INFO inside the SDK transport proves all three forms
    are collected secrets.
    """
    selector = "squad bay/ qé"
    sentinel_reason = b"UNTRUSTED_PRIVATE_REASON " + selector.encode("utf-8")
    caplog.set_level(logging.INFO, logger="httpx")

    def evil(request: httpx.Request) -> httpx.Response:
        # Unstructured record emitted inside the SDK transport: the three
        # selector encodings stand alone -- no sensitive key or URL sits
        # next to them -- and every form must collapse to ``***``.
        logging.getLogger("httpx").info(
            "probe %s %s %s",
            selector,
            quote(selector, safe=""),
            quote_plus(selector, safe=""),
        )
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": MSG}, "data": {"result": []}},
            extensions={"http_version": http_version, "reason_phrase": sentinel_reason},
        )

    _, log = execute(
        kind,
        kwargs={"page_start": 0, "page_size": 10, "space_id_name": selector},
        script=[evil],
    )
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "httpx" and record.levelno == logging.INFO
    ]
    # The probe is emitted while the response is in flight, so it precedes
    # the owned request line; both must be exactly these masked forms.
    assert lines == ["probe *** *** ***", expected]
    joined = "\n".join(lines)
    for form in (selector, quote(selector, safe=""), quote_plus(selector, safe="")):
        assert form not in joined
    assert "UNTRUSTED_PRIVATE" not in joined
    assert "spaceIdName" not in joined
    wire = [request for request in _http(log) if request.url.path == PATH]
    assert len(wire) == 1
    assert wire[0].url.params["spaceIdName"] == selector


def test_native_client_inside_sdk_context_keeps_own_log(kind, caplog):
    """A native HTTPX client inside the SDK context keeps its own INFO line.

    The inner client is a real ``httpx.Client``/``httpx.AsyncClient`` on its
    own MockTransport and its request is a fresh Request built from
    ``str(request.url)`` -- identical origin/path/query, different URL
    object -- so the redaction filter's identity check lets the native
    record through untouched (full URL, version, status, reason) while the
    owned SDK record is still masked to ``***``.
    """
    selector = "native pad/ z"
    caplog.set_level(logging.INFO, logger="httpx")
    log = []

    def outer_response() -> httpx.Response:
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": MSG}, "data": {"result": []}},
            extensions={
                "http_version": b"HTTP/7 UNTRUSTED_OUTER",
                "reason_phrase": b"UNTRUSTED_OUTER_REASON",
            },
        )

    def native_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            599,
            json={"native": True},
            extensions={
                "http_version": b"HTTP/9",
                "reason_phrase": b"NATIVE_REASON_KEEP",
            },
        )

    async def native_handler_async(request: httpx.Request) -> httpx.Response:
        return native_handler(request)

    if kind == "sync":

        def handler(request: httpx.Request) -> httpx.Response:
            log.append(("http", request))
            with httpx.Client(transport=httpx.MockTransport(native_handler)) as native:
                native.get(str(request.url))
            return outer_response()

    else:

        async def handler(request: httpx.Request) -> httpx.Response:
            log.append(("http", request))
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(native_handler_async)
            ) as native:
                await native.get(str(request.url))
            return outer_response()

    _, log = execute(
        kind,
        kwargs={"page_start": 0, "page_size": 10, "space_id_name": selector},
        transport=httpx.MockTransport(handler),
        log=log,
    )
    wire = [request for request in _http(log) if request.url.path == PATH]
    assert len(wire) == 1
    assert wire[0].url.params["spaceIdName"] == selector
    full_url = str(wire[0].url)
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "httpx" and record.levelno == logging.INFO
    ]
    # The owned SDK record stays fully masked.
    assert 'HTTP Request: GET *** "HTTP 200 ***"' in lines
    # The native record keeps the exact URL (origin + path + query) plus its
    # own distinct version, status and reason phrase.
    native = [line for line in lines if full_url in line]
    assert native == [f'HTTP Request: GET {full_url} "HTTP/9 599 NATIVE_REASON_KEEP"']
