"""Tests for device algorithm queries (731 list_devices / 730 list_supported)."""

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

MODES = ("sync", "async")
PATH_DEVICES = "/api/v3/intelligent/model/device"
PATH_SUPPORTED = "/api/v3/intelligent/model/device/support"
ENTRY = {
    "list_devices": "device_algorithms.list_devices",
    "list_supported": "device_algorithms.list_supported",
}
PATHS = {"list_devices": PATH_DEVICES, "list_supported": PATH_SUPPORTED}
AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
META_OK = {"code": 200, "message": "ok", "moreInfo": None}
SERIAL = "DevÄbÇ-09中文字Zz"  # mixed-case, unicode, opaque
CANARY = "CANARY-9f3z-leak"
# Synthetic, non-secret marker values; never real device/model identifiers.
MODEL_NAME = "ALG- synthetic-人形检测-X1"
MODEL_ID = "mdl-SYNTH-9z8y7x"
RAW_MARK = "RAW-STR-标记-55"


def _ok(data):
    return {"meta": dict(META_OK), "data": data, "extra": 0}


def _build(mode, payload, region=None):
    biz, auth = [], []

    def business(request):
        biz.append(request)
        return httpx.Response(200, json=payload)

    def authenticate(request):
        auth.append(request)
        return httpx.Response(200, json=AUTH_BODY)

    rec = DiagnosticRecorder()
    cls = Client if mode == "sync" else AsyncClient
    client = cls(
        "mock-key",
        "mock-secret",
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(authenticate),
        transport_factory=lambda origin: httpx.MockTransport(business),
        diagnostic_recorder=rec,
    )
    return client, biz, auth, rec


def _do(mode, client, method, args=(), kwargs=None):
    kwargs = kwargs or {}
    if mode == "sync":
        with client:
            return getattr(client.device_algorithms, method)(*args, **kwargs)

    async def main():
        async with client:
            return await getattr(client.device_algorithms, method)(*args, **kwargs)

    return asyncio.run(main())


def _call(mode, method, kwargs, response, args=()):
    client, biz, auth, rec = _build(mode, response)
    result = _do(mode, client, method, args=args, kwargs=kwargs)
    return result, biz, auth, rec


def _assert_wire(request, path, fields):
    assert request.method == "GET"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == path
    assert request.url.query == b""
    tokens = request.headers.get_list("accessToken")
    assert tokens == ["SYNTH_TOKEN"]  # exactly one token header, none elsewhere
    ctypes = request.headers.get_list("content-type")
    assert len(ctypes) == 1
    assert "application/x-www-form-urlencoded" in ctypes[0]
    assert "SYNTH_TOKEN" not in str(request.url)
    body = request.content.decode("utf-8")
    assert "SYNTH_TOKEN" not in body
    assert parse_qs(body, keep_blank_values=True) == fields
    if fields == {}:
        assert request.content == b""


def _events(rec):
    """report() exposes a single flat event list; there is no nesting."""
    return rec.report()["events"]


def _assert_diag(rec, entry, mode, outcome):
    events = _events(rec)
    facade = [
        e
        for e in events
        if e.get("kind") == "call" and e.get("entrypoint") == entry and e.get("mode") == mode
    ]
    outcomes = {e.get("outcome") for e in facade}
    assert outcomes == {"started", outcome}, events
    # A proper operation is recorded on the real request attempt.
    attempts = [e for e in events if e.get("kind") == "attempt" and e.get("operation") == entry]
    assert attempts, events


