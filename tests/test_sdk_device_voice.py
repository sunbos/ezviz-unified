"""Contract tests for the domestic device voice catalog facade.

``device_voice.list`` (GET) and ``device_voice.rename`` (PUT) share one
domestic URI and transport every argument in the query string.  The real
sync/async facades run against in-memory ``httpx.MockTransport``s; all
values are synthetic (reserved ``.invalid`` hosts, dummy token).
"""

import asyncio
import sys

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

API_PATH = "/api/route/voice/v3/devices/voices"
BIZ_HOST = "open.ys7.com"
LIST_OPERATION = "device_voice.list"
RENAME_OPERATION = "device_voice.rename"

AUTH_PAYLOAD = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
ACK_PAYLOAD = {"meta": {"code": 200, "message": "ok", "moreInfo": None}}

DEVICE_SERIAL = "CSEPMR511Äö123Xy"
VOICE_ID = 987
VOICE_NAME = "门铃问候 ÄÖ 名称"
PADDED_VOICE_NAME = "  夜间 Näme  "
VOICE_URL = "https://voice-archive.invalid/资料库/Grüße 文件.m4a?sign=ab%2Fcd%3D&tag=Ä+Ö&mix=1 2"
SIGNED_URL = "https://voice-archive.invalid/material/问候.m4a?X-Sign=zz%2F%3D&expire=9"

RICH_ROWS = [
    {
        "voiceId": 3,
        "voiceName": "第一条问候",
        "voiceUrl": SIGNED_URL,
        "status": 1,
        "time": 1714980489,
        # Legacy spelling rides along untouched as a compat column.
        "updateTime": "2024-05-06 07:08:09",
        "vendorExtra": {"keep": [1, "x"]},
    },
    {
        "voiceId": -8,
        "voiceName": "Second Ñame",
        "voiceUrl": "https://voice-archive.invalid/raw.wav",
        # Unknown future status value must pass through unconverted.
        "status": "SYNCING_2077",
        "time": 1700000000,
        "surplus": "passthrough",
    },
    {"voiceId": 0, "voiceName": "仅编号"},
    {},
]

_MISSING = object()
MODES = pytest.mark.parametrize("mode", ["sync", "async"])


class _Harness:
    """Shared setup/runner driving the real facade in both modes."""

    def __init__(self, mode, biz_handler, region=None):
        assert mode in ("sync", "async")
        self.mode = mode
        self.recorder = DiagnosticRecorder()
        self.auth_requests = []
        self.biz_requests = []

        def _auth(request):
            self.auth_requests.append(request)
            return httpx.Response(200, json=AUTH_PAYLOAD)

        def _biz(request):
            self.biz_requests.append(request)
            return biz_handler(request)

        client_cls = Client if mode == "sync" else AsyncClient
        self.client = client_cls(
            "SYNTHKEY",
            "SYNTHSECRET",
            region=region or RegionConfig.china(),
            auth_transport=httpx.MockTransport(_auth),
            transport_factory=lambda origin: httpx.MockTransport(_biz),
            diagnostic_recorder=self.recorder,
        )

    def run(self, steps):
        """Execute ``[(method, args, kwargs), ...]`` inside one session."""
        if self.mode == "sync":
            with self.client as client:
                facade = client.device_voice
                return [getattr(facade, name)(*args, **kwargs) for name, args, kwargs in steps]

        async def _run_async():
            async with self.client as client:
                facade = client.device_voice
                results = []
                for name, args, kwargs in steps:
                    results.append(await getattr(facade, name)(*args, **kwargs))
                return results

        return asyncio.run(_run_async())

    def call(self, name, *args, **kwargs):
        return self.run([(name, args, kwargs)])[0]


def _events(harness, kind=None):
    events = harness.recorder.report()["events"]
    return [e for e in events if kind is None or e.get("kind") == kind]


def _call_events(harness, entrypoint):
    return [e for e in _events(harness, "call") if e.get("entrypoint") == entrypoint]


def _attempt_events(harness, operation=None):
    events = _events(harness, "attempt")
    if operation is not None:
        events = [e for e in events if e.get("operation") == operation]
    return events


def _assert_call_scope(harness, entrypoint, outcomes):
    events = _call_events(harness, entrypoint)
    assert [e.get("outcome") for e in events] == outcomes
    assert all(e.get("mode") == harness.mode for e in events)


