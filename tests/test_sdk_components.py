"""Contract tests for the component-path catalogue (docs 774-3873, domestic only).

Everything runs through real ``Client``/``AsyncClient`` instances wired to
``httpx.MockTransport``; no live endpoint is contacted. The facade is
mounted on ``client.components`` by the separate integration task, so the
classes are instantiated directly here. All device serials are obvious
EVAL placeholders, not real or vendor example IDs.

These tests assume the core path-parameter/``JSON_NULL`` contract of the
parallel task: a ``{deviceSerial}`` placeholder renders as one
UTF-8 percent-encoded path segment and ``JSON_NULL`` produces the literal
body ``b"null"``. Expected paths are built from the doc's fixed literal
segments, never read back from the request object.
"""

import asyncio
import json
from types import MappingProxyType
from urllib.parse import quote

import httpx
import pytest

from ezviz_unified import AsyncClient, Client, RegionConfig
from ezviz_unified.components import AsyncComponents, Components
from ezviz_unified.errors import ApiError, AuthenticationError, DeviceError


# Expected paths: the source contract template with {deviceSerial}
# replaced by a placeholder built independently from the SDK.
def _expected(template, serial):
    return template.replace("{deviceSerial}", quote(serial, safe=""))


FACE_VIDEO = "/api/v3/otap/prop/{deviceSerial}/Video/1/DoorLockCtrl/FaceRecognitionVoiceTipSwitch"
FACE_LOCK = "/api/v3/otap/prop/{deviceSerial}/DoorLock/0/DoorLockCtrl/FaceRecognitionVoiceTipSwitch"
TEMPERATURE = "/api/v3/otap/prop/{deviceSerial}/global/0/Humiture/Temperature"
SMOKE = "/api/v3/otap/prop/{deviceSerial}/SmokeSensor/0/PowerMgr/CountdownInfo"
GAS = "/api/v3/otap/action/{deviceSerial}/GasSensor/0/PowerMgr/GetPowerSwitchCountdown"
CHIME = "/api/v3/otap/prop/{deviceSerial}/Default/0/PowerMgr/SurplusPower"
HDD = "/api/v3/otap/action/{deviceSerial}/Default/0/HDD/FormatSpecifyHDD"
HUMAN = '/api/v3/otap/prop/{{deviceSerial}}/global/["0"]/HumanDetection/InDetectEnabled'

FACE_VIDEO_GET = "cn.components.face_voice_video.get"
FACE_VIDEO_SET = "cn.components.face_voice_video.set"
FACE_LOCK_GET = "cn.components.face_voice_lock.get"
TEMPERATURE_GET = "cn.components.temperature.get"
SMOKE_GET = "cn.components.smoke_countdown.get"
GAS_QUERY = "cn.components.gas_countdown.query"
CHIME_SET = "cn.components.chime_power.set"
HDD_INVOKE = "cn.components.hdd_format.invoke"
HUMAN_SET = "cn.components.human_detection.set"


def _envelope(code=200, message="ok", data=None, more_info=None):
    meta = {"code": code, "message": message}
    if more_info is not None:
        meta["moreInfo"] = more_info
    body = {"meta": meta}
    if data is not None:
        body["data"] = data
    return httpx.Response(200, json=body)


def _unreachable(request):
    raise AssertionError("business endpoint must never be reached")


def _build(kind, region, business):
    """Real client over MockTransport plus a directly-built facade."""
    biz_calls = []
    auth_calls = []

    def on_auth(request):
        auth_calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": f"TOKEN-{len(auth_calls)}",
                    "expireTime": 2_000_000,
                },
            },
        )

    def on_business(request):
        biz_calls.append(request)
        return business(request)

    cls = Client if kind == "sync" else AsyncClient
    client = cls(
        "K",
        "S",
        region=region,
        clock=lambda: 1000,
        auth_transport=httpx.MockTransport(on_auth),
        transport_factory=lambda origin: httpx.MockTransport(on_business),
    )
    facade = (Components if kind == "sync" else AsyncComponents)(client)
    return client, facade, biz_calls, auth_calls


async def _invoke(kind, facade, operation_id, serial, **kwargs):
    result = facade.call(operation_id, serial, **kwargs)
    return await result if kind == "async" else result


async def _shutdown(kind, client):
    if kind == "sync":
        client.close()
    else:
        await client.aclose()


