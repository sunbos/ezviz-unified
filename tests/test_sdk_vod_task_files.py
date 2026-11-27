"""Offline contracts for ``client.vod.task_files`` (domestic doc 3964).

Fully offline and synthetic: scripted token providers plus
``httpx.MockTransport`` / gated ``httpx.AsyncBaseTransport`` doubles only.
No environment, network, or credential access.

Contract facts under test (evidence/3964.json + codex-review.md):

* ``GET /api/service/open/cloud/vod/task/files/{taskId}`` — ``taskId`` is one
  path segment, percent-encoded exactly once by the shared binder.
* Token rides in the ``accessToken`` request header (E8); the request carries
  no query, no body and no pagination (E24 empty table).
* Success is root ``meta.code`` integer 200; ``data`` must be a list of file
  objects. ``fileName`` may be a string or an object — both forms, missing
  fields and unknown extensions pass through untouched.
* Error table E96 lists 200/500/400/406 and no 404; classification uses the
  shared defaults (business 403 -> ApiError, HTTP 403 or 10031 ->
  PermissionDeniedError, business 404 -> ApiError, 10002 -> AuthenticationError
  with at most one read-replay).
* ``fileUrl``/``coverPicUrl``/``folderNode`` never trigger a second request.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote, unquote

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

PATH_PREFIX = "/api/service/open/cloud/vod/task/files/"

TASK = "任务 TASK_PRIVATE +&%"  # UTF-8 + reserved chars, still one segment
TASK_ASCII = "TASK_PRIVATE_abc123"
TOKEN_1 = "{{SECRET_vodtaskfiles1}}" + "x" * 48
TOKEN_2 = "{{SECRET_vodtaskfiles1}}" + "y" * 48
MSG = "BACKEND_PRIVATE"
FILE_URL = "https://synthetic-media.invalid/NO_MEDIA_PRIVATE/file"
COVER_URL = "https://synthetic-media.invalid/NO_MEDIA_PRIVATE/cover"
FOLDER_NODE = "NODE_PRIVATE_" + "n" * 40

ROW_STRING_NAME = {
    "folderNode": FOLDER_NODE,
    "fileName": "NAME_PRIVATE.mp4",
    "fileUrl": FILE_URL,
    "coverPic": "COVERID_PRIVATE",
    "coverPicUrl": COVER_URL,
    "unknownField": {"nested": [None, "EXT_PRIVATE"]},
}
ROW_OBJECT_NAME = {
    "folderNode": FOLDER_NODE,
    "fileName": {"name": "NAME_PRIVATE", "future": {"x": None}},
    "fileUrl": FILE_URL,
    "coverPicUrl": COVER_URL,
}
ROW_MINIMAL = {}
ROW_MISSING = {"fileUrl": FILE_URL}
ROWS = [ROW_STRING_NAME, ROW_OBJECT_NAME, ROW_MINIMAL, ROW_MISSING]

BODY = {
    "meta": {"code": 200, "message": MSG, "moreInfo": None},
    "data": ROWS,
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


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("synthetic net down", request=request)


class _SyncProvider:
    """Scripted provider recording the get_token/invalidate call order."""

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


class _BlockingTransport(httpx.AsyncBaseTransport):
    """Holds the in-flight request open until the awaiting task is cancelled."""

    def __init__(self, log: list) -> None:
        self._log = log
        self.request_count = 0
        self.started = asyncio.Event()
        self.cancelled_error = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._log.append(("http", request))
        self.request_count += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            self.cancelled_error = error
            raise
        raise AssertionError("gate released unexpectedly")

    async def aclose(self) -> None:
        return None


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
    """The outermost business ``call`` span (``parent_span_id`` is ``None``).

    ``vod.task_files`` is recorded under ``entrypoint="other"`` until the
    shared diagnostic whitelist registers it (a C-side integration step), so
    the span is located by structure, not by name.
    """
    calls = [
        event for event in events if event["kind"] == "call" and event["parent_span_id"] is None
    ]
    started = [event for event in calls if event["outcome"] == "started"]
    terminal = [event for event in calls if event["outcome"] != "started"]
    assert len(started) == 1 and len(terminal) == 1
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _close_span(events: list) -> dict:
    closes = [event for event in events if event["kind"] == "close"]
    started = [event for event in closes if event["outcome"] == "started"]
    terminal = [event for event in closes if event["outcome"] != "started"]
    assert len(started) == 1 and len(terminal) == 1
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _attempts(events: list) -> list:
    return [
        event for event in events if event["kind"] == "attempt" and event["outcome"] == "started"
    ]


def _retries(events: list) -> list:
    return [event for event in events if event["kind"] == "retry"]


def _dump_events(recorder: DiagnosticRecorder) -> str:
    return json.dumps(_events(recorder), default=repr, ensure_ascii=False) + recorder.to_json()


def execute(
    kind,
    *,
    task_id=TASK_ASCII,
    region=None,
    script=None,
    provider_script=None,
    recorder=None,
    transport=None,
    log=None,
):
    """Drive ``client.vod.task_files`` over scripted provider + transport."""
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
                result = client.vod.task_files(task_id)
        else:

            async def run():
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, provider_script), **options
                ) as client:
                    bound = client.vod.task_files(task_id)
                    assert asyncio.iscoroutine(bound), "async task_files must return a coroutine"
                    return await bound

            result = asyncio.run(run())
    except ValueError:
        # Invalid input must fail before provider, factory and HTTP.
        assert not log, log
        raise
    return result, log


def _expect_wire(request: httpx.Request, token: str, task_id: str) -> None:
    """Full wire fingerprint: method, path with encoded segment, header token."""
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "open.ys7.com"
    # raw_path keeps the one-time percent-encoding; url.path would decode %XX.
    assert request.url.raw_path.decode("ascii") == PATH_PREFIX + quote(task_id, safe="")
    # The decoded path still ends with the caller's single segment verbatim.
    assert request.url.path == PATH_PREFIX + task_id
    assert request.headers["accessToken"] == token
    header_keys = {key.lower() for key in request.headers.keys()}
    assert "taskid" not in header_keys
    assert "content-type" not in header_keys
    assert "authorization" not in header_keys
    # No query parameters at all — token is in the header, taskId in the path.
    assert len(request.url.params.multi_items()) == 0
    assert "accessToken" not in request.url.params
    assert "taskId" not in request.url.params
    assert not request.content


@pytest.fixture(params=("sync", "async"))
def kind(request):
    return request.param


# ---------------------------------------------------------------------------
# Wire shape and response passthrough
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_id",
    [
        TASK_ASCII,
        TASK,  # UTF-8 + reserved characters: encoded once in the path
        "plain-123_OK",
        "a" * 200,  # no 1370-style 32-char cap is carried over
        "with space",
        "100%25",  # literal percent is data, encoded to %25
    ],
)
def test_exact_wire_and_response(kind, task_id):
    result, log = execute(kind, task_id=task_id)
    assert _kinds(log) == ["get_token", "factory", "http"]
    _expect_wire(_http(log)[0], TOKEN_1, task_id)
    # The decoded last path segment round-trips to the caller's value.
    last = request_path_tail(_http(log)[0])
    assert unquote(last) == task_id
    assert result.http_status == 200 and result.code == 200
    assert type(result.code) is int
    assert result.operation == "vod.task_files"
    assert result.raw == BODY
    assert result.data == ROWS and result.data is result.raw["data"]


def request_path_tail(request: httpx.Request) -> str:
    raw = request.url.raw_path.decode("ascii")
    assert raw.startswith(PATH_PREFIX)
    return raw[len(PATH_PREFIX) :]


def test_filename_both_shapes_and_extensions_preserved(kind):
    """String and object ``fileName``, missing fields, unknown keys pass through."""
    result, _ = execute(kind, script=[_resp(BODY)])
    rows = result.data
    assert rows[0]["fileName"] == "NAME_PRIVATE.mp4"
    assert rows[1]["fileName"] == {"name": "NAME_PRIVATE", "future": {"x": None}}
    assert rows[2] == {}
    assert rows[3] == {"fileUrl": FILE_URL}
    assert rows[0]["unknownField"] == {"nested": [None, "EXT_PRIVATE"]}
    assert result.raw["unrelatedTopLevel"] == "preserved"


def test_empty_list_is_success(kind):
    payload = {"meta": {"code": 200, "message": MSG}, "data": []}
    result, log = execute(kind, script=[_resp(payload)])
    assert result.data == [] and result.data is result.raw["data"]
    assert len(_http(log)) == 1


def test_media_fields_never_trigger_second_request(kind):
    """fileUrl/coverPicUrl/folderNode stay inert: exactly one HTTP request."""
    result, log = execute(kind)
    assert len(_http(log)) == 1
    assert result.data[0]["fileUrl"] == FILE_URL
    assert result.data[0]["coverPicUrl"] == COVER_URL
    assert result.data[0]["folderNode"] == FOLDER_NODE


# ---------------------------------------------------------------------------
# Response-shape enforcement: data must be a list of objects
# ---------------------------------------------------------------------------


BAD_DATA = [
    MISS,  # data key absent
    None,
    {},
    {"result": []},
    "text",
    7,
    [None],
    [3],
    ["not-a-row"],
    [[], {}],
    [{"ok": 1}, "bad"],
]


@pytest.mark.parametrize("data", BAD_DATA)
def test_reject_non_object_array(kind, data):
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
        ({"code": "200", "data": []}, ProtocolError),  # wrong envelope root
        ({"meta": {"code": "200"}, "data": []}, ApiError),  # string success code
        ({"meta": {"code": True}, "data": []}, ProtocolError),
        ({"meta": {"code": 200.0}, "data": []}, ProtocolError),
        ({"meta": {"code": [200]}, "data": []}, ProtocolError),
    ],
)
def test_bad_envelope(kind, envelope, exc):
    log = []
    with pytest.raises(exc) as error:
        execute(kind, script=[_resp(envelope)], log=log)
    assert type(error.value) is exc and len(_http(log)) == 1


# ---------------------------------------------------------------------------
# Validation before any I/O
# ---------------------------------------------------------------------------


# Rejected at the facade before provider/factory/HTTP: blank, wrong type, or
# not UTF-8 encodable. These never reach the path binder.
BAD_TASK_ID_FACADE = ["", "   ", "\t", b"task-id", True, 7, None, "\ud800"]


@pytest.mark.parametrize("bad", BAD_TASK_ID_FACADE)
def test_invalid_task_id_rejected_before_any_io(kind, bad):
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError):
        execute(kind, task_id=bad, script=[], log=log, recorder=recorder)
    assert log == []
    events = _events(recorder)
    terminal = _facade_span(events)
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "validation"
    assert not _attempts(events)
    assert _close_span(events)["outcome"] == "succeeded"


# Rejected by ``render_path`` inside ``client.request._prepare``: these are
# non-blank UTF-8 strings so the facade-level check lets them through, but the
# single-segment binder refuses them. ``_prepare`` runs before the auth loop,
# so provider/factory/HTTP all stay silent — identical to facade rejection.
BAD_TASK_ID_SEGMENT = ["a/b", "a\\b", ".", "..", "a\x00b", "a\x1fb", "a\x7fb"]


@pytest.mark.parametrize("bad", BAD_TASK_ID_SEGMENT)
def test_invalid_task_id_rejected_by_path_binder(kind, bad):
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(ValueError):
        execute(kind, task_id=bad, script=[], log=log, recorder=recorder)
    assert log == []
    assert not _http(log)
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "failed"


def test_overseas_region_rejected_before_any_io(kind):
    log = []
    with pytest.raises(ValueError) as error:
        execute(kind, region=RegionConfig.overseas(), script=[], log=log)
    assert log == []
    assert "domestic" in str(error.value)


def test_validation_message_never_echoes_task_id(kind):
    marker = "HIDDEN_TASK_" + "v" * 40
    with pytest.raises(ValueError) as slash_case:
        execute(kind, task_id=marker + "/x")
    assert marker not in str(slash_case.value)
    with pytest.raises(ValueError) as blank_case:
        execute(kind, task_id="  ")
    assert marker not in str(blank_case.value)


def test_task_id_error_chain_never_echoes_value(kind):
    """A surrogate ``task_id`` fails at the facade before provider/factory/HTTP.

    ``UnicodeEncodeError.args`` embeds the offending string; if it stayed on
    ``__context__`` the caller's id would be recoverable from the chain.
    """
    marker = "HIDDEN_TASK_" + "w" * 40 + "\ud800"
    log = []
    with pytest.raises(ValueError) as captured:
        execute(kind, task_id=marker, script=[], log=log)
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
# 10002 stale-token: read-only replay at most once
# ---------------------------------------------------------------------------


def test_refresh_replays_once(kind):
    log = []
    recorder = DiagnosticRecorder()
    result, _ = execute(
        kind,
        script=[_resp(STALE), _resp()],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
        log=log,
        recorder=recorder,
    )
    calls = _http(log)
    assert len(calls) == 2
    _expect_wire(calls[0], TOKEN_1, TASK_ASCII)
    _expect_wire(calls[1], TOKEN_2, TASK_ASCII)
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.code == 200 and result.data == ROWS
    events = _events(recorder)
    assert len(_attempts(events)) == 2
    retries = _retries(events)
    assert len(retries) == 1 and retries[0]["outcome"] == "scheduled"
    assert _facade_span(events)["outcome"] == "succeeded"


@pytest.mark.parametrize("code", [10002, "10002"])
def test_refresh_at_most_once_on_repeated_stale(kind, code):
    log = []
    recorder = DiagnosticRecorder()
    stale = {"meta": {"code": code, "message": MSG}}
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            script=[_resp(stale), _resp(stale)],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
            recorder=recorder,
        )
    assert captured.value.invalid_token is True
    calls = _http(log)
    assert len(calls) == 2
    _expect_wire(calls[0], TOKEN_1, TASK_ASCII)
    _expect_wire(calls[1], TOKEN_2, TASK_ASCII)
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert invalidations == [("invalidate", TOKEN_1), ("invalidate", TOKEN_2)]
    events = _events(recorder)
    assert _facade_span(events)["outcome"] == "failed"
    assert len(_retries(events)) == 1


# ---------------------------------------------------------------------------
# Error classification (E96 table: 200/500/400/406; no 404 in the source)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (200, 10031, PermissionDeniedError),
        (200, "10031", PermissionDeniedError),
        (403, MISS, PermissionDeniedError),
        (200, 400, ApiError),
        (200, "400", ApiError),
        (200, 403, ApiError),
        (200, "403", ApiError),
        (200, 404, ApiError),
        (200, "404", ApiError),
        (200, 406, ApiError),
        (200, "406", ApiError),
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
    # ``_POLICY_META`` keeps upstream text verbatim; only request secrets are
    # redacted (see the dedicated privacy tests below).
    assert type(error.value) is expected and error.value.http_status == status
    if code is MISS:
        assert error.value.code is None
    else:
        assert error.value.code == code and type(error.value.code) is type(code)
    assert _kinds(log) == ["get_token", "factory", "http"]


@pytest.mark.parametrize(
    "problem,reason",
    [
        (httpx.ReadTimeout("TIMEOUT_PRIVATE"), "timeout"),
        (httpx.ConnectError("CONNECT_PRIVATE"), "network"),
    ],
)
def test_transport_failure_is_final(kind, problem, reason):
    log = []
    recorder = DiagnosticRecorder()
    with pytest.raises(TransportError) as captured:
        execute(kind, script=[problem], log=log, recorder=recorder)
    assert captured.value.reason == reason
    assert len(_http(log)) == 1 and not _retries(_events(recorder))


def test_closed_client_raises_transport_closed(kind):
    log = []
    recorder = DiagnosticRecorder()
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [_resp()]),
        diagnostic_recorder=recorder,
    )
    if kind == "sync":
        client = Client(token_provider=_SyncProvider(log, [_token(TOKEN_1)]), **options)
        client.close()
        with pytest.raises(TransportError) as captured:
            client.vod.task_files(TASK_ASCII)
        exc = captured.value
    else:

        async def run():
            client = AsyncClient(token_provider=_AsyncProvider(log, [_token(TOKEN_1)]), **options)
            await client.aclose()
            with pytest.raises(TransportError) as captured:
                await client.vod.task_files(TASK_ASCII)
            return captured.value

        exc = asyncio.run(run())
    assert exc.reason == "closed"
    assert not _http(log)
    events = _events(recorder)
    terminal = _facade_span(events)
    assert terminal["outcome"] == "failed"
    assert terminal["reason"] == "closed"
    assert _close_span(events)["outcome"] == "succeeded"


def test_async_cancel_propagates():
    log = []
    transport = _BlockingTransport(log)
    recorder = DiagnosticRecorder()

    async def run():
        async with AsyncClient(
            region=RegionConfig.china(),
            token_provider=_AsyncProvider(log, [_token(TOKEN_1)]),
            transport_factory=_fixed_factory(log, transport),
            diagnostic_recorder=recorder,
        ) as client:
            task = asyncio.create_task(client.vod.task_files(TASK_ASCII))
            await asyncio.wait_for(transport.started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert transport.request_count == 1
    events = _events(recorder)
    terminal = _facade_span(events)
    assert terminal["outcome"] == "cancelled"
    assert terminal["error_family"] == "interrupted"
    assert _close_span(events)["outcome"] == "succeeded"


# ---------------------------------------------------------------------------
# Privacy: sentinels never reach errors, logs or diagnostics
# ---------------------------------------------------------------------------


def test_sensitive_values_not_leaked_on_error(kind, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    err_body = {
        "meta": {
            "code": 400,
            "message": f"{TASK_ASCII} missing; {FOLDER_NODE} denied; {TOKEN_1}; {FILE_URL}",
        },
        "data": [{"fileUrl": FILE_URL, "folderNode": FOLDER_NODE}],
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(kind, script=[_resp(err_body)], recorder=recorder, log=log)
    _expect_wire(_http(log)[0], TOKEN_1, TASK_ASCII)
    surface = str(captured.value) + repr(captured.value) + caplog.text + _dump_events(recorder)
    for secret in (TASK_ASCII, FOLDER_NODE, TOKEN_1, FILE_URL, COVER_URL, MSG):
        assert secret not in surface


def test_response_media_fields_hidden_from_repr_and_diagnostics(kind, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    result, log = execute(kind, recorder=recorder)
    assert result.data == ROWS and result.raw == BODY
    surface = repr(result) + str(result) + caplog.text + _dump_events(recorder)
    for secret in (TOKEN_1, FILE_URL, COVER_URL, FOLDER_NODE, TASK_ASCII, MSG):
        assert secret not in surface


def test_encoded_task_id_not_leaked_in_error(kind, caplog):
    """Both the raw and percent-encoded task id are registered secrets."""
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    weird = "任务 A/B C"  # contains a slash → rejected client-side; use legal but encoded
    weird = "任务 A+B%C"  # legal single segment needing percent-encoding
    err_body = {
        "meta": {
            "code": 400,
            "message": f"task {weird} / {quote(weird, safe='')} failed; token {TOKEN_1}",
        }
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(kind, task_id=weird, script=[_resp(err_body)], recorder=recorder, log=log)
    _expect_wire(_http(log)[0], TOKEN_1, weird)
    surface = str(captured.value) + repr(captured.value) + caplog.text + _dump_events(recorder)
    assert weird not in surface
    assert quote(weird, safe="") not in surface


# ---------------------------------------------------------------------------
# Identity / diagnostics parity
# ---------------------------------------------------------------------------


def test_returns_the_client_request_result_unchanged(kind):
    recorder = DiagnosticRecorder()
    sentinel = ApiResponse(200, 200, operation="vod.task_files", data=[])
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
            result = client.vod.task_files(TASK_ASCII)
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
                return await client.vod.task_files(TASK_ASCII)

        result = asyncio.run(run())
    assert result is sentinel
    assert produced == ["vod.task_files"]
    assert _facade_span(_events(recorder))["outcome"] == "succeeded"


def test_provider_exception_identity_preserved(kind):
    marker = AuthenticationError("marker failure")
    log = []
    recorder = DiagnosticRecorder()
    options = dict(
        region=RegionConfig.china(),
        transport_factory=_scripted_factory(log, [lambda request: _resp()]),
        diagnostic_recorder=recorder,
    )
    if kind == "sync":
        with Client(token_provider=_SyncProvider(log, [marker]), **options) as client:
            with pytest.raises(AuthenticationError) as captured:
                client.vod.task_files(TASK_ASCII)
        captured_value = captured.value
    else:

        async def run():
            async with AsyncClient(
                token_provider=_AsyncProvider(log, [marker]), **options
            ) as client:
                with pytest.raises(AuthenticationError) as captured:
                    await client.vod.task_files(TASK_ASCII)
                return captured.value

        captured_value = asyncio.run(run())
    assert captured_value is marker
    assert _kinds(log) == ["get_token"]
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "authentication"


def test_diagnostic_on_off_same_wire_and_result(kind):
    plain_result, plain_log = execute(kind)
    recorder = DiagnosticRecorder()
    diag_result, diag_log = execute(kind, recorder=recorder)
    assert _kinds(plain_log) == _kinds(diag_log)
    plain_req, diag_req = _http(plain_log)[0], _http(diag_log)[0]
    assert plain_req.method == diag_req.method
    assert plain_req.url.raw_path == diag_req.url.raw_path
    assert plain_req.content == diag_req.content
    assert sorted(plain_req.headers.items()) == sorted(diag_req.headers.items())
    assert diag_result.raw == plain_result.raw == BODY
    assert diag_result.data is diag_result.raw["data"]
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "succeeded"
    assert terminal["error_family"] == "none"


def test_httpx_request_line_has_no_query(kind, caplog):
    """The logged request line must not carry a query string (no token in URL)."""
    caplog.set_level(logging.INFO, logger="httpx")
    _, log = execute(kind)
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "httpx" and record.levelno == logging.INFO
    ]
    assert len(lines) == 1
    assert "?" not in lines[0]
    assert _http(log)
