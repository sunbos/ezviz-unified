import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

pytestmark = pytest.mark.parametrize("mode", ["sync", "async"])

AUTH_HOST = "synthetic-auth.ezvizlife.com"
AUTH_BASE = "https://" + AUTH_HOST
API_HOST = "synthetic-api.ezvizlife.com"
TOKEN = "SYNTH_TOKEN"
SERIAL = "sYnTh-序"
DATA = {
    "timeZone": "SYNTH/Zone",
    "wifiSsid": "SYNTH_SSID",
    "deviceSerial": SERIAL,
    "futureField": {"marker": "SYNTH_PRIVATE"},
}
MARKERS = ("SYNTH/Zone", "SYNTH_SSID", "SYNTH_PRIVATE", SERIAL, TOKEN)
OK_BODY = {"code": "200", "msg": "ok", "data": DATA}


def _auth_ok(request):
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": TOKEN,
                "expireTime": 9999999999999,
                "areaDomain": "https://" + API_HOST,
            },
        },
    )


def _client(mode, biz=None, region=None):
    seen, rec = {"auth": [], "biz": []}, DiagnosticRecorder()

    def auth(request):
        seen["auth"].append(request)
        return _auth_ok(request)

    def route(request):
        seen["biz"].append(request)
        handler = biz or (lambda r: httpx.Response(200, json=dict(OK_BODY, data=dict(DATA))))
        return handler(request)

    cls = AsyncClient if mode == "async" else Client
    client = cls(
        "mock-key",
        "mock-secret",
        region=region or RegionConfig.overseas(auth_base_url=AUTH_BASE),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(route),
        diagnostic_recorder=rec,
    )
    return client, rec, seen


def _invoke(devices, op):
    """Run one (method, args[, kwargs]) op against a devices namespace."""
    return getattr(devices, op[0])(*op[1], **(op[2] if len(op) > 2 else {}))


def _call(client, *ops):
    """Context-manage the client (sync or async) and run ops inside it."""
    if isinstance(client, AsyncClient):

        async def run():
            async with client:
                return [await _invoke(client.devices, op) for op in ops]

        return asyncio.run(run())
    with client:
        return [_invoke(client.devices, op) for op in ops]


def _form(request):
    assert request.method == "POST"
    assert request.url.host == API_HOST
    assert request.url.path == "/api/lapp/device/info"
    assert not request.url.query
    assert TOKEN not in str(request.url)
    assert "authorization" not in request.headers
    assert all(TOKEN not in v for v in request.headers.values())
    assert "application/x-www-form-urlencoded" in request.headers.get("content-type", "")
    return parse_qs(request.read().decode("utf-8"), keep_blank_values=True)


def _utc_events(rec):
    return [
        e
        for e in rec.report()["events"]
        if e.get("kind") == "call" and e.get("entrypoint") == "devices.get_utc"
    ]


def test_get_utc_posts_version_and_get_stays_plain(mode):
    client, rec, seen = _client(mode)
    resp, plain = _call(client, ("get_utc", (SERIAL,)), ("get", (SERIAL,)))

    assert isinstance(resp, ApiResponse) and isinstance(plain, ApiResponse)
    assert resp.code == "200" and resp.message == "ok"
    assert resp.data == DATA and resp.raw == OK_BODY
    assert len(seen["auth"]) == 1 and len(seen["biz"]) == 2  # cached token, one call each
    auth_req = seen["auth"][0]
    assert auth_req.method == "POST" and auth_req.url.host == AUTH_HOST

    utc_req, get_req = seen["biz"]
    assert _form(utc_req) == {"accessToken": [TOKEN], "deviceSerial": [SERIAL], "version": ["utc"]}
    assert _form(get_req) == {"accessToken": [TOKEN], "deviceSerial": [SERIAL]}

    assert [e["outcome"] for e in _utc_events(rec)] == ["started", "succeeded"]
    ops = {e.get("operation") for e in rec.report()["events"]}
    assert "devices.get_utc" in ops
    dump = json.dumps(rec.report(), ensure_ascii=False)
    for marker in MARKERS:
        assert marker not in repr(resp)
        assert marker not in dump