def _scenario(kind, region, business, body):
    """Run ``body(facade)`` on a fresh client; returns (result, calls, auths)."""

    async def main():
        client, facade, calls, auths = _build(kind, region, business)
        try:
            result = await body(facade)
        finally:
            await _shutdown(kind, client)
        return result, calls, auths

    return asyncio.run(main())


# ---------------------------------------------------------------------------
# catalogue metadata — pure local, no I/O
# ---------------------------------------------------------------------------


def test_describe_and_list_documented_are_local():
    facade = Components(None)  # type: ignore[arg-type]  # metadata needs no client
    entries = facade.list_documented()
    assert len(entries) == 9
    ids = {e.operation_id for e in entries}
    assert ids == {
        FACE_VIDEO_GET,
        FACE_VIDEO_SET,
        FACE_LOCK_GET,
        TEMPERATURE_GET,
        SMOKE_GET,
        GAS_QUERY,
        CHIME_SET,
        HDD_INVOKE,
        HUMAN_SET,
    }
    for entry in entries:
        assert entry.operation_id.startswith("cn.components.")
        assert entry.source_doc_id.isdigit()
        # Authoritative source URL per the collection manifest.
        assert entry.source_url == f"https://open.ys7.com/help/{entry.source_doc_id}"
        assert entry.raw_path.startswith("/api/v3/otap/")
        assert entry.method in {"GET", "PUT"}
        assert isinstance(entry.callable, bool)
        assert isinstance(entry.read_only, bool)
        assert isinstance(entry.limitations, tuple)
        assert isinstance(entry.documented_models, tuple)

    assert facade.describe(FACE_VIDEO_GET) is entries[0]
    assert facade.describe(HDD_INVOKE).callable is False
    assert facade.describe(HUMAN_SET).callable is False
    # Whitelist: declared for the three lock/voice docs only; never False.
    assert facade.describe(FACE_VIDEO_GET).whitelist_required is True
    assert facade.describe(FACE_VIDEO_SET).whitelist_required is True
    assert facade.describe(FACE_LOCK_GET).whitelist_required is True
    assert facade.describe(TEMPERATURE_GET).whitelist_required is None
    assert facade.describe(CHIME_SET).whitelist_required is None
    assert facade.describe(HDD_INVOKE).whitelist_required is None
    assert facade.describe(HUMAN_SET).whitelist_required is None
    # The 3873 raw template is preserved verbatim.
    assert facade.describe(HUMAN_SET).raw_path == HUMAN
    assert facade.describe(HUMAN_SET).documented_models == ()
    with pytest.raises(ValueError):
        facade.describe("cn.components.unknown.op")
    with pytest.raises(ValueError):
        facade.describe(123)  # type: ignore[arg-type]


def test_describe_returns_same_doc_record_for_async():
    facade = AsyncComponents(None)  # type: ignore[arg-type]  # metadata needs no client
    info = facade.describe(GAS_QUERY)
    assert info.method == "PUT" and info.callable is True
    # Doc 998's opening line declares a countdown query; PUT is the
    # documented transport, not a write action.
    assert info.read_only is True