def _assert_attempts(harness, operation, outcomes):
    events = _attempt_events(harness, operation)
    assert [e.get("outcome") for e in events] == outcomes
    assert all(e.get("mode") == harness.mode for e in events)
    # A single method call selects only its own operation.
    assert {e.get("operation") for e in _attempt_events(harness)} == {operation}


def _assert_query_only(request, method, expected_params):
    assert request.method == method
    assert request.url.host == BIZ_HOST
    assert request.url.path == API_PATH
    assert request.content == b""
    header_names = {name.lower() for name in request.headers.keys()}
    assert "content-type" not in header_names
    assert "authorization" not in header_names
    assert not any("token" in name for name in header_names)
    items = request.url.params.multi_items()
    assert sorted(name for name, _ in items) == sorted(expected_params)
    assert dict(items) == expected_params


@MODES
@pytest.mark.parametrize("more_info", [None, {"note": "额外信息"}])
@pytest.mark.parametrize("rows", [RICH_ROWS, []], ids=["rows", "empty"])
def test_list_success(mode, more_info, rows):
    payload = {
        "meta": {"code": 200, "message": "查询成功", "moreInfo": more_info},
        "voiceInfos": rows,
        "data": {
            "voiceInfos": [{"voiceId": 999, "decoy": True}],
            "keep": "data-field",
        },
        "extraTop": {"keep": "top-field"},
    }
    harness = _Harness(mode, lambda request: httpx.Response(200, json=payload))

    response = harness.call("list", DEVICE_SERIAL)

    assert len(harness.auth_requests) == 1
    assert len(harness.biz_requests) == 1
    _assert_query_only(
        harness.biz_requests[0],
        "GET",
        {"accessToken": "SYNTH_TOKEN", "deviceSerial": DEVICE_SERIAL},
    )
    assert isinstance(response, ApiResponse)
    assert response.code == 200
    assert response.message == "查询成功"
    assert response.operation == LIST_OPERATION
    # Top-level voiceInfos projects to .data verbatim; data.voiceInfos ignored.
    assert response.data == rows
    assert response.raw == payload
    assert response.raw["voiceInfos"] == rows
    assert response.raw["data"]["voiceInfos"] == [{"voiceId": 999, "decoy": True}]
    assert response.raw["meta"]["moreInfo"] == more_info
    if rows is RICH_ROWS:
        # Pass-through, not normalization: the documented integer `time`,
        # an unknown future `status`, and every unknown column keep their
        # exact wire values, and no converted twin keys appear.
        first, second = response.data[0], response.data[1]
        assert first["time"] == 1714980489
        assert isinstance(first["time"], int)
        assert first["status"] == 1
        assert first["updateTime"] == "2024-05-06 07:08:09"
        assert first["vendorExtra"] == {"keep": [1, "x"]}
        assert second["status"] == "SYNCING_2077"
        assert second["time"] == 1700000000
        assert second["surplus"] == "passthrough"
        assert set(first) == {
            "voiceId",
            "voiceName",
            "voiceUrl",
            "status",
            "time",
            "updateTime",
            "vendorExtra",
        }
        assert set(second) == {
            "voiceId",
            "voiceName",
            "voiceUrl",
            "status",
            "time",
            "surplus",
        }
    _assert_call_scope(harness, LIST_OPERATION, ["started", "succeeded"])
    _assert_attempts(harness, LIST_OPERATION, ["started", "succeeded"])


@MODES
@pytest.mark.parametrize("voice_id", [VOICE_ID, 0, -12345678901234567890])
@pytest.mark.parametrize("voice_name", [VOICE_NAME, PADDED_VOICE_NAME])
def test_rename_success(mode, voice_id, voice_name):
    harness = _Harness(mode, lambda request: httpx.Response(200, json=ACK_PAYLOAD))

    response = harness.call(
        "rename",
        DEVICE_SERIAL,
        voice_id,
        voice_name=voice_name,
        voice_url=VOICE_URL,
    )

    # One write only: no model probe, no media/URL fetch, no pre/post-read.
    assert len(harness.auth_requests) == 1
    assert len(harness.biz_requests) == 1
    _assert_query_only(
        harness.biz_requests[0],
        "PUT",
        {
            "accessToken": "SYNTH_TOKEN",
            "deviceSerial": DEVICE_SERIAL,
            "voiceId": str(voice_id),
            "voiceName": voice_name,
            "voiceUrl": VOICE_URL,
        },
    )
    assert response.code == 200
    assert response.message == "ok"
    assert response.operation == RENAME_OPERATION
    assert response.data is None
    assert "data" not in response.raw
    assert response.raw == ACK_PAYLOAD
    _assert_call_scope(harness, RENAME_OPERATION, ["started", "succeeded"])
    _assert_attempts(harness, RENAME_OPERATION, ["started", "succeeded"])


