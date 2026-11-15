"""Contract tests for the OTAP facade (docs 1512/1513/1514/5891, domestic only).

Everything runs through real ``Client``/``AsyncClient`` instances wired to
``httpx.MockTransport``; no live endpoint is contacted. The facade is
mounted on ``client.otap`` by the separate integration task, so the new
classes are instantiated directly here. All device serials are obvious
EVAL placeholders, not real or vendor example IDs.
"""

import asyncio
import json
from types import MappingProxyType

import httpx
import pytest

from ezviz_unified import AsyncClient, Client, RegionConfig
from ezviz_unified.errors import ApiError, AuthenticationError, DeviceError
from ezviz_unified.otap import AsyncOtap, Otap

PROP_PATH = "/api/v3/device/otap/prop"
ACTION_PATH = "/api/v3/device/otap/action"
LATEST_PATH = "/api/service/device/otap/prop/latest"

READ_KWARGS = {
    "resource_category": "global",
    "local_index": "0",
    "domain_identifier": "LightDomain",
    "property_identifier": "TimeZone",
}
ACTION_KWARGS = {
    "resource_category": "global",
    "local_index": "0",
    "domain_identifier": "LightDomain",
    "action_identifier": "Restart",
}


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
    """Real client over MockTransport plus a directly-built OTAP facade."""
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
    facade = (Otap if kind == "sync" else AsyncOtap)(client)
    return client, facade, biz_calls, auth_calls