# ---------------------------------------------------------------------------
# happy paths — one request per call, correct path/method/body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_no_body_gets_and_device_meta_layer(kind):
    serial = "EVALDEV001"

    def business(request):
        return _envelope(data={"enabled": True})

    async def body(facade):
        a = await _invoke(kind, facade, FACE_VIDEO_GET, serial)
        b = await _invoke(kind, facade, FACE_LOCK_GET, serial)
        c = await _invoke(kind, facade, TEMPERATURE_GET, serial)
        d = await _invoke(kind, facade, SMOKE_GET, serial)
        return a, b, c, d

    (a, b, c, d), calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 4 and len(auths) == 1
    paths = [r.url.raw_path.decode("ascii") for r in calls]
    assert paths == [
        _expected(FACE_VIDEO, serial),
        _expected(FACE_LOCK, serial),
        _expected(TEMPERATURE, serial),
        _expected(SMOKE, serial),
    ]
    for request in calls:
        assert request.method == "GET"
        assert request.headers["accessToken"] == "TOKEN-1"
        assert not request.content
        # Docs 774/812/1134/1447 declare Content-Type even for GETs
        # with no body.
        assert request.headers["Content-Type"] == "application/json"
    assert a.data == {"enabled": True}
    assert b.data == {"enabled": True}
    assert c.data == {"enabled": True}
    assert d.data == {"enabled": True}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_face_voice_set_body_and_headers(kind):
    serial = "EVALDEV001"

    def business(request):
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, FACE_VIDEO_SET, serial, payload={"enabled": True})

    result, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200
    assert len(calls) == 1 and len(auths) == 1
    request = calls[0]
    assert request.method == "PUT"
    assert request.url.raw_path.decode("ascii") == _expected(FACE_VIDEO, serial)
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {"enabled": True}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_face_voice_set_allows_omitted_enabled(kind):
    """Doc 775 marks ``enabled`` optional; an empty object is a legal body."""

    def business(request):
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, FACE_VIDEO_SET, "EVALDEV001", payload={})

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert json.loads(calls[0].content) == {}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_gas_countdown_sends_explicit_null(kind):
    serial = "EVALDEV001"

    def business(request):
        return _envelope(data={"enable": True, "timeRemaining": 60, "switch": False})

    async def body(facade):
        return await _invoke(kind, facade, GAS_QUERY, serial, payload=None)

    result, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200
    assert len(calls) == 1 and len(auths) == 1
    request = calls[0]
    assert request.method == "PUT"
    assert request.url.raw_path.decode("ascii") == _expected(GAS, serial)
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == b"null"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_chime_power_sends_bare_integer(kind):
    serial = "EVALDEV001"

    def business(request):
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, CHIME_SET, serial, payload=0)

    result, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200
    request = calls[0]
    assert request.method == "PUT"
    assert request.url.raw_path.decode("ascii") == _expected(CHIME, serial)
    assert request.content == b"0"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_chime_power_boundary_values(kind):
    values = iter([0, 100])

    def business(request):
        assert json.loads(request.content) == next(values)
        return _envelope()

    async def body(facade):
        await _invoke(kind, facade, CHIME_SET, "EVALDEV001", payload=0)
        await _invoke(kind, facade, CHIME_SET, "EVALDEV001", payload=100)

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# pre-auth rejection — zero auth traffic, zero business traffic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_preauth_rejection_covers_all_payload_rules(kind):
    circular = {}
    circular["self"] = circular
    cases = [
        # no-body operations reject every payload value
        (FACE_VIDEO_GET, {"payload": None}),
        (FACE_VIDEO_GET, {"payload": {}}),
        (FACE_LOCK_GET, {"payload": 0}),
        (TEMPERATURE_GET, {"payload": "x"}),
        (SMOKE_GET, {"payload": []}),
        # explicit-null query: missing payload is rejected, non-None too
        (GAS_QUERY, {}),
        (GAS_QUERY, {"payload": {}}),
        (GAS_QUERY, {"payload": 0}),
        (GAS_QUERY, {"payload": False}),
        (GAS_QUERY, {"payload": "null"}),
        # face-voice mapping: field and type checked
        (FACE_VIDEO_SET, {}),
        (FACE_VIDEO_SET, {"payload": None}),
        (FACE_VIDEO_SET, {"payload": []}),
        (FACE_VIDEO_SET, {"payload": {"enabled": 1}}),
        (FACE_VIDEO_SET, {"payload": {"enabled": "true"}}),
        (FACE_VIDEO_SET, {"payload": {"other": True}}),
        (FACE_VIDEO_SET, {"payload": {"enabled": True, "extra": 1}}),
        (FACE_VIDEO_SET, {"payload": {"enabled": True, "self": circular}}),
        (FACE_VIDEO_SET, {"payload": {"enabled": float("nan")}}),
        (FACE_VIDEO_SET, {"payload": {1: True}}),  # non-string key rejected
        # chime power: strict int in [0, 100], bool rejected
        (CHIME_SET, {}),
        (CHIME_SET, {"payload": None}),
        (CHIME_SET, {"payload": True}),
        (CHIME_SET, {"payload": False}),
        (CHIME_SET, {"payload": -1}),
        (CHIME_SET, {"payload": 101}),
        (CHIME_SET, {"payload": 50.0}),
        (CHIME_SET, {"payload": "50"}),
    ]

    async def body(facade):
        for operation_id, kwargs in cases:
            with pytest.raises(ValueError):
                await _invoke(kind, facade, operation_id, "EVALDEV001", **kwargs)

    _, calls, auths = _scenario(kind, RegionConfig.china(), _unreachable, body)
    assert not calls and not auths


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_non_callable_and_unknown_ids_fail_before_auth(kind):
    marker = "PRIVATE-SERIAL-MARKER"
    cases = [
        (HDD_INVOKE, "EVALDEV001", {}),
        (HUMAN_SET, "EVALDEV001", {"payload": True}),
        ("cn.components.no.such.op", "EVALDEV001", {}),
        ("", "EVALDEV001", {}),
        (None, "EVALDEV001", {}),
        (123, "EVALDEV001", {}),
        (b"cn.components.temperature.get", "EVALDEV001", {}),
    ]

    async def body(facade):
        for operation_id, serial, kwargs in cases:
            with pytest.raises(ValueError) as excinfo:
                await _invoke(kind, facade, operation_id, serial, **kwargs)
            assert marker not in str(excinfo.value)
            assert marker not in repr(excinfo.value)

    _, calls, auths = _scenario(kind, RegionConfig.china(), _unreachable, body)
    assert not calls and not auths


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "operation_id,kwargs",
    [
        (FACE_VIDEO_GET, {}),
        (FACE_VIDEO_SET, {"payload": {"enabled": True}}),
        (GAS_QUERY, {"payload": None}),
        (CHIME_SET, {"payload": 50}),
    ],
)
def test_overseas_region_gate_precedes_auth(kind, operation_id, kwargs):
    """Component paths are domestic-only: a valid call still fails pre-auth."""

    async def body(facade):
        with pytest.raises(ValueError):
            await _invoke(kind, facade, operation_id, "EVALDEV001", **kwargs)

    _, calls, auths = _scenario(kind, RegionConfig.overseas(), _unreachable, body)
    assert not calls and not auths


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_path_params_reject_bad_serials_before_auth(kind):
    """Bad path-segment values are rejected by the core request layer."""
    bad = ["", " ", "..", ".", "a/b", "a\\b", "a\x00b", "a\x7fb", "a\nb", "a\rb"]

    async def body(facade):
        for serial in bad:
            with pytest.raises(ValueError):
                await _invoke(kind, facade, FACE_VIDEO_GET, serial)

    _, calls, auths = _scenario(kind, RegionConfig.china(), _unreachable, body)
    assert not calls and not auths


