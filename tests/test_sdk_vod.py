"""Contract tests for ``client.vod.files`` / ``AsyncClient.vod.files`` (SP122).

Fully offline and synthetic: scripted token providers plus
``httpx.MockTransport`` / gated ``httpx.AsyncBaseTransport`` doubles only.
No environment, network, or credential access.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    ProtocolError,
    RegionConfig,
    TransportError,
)
from ezviz_unified.auth_models import Token
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.errors import PermissionDeniedError

ORIGIN = "https://open.ys7.com"
FAR_FUTURE = 9_999_999_999.0

NODE_A = "VODNODE_" + "a" * 56
NODE_B = "VODNODE_" + "b" * 56
TOKEN_1 = "VODTOKEN_" + "x" * 56
TOKEN_2 = "VODTOKEN_" + "y" * 56
MEDIA_URL = "https://media.example.invalid/vod/" + "m" * 64
SPACE_NAME = "录像空间-" + "汉" * 64

BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": [{"fileNodeId": "n1", "unknownField": {"nested": [1]}}, "raw-item", 7],
    "unrelatedTopLevel": "preserved",
}
STALE = {"meta": {"code": 10002, "message": "stale token"}, "data": None}
DENIED = {"meta": {"code": 10017, "message": "denied"}, "data": None}
FAILED = {"meta": {"code": 5000, "message": "synthetic failure"}, "data": None}


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

    def __init__(self) -> None:
        self.request_count = 0
        self.started = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        self.started.set()
        await asyncio.Event().wait()
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


def _fingerprint(request: httpx.Request) -> tuple:
    return (
        request.method,
        request.url.raw_path.decode("ascii"),
        request.content,
        tuple(sorted(request.headers.items())),
    )


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
    """Recorded events from the frozen ``report()["events"]`` snapshot."""
    events = recorder.report()["events"]
    assert isinstance(events, list)
    for event in events:
        assert isinstance(event, dict)
        assert _EVENT_KEYS <= frozenset(event)
    return events


def _facade_span(events: list, entrypoint: str = "vod.files") -> dict:
    """Assert exactly one started and one terminal event for the facade call."""
    calls = [
        event for event in events if event["kind"] == "call" and event["entrypoint"] == entrypoint
    ]
    started = [event for event in calls if event["outcome"] == "started"]
    terminal = [event for event in calls if event["outcome"] != "started"]
    assert len(started) == 1, f"{entrypoint} must emit exactly one started event"
    assert len(terminal) == 1, f"{entrypoint} must emit exactly one terminal event"
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _close_span(events: list) -> dict:
    """Assert exactly one started and one terminal close-scope event."""
    closes = [event for event in events if event["kind"] == "close"]
    started = [event for event in closes if event["outcome"] == "started"]
    terminal = [event for event in closes if event["outcome"] != "started"]
    assert len(started) == 1, "close must emit exactly one started event"
    assert len(terminal) == 1, "close must emit exactly one terminal event"
    assert started[0]["span_id"] == terminal[0]["span_id"]
    return terminal[0]


def _dump_events(recorder: DiagnosticRecorder) -> str:
    return json.dumps(_events(recorder), default=repr, ensure_ascii=False) + recorder.to_json()


def execute(
    kind,
    *,
    args=None,
    kwargs=None,
    region=None,
    script=None,
    provider_script=None,
    recorder=None,
    transport=None,
    log=None,
):
    """Drive ``client.vod.files`` over scripted provider + transport."""
    args = () if args is None else args
    kwargs = {} if kwargs is None else kwargs
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
                assert hasattr(client, "vod"), "vod facade missing"
                result = client.vod.files(*args, **kwargs)
        else:

            async def run():
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, provider_script), **options
                ) as client:
                    assert hasattr(client, "vod"), "vod facade missing"
                    bound = client.vod.files(*args, **kwargs)
                    assert asyncio.iscoroutine(bound), "async files must return a coroutine"
                    return await bound

            result = asyncio.run(run())
    except ValueError:
        # Invalid input must fail before provider, factory and HTTP.
        assert not log, log
        raise
    return result, log


WIRE_CASES = {
    "nodes_csv_order_and_duplicates": (
        ([NODE_B, NODE_A, NODE_B],),
        {},
        {"fileNodeIds": f"{NODE_B},{NODE_A},{NODE_B}"},
    ),
    "nodes_tuple": (
        (("T1", "T2"),),
        {},
        {"fileNodeIds": "T1,T2"},
    ),
    "nodes_fifty_boundary": (
        ([f"N{i}" for i in range(50)],),
        {},
        {"fileNodeIds": ",".join(f"N{i}" for i in range(50))},
    ),
    "space_id_zero": ((), {"space_id": 0}, {"spaceId": "0"}),
    "space_id_negative": ((), {"space_id": -7}, {"spaceId": "-7"}),
    "space_name_long_unicode": (
        (),
        {"space_name": SPACE_NAME},
        {"spaceName": SPACE_NAME},
    ),
    "all_selectors_together": (
        (["N1"],),
        {"space_id": 0, "space_name": "空间"},
        {"fileNodeIds": "N1", "spaceId": "0", "spaceName": "空间"},
    ),
}


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("label", list(WIRE_CASES))
def test_files_exact_wire(kind, label):
    args, kwargs, expected = WIRE_CASES[label]
    result, log = execute(kind, args=args, kwargs=kwargs)
    kinds = _kinds(log)
    assert kinds.count("get_token") == 1 and kinds.count("http") == 1
    assert kinds.index("get_token") < kinds.index("http")
    request = _http(log)[0]
    assert request.method == "GET"
    assert request.url.path == "/api/service/open/vod/files"
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == TOKEN_1
    assert "accessToken" not in request.url.params
    assert not request.content
    assert "content-type" not in {k.lower() for k in request.headers.keys()}
    assert dict(request.url.params) == expected
    assert result.http_status == 200 and result.code == 200
    assert result.operation == "vod.files"
    assert result.raw == BODY
    assert result.data is result.raw["data"] and result.data == BODY["data"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_snapshots_ids_before_authentication(kind):
    ids = [NODE_B, NODE_A, NODE_B]

    def mutating_token():
        ids[:] = ["ZZZ_REPLACED"]
        return _token(TOKEN_1)

    result, log = execute(kind, args=(ids,), provider_script=[mutating_token])
    assert _kinds(log).index("get_token") < _kinds(log).index("http")
    assert _http(log)[0].url.params["fileNodeIds"] == f"{NODE_B},{NODE_A},{NODE_B}"
    assert ids == ["ZZZ_REPLACED"]
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_data_array_passthrough_and_no_flatten(kind):
    data = [{"fileNodeId": "n1", "unknown": {"deep": [1]}}, "raw-item", 7]
    body = {"meta": {"code": 200, "message": "ok"}, "data": data, "unknownTop": {"x": 1}}
    result, _ = execute(kind, args=(["n1"],), script=[_resp(body)])
    assert result.raw == body and result.raw["unknownTop"] == {"x": 1}
    assert isinstance(result.data, list) and result.data == data
    assert result.data is result.raw["data"]

    empty = {"meta": {"code": 200, "message": "ok"}, "data": []}
    result, _ = execute(kind, args=(["n1"],), script=[_resp(empty)])
    assert result.data == [] and result.data is result.raw["data"]

    nodata = {"meta": {"code": 200, "message": "ok"}}
    result, _ = execute(kind, args=(["n1"],), script=[_resp(nodata)])
    assert result.data is None


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_replays_read_once_on_stale_token(kind):
    result, log = execute(
        kind,
        args=(["N1"],),
        script=[_resp(STALE), _resp()],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
    )
    requests = _http(log)
    assert len(requests) == 2
    assert requests[0].headers["accessToken"] == TOKEN_1
    assert requests[1].headers["accessToken"] == TOKEN_2
    assert _kinds(log).count("get_token") == 2
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.code == 200 and result.data == BODY["data"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_replay_happens_at_most_once(kind):
    log = []
    with pytest.raises((ApiError, AuthenticationError)):
        execute(
            kind,
            args=(["N1"],),
            script=[_resp(STALE), _resp(STALE)],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
        )
    assert _kinds(log).count("http") == 2
    assert _kinds(log).count("get_token") == 2


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "script,error_types",
    [
        # Responses are built inside the handler: a pre-created httpx.Response
        # gets its stream bound to a sync client on first use and can never be
        # re-read by the async transport.
        ([lambda request: _resp(DENIED)], (ApiError, PermissionDeniedError)),
        ([lambda request: _resp(FAILED)], (ApiError,)),
        ([lambda request: _resp(BODY, status=500)], (ApiError,)),
        ([_timeout], (TransportError,)),
    ],
)
def test_files_failures_do_not_replay(kind, script, error_types):
    log = []
    with pytest.raises(Exception) as captured:
        execute(kind, args=(["N1"],), script=script, log=log)
    assert isinstance(captured.value, error_types)
    assert _kinds(log).count("http") == 1
    assert _kinds(log).count("get_token") == 1
    assert not [entry for entry in log if entry[0] == "invalidate"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_http_error_status_is_preserved(kind):
    with pytest.raises(ApiError) as captured:
        execute(kind, args=(["N1"],), script=[_resp(BODY, status=500)])
    assert captured.value.http_status == 500


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "code,error_type",
    [("200", ApiError), (200.0, ProtocolError), (True, ProtocolError)],
)
def test_files_success_code_type_is_strict(kind, code, error_type):
    with pytest.raises(error_type):
        execute(
            kind,
            args=(["N1"],),
            script=[_resp({"meta": {"code": code, "message": "x"}, "data": []})],
        )


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "body,expect_error",
    [
        (
            {
                "meta": {"code": 200, "message": "see ticket 10002"},
                "data": [{"code": "10002", "note": "x10002x"}],
            },
            False,
        ),
        (
            {"meta": {"code": 2000, "message": "upstream 10002 reference"}, "data": None},
            True,
        ),
    ],
)
def test_files_short_10002_collision_does_not_trigger_replay(kind, body, expect_error):
    log = []
    if expect_error:
        with pytest.raises(ApiError):
            execute(kind, args=(["N1"],), script=[_resp(body)], log=log)
    else:
        result, _ = execute(kind, args=(["N1"],), script=[_resp(body)], log=log)
        assert result.code == 200 and result.data == body["data"]
    assert _kinds(log).count("http") == 1
    assert _kinds(log).count("get_token") == 1
    assert not [entry for entry in log if entry[0] == "invalidate"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_id_colliding_with_10002_still_replays_once(kind):
    """A real fileNodeId of ``"10002"`` must not confuse the stale-token path."""
    stale = {"meta": {"code": 10002, "message": "stale"}, "data": None}
    ok = {"meta": {"code": 200, "message": "ok"}, "data": [{"fileNodeId": "10002"}]}
    result, log = execute(
        kind,
        args=(["10002"],),
        script=[_resp(stale), _resp(ok)],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
    )
    requests = _http(log)
    assert len(requests) == 2
    assert requests[0].headers["accessToken"] == TOKEN_1
    assert requests[1].headers["accessToken"] == TOKEN_2
    assert _kinds(log).count("get_token") == 2
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.code == 200 and result.data == ok["data"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_domestic_only(kind):
    with pytest.raises(ValueError):
        execute(kind, args=(["N1"],), region=RegionConfig.overseas())


INVALID = [
    (("N1,N2",), {}),
    ((b"N1",), {}),
    (((x for x in ["N1"]),), {}),
    (({"n": 1},), {}),
    ((5,), {}),
    ((["N1,N2"],), {}),
    ((["N1", ""],), {}),
    ((["N1", "   "],), {}),
    ((["\ud800"],), {}),
    (([None],), {}),
    (([1],), {}),
    (([True],), {}),
    (([b"N"],), {}),
    (([],), {}),
    ((["N"] * 51,), {}),
    ((None,), {}),
    ((), {}),
    ((), {"space_id": None, "space_name": None}),
    ((), {"space_id": True}),
    ((), {"space_id": False}),
    ((), {"space_id": "7"}),
    ((), {"space_id": 1.5}),
    ((), {"space_name": ""}),
    ((), {"space_name": "   "}),
    ((), {"space_name": 1}),
    ((), {"space_name": "\ud800"}),
    ((), {"space_name": b"x"}),
    ((["N1"],), {"space_id": True}),
    ((["N1"],), {"space_name": " "}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("bad", INVALID)
def test_invalid_inputs_rejected_before_any_io(kind, bad):
    args, kwargs = bad
    with pytest.raises(ValueError):
        execute(kind, args=args, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_requires_at_least_one_selector(kind):
    for args, kwargs in (((), {}), ((None,), {}), ((), {"space_id": None, "space_name": None})):
        with pytest.raises(ValueError):
            execute(kind, args=args, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_validation_errors_do_not_echo_input_values(kind):
    marker = "HIDDEN_VALUE_" + "v" * 40
    with pytest.raises(ValueError) as comma_case:
        execute(kind, args=([marker + ",x"],))
    assert marker not in str(comma_case.value)
    with pytest.raises(ValueError) as type_case:
        execute(kind, kwargs={"space_id": marker})
    assert marker not in str(type_case.value)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_on_closed_client_fails_without_http(kind):
    log = []
    factory = _scripted_factory(log, [_resp()])
    recorder = DiagnosticRecorder()
    options = dict(
        region=RegionConfig.china(),
        transport_factory=factory,
        diagnostic_recorder=recorder,
    )
    if kind == "sync":
        client = Client(token_provider=_SyncProvider(log, [_token(TOKEN_1)]), **options)
        client.close()
        with pytest.raises(TransportError) as captured:
            client.vod.files(["N1"])
        exc = captured.value
    else:

        async def run():
            client = AsyncClient(token_provider=_AsyncProvider(log, [_token(TOKEN_1)]), **options)
            await client.aclose()
            with pytest.raises(TransportError) as captured:
                await client.vod.files(["N1"])
            return captured.value

        exc = asyncio.run(run())
    assert exc.reason == "closed"
    assert not _http(log)
    events = _events(recorder)
    terminal = _facade_span(events)
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "transport"
    assert terminal["reason"] == "closed"
    assert _close_span(events)["outcome"] == "succeeded"


def test_async_files_cancellation_propagates():
    transport = _BlockingTransport()
    log = []
    recorder = DiagnosticRecorder()

    async def run():
        async with AsyncClient(
            region=RegionConfig.china(),
            token_provider=_AsyncProvider(log, [_token(TOKEN_1)]),
            transport_factory=_fixed_factory(log, transport),
            diagnostic_recorder=recorder,
        ) as client:
            task = asyncio.create_task(client.vod.files(["N1"]))
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


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_diagnostic_on_off_same_wire_and_result(kind):
    args = ([NODE_B, NODE_A, NODE_B],)
    kwargs = {"space_id": 0, "space_name": "空间"}
    plain_result, plain_log = execute(kind, args=args, kwargs=kwargs)
    recorder = DiagnosticRecorder()
    diag_result, diag_log = execute(kind, args=args, kwargs=kwargs, recorder=recorder)
    assert [_fingerprint(r) for r in _http(plain_log)] == [_fingerprint(r) for r in _http(diag_log)]
    assert _kinds(plain_log) == _kinds(diag_log)
    assert diag_result.raw == plain_result.raw == BODY
    assert diag_result.data == plain_result.data == BODY["data"]
    assert diag_result.http_status == plain_result.http_status == 200
    assert diag_result.code == plain_result.code == 200
    assert diag_result.operation == plain_result.operation == "vod.files"
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "succeeded"
    assert terminal["error_family"] == "none"


# Per-scenario expectations for the facade ``call`` span terminal event:
# (outcome, error_family, reason).
_DIAG_SCENARIOS = {
    "success": ("succeeded", "none", "none"),
    "validation": ("failed", "validation", "none"),
    "auth": ("failed", "authentication", "none"),
    "transport": ("failed", "transport", "timeout"),
    "business": ("failed", "api", "none"),
}


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("scenario", list(_DIAG_SCENARIOS))
def test_diagnostic_terminal_state_visible(kind, scenario):
    recorder = DiagnosticRecorder()
    log = []
    args, kwargs = (["N1"],), {}
    script = [lambda request: _resp()]
    provider_script = [_token(TOKEN_1)]
    expected = None
    if scenario == "validation":
        args = ([],)
        expected = ValueError
    elif scenario == "auth":
        provider_script = [AuthenticationError("synthetic auth failure")]
        expected = AuthenticationError
    elif scenario == "transport":
        script = [_timeout]
        expected = TransportError
    elif scenario == "business":
        script = [lambda request: _resp(FAILED)]
        expected = ApiError
    if expected is None:
        execute(
            kind,
            args=args,
            kwargs=kwargs,
            script=script,
            provider_script=provider_script,
            recorder=recorder,
            log=log,
        )
    else:
        with pytest.raises(expected):
            execute(
                kind,
                args=args,
                kwargs=kwargs,
                script=script,
                provider_script=provider_script,
                recorder=recorder,
                log=log,
            )
    outcome, family, reason = _DIAG_SCENARIOS[scenario]
    events = _events(recorder)
    terminal = _facade_span(events)
    assert terminal["outcome"] == outcome
    assert terminal["error_family"] == family
    assert terminal["reason"] == reason
    assert terminal["mode"] == kind


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_diagnostic_off_and_on_raise_identically(kind):
    with pytest.raises(ApiError) as plain:
        execute(kind, args=(["N1"],), script=[_resp(FAILED)])
    recorder = DiagnosticRecorder()
    with pytest.raises(ApiError) as recorded:
        execute(kind, args=(["N1"],), script=[_resp(FAILED)], recorder=recorder)
    assert type(recorded.value) is type(plain.value)
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "failed"
    assert terminal["error_family"] == "api"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_sensitive_values_not_leaked_on_error(kind, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    err_body = {
        "meta": {
            "code": 400,
            "message": f"{NODE_A} missing; {SPACE_NAME} denied; {TOKEN_1}",
        },
        "data": {"mediaURL": MEDIA_URL},
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(
            kind,
            args=([NODE_A, NODE_B],),
            kwargs={"space_name": SPACE_NAME},
            script=[_resp(err_body)],
            recorder=recorder,
            log=log,
        )
    request = _http(log)[0]
    assert request.url.params["fileNodeIds"] == f"{NODE_A},{NODE_B}"
    assert request.url.params["spaceName"] == SPACE_NAME
    assert request.headers["accessToken"] == TOKEN_1
    surface = str(captured.value) + repr(captured.value) + caplog.text + _dump_events(recorder)
    for secret in (NODE_A, NODE_B, SPACE_NAME, TOKEN_1, MEDIA_URL):
        assert secret not in surface


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_response_url_hidden_from_repr_and_diagnostics(kind, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    body = {
        "meta": {"code": 200, "message": "ok"},
        "data": [{"fileNodeId": NODE_A, "mediaURL": MEDIA_URL}],
    }
    result, log = execute(kind, args=([NODE_A],), script=[_resp(body)], recorder=recorder)
    assert result.data == body["data"]
    assert _http(log)[0].url.params["fileNodeIds"] == NODE_A
    surface = repr(result) + caplog.text + _dump_events(recorder)
    for secret in (NODE_A, TOKEN_1, MEDIA_URL):
        assert secret not in surface


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_returns_the_client_request_result_unchanged(kind):
    """``vod.files`` must return the exact ``ApiResponse`` ``client.request`` returns."""
    recorder = DiagnosticRecorder()
    sentinel = ApiResponse(200, 200, operation="vod.files", data={"marker": True})
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
            result = client.vod.files(["N1"])
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
                return await client.vod.files(["N1"])

        result = asyncio.run(run())
    assert result is sentinel
    assert produced == ["vod.files"]
    terminal = _facade_span(_events(recorder))
    assert terminal["outcome"] == "succeeded"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "overrides,error_types,tokens,factories,https",
    [
        ({"args": ([],)}, (ValueError,), 0, 0, 0),
        (
            {"provider_script": [AuthenticationError("boom")]},
            (AuthenticationError,),
            1,
            0,
            0,
        ),
        ({"script": [_timeout]}, (TransportError,), 1, 1, 1),
        ({"close_first": True}, (TransportError,), 0, 0, 0),
        (
            {
                "script": [
                    lambda request: _resp(STALE),
                    lambda request: _resp(STALE),
                ],
                "provider_script": [_token(TOKEN_1), _token(TOKEN_2)],
            },
            (ApiError, AuthenticationError),
            2,
            1,
            2,
        ),
    ],
)
def test_diagnostic_on_off_same_request_count_and_order(
    kind, overrides, error_types, tokens, factories, https
):
    """Recorder on/off must not change provider/factory/HTTP counts or order."""
    sequences = []
    for diag in (False, True):
        log = []
        recorder = DiagnosticRecorder() if diag else None
        options = dict(
            region=RegionConfig.china(),
            transport_factory=_scripted_factory(
                log, overrides.get("script", [lambda request: _resp()])
            ),
        )
        if recorder is not None:
            options["diagnostic_recorder"] = recorder
        provider_script = overrides.get("provider_script", [_token(TOKEN_1)])
        args = overrides.get("args", (["N1"],))
        if kind == "sync":
            client = Client(token_provider=_SyncProvider(log, provider_script), **options)
            if overrides.get("close_first"):
                client.close()
            try:
                with pytest.raises(error_types):
                    client.vod.files(*args)
            finally:
                if not overrides.get("close_first"):
                    client.close()
        else:

            async def run():
                client = AsyncClient(token_provider=_AsyncProvider(log, provider_script), **options)
                if overrides.get("close_first"):
                    await client.aclose()
                try:
                    with pytest.raises(error_types):
                        await client.vod.files(*args)
                finally:
                    if not overrides.get("close_first"):
                        await client.aclose()

            asyncio.run(run())
        kinds = _kinds(log)
        assert kinds.count("get_token") == tokens
        assert kinds.count("factory") == factories
        assert kinds.count("http") == https
        sequences.append(kinds)
    assert sequences[0] == sequences[1]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_provider_exception_identity_is_preserved(kind):
    """A provider-raised marker must propagate unchanged, recorder on or off."""
    for diag in (False, True):
        marker = AuthenticationError("marker failure")
        log = []
        recorder = DiagnosticRecorder() if diag else None
        options = dict(
            region=RegionConfig.china(),
            transport_factory=_scripted_factory(log, [lambda request: _resp()]),
        )
        if recorder is not None:
            options["diagnostic_recorder"] = recorder
        if kind == "sync":
            with Client(token_provider=_SyncProvider(log, [marker]), **options) as client:
                with pytest.raises(AuthenticationError) as captured:
                    client.vod.files(["N1"])
            captured_value = captured.value
        else:

            async def run(options=options, log=log, marker=marker):
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, [marker]), **options
                ) as client:
                    with pytest.raises(AuthenticationError) as captured:
                        await client.vod.files(["N1"])
                return captured.value

            captured_value = asyncio.run(run())
        assert captured_value is marker
        assert _kinds(log).count("get_token") == 1
        assert _kinds(log).count("http") == 0
        if diag:
            terminal = _facade_span(_events(recorder))
            assert terminal["outcome"] == "failed"
            assert terminal["error_family"] == "authentication"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_files_query_preserves_duplicate_keys_whitespace_and_none(kind):
    """Query encoding must not drop duplicate keys, strip values, or send None."""
    padded_name = "  空间  "
    result, log = execute(
        kind,
        args=([NODE_A, NODE_A],),
        kwargs={"space_name": padded_name, "space_id": None},
    )
    request = _http(log)[0]
    pairs = request.url.params.multi_items()
    assert ("fileNodeIds", f"{NODE_A},{NODE_A}") in pairs
    assert ("spaceName", padded_name) in pairs
    keys = [key for key, _ in pairs]
    assert "spaceId" not in keys
    assert request.url.params["spaceName"] == padded_name
    assert result.code == 200
