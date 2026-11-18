"""Wire-contract tests for the four frozen ``client.vod`` remux/task/address methods.

Covered entrypoints (sync and async): ``vod.create_remux_task`` (doc 3971),
``vod.task`` (doc 3972), ``vod.tasks`` (doc 4941), ``vod.recording_urls``
(doc 3973). Fully offline and synthetic: scripted token providers plus
``httpx.MockTransport`` doubles only. No environment, network, or credential
access; no real SDK calls are authorized or attempted here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    RegionConfig,
    TransportError,
)
from ezviz_unified.auth_models import Token

ORIGIN = "https://open.ys7.com"
FAR_FUTURE = 9_999_999_999.0

DEVICE = "C" + "a" * 17  # legal 18-char device serial sentinel
LOCAL_INDEX = "1"
BUS7_SPACE = 34
TASK_ID = "REMUX-" + "t" * 58
TOKEN_1 = "VODREMUXTOKEN_" + "x" * 56
TOKEN_2 = "VODREMUXTOKEN_" + "y" * 56

START = datetime(2026, 1, 2, 3, 4, 5)
END = datetime(2026, 1, 2, 4, 5, 6)

TASK_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {
        "taskId": TASK_ID,
        "taskType": 1,
        "taskSubType": 81,
        "taskStatus": 2,
        "progressRate": 45,
        "unknownField": {"nested": [1]},
    },
    "unrelatedTopLevel": "preserved",
}
TASKS_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {
        "result": [
            {"taskId": "A", "taskType": 7, "taskStatus": 3, "progressRate": 0},
            {"taskId": "B", "taskType": 8, "taskStatus": 1},
        ],
        "pageStart": 0,
        "pageSize": 10,
        "total": 2,
    },
}
URLS_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": [
        {
            "hlsAddress": "https://media.example.invalid/vod/hls/" + "m" * 48,
            "stopTime": "2026-01-02 04:05:06",
            "expireTime": "2026-01-02 05:00:00",
            "unknownField": [1],
        }
    ],
}
BODY = {"meta": {"code": 200, "message": "ok"}, "data": {"ok": True}}
STALE = {"meta": {"code": 10002, "message": "stale token"}, "data": None}
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


def _http(log: list) -> list:
    return [entry[1] for entry in log if entry[0] == "http"]


def _kinds(log: list) -> list:
    return [entry[0] for entry in log]


def _form(request: httpx.Request) -> dict:
    return dict(parse_qsl(request.content.decode("utf-8")))


def execute(
    kind,
    method,
    *,
    args=None,
    kwargs=None,
    region=None,
    script=None,
    provider_script=None,
    log=None,
):
    """Drive ``client.vod.<method>`` over scripted provider + transport."""
    args = () if args is None else args
    kwargs = {} if kwargs is None else kwargs
    log = [] if log is None else log
    provider_script = [_token(TOKEN_1)] if provider_script is None else provider_script
    factory = _scripted_factory(log, [_resp()] if script is None else script)
    options = dict(
        region=region or RegionConfig.china(),
        transport_factory=factory,
    )
    try:
        if kind == "sync":
            with Client(token_provider=_SyncProvider(log, provider_script), **options) as client:
                assert hasattr(client, "vod"), "vod facade missing"
                result = getattr(client.vod, method)(*args, **kwargs)
        else:

            async def run():
                async with AsyncClient(
                    token_provider=_AsyncProvider(log, provider_script), **options
                ) as client:
                    assert hasattr(client, "vod"), "vod facade missing"
                    bound = getattr(client.vod, method)(*args, **kwargs)
                    assert asyncio.iscoroutine(bound), "async facade must return a coroutine"
                    return await bound

            result = asyncio.run(run())
    except ValueError:
        # Invalid input must fail before provider, factory and HTTP.
        assert not log, log
        raise
    return result, log


# ---------------------------------------------------------------------------
# create_remux_task: POST /api/service/open/vod/media/trans/code
# ---------------------------------------------------------------------------

_REMUX_ARGS = (DEVICE,)
_REMUX_KW = {"local_index": LOCAL_INDEX, "bus_type": "1", "start_time": START, "end_time": END}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_exact_wire_minimal(kind):
    result, log = execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=_REMUX_KW)
    assert _kinds(log) == ["get_token", "factory", "http"]
    request = _http(log)[0]
    assert request.method == "POST"
    assert request.url.path == "/api/service/open/vod/media/trans/code"
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == TOKEN_1
    assert request.headers["deviceSerial"] == DEVICE
    assert request.headers["localIndex"] == LOCAL_INDEX
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert "accessToken" not in request.url.params
    assert not dict(request.url.params)
    form = _form(request)
    assert form == {
        "busType": "1",
        "startTime": "2026-01-02 03:04:05",
        "endTime": "2026-01-02 04:05:06",
    }
    assert result.http_status == 200 and result.code == 200
    assert result.operation == "vod.create_remux_task"
    assert result.raw == BODY


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_exact_wire_all_optionals(kind):
    kwargs = dict(
        _REMUX_KW,
        bus_type="71",
        format="MP4",
        space_id=0,
        result_space_id=-5,
        validate_code="验证码-42",
        acceleration=1,
    )
    result, log = execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)
    request = _http(log)[0]
    form = _form(request)
    assert form == {
        "busType": "71",
        "startTime": "2026-01-02 03:04:05",
        "endTime": "2026-01-02 04:05:06",
        "format": "MP4",
        "spaceId": "0",
        "resultSpaceId": "-5",
        "validateCode": "验证码-42",
        "acceleration": "1",
    }
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_bus_type_7_sends_space_id(kind):
    kwargs = dict(_REMUX_KW, bus_type="7", space_id=BUS7_SPACE)
    result, log = execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)
    form = _form(_http(log)[0])
    assert form["busType"] == "7"
    assert form["spaceId"] == str(BUS7_SPACE)
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_bus_type_7_without_space_id_rejected(kind):
    kwargs = dict(_REMUX_KW, bus_type="7")
    with pytest.raises(ValueError):
        execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_bus_type_71_explicit_hls_rejected(kind):
    kwargs = dict(_REMUX_KW, bus_type="71", format="HLS")
    with pytest.raises(ValueError):
        execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_bus_type_71_none_format_omits_field(kind):
    kwargs = dict(_REMUX_KW, bus_type="71", format=None)
    result, log = execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)
    form = _form(_http(log)[0])
    assert form["busType"] == "71"
    assert "format" not in form
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "delta",
    [timedelta(seconds=10), timedelta(hours=48)],
)
def test_remux_interval_boundaries_accepted(kind, delta):
    kwargs = dict(_REMUX_KW, end_time=START + delta)
    result, log = execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)
    form = _form(_http(log)[0])
    assert form["endTime"] == (START + delta).strftime("%Y-%m-%d %H:%M:%S")
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "delta",
    [timedelta(seconds=9), timedelta(hours=48, seconds=1)],
)
def test_remux_interval_out_of_bounds_rejected(kind, delta):
    kwargs = dict(_REMUX_KW, end_time=START + delta)
    with pytest.raises(ValueError):
        execute(kind, "create_remux_task", args=_REMUX_ARGS, kwargs=kwargs)


_REMUX_INVALID = [
    # header rules: non-blank ASCII str, no control/DEL, no lead/trail space
    (("",), {}),
    (("   ",), {}),
    ((DEVICE + "\n",), {}),
    ((" " + DEVICE,), {}),
    ((DEVICE + " ",), {}),
    (("设备串口",), {}),
    ((DEVICE,), {"local_index": 1}),  # local_index must be str
    ((DEVICE,), {"local_index": " "}),  # leading/trailing space refused
    ((DEVICE,), {"local_index": "1\n"}),  # control char refused
    ((DEVICE,), {"local_index": "通道1"}),  # non-ASCII refused
    # bus_type strict str enum
    ((DEVICE,), {"bus_type": 1}),
    ((DEVICE,), {"bus_type": "2"}),
    ((DEVICE,), {"bus_type": "71 "}),
    ((DEVICE,), {"bus_type": ""}),
    # naive whole-second datetimes
    ((DEVICE,), {"start_time": "2026-01-02 03:04:05"}),
    ((DEVICE,), {"start_time": START.replace(microsecond=1)}),
    ((DEVICE,), {"start_time": START.replace(tzinfo=timezone.utc)}),
    ((DEVICE,), {"end_time": START}),
    # optional ints non-bool
    ((DEVICE,), {"space_id": True}),
    ((DEVICE,), {"space_id": "34"}),
    ((DEVICE,), {"result_space_id": False}),
    ((DEVICE,), {"acceleration": True}),
    ((DEVICE,), {"acceleration": 2}),
    ((DEVICE,), {"acceleration": -1}),
    # validate_code non-blank UTF-8 str
    ((DEVICE,), {"validate_code": ""}),
    ((DEVICE,), {"validate_code": "   "}),
    ((DEVICE,), {"validate_code": 42}),
    ((DEVICE,), {"format": "AVI"}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("bad_args,bad_kwargs", _REMUX_INVALID)
def test_remux_invalid_inputs_rejected_before_any_io(kind, bad_args, bad_kwargs):
    kwargs = {**_REMUX_KW, **bad_kwargs}
    with pytest.raises(ValueError):
        execute(kind, "create_remux_task", args=bad_args, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_never_replays_on_stale_token(kind):
    """Non-idempotent write: 10002 is terminal, one invalidate, no second call."""
    log = []
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            "create_remux_task",
            args=_REMUX_ARGS,
            kwargs=_REMUX_KW,
            script=[_resp(STALE), _resp()],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
        )
    assert captured.value.invalid_token is True
    assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
    assert len(_http(log)) == 1
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "script,error_types",
    [
        ([lambda request: _resp(FAILED)], (ApiError,)),
        ([lambda request: _resp(BODY, status=500)], (ApiError,)),
        ([_timeout], (TransportError,)),
    ],
)
def test_remux_failures_do_not_replay(kind, script, error_types):
    log = []
    with pytest.raises(Exception) as captured:
        execute(
            kind, "create_remux_task", args=_REMUX_ARGS, kwargs=_REMUX_KW, script=script, log=log
        )
    assert isinstance(captured.value, error_types)
    assert _kinds(log).count("http") == 1
    assert _kinds(log).count("get_token") == 1
    assert not [entry for entry in log if entry[0] == "invalidate"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_returns_plain_api_response(kind):
    """Creation returns the ApiResponse unchanged — never a task handle."""
    result, log = execute(
        kind,
        "create_remux_task",
        args=_REMUX_ARGS,
        kwargs=_REMUX_KW,
        script=[_resp({"meta": {"code": 200}, "data": {"taskId": TASK_ID}})],
    )
    assert type(result) is ApiResponse
    assert result.data == {"taskId": TASK_ID}
    assert result.raw["meta"]["code"] == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_domestic_only(kind):
    with pytest.raises(ValueError):
        execute(
            kind,
            "create_remux_task",
            args=_REMUX_ARGS,
            kwargs=_REMUX_KW,
            region=RegionConfig.overseas(),
        )


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_remux_sensitive_values_not_on_wire_or_error(kind):
    """deviceSerial/validateCode/token travel on the wire but stay out of errors."""
    marker_code = "VCODE-" + "v" * 44
    echo = {
        "meta": {"code": 400, "message": f"{DEVICE} {marker_code} {TOKEN_1} rejected"},
        "data": None,
    }
    kwargs = dict(_REMUX_KW, validate_code=marker_code)
    log = []
    with pytest.raises(ApiError) as captured:
        execute(
            kind,
            "create_remux_task",
            args=_REMUX_ARGS,
            kwargs=kwargs,
            script=[_resp(echo)],
            log=log,
        )
    request = _http(log)[0]
    assert request.headers["deviceSerial"] == DEVICE
    assert _form(request)["validateCode"] == marker_code
    assert request.headers["accessToken"] == TOKEN_1
    surface = str(captured.value) + repr(captured.value)
    for secret in (DEVICE, marker_code, TOKEN_1):
        assert secret not in surface


# ---------------------------------------------------------------------------
# task: GET /api/service/open/vod/task
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_task_exact_wire(kind):
    result, log = execute(kind, "task", args=(TASK_ID,))
    assert _kinds(log) == ["get_token", "factory", "http"]
    request = _http(log)[0]
    assert request.method == "GET"
    assert request.url.path == "/api/service/open/vod/task"
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == TOKEN_1
    assert "accessToken" not in request.url.params
    assert not request.content
    assert "content-type" not in {key.lower() for key in request.headers.keys()}
    # No business headers: deviceSerial/localIndex are create/remux-only.
    assert "deviceserial" not in {key.lower() for key in request.headers.keys()}
    assert "localindex" not in {key.lower() for key in request.headers.keys()}
    assert dict(request.url.params) == {"taskId": TASK_ID}
    assert result.operation == "vod.task"
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_task_response_passthrough_keeps_raw_fields(kind):
    """taskType=1/taskSubType/taskStatus/progressRate pass through untouched."""
    result, _ = execute(kind, "task", args=(TASK_ID,), script=[_resp(TASK_BODY)])
    assert result.raw == TASK_BODY
    assert result.data is result.raw["data"]
    assert result.data["taskType"] == 1
    assert result.data["taskSubType"] == 81
    assert result.data["taskStatus"] == 2
    assert result.data["progressRate"] == 45
    assert result.data["unknownField"] == {"nested": [1]}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_task_missing_meta_message_ok(kind):
    body = {"meta": {"code": 200}, "data": {"taskId": TASK_ID}}
    result, _ = execute(kind, "task", args=(TASK_ID,), script=[_resp(body)])
    assert result.code == 200 and result.data == {"taskId": TASK_ID}


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "task_id",
    [
        "  " + TASK_ID + "  ",  # leading/trailing whitespace kept
        "任务-" + "汉" * 20,  # arbitrary UTF-8
        "x" * 200,  # no invented length cap
    ],
)
def test_task_id_verbatim_long_unicode_whitespace(kind, task_id):
    result, log = execute(kind, "task", args=(task_id,))
    assert _http(log)[0].url.params["taskId"] == task_id
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("bad", ["", "   ", 7, None, b"T1", ["T1"], "\ud800"])
def test_task_invalid_id_rejected_before_any_io(kind, bad):
    with pytest.raises(ValueError):
        execute(kind, "task", args=(bad,))


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_task_replays_read_once_on_stale_token(kind):
    result, log = execute(
        kind,
        "task",
        args=(TASK_ID,),
        script=[_resp(STALE), _resp(TASK_BODY)],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
    )
    requests = _http(log)
    assert len(requests) == 2
    assert requests[0].headers["accessToken"] == TOKEN_1
    assert requests[1].headers["accessToken"] == TOKEN_2
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.code == 200 and result.data["taskId"] == TASK_ID


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_task_replay_at_most_once_on_repeated_10002(kind):
    log = []
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            "task",
            args=(TASK_ID,),
            script=[_resp(STALE), _resp(STALE)],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
        )
    assert captured.value.invalid_token is True
    # Second 10002 invalidates the second token too, but no third call.
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
        "invalidate",
    ]
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert invalidations == [("invalidate", TOKEN_1), ("invalidate", TOKEN_2)]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_task_domestic_only(kind):
    with pytest.raises(ValueError):
        execute(kind, "task", args=(TASK_ID,), region=RegionConfig.overseas())


# ---------------------------------------------------------------------------
# tasks: GET /api/service/open/vod/tasks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("task_type", [7, 8])
def test_tasks_exact_wire_minimal(kind, task_type):
    result, log = execute(kind, "tasks", args=(task_type,))
    assert _kinds(log) == ["get_token", "factory", "http"]
    request = _http(log)[0]
    assert request.method == "GET"
    assert request.url.path == "/api/service/open/vod/tasks"
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == TOKEN_1
    assert "accessToken" not in request.url.params
    assert not request.content
    assert "content-type" not in {key.lower() for key in request.headers.keys()}
    assert dict(request.url.params) == {"taskType": str(task_type)}
    assert result.operation == "vod.tasks"
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_exact_wire_all_optionals(kind):
    kwargs = {
        "page_start": -3,
        "page_size": 0,
        "start_time": "单侧开始 arbitrary 文本",
        "end_time": "2026-13-45 99:99:99",
    }
    result, log = execute(kind, "tasks", args=(8,), kwargs=kwargs)
    params = dict(_http(log)[0].url.params)
    assert params == {
        "taskType": "8",
        "pageStart": "-3",
        "pageSize": "0",
        "startTime": "单侧开始 arbitrary 文本",
        "endTime": "2026-13-45 99:99:99",
    }
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_none_optionals_omitted(kind):
    """None optionals are omitted; no injected 0/10 page defaults."""
    result, log = execute(kind, "tasks", args=(7,))
    params = dict(_http(log)[0].url.params)
    assert params == {"taskType": "7"}
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_single_sided_times_independent(kind):
    """start_time and end_time are sent alone, never required as a pair."""
    _, log = execute(kind, "tasks", args=(7,), kwargs={"start_time": "only-start"})
    assert dict(_http(log)[0].url.params) == {"taskType": "7", "startTime": "only-start"}
    _, log = execute(kind, "tasks", args=(7,), kwargs={"end_time": "only-end"})
    assert dict(_http(log)[0].url.params) == {"taskType": "7", "endTime": "only-end"}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_result_passthrough_object_not_list(kind):
    """data.result[] + pageStart/pageSize/total, never data[]/lastId/hasNext."""
    result, _ = execute(kind, "tasks", args=(7,), script=[_resp(TASKS_BODY)])
    assert result.raw == TASKS_BODY
    assert result.data is result.raw["data"]
    assert isinstance(result.data, dict)
    assert result.data["result"][0]["taskType"] == 7
    assert result.data["result"][1]["taskType"] == 8
    assert result.data["total"] == 2
    assert "lastId" not in result.data
    assert "hasNext" not in result.data


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_empty_result_and_missing_data_preserved(kind):
    empty = {"meta": {"code": 200, "message": "ok"}, "data": {"result": [], "total": 0}}
    result, _ = execute(kind, "tasks", args=(7,), script=[_resp(empty)])
    assert result.data == {"result": [], "total": 0}
    nodata = {"meta": {"code": 200, "message": "ok"}}
    result, _ = execute(kind, "tasks", args=(7,), script=[_resp(nodata)])
    assert result.data is None


_TASKS_INVALID = [
    (("7",), {}),  # bus_type str must not pass
    ((1,), {}),  # 3972 internal taskType not accepted
    ((7.0,), {}),
    ((True,), {}),
    ((None,), {}),
    ((9,), {}),
    ((7,), {"page_start": True}),
    ((7,), {"page_start": "0"}),
    ((7,), {"page_start": 1.5}),
    ((7,), {"page_size": False}),
    ((7,), {"page_size": "10"}),
    ((7,), {"start_time": ""}),
    ((7,), {"start_time": "   "}),
    ((7,), {"start_time": 42}),
    ((7,), {"end_time": "\ud800"}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("bad_args,bad_kwargs", _TASKS_INVALID)
def test_tasks_invalid_inputs_rejected_before_any_io(kind, bad_args, bad_kwargs):
    with pytest.raises(ValueError):
        execute(kind, "tasks", args=bad_args, kwargs=bad_kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_replays_read_once_on_stale_token(kind):
    result, log = execute(
        kind,
        "tasks",
        args=(7,),
        script=[_resp(STALE), _resp(TASKS_BODY)],
        provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
    )
    assert _kinds(log) == [
        "get_token",
        "factory",
        "http",
        "invalidate",
        "get_token",
        "http",
    ]
    requests = _http(log)
    assert requests[0].headers["accessToken"] == TOKEN_1
    assert requests[1].headers["accessToken"] == TOKEN_2
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)
    assert result.data["total"] == 2


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_no_autopagination(kind):
    """One call = one HTTP request; the SDK never advances pages itself."""
    result, log = execute(
        kind,
        "tasks",
        args=(7,),
        kwargs={"page_start": 0, "page_size": 2},
        script=[_resp(TASKS_BODY)],
    )
    assert _kinds(log).count("http") == 1
    assert _http(log)[0].url.params["pageStart"] == "0"
    assert result.data["pageSize"] == 10  # server value preserved, not our page_size


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_tasks_domestic_only(kind):
    with pytest.raises(ValueError):
        execute(kind, "tasks", args=(7,), region=RegionConfig.overseas())


# ---------------------------------------------------------------------------
# recording_urls: POST /api/service/open/vod/file/address/hls
# ---------------------------------------------------------------------------

_URLS_KW = {
    "local_index": LOCAL_INDEX,
    "start_time": START,
    "end_time": END,
    "format": "MP4",
}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_exact_wire_minimal(kind):
    result, log = execute(kind, "recording_urls", args=(DEVICE,), kwargs=_URLS_KW)
    assert _kinds(log) == ["get_token", "factory", "http"]
    request = _http(log)[0]
    assert request.method == "POST"
    assert request.url.path == "/api/service/open/vod/file/address/hls"
    assert request.url.host == "open.ys7.com"
    assert request.headers["accessToken"] == TOKEN_1
    assert request.headers["deviceSerial"] == DEVICE
    assert request.headers["localIndex"] == LOCAL_INDEX
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert "accessToken" not in request.url.params
    assert not dict(request.url.params)
    form = _form(request)
    assert form == {
        "startTime": "2026-01-02 03:04:05",
        "endTime": "2026-01-02 04:05:06",
        "format": "MP4",
    }
    assert result.operation == "vod.recording_urls"
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("format_value", ["MP4", "HLS"])
def test_recording_urls_both_formats_sent(kind, format_value):
    kwargs = dict(_URLS_KW, format=format_value, expire_seconds=1, space_id=-9)
    result, log = execute(kind, "recording_urls", args=(DEVICE,), kwargs=kwargs)
    form = _form(_http(log)[0])
    assert form["format"] == format_value
    assert form["expireSeconds"] == "1"
    assert form["spaceId"] == "-9"
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_expire_seconds_boundaries(kind):
    for seconds in (1, 604800):
        kwargs = dict(_URLS_KW, expire_seconds=seconds)
        result, log = execute(kind, "recording_urls", args=(DEVICE,), kwargs=kwargs)
        assert _form(_http(log)[0])["expireSeconds"] == str(seconds)
        assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("seconds", [0, -1, 604801, True, "1800", 30.0])
def test_recording_urls_expire_seconds_invalid(kind, seconds):
    kwargs = dict(_URLS_KW, expire_seconds=seconds)
    with pytest.raises(ValueError):
        execute(kind, "recording_urls", args=(DEVICE,), kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_zero_length_interval_allowed(kind):
    """start == end has no documented positive minimum; only the 48h cap binds."""
    kwargs = dict(_URLS_KW, end_time=START)
    result, log = execute(kind, "recording_urls", args=(DEVICE,), kwargs=kwargs)
    form = _form(_http(log)[0])
    assert form["startTime"] == form["endTime"] == "2026-01-02 03:04:05"
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_forty_eight_hour_boundary(kind):
    kwargs = dict(_URLS_KW, end_time=START + timedelta(hours=48))
    result, log = execute(kind, "recording_urls", args=(DEVICE,), kwargs=kwargs)
    assert _form(_http(log)[0])["endTime"] == "2026-01-04 03:04:05"
    assert result.code == 200


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "delta",
    [timedelta(seconds=-1), timedelta(hours=48, seconds=1)],
)
def test_recording_urls_interval_out_of_bounds_rejected(kind, delta):
    kwargs = dict(_URLS_KW, end_time=START + delta)
    with pytest.raises(ValueError):
        execute(kind, "recording_urls", args=(DEVICE,), kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_response_field_names_preserved(kind):
    """hlsAddress/stopTime/expireTime stay verbatim; format=MP4 creates no mp4Address."""
    result, _ = execute(
        kind, "recording_urls", args=(DEVICE,), kwargs=_URLS_KW, script=[_resp(URLS_BODY)]
    )
    assert result.raw == URLS_BODY
    assert isinstance(result.data, list)
    item = result.data[0]
    assert item is result.raw["data"][0]
    assert item["hlsAddress"].startswith("https://media.example.invalid/")
    assert item["stopTime"] == "2026-01-02 04:05:06"
    assert item["expireTime"] == "2026-01-02 05:00:00"
    assert "mp4Address" not in item
    assert item["unknownField"] == [1]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_none_optionals_omitted(kind):
    """None expire_seconds/space_id are omitted; no injected 1800/default space."""
    result, log = execute(
        kind,
        "recording_urls",
        args=(DEVICE,),
        kwargs=dict(_URLS_KW, expire_seconds=None, space_id=None),
    )
    form = _form(_http(log)[0])
    assert "expireSeconds" not in form
    assert "spaceId" not in form
    assert result.code == 200


_URLS_INVALID = [
    (("",), {}),
    ((DEVICE + "\x7f",), {}),
    (("设备串口",), {}),
    ((DEVICE,), {"local_index": None}),
    ((DEVICE,), {"local_index": "1 "}),
    ((DEVICE,), {"local_index": "频道"}),
    ((DEVICE,), {"start_time": END, "end_time": START}),  # reversed
    ((DEVICE,), {"start_time": START.replace(microsecond=1)}),
    ((DEVICE,), {"start_time": START.replace(tzinfo=timezone.utc)}),
    ((DEVICE,), {"format": None}),
    ((DEVICE,), {"format": "AVI"}),
    ((DEVICE,), {"format": "mp4"}),
    ((DEVICE,), {"format": 1}),
    ((DEVICE,), {"space_id": True}),
    ((DEVICE,), {"space_id": "9"}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("bad_args,bad_kwargs", _URLS_INVALID)
def test_recording_urls_invalid_inputs_rejected_before_any_io(kind, bad_args, bad_kwargs):
    kwargs = {**_URLS_KW, **bad_kwargs}
    with pytest.raises(ValueError):
        execute(kind, "recording_urls", args=bad_args, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_never_replays_on_stale_token(kind):
    """read_only=False disables replay even though issuance may be idempotent."""
    log = []
    with pytest.raises(AuthenticationError) as captured:
        execute(
            kind,
            "recording_urls",
            args=(DEVICE,),
            kwargs=_URLS_KW,
            script=[_resp(STALE), _resp(URLS_BODY)],
            provider_script=[_token(TOKEN_1), _token(TOKEN_2)],
            log=log,
        )
    assert captured.value.invalid_token is True
    assert _kinds(log) == ["get_token", "factory", "http", "invalidate"]
    assert len(_http(log)) == 1
    invalidations = [entry for entry in log if entry[0] == "invalidate"]
    assert len(invalidations) == 1 and invalidations[0][1] in (None, TOKEN_1)


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "script,error_types",
    [
        ([lambda request: _resp(FAILED)], (ApiError,)),
        ([lambda request: _resp(BODY, status=500)], (ApiError,)),
        ([_timeout], (TransportError,)),
    ],
)
def test_recording_urls_failures_do_not_replay(kind, script, error_types):
    log = []
    with pytest.raises(Exception) as captured:
        execute(kind, "recording_urls", args=(DEVICE,), kwargs=_URLS_KW, script=script, log=log)
    assert isinstance(captured.value, error_types)
    assert _kinds(log).count("http") == 1
    assert _kinds(log).count("get_token") == 1
    assert not [entry for entry in log if entry[0] == "invalidate"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_domestic_only(kind):
    with pytest.raises(ValueError):
        execute(
            kind, "recording_urls", args=(DEVICE,), kwargs=_URLS_KW, region=RegionConfig.overseas()
        )


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_recording_urls_device_token_not_in_error(kind):
    """The wire carries deviceSerial/token verbatim; the error surface redacts."""
    echo = {
        "meta": {"code": 400, "message": f"{DEVICE} denied; token {TOKEN_1}"},
        "data": None,
    }
    log = []
    with pytest.raises(ApiError) as captured:
        execute(
            kind, "recording_urls", args=(DEVICE,), kwargs=_URLS_KW, script=[_resp(echo)], log=log
        )
    request = _http(log)[0]
    assert request.headers["deviceSerial"] == DEVICE
    assert request.headers["accessToken"] == TOKEN_1
    surface = str(captured.value) + repr(captured.value)
    for secret in (DEVICE, TOKEN_1):
        assert secret not in surface