def _assert_diag_failed_early(rec, entry, mode):
    """Two facade events (started/failed), no auth/attempt ever ran."""
    events = _events(rec)
    facade = [
        e
        for e in events
        if e.get("kind") == "call" and e.get("entrypoint") == entry and e.get("mode") == mode
    ]
    assert {e.get("outcome") for e in facade} == {"started", "failed"}, events
    assert not [e for e in events if e.get("kind") == "attempt"], events
    assert not [e for e in events if e.get("kind") == "authentication"], events


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "kwargs,fields",
    [
        ({}, {}),
        ({"device_serial": SERIAL}, {"deviceSerial": [SERIAL]}),
        ({"page_start": 0}, {"pageStart": ["0"]}),
        ({"page_start": 500}, {"pageStart": ["500"]}),
        ({"page_size": 8}, {"pageSize": ["8"]}),
        ({"page_size": 50}, {"pageSize": ["50"]}),
        (
            {"page_start": 3, "page_size": 8},
            {"pageStart": ["3"], "pageSize": ["8"]},
        ),
    ],
)
def test_list_devices_wire(mode, kwargs, fields):
    rows = [{"deviceSerial": "A", "models": []}, {"deviceSerial": "B"}]
    result, biz, auth, rec = _call(mode, "list_devices", kwargs, _ok(rows))
    assert len(auth) == 1
    assert len(biz) == 1
    _assert_wire(biz[0], PATH_DEVICES, fields)
    assert isinstance(result, ApiResponse)
    assert result.message == "ok"
    assert result.data == rows
    _assert_diag(rec, ENTRY["list_devices"], mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
def test_list_supported_wire(mode):
    row = {
        "modelName": MODEL_NAME,
        "appId": MODEL_ID,
        "version": "v2",
        "loadStatus": 1,
        "desc": "synthetic row",
        "unknownField": {"keep": "me"},
    }
    result, biz, auth, rec = _call(mode, "list_supported", {}, _ok([row]), args=(SERIAL,))
    assert len(auth) == 1
    assert len(biz) == 1
    _assert_wire(biz[0], PATH_SUPPORTED, {"deviceSerial": [SERIAL]})
    assert result.message == "ok"
    assert result.data == [row]
    assert result.data[0]["appId"] == MODEL_ID
    assert result.data[0]["desc"] == "synthetic row"
    assert result.data[0]["unknownField"] == {"keep": "me"}
    _assert_diag(rec, ENTRY["list_supported"], mode, "succeeded")


@pytest.mark.parametrize("mode", MODES)
def test_list_supported_empty_and_row_fields(mode):
    # An empty list is a valid result and known row fields are preserved.
    result, biz, _, _ = _call(mode, "list_supported", {}, _ok([]), args=(SERIAL,))
    assert result.data == []
    row = {"appId": "app-1", "desc": "uses face match"}
    result, biz, _, _ = _call(mode, "list_supported", {}, _ok([row]), args=(SERIAL,))
    assert result.data == [row]
    assert result.data[0]["appId"] == "app-1"
    assert result.data[0]["desc"] == "uses face match"


@pytest.mark.parametrize("mode", MODES)
def test_list_devices_nested_models_record(mode):
    # 731 returns devices with a nested models array; rows pass through.
    model = {
        "modelName": MODEL_NAME,
        "appId": MODEL_ID,
        "version": "3.1",
        "loadStatus": 0,
        "unexpected": [1, 2],
    }
    row = {"deviceSerial": SERIAL, "models": [model], "odd": True}
    result, biz, _, _ = _call(mode, "list_devices", {}, _ok([row]))
    assert len(biz) == 1
    assert result.data == [row]
    assert result.data[0]["models"][0] == model


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"deviceSerial": "X"}],
        [
            {"deviceSerial": "X", "models": [], "odd": {"k": 1}},
            {"other": "row", "models": [{"id": 1}]},
        ],
    ],
)
def test_return_shapes(mode, data):
    payload = _ok(data)
    payload["meta"]["trace"] = "unknown-meta-key"
    result, biz, auth, rec = _call(mode, "list_devices", {"page_size": 8}, payload)
    assert len(biz) == 1
    assert result.data == data
    assert result.raw["meta"]["moreInfo"] is None
    assert result.raw["meta"]["trace"] == "unknown-meta-key"
    assert result.raw["extra"] == 0


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("data", [None, {}, [7], ["x"], [None], [{"a": 1}, 5]])
def test_bad_data_shapes(mode, data):
    client, biz, auth, _ = _build(mode, _ok(data))
    with pytest.raises(ProtocolError):
        _do(mode, client, "list_devices")
    assert len(biz) == 1


@pytest.mark.parametrize("mode", MODES)
def test_list_supported_api_error_2001(mode):
    # Ordinary business failure: exact type, preserved code, real operation,
    # one auth + one business request, no retry.
    payload = {"meta": {"code": 2001, "message": "oops", "moreInfo": None}, "data": []}
    client, biz, auth, rec = _build(mode, payload)
    with pytest.raises(ApiError) as exc_info:
        _do(mode, client, "list_supported", args=(SERIAL,))
    exc = exc_info.value
    assert type(exc) is ApiError
    assert exc.code == 2001
    assert exc.operation == "device_algorithms.list_supported"
    assert len(auth) == 1
    assert len(biz) == 1
    _assert_diag(rec, ENTRY["list_supported"], mode, "failed")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("code", ["200", 10005])