async def _invoke(kind, facade, name, *args, **kwargs):
    result = getattr(facade, name)(*args, **kwargs)
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


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_request_contracts(kind):
    payload = {"timeZone": "CST-08:00:00", "label": "温度"}
    params = {"speed": 1}

    def business(request):
        return _envelope(data={"timeZone": "CST-08:00:00"})

    async def body(facade):
        get = await _invoke(kind, facade, "get_property", "EVALDEV001", **READ_KWARGS)
        # payload is the second positional-or-keyword parameter: passed
        # positionally for set_property, by keyword for invoke_action.
        await _invoke(kind, facade, "set_property", "EVALDEV001", payload, **READ_KWARGS)
        await _invoke(kind, facade, "invoke_action", "EVALDEV001", payload=params, **ACTION_KWARGS)
        await _invoke(
            kind,
            facade,
            "latest_property",
            "EVALDEV001",
            **{**READ_KWARGS, "local_index": "10"},
        )
        return get

    get, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    # One token and exactly one HTTP request per facade call: the SDK never
    # chains set->latest and never issues implicit follow-up requests.
    assert len(calls) == 4 and len(auths) == 1
    get_req, set_req, act_req, lat_req = calls

    assert get_req.method == "GET" and get_req.url.path == PROP_PATH
    assert not get_req.url.query and not get_req.content
    assert "content-type" not in get_req.headers
    for key, value in {
        "deviceSerial": "EVALDEV001",
        "resourceCategory": "global",
        "localIndex": "0",
        "domainIdentifier": "LightDomain",
        "propIdentifier": "TimeZone",
        "accessToken": "TOKEN-1",
    }.items():
        assert get_req.headers[key] == value
    assert "actionIdentifier" not in get_req.headers
    assert get.code == 200 and get.data == {"timeZone": "CST-08:00:00"}

    assert set_req.method == "PUT" and set_req.url.path == PROP_PATH
    assert set_req.headers["Content-Type"] == "application/json"
    # Bare JSON object on the wire — no {"Body": ...} wrapper (conflict C4).
    assert json.loads(set_req.content) == payload
    assert payload == {"timeZone": "CST-08:00:00", "label": "温度"}  # untouched

    assert act_req.method == "PUT" and act_req.url.path == ACTION_PATH
    assert act_req.headers["actionIdentifier"] == "Restart"
    assert "propIdentifier" not in act_req.headers
    assert json.loads(act_req.content) == params

    assert lat_req.method == "GET" and lat_req.url.path == LATEST_PATH
    assert lat_req.headers["localIndex"] == "10"
    assert lat_req.headers["propIdentifier"] == "TimeZone"
    assert not lat_req.url.query and not lat_req.content


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("region", [RegionConfig.china(), RegionConfig.overseas()])
def test_preauth_rejection(kind, region):
    circular = {}
    circular["self"] = circular
    # Domestic runs exercise invalid input; overseas runs prove the region
    # gate fires before auth even with fully valid arguments.
    invalid = [
        ("get_property", ("  ",), READ_KWARGS),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "property_identifier": " "}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": 0}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "0\n1"}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "0\x001"}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "0\x1f1"}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "0\x7f1"}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "0\t1"}),
        ("get_property", ("EVALDEV000",), {**READ_KWARGS, "domain_identifier": "照明域"}),
        ("latest_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "01"}),
        ("latest_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "-1"}),
        ("latest_property", ("EVALDEV000",), {**READ_KWARGS, "local_index": "12345678901"}),
        ("latest_property", ("EVALDEV000",), {**READ_KWARGS, "resource_category": "r" * 255}),
        ("latest_property", ("EVALDEV000",), {**READ_KWARGS, "property_identifier": "p" * 255}),
        ("set_property", ("EVALDEV000",), {**READ_KWARGS, "payload": [1]}),
        ("set_property", ("EVALDEV000",), {**READ_KWARGS, "payload": {"v": float("nan")}}),
        ("set_property", ("EVALDEV000",), {**READ_KWARGS, "payload": {"v": {3: "x"}}}),
        ("invoke_action", ("EVALDEV000",), {**ACTION_KWARGS, "payload": {"c": circular}}),
        ("invoke_action", ("EVALDEV000",), {**ACTION_KWARGS, "payload": {"v": object()}}),
        ("set_property", ("EVALDEV000",), {**READ_KWARGS, "payload": {"accessToken": "leak"}}),
    ]
    valid = [
        ("get_property", ("EVALDEV000",), READ_KWARGS),
        ("set_property", ("EVALDEV000",), {**READ_KWARGS, "payload": {"ok": True}}),
        ("invoke_action", ("EVALDEV000",), {**ACTION_KWARGS, "payload": {}}),
        ("latest_property", ("EVALDEV000",), READ_KWARGS),
    ]
    cases = invalid if region.scope == "domestic" else valid

    async def body(facade):
        for name, args, kwargs in cases:
            with pytest.raises(ValueError):
                await _invoke(kind, facade, name, *args, **kwargs)

    _, calls, auths = _scenario(kind, region, _unreachable, body)
    assert not calls and not auths  # zero auth traffic, zero business traffic


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_payload_accepts_mapping_proxy_positionally(kind):
    proxy = MappingProxyType({"mode": 1, "nested": {"on": True}})
    bodies = []

    def business(request):
        bodies.append(json.loads(request.content))
        return _envelope()

    async def body(facade):
        # Mapping root copied before serialization; payload positional.
        return await _invoke(kind, facade, "set_property", "EVALDEV000", proxy, **READ_KWARGS)

    result, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200 and len(calls) == 1
    assert bodies == [{"mode": 1, "nested": {"on": True}}]
    assert dict(proxy) == {"mode": 1, "nested": {"on": True}}  # untouched


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "method,kwargs",
    [("set_property", READ_KWARGS), ("invoke_action", ACTION_KWARGS)],
)
def test_payload_deep_nesting_is_fixed_value_error_before_auth(kind, method, kwargs):
    deep = root = {}
    for _ in range(5000):
        nxt = {}
        root["next"] = nxt
        root = nxt

    async def body(facade):
        with pytest.raises(ValueError) as excinfo:
            await _invoke(kind, facade, method, "EVALDEV000", {"node": deep}, **kwargs)
        # RecursionError collapses to the fixed ValueError with a clean chain.
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    _, calls, auths = _scenario(kind, RegionConfig.china(), _unreachable, body)
    assert not calls and not auths


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_payload_surrogate_error_leaks_no_payload_or_cause(kind):
    marker = "PRIVATE-PAYLOAD-MARKER"
    payload = {"note": marker + "\udcff"}

    async def body(facade):
        with pytest.raises(ValueError) as excinfo:
            await _invoke(kind, facade, "invoke_action", "EVALDEV000", payload, **ACTION_KWARGS)
        # UnicodeEncodeError.object embeds the raw payload; the fixed
        # ValueError must not expose it via message, cause, or context.
        assert marker not in str(excinfo.value)
        assert marker not in repr(excinfo.value)
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    _, calls, auths = _scenario(kind, RegionConfig.china(), _unreachable, body)
    assert not calls and not auths


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("char", ["\x1f", "\x7f", "\x80", "\t", "é"])
def test_header_local_limit_rejects_controls_del_and_non_ascii(kind, char):
    # Local transport limit, not a vendor rule: only printable ASCII
    # (0x20-0x7E) is allowed, so DEL and other controls are rejected too.
    async def body(facade):
        with pytest.raises(ValueError):
            await _invoke(
                kind,
                facade,
                "get_property",
                "EVALDEV000",
                **{**READ_KWARGS, "domain_identifier": f"d{char}d"},
            )

    _, calls, auths = _scenario(kind, RegionConfig.china(), _unreachable, body)
    assert not calls and not auths


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_v3_parameters_only_require_non_blank(kind):
    async def body(facade):
        await _invoke(
            kind,
            facade,
            "get_property",
            "EvalMixed0001",
            resource_category="rc",
            local_index="01",
            domain_identifier="d",
            property_identifier="p",
        )
        await _invoke(
            kind,
            facade,
            "invoke_action",
            "evallower0002",
            resource_category="rc",
            local_index="00000000001",
            domain_identifier="d",
            action_identifier="a",
            payload={},
        )
        # Boundary: space (0x20) and tilde (0x7E) are printable ASCII and
        # are allowed by the local transport limit.
        await _invoke(
            kind,
            facade,
            "get_property",
            "EVALDEV002",
            resource_category="r c",
            local_index="~ ~",
            domain_identifier="d",
            property_identifier="p",
        )

    _, calls, _ = _scenario(kind, RegionConfig.china(), lambda req: _envelope(), body)
    # Conflict C8: the strict localIndex format is a doc 5891 rule only —
    # v3 endpoints accept any non-blank string, and serial case is untouched.
    assert [c.headers["deviceSerial"] for c in calls] == [
        "EvalMixed0001",
        "evallower0002",
        "EVALDEV002",
    ]
    assert [c.headers["localIndex"] for c in calls] == ["01", "00000000001", "~ ~"]
    assert calls[0].headers["resourceCategory"] == "rc"
    assert calls[2].headers["resourceCategory"] == "r c"
    assert calls[1].headers["actionIdentifier"] == "a"
    assert json.loads(calls[1].content) == {}  # empty object is a legal body


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_read_refreshes_token_once_on_10002(kind):
    responses = iter(
        [
            _envelope(code=10002, message="token expired"),
            _envelope(data={"timeZone": "CST-08:00:00"}),
        ]
    )

    def business(request):
        return next(responses)

    async def body(facade):
        return await _invoke(kind, facade, "latest_property", "EVALDEV000", **READ_KWARGS)

    result, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200 and result.data == {"timeZone": "CST-08:00:00"}
    assert len(calls) == 2 and len(auths) == 2
    assert calls[0].headers["accessToken"] == "TOKEN-1"
    assert calls[1].headers["accessToken"] == "TOKEN-2"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("set_property", {**READ_KWARGS, "payload": {"mode": 1}}),
        ("invoke_action", {**ACTION_KWARGS, "payload": {"speed": 1}}),
    ],
)
def test_writes_are_never_replayed(kind, method, kwargs):
    responses = iter([_envelope(code=10002, message="expired"), _envelope()])

    def business(request):
        return next(responses)

    async def body(facade):
        with pytest.raises(AuthenticationError):
            await _invoke(kind, facade, method, "EVALDEV000", **kwargs)

    _, calls, auths = _scenario(kind, RegionConfig.china(), business, body)
    # One logical call -> one request: an expired-token answer invalidates
    # the token but the write is still not replayed.
    assert len(calls) == 1 and len(auths) == 1


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
            await _invoke(kind, facade, "get_property", "EVALDEV000", **READ_KWARGS)
        assert first.value.code in (20018, "20018")  # original business code kept
        assert first.value.http_status == 200
        # Conflict C3: HTTP status and meta.code are different layers — an
        # HTTP 500 carrying meta.code=200 still fails at the HTTP layer.
        with pytest.raises(ApiError):
            await _invoke(kind, facade, "get_property", "EVALDEV000", **READ_KWARGS)

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_set_property_msgdetail_does_not_override_success(kind):
    # Conflict C1: doc 1513's own success sample shows meta.code=200 while
    # msgDetail carries error text — the business code alone decides.
    def business(request):
        return _envelope(
            message="Operation succeeded",
            more_info={"msgDetail": "param error : 设备不支持该功能"},
        )

    async def body(facade):
        return await _invoke(kind, facade, "set_property", "EVALDEV000", {"mode": 1}, **READ_KWARGS)

    result, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert result.code == 200 and result.message == "Operation succeeded"
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_invoke_action_device_layer(kind):
    responses = iter(
        [
            _envelope(
                data={},
                more_info={"deviceMeta": {"code": "0x00000000", "errorMsg": ""}},
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
        ok = await _invoke(kind, facade, "invoke_action", "EVALDEV000", {"a": 1}, **ACTION_KWARGS)
        assert ok.code == 200 and ok.data == {}
        absent = await _invoke(
            kind, facade, "invoke_action", "EVALDEV000", {"a": 1}, **ACTION_KWARGS
        )
        assert absent.code == 200
        with pytest.raises(DeviceError):
            await _invoke(kind, facade, "invoke_action", "EVALDEV000", {"a": 1}, **ACTION_KWARGS)

    _, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    assert len(calls) == 3


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_latest_property_passes_data_through_verbatim(kind):
    shadow = {
        "desired": {
            "value": {"level": [1, None, True]},
            "time": "2026-11-15T10:00:00.000+08:00",
        },
        "reported": {"value": None, "time": None},
        "futureField": {"unknown": "kept"},
    }

    def business(request):
        return httpx.Response(
            200,
            json={
                "meta": {"code": 200, "message": "ok", "moreInfo": {"unseen": 1}},
                "data": shadow,
            },
        )

    async def body(facade):
        return await _invoke(kind, facade, "latest_property", "EVALDEV000", **READ_KWARGS)

    result, calls, _ = _scenario(kind, RegionConfig.china(), business, body)
    # Null value/time on either side and unknown fields pass through; two
    # null sides are a valid answer, not an error.
    assert result.data == shadow
    assert len(calls) == 1