@MODES
@pytest.mark.parametrize(
    "voice_infos",
    [
        pytest.param(_MISSING, id="missing"),
        pytest.param(None, id="none"),
        pytest.param("oops", id="scalar"),
        pytest.param({"a": 1}, id="dict"),
        pytest.param([{"ok": 1}, 7], id="non-dict-item"),
    ],
)
def test_list_voice_infos_shape(mode, voice_infos):
    payload = {"meta": {"code": 200, "message": "ok", "moreInfo": None}}
    if voice_infos is not _MISSING:
        payload["voiceInfos"] = voice_infos
    harness = _Harness(mode, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(ProtocolError) as exc_info:
        harness.call("list", DEVICE_SERIAL)

    # The transport succeeded; the failure is in payload parsing.
    assert exc_info.value.operation == LIST_OPERATION
    assert exc_info.value.http_status == 200
    assert len(harness.auth_requests) == 1
    assert len(harness.biz_requests) == 1
    _assert_call_scope(harness, LIST_OPERATION, ["started", "failed"])
    _assert_attempts(harness, LIST_OPERATION, ["started", "succeeded"])


@MODES
@pytest.mark.parametrize(
    "meta,error",
    [
        pytest.param({"message": "ok", "moreInfo": None}, ProtocolError, id="missing"),
        pytest.param(
            {"code": None, "message": "ok", "moreInfo": None},
            ProtocolError,
            id="none",
        ),
        pytest.param(
            {"code": 200.0, "message": "ok", "moreInfo": None},
            ProtocolError,
            id="float",
        ),
        pytest.param(
            {"code": True, "message": "ok", "moreInfo": None},
            ProtocolError,
            id="bool",
        ),
        pytest.param(
            {"code": "200", "message": "ok", "moreInfo": None},
            ApiError,
            id="str-200",
        ),
        pytest.param(
            {"code": 500, "message": "server err", "moreInfo": None},
            ApiError,
            id="int-500",
        ),
    ],
)
def test_list_meta_code_contract(mode, meta, error):
    payload = {"meta": meta, "voiceInfos": []}
    harness = _Harness(mode, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(error) as exc_info:
        harness.call("list", DEVICE_SERIAL)

    if error is ApiError:
        assert not isinstance(exc_info.value, ProtocolError)
    assert len(harness.auth_requests) == 1
    assert len(harness.biz_requests) == 1
    _assert_call_scope(harness, LIST_OPERATION, ["started", "failed"])
    _assert_attempts(harness, LIST_OPERATION, ["started", "failed"])


@MODES
def test_list_refreshes_token_and_retries(mode):
    expired = {"meta": {"code": 10002, "message": "accessToken expired", "moreInfo": None}}
    ok = {
        "meta": {"code": 200, "message": "ok", "moreInfo": None},
        "voiceInfos": RICH_ROWS,
    }
    responses = iter([expired, ok])
    harness = _Harness(mode, lambda request: httpx.Response(200, json=next(responses)))

    response = harness.call("list", DEVICE_SERIAL)

    assert response.code == 200
    assert response.data == RICH_ROWS
    # Reads follow the invalid-token refresh rule: refresh + one retry.
    assert len(harness.auth_requests) == 2
    assert len(harness.biz_requests) == 2
    for request in harness.biz_requests:
        _assert_query_only(
            request,
            "GET",
            {"accessToken": "SYNTH_TOKEN", "deviceSerial": DEVICE_SERIAL},
        )
    _assert_call_scope(harness, LIST_OPERATION, ["started", "succeeded"])
    _assert_attempts(
        harness,
        LIST_OPERATION,
        ["started", "failed", "started", "succeeded"],
    )


@MODES
def test_rename_token_expired_no_retry(mode):
    expired = {"meta": {"code": 10002, "message": "accessToken expired", "moreInfo": None}}
    harness = _Harness(mode, lambda request: httpx.Response(200, json=expired))

    with pytest.raises(AuthenticationError) as exc_info:
        harness.call(
            "rename",
            DEVICE_SERIAL,
            VOICE_ID,
            voice_name=VOICE_NAME,
            voice_url=VOICE_URL,
        )

    assert exc_info.value.code == 10002
    assert exc_info.value.operation == RENAME_OPERATION
    # Writes never refresh or retry.
    assert len(harness.auth_requests) == 1
    assert len(harness.biz_requests) == 1
    _assert_call_scope(harness, RENAME_OPERATION, ["started", "failed"])
    _assert_attempts(harness, RENAME_OPERATION, ["started", "failed"])


@MODES
def test_session_reuses_cached_token(mode):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "meta": {"code": 200, "message": "ok", "moreInfo": None},
                    "voiceInfos": [],
                },
            )
        return httpx.Response(200, json=ACK_PAYLOAD)

    harness = _Harness(mode, handler)
    listed, renamed = harness.run(
        [
            ("list", (DEVICE_SERIAL,), {}),
            ("rename", (DEVICE_SERIAL, VOICE_ID), dict(VALID_RENAME_KW)),
        ]
    )

    assert listed.operation == LIST_OPERATION
    assert renamed.operation == RENAME_OPERATION
    # One managed token serves both operations.
    assert len(harness.auth_requests) == 1
    assert [r.method for r in harness.biz_requests] == ["GET", "PUT"]
    assert {e.get("operation") for e in _attempt_events(harness)} == {
        LIST_OPERATION,
        RENAME_OPERATION,
    }
    _assert_call_scope(harness, LIST_OPERATION, ["started", "succeeded"])
    _assert_call_scope(harness, RENAME_OPERATION, ["started", "succeeded"])