def test_list_devices_business_error_code_preserved(mode, code):
    # A non-success scalar business code raises ApiError and keeps its value.
    payload = {"meta": {"code": code, "message": "oops", "moreInfo": None}, "data": []}
    client, biz, auth, rec = _build(mode, payload)
    with pytest.raises(ApiError) as exc_info:
        _do(mode, client, "list_devices")
    assert exc_info.value.code == code
    assert len(biz) == 1
    _assert_diag(rec, ENTRY["list_devices"], mode, "failed")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"code": True, "message": "x", "moreInfo": None}, "data": []},
        {"meta": {"code": 200.0, "message": "x", "moreInfo": None}, "data": []},
        {"meta": {"message": "x", "moreInfo": None}, "data": []},  # code path missing
        {"meta": None, "data": []},  # meta not a mapping -> no meta.code
        {"code": 200, "data": []},  # top-level code is not meta.code -> fail
    ],
)
def test_invalid_meta_code_is_protocol_error(mode, payload):
    client, biz, auth, _ = _build(mode, payload)
    with pytest.raises(ProtocolError):
        _do(mode, client, "list_devices")
    assert len(biz) == 1


@pytest.mark.parametrize("mode", MODES)
def test_diag_and_repr_exclude_canary(mode):
    payload = {
        "meta": {"code": 200, "message": "ok", "moreInfo": {"t": CANARY}},
        "data": [
            {
                "deviceSerial": SERIAL,
                "models": [{"modelName": MODEL_NAME, "appId": MODEL_ID}],
                "note": RAW_MARK,
            }
        ],
        CANARY: 1,
    }
    client, biz, auth, rec = _build(mode, payload)
    result = _do(mode, client, "list_supported", args=(SERIAL,))
    assert result.raw["meta"]["moreInfo"] == {"t": CANARY}
    assert result.raw[CANARY] == 1
    report_json = json.dumps(rec.report(), ensure_ascii=False)
    for marker in (CANARY, MODEL_NAME, MODEL_ID, SERIAL, RAW_MARK):
        assert marker not in repr(result)
        assert marker not in repr(client)
        assert marker not in report_json
        assert marker not in rec.to_json()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"device_serial": ""},
        {"device_serial": "   "},
        {"device_serial": "\ud800"},
        {"device_serial": 42},
        {"page_start": -1},
        {"page_start": True},
        {"page_size": 7},
        {"page_size": 51},
        {"page_size": True},
        {"device_serial": "X", "page_start": 0},
        {"device_serial": "X", "page_size": 8},
    ],
)
def test_list_devices_validation(mode, kwargs):
    client, biz, auth, rec = _build(mode, _ok([]))
    with pytest.raises(ValueError):
        _do(mode, client, "list_devices", kwargs=kwargs)
    assert biz == []
    assert auth == []
    _assert_diag_failed_early(rec, ENTRY["list_devices"], mode)


@pytest.mark.parametrize("mode", MODES)
def test_list_devices_keyword_only(mode):
    client, biz, auth, _ = _build(mode, _ok([]))
    with pytest.raises(TypeError):
        _do(mode, client, "list_devices", args=(SERIAL,))
    assert biz == []
    assert auth == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("serial", ["", "  ", "\ud800", 123])
def test_list_supported_validation(mode, serial):
    client, biz, auth, rec = _build(mode, _ok([]))
    with pytest.raises(ValueError):
        _do(mode, client, "list_supported", args=(serial,))
    assert biz == []
    assert auth == []
    _assert_diag_failed_early(rec, ENTRY["list_supported"], mode)


@pytest.mark.parametrize("mode", MODES)
def test_list_supported_requires_serial(mode):
    client, biz, auth, _ = _build(mode, _ok([]))
    with pytest.raises(TypeError):
        _do(mode, client, "list_supported")
    assert biz == []
    assert auth == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "method,args,kwargs",
    [("list_devices", (), {}), ("list_supported", ("SN1",), {})],
)
def test_overseas_region_rejected(mode, method, args, kwargs):
    client, biz, auth, rec = _build(mode, _ok([]), region=RegionConfig.overseas())
    with pytest.raises(ValueError):
        _do(mode, client, method, args=args, kwargs=kwargs)
    assert biz == []
    assert auth == []
    _assert_diag_failed_early(rec, ENTRY[method], mode)