# ---------------------------------------------------------------------------
# wire-level behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_serial_is_percent_encoded_once_in_raw_path(kind):
    serial = "EVAL DEV%2F001"  # space and a literal '%' encode once each
    expected = _expected(TEMPERATURE, serial)

    def business(request):
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, TEMPERATURE_GET, serial)

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    request = calls[0]
    # raw_path keeps the percent-encoding; url.path is the decoded view.
    assert request.url.raw_path.decode("ascii") == expected
    assert "%20" in expected  # the space became '%20'
    assert "%25" in expected  # the '%' of the input became '%25' — no double unquote
    # The decoded view still carries the literal characters, not separators.
    assert "EVAL DEV%2F001" in request.url.path


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_read_refreshes_token_once_on_10002(kind):
    responses = iter(
        [
            _envelope(code=10002, message="token expired"),
            _envelope(data={"enabled": False}),
        ]
    )

    def business(request):
        return next(responses)

    async def body(facade):
        return await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")

    result, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200 and result.data == {"enabled": False}
    assert len(calls) == 2 and len(auths) == 2
    assert calls[0].headers["accessToken"] == "TOKEN-1"
    assert calls[1].headers["accessToken"] == "TOKEN-2"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_gas_query_refreshes_once_and_replays_identical_null_body(kind):
    """Doc 998 is a declared countdown query on PUT transport: an expired
    token refreshes once and the identical null body is replayed once."""
    responses = iter(
        [
            _envelope(code=10002, message="token expired"),
            _envelope(data={"enable": True, "timeRemaining": 60, "switch": False}),
        ]
    )

    def business(request):
        return next(responses)

    async def body(facade):
        return await _invoke(kind, facade, GAS_QUERY, "EVALDEV001", payload=None)

    result, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200 and result.data == {
        "enable": True,
        "timeRemaining": 60,
        "switch": False,
    }
    assert len(calls) == 2 and len(auths) == 2
    assert calls[0].headers["accessToken"] == "TOKEN-1"
    assert calls[1].headers["accessToken"] == "TOKEN-2"
    for request in calls:
        assert request.method == "PUT"
        assert request.content == b"null"
        assert request.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "operation_id,kwargs",
    [
        (FACE_VIDEO_SET, {"payload": {"enabled": True}}),
        (CHIME_SET, {"payload": 42}),
    ],
)
def test_writes_are_never_replayed(kind, operation_id, kwargs):
    responses = iter([_envelope(code=10002, message="expired"), _envelope()])

    def business(request):
        return next(responses)

    async def body(facade):
        with pytest.raises(AuthenticationError):
            await _invoke(kind, facade, operation_id, "EVALDEV001", **kwargs)

    _, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 1 and len(auths) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_device_meta_layer_checked_when_present(kind):
    responses = iter(
        [
            _envelope(
                data={},
                more_info={"deviceMeta": {"code": "0x00000000", "errorMsg": "ok"}},
            ),
            _envelope(data={}),  # deviceMeta absent: platform code alone decides
            _envelope(
                data={},
                more_info={"deviceMeta": {"code": "0x00000001", "errorMsg": "busy"}},
            ),
        ]
    )

    def business(request):
        return next(responses)

    async def body(facade):
        ok = await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")
        assert ok.code == 200
        absent = await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")
        assert absent.code == 200
        with pytest.raises(DeviceError):
            await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 3


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_meta_code_and_http_status_stay_separate(kind):
    responses = iter(
        [
            _envelope(code=20018, message="user does not own the device"),
            httpx.Response(500, json={"meta": {"code": 200, "message": "ok"}}),
        ]
    )

    def business(request):
        return next(responses)

    async def body(facade):
        with pytest.raises(ApiError) as first:
            await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")
        assert first.value.code in (20018, "20018")
        assert first.value.http_status == 200
        with pytest.raises(ApiError):
            await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_anonymous_client_rejects_component_call(kind):
    """Anonymous clients cannot run authenticated component operations."""
    biz_calls = []
    client_cls = Client if kind == "sync" else AsyncClient
    client = client_cls(
        region=RegionConfig.china(),
        anonymous=True,
        transport_factory=lambda origin: httpx.MockTransport(
            lambda request: biz_calls.append(request) or _unreachable(request)
        ),
    )
    facade = (Components if kind == "sync" else AsyncComponents)(client)

    async def main():
        try:
            with pytest.raises(ValueError):
                await _invoke(kind, facade, FACE_VIDEO_GET, "EVALDEV001")
        finally:
            await _shutdown(kind, client)

    asyncio.run(main())
    assert not biz_calls


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_serial_with_unicode_and_plus_sign_encodes_verbatim(kind):
    serial = "设备+001"

    def business(request):
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, SMOKE_GET, serial)

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    raw = calls[0].url.raw_path.decode("ascii")
    assert raw == _expected(SMOKE, serial)
    # The '+' in the input is data, not a separator: it encodes to %2B.
    assert "%2B" in raw


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_payload_mapping_is_copied_before_serialization(kind):
    proxy = MappingProxyType({"enabled": False})

    def business(request):
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, FACE_VIDEO_SET, "EVALDEV001", payload=proxy)

    result, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200
    assert json.loads(calls[0].content) == {"enabled": False}
    assert dict(proxy) == {"enabled": False}  # untouched


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_chime_power_body_is_not_rewrapped(kind):
    """Doc 868 body is a bare integer, never an object wrapper."""

    def business(request):
        assert request.content == b"37"
        return _envelope()

    async def body(facade):
        return await _invoke(kind, facade, CHIME_SET, "EVALDEV001", payload=37)

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_path_placeholders_and_resource_segments_stay_fixed(kind):
    """The documented literal segments are never swapped between paths."""

    def business(request):
        return _envelope()

    serials = iter(["EVALDEV001", "EVALDEV002", "EVALDEV003"])

    async def body(facade):
        await _invoke(kind, facade, FACE_VIDEO_GET, next(serials))
        await _invoke(kind, facade, FACE_LOCK_GET, next(serials))
        await _invoke(kind, facade, SMOKE_GET, next(serials))

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert "Video/1/" in calls[0].url.raw_path.decode("ascii")
    assert "DoorLock/0/" in calls[1].url.raw_path.decode("ascii")
    assert "SmokeSensor/0/" in calls[2].url.raw_path.decode("ascii")
    # No request substitutes one documented resource path for another.
    assert "Video/1/" not in calls[1].url.raw_path.decode("ascii")
    assert "DoorLock/0/" not in calls[0].url.raw_path.decode("ascii")