VALID_RENAME_KW = {"voice_name": VOICE_NAME, "voice_url": VOICE_URL}

INVALID_CALLS = [
    pytest.param("list", ("",), {}, id="list/serial-empty"),
    pytest.param("list", (" \t\n",), {}, id="list/serial-blank"),
    pytest.param("list", (123,), {}, id="list/serial-nonstring"),
    pytest.param("list", (None,), {}, id="list/serial-none"),
    pytest.param("list", (b"bytes",), {}, id="list/serial-bytes"),
    pytest.param("list", ("SN\ud800X",), {}, id="list/serial-surrogate"),
    pytest.param("rename", ("", VOICE_ID), dict(VALID_RENAME_KW), id="rename/serial-empty"),
    pytest.param("rename", (" \t", VOICE_ID), dict(VALID_RENAME_KW), id="rename/serial-blank"),
    pytest.param(
        "rename",
        ("SN\udc00", VOICE_ID),
        dict(VALID_RENAME_KW),
        id="rename/serial-surrogate",
    ),
    pytest.param("rename", (DEVICE_SERIAL, True), dict(VALID_RENAME_KW), id="rename/id-bool"),
    pytest.param("rename", (DEVICE_SERIAL, "987"), dict(VALID_RENAME_KW), id="rename/id-str"),
    pytest.param("rename", (DEVICE_SERIAL, 9.5), dict(VALID_RENAME_KW), id="rename/id-float"),
    pytest.param("rename", (DEVICE_SERIAL, None), dict(VALID_RENAME_KW), id="rename/id-none"),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": "", "voice_url": VOICE_URL},
        id="rename/name-empty",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": "   ", "voice_url": VOICE_URL},
        id="rename/name-blank",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": 11, "voice_url": VOICE_URL},
        id="rename/name-nonstring",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": "N\ud801", "voice_url": VOICE_URL},
        id="rename/name-surrogate",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": VOICE_NAME, "voice_url": ""},
        id="rename/url-empty",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": VOICE_NAME, "voice_url": "  "},
        id="rename/url-blank",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": VOICE_NAME, "voice_url": 7.25},
        id="rename/url-nonstring",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": VOICE_NAME, "voice_url": "u\udc01"},
        id="rename/url-surrogate",
    ),
]