@pytest.mark.parametrize("data", [DATA, {"timeZone": "", "wifiSsid": ""}, {"timeZone": 7}, {}])
def test_get_utc_preserves_data_verbatim(mode, data):
    body = {"code": "200", "msg": "ok", "data": data}
    client, _, _ = _client(mode, biz=lambda r: httpx.Response(200, json=body))
    (resp,) = _call(
        client,
        (
            "get_utc",
            (SERIAL,),
        ),
    )
    assert resp.data == data  # no defaults, no timezone parsing
    assert resp.raw == body


@pytest.mark.parametrize("bad", ["oops", ["x"], 7, None])
def test_get_utc_malformed_data_raises_protocol_error(mode, bad):
    client, _, seen = _client(
        mode, biz=lambda r: httpx.Response(200, json={"code": "200", "data": bad})
    )
    with pytest.raises(ProtocolError):
        _call(
            client,
            (
                "get_utc",
                (SERIAL,),
            ),
        )
    assert len(seen["biz"]) == 1


def test_get_utc_business_error_is_sanitized_api_error(mode):
    body = {
        "code": "20018",
        "msg": f"device {SERIAL} rejected wifiSsid=SYNTH_SSID timeZone=SYNTH/Zone",
    }
    client, rec, seen = _client(mode, biz=lambda r: httpx.Response(200, json=body))
    with pytest.raises(ApiError) as excinfo:
        _call(
            client,
            (
                "get_utc",
                (SERIAL,),
            ),
        )
    err = excinfo.value
    assert err.code == "20018" and err.operation == "devices.get_utc"
    assert len(seen["biz"]) == 1 and len(seen["auth"]) == 1  # no extra retry
    assert [e["outcome"] for e in _utc_events(rec)] == ["started", "failed"]
    rendered = str(err) + repr(err) + json.dumps(rec.report(), ensure_ascii=False)
    for marker in MARKERS:
        assert marker not in rendered


def test_get_utc_business_error_keeps_benign_text(mode):
    body = {"code": "20018", "msg": "device busy"}
    client, _, _ = _client(mode, biz=lambda r: httpx.Response(200, json=body))
    with pytest.raises(ApiError) as excinfo:
        _call(
            client,
            (
                "get_utc",
                (SERIAL,),
            ),
        )
    err = excinfo.value
    assert "device busy" in str(err)
    assert err.code == "20018" and err.operation == "devices.get_utc"


def _assert_rejected_pre_auth(rec, seen):
    assert seen == {"auth": [], "biz": []}
    assert [e["outcome"] for e in _utc_events(rec)] == ["started", "failed"]


@pytest.mark.parametrize("serial", ["", "   ", "\t\n", 0, None, "SER\ud800"])
def test_get_utc_rejects_invalid_serial_pre_auth(mode, serial):
    client, rec, seen = _client(mode)
    with pytest.raises(ValueError):
        _call(
            client,
            (
                "get_utc",
                (serial,),
            ),
        )
    _assert_rejected_pre_auth(rec, seen)


def test_get_utc_rejects_domestic_region_pre_auth(mode):
    client, rec, seen = _client(mode, region=RegionConfig.china())
    with pytest.raises(ValueError):
        _call(
            client,
            (
                "get_utc",
                (SERIAL,),
            ),
        )
    _assert_rejected_pre_auth(rec, seen)


@pytest.mark.parametrize("method", ["get", "get_utc"])
def test_devices_rejects_version_keyword(mode, method):
    client, _, seen = _client(mode)
    with pytest.raises(TypeError):
        _call(client, (method, (SERIAL,), {"version": "utc"}))
    assert seen == {"auth": [], "biz": []}