SIGNATURE_MISTAKES = [
    # Unknown pagination kwarg: the catalog has no paging parameters.
    pytest.param(
        "list",
        (DEVICE_SERIAL,),
        {"page": 1},
        id="list/unexpected-page",
    ),
    pytest.param(
        "list",
        (DEVICE_SERIAL,),
        {"page_size": 50},
        id="list/unexpected-page-size",
    ),
    # Missing required keywords: TypeError raised by the signature itself.
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_name": VOICE_NAME},
        id="rename/missing-voice-url",
    ),
    pytest.param(
        "rename",
        (DEVICE_SERIAL, VOICE_ID),
        {"voice_url": VOICE_URL},
        id="rename/missing-voice-name",
    ),
]


@MODES
@pytest.mark.parametrize("name,args,kwargs", INVALID_CALLS)
def test_arguments_validated_before_io(mode, name, args, kwargs):
    harness = _Harness(mode, lambda request: httpx.Response(200, json=ACK_PAYLOAD))

    with pytest.raises(ValueError):
        harness.call(name, *args, **kwargs)

    # All arguments validated before any IO.
    assert harness.auth_requests == []
    assert harness.biz_requests == []
    entrypoint = LIST_OPERATION if name == "list" else RENAME_OPERATION
    _assert_call_scope(harness, entrypoint, ["started", "failed"])
    assert _attempt_events(harness) == []


@MODES
@pytest.mark.parametrize("name,args,kwargs", SIGNATURE_MISTAKES)
def test_signature_mistakes_raise_typeerror(mode, name, args, kwargs):
    harness = _Harness(mode, lambda request: httpx.Response(200, json=ACK_PAYLOAD))

    with pytest.raises(TypeError):
        harness.call(name, *args, **kwargs)

    assert harness.auth_requests == []
    assert harness.biz_requests == []


@MODES
@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        pytest.param("list", (DEVICE_SERIAL,), {}, id="list"),
        pytest.param(
            "rename",
            (DEVICE_SERIAL, VOICE_ID),
            dict(VALID_RENAME_KW),
            id="rename",
        ),
    ],
)
def test_overseas_region_rejected_before_io(mode, name, args, kwargs):
    # Independently proves region rejection: fully valid call data that a
    # domestic run accepts, refused on the overseas region with no IO.
    harness = _Harness(
        mode,
        lambda request: httpx.Response(200, json=ACK_PAYLOAD),
        region=RegionConfig.overseas(),
    )

    with pytest.raises(ValueError):
        harness.call(name, *args, **kwargs)

    assert harness.auth_requests == []
    assert harness.biz_requests == []
    entrypoint = LIST_OPERATION if name == "list" else RENAME_OPERATION
    _assert_call_scope(harness, entrypoint, ["started", "failed"])
    assert _attempt_events(harness) == []


def test_huge_voice_id_preflight_vs_interpreter_cap():
    # Interpreter cap enabled: str(10**limit) raises ValueError inside the
    # interpreter itself, before the SDK can emit auth/business traffic.
    limit = sys.get_int_max_str_digits()
    if limit:
        harness = _Harness("sync", lambda request: httpx.Response(200, json=ACK_PAYLOAD))

        with pytest.raises(ValueError):
            harness.call(
                "rename",
                DEVICE_SERIAL,
                10**limit,
                **VALID_RENAME_KW,
            )

        assert harness.auth_requests == []
        assert harness.biz_requests == []
        _assert_call_scope(harness, RENAME_OPERATION, ["started", "failed"])
        assert _attempt_events(harness) == []
    else:
        # Cap disabled: the SDK imposes no digit cap of its own; a
        # 4301-digit voiceId is transported verbatim.
        huge = 10**4300
        harness = _Harness("sync", lambda request: httpx.Response(200, json=ACK_PAYLOAD))

        response = harness.call(
            "rename",
            DEVICE_SERIAL,
            huge,
            **VALID_RENAME_KW,
        )

        assert response.code == 200
        assert len(harness.auth_requests) == 1
        assert len(harness.biz_requests) == 1
        _assert_query_only(
            harness.biz_requests[0],
            "PUT",
            {
                "accessToken": "SYNTH_TOKEN",
                "deviceSerial": DEVICE_SERIAL,
                "voiceId": str(huge),
                "voiceName": VOICE_NAME,
                "voiceUrl": VOICE_URL,
            },
        )
        assert harness.biz_requests[0].url.params["voiceId"] == str(huge)
        _assert_call_scope(harness, RENAME_OPERATION, ["started", "succeeded"])
        _assert_attempts(harness, RENAME_OPERATION, ["started", "succeeded"])
