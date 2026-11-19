"""Wire-level coverage for the overseas read-only DDNS facade.

Everything here is offline: both the auth endpoint and the business origin are
driven through ``httpx.MockTransport`` against reserved synthetic subdomains.
No real credentials, no real endpoints, no retry/order/token manipulation.
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

APP_KEY = "SYNTHKEY"
APP_SECRET = "SYNTHSECRET"
TOKEN = "SYNTH_TOKEN"
AUTH_ORIGIN = "https://synthetic-auth.ezvizlife.com"
API_ORIGIN = "https://synthetic-api.ezvizlife.com"
AUTH_OK = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": API_ORIGIN,
    },
}

# Selector inputs (the caller domain must never become a request origin).
SELECTOR_SERIAL = "MiXeD-SerIAL-42"
SELECTOR_UTF8 = "设备-SN-42"
SELECTOR_DOMAIN = "opaque-selector.invalid"
# Response-only device mapping identifiers, all synthetic.
RETURNED_WAN = "returned-wan.invalid"
DEVICE_HOST = "synth-device-host.internal"
DEVICE_IP = "192.0.2.44"
DEVICE_PORT = 10443
HIDDNS_HTTP_PORT = 38081
MAPPING_CMD_PORT = 38082
SUB_SERIAL = "SubSER-9"
RESP_SERIAL = "SerIAL-Keep-Case"

GET_ROW = {
    "deviceSerial": RESP_SERIAL,
    "serial": RESP_SERIAL,
    "subSerial": SUB_SERIAL,
    "domain": RETURNED_WAN,
    "deviceHost": DEVICE_HOST,
    "deviceIp": DEVICE_IP,
    "devicePort": DEVICE_PORT,
    "upnp": True,
    "upnpMappingMode": 1,
    "hiddnsHttpPort": HIDDNS_HTTP_PORT,
    "mappingHiddnsCmdPort": MAPPING_CMD_PORT,
    "futureExtra": {"deep": ["keep", 7]},
}
LIST_ROWS = [
    dict(GET_ROW),
    {
        "deviceSerial": "OtherSer-77",
        "subSerial": SUB_SERIAL,
        "domain": RETURNED_WAN,
        "deviceIp": DEVICE_IP,
        "devicePort": DEVICE_PORT,
        "upnpMappingMode": 0,
        "anotherUnknown": None,
    },
]
# Values that must never leak into public reprs or diagnostic output.
PRIVATE_MARKERS = [
    TOKEN,
    APP_KEY,
    APP_SECRET,
    SELECTOR_DOMAIN,
    RETURNED_WAN,
    DEVICE_HOST,
    DEVICE_IP,
    str(DEVICE_PORT),
    str(HIDDNS_HTTP_PORT),
    str(MAPPING_CMD_PORT),
    SUB_SERIAL,
    RESP_SERIAL,
    "OtherSer-77",
    "synthetic-api.ezvizlife.com",
    "synthetic-auth.ezvizlife.com",
]

MODES = pytest.mark.parametrize("mode", ["sync", "async"])
PATHS = {
    "get": "/api/lapp/ddns/get",
    "list": "/api/lapp/ddns/list",
    "share_list": "/api/lapp/ddns/share/list",
}


class Wire:
    """Records every request crossing the two mock transports."""

    def __init__(self, responder, auth_payload=None):
        self._responder = responder
        self._auth_payload = AUTH_OK if auth_payload is None else auth_payload
        self.auth_requests: list[httpx.Request] = []
        self.api_requests: list[httpx.Request] = []
        self.factory_origins: list[str] = []

    def auth(self, request: httpx.Request) -> httpx.Response:
        self.auth_requests.append(request)
        return httpx.Response(200, json=self._auth_payload)

    def api(self, request: httpx.Request) -> httpx.Response:
        self.api_requests.append(request)
        return self._responder(request)

    def transport(self, origin: str) -> httpx.MockTransport:
        self.factory_origins.append(origin)
        return httpx.MockTransport(self.api)


def json_responder(payload, status: int = 200):
    return lambda request: httpx.Response(status, json=payload)


def ok(data, **extra):
    return json_responder({"code": "200", "msg": "synthetic ok", "data": data, **extra})


def invoke(mode: str, wire: Wire, method: str, *, region=None, recorder=None, **kwargs):
    """Run ``client.ddns.<method>(**kwargs)`` on a context-managed client."""

    client_kwargs = {
        "region": region
        if region is not None
        else RegionConfig.overseas(auth_base_url=AUTH_ORIGIN),
        "auth_transport": httpx.MockTransport(wire.auth),
        "transport_factory": wire.transport,
    }
    if recorder is not None:
        client_kwargs["diagnostic_recorder"] = recorder

    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **client_kwargs) as client:
            return getattr(client.ddns, method)(**kwargs)

    async def _go():
        async with AsyncClient(APP_KEY, APP_SECRET, **client_kwargs) as client:
            return await getattr(client.ddns, method)(**kwargs)

    return asyncio.run(_go())


def form_of(request: httpx.Request) -> dict:
    return dict(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))


def assert_wire_common(wire: Wire, path: str):
    """Counts, origins, path and token placement shared by every call."""

    assert len(wire.auth_requests) == 1
    assert len(wire.api_requests) == 1
    # Business traffic must originate from the token's areaDomain, never from
    # the caller-supplied domain selector or a WAN domain inside a response.
    assert wire.factory_origins == [API_ORIGIN]
    auth_req = wire.auth_requests[0]
    assert auth_req.method == "POST"
    assert auth_req.url.host == "synthetic-auth.ezvizlife.com"
    req = wire.api_requests[0]
    assert req.method == "POST"
    assert req.url.path == path
    assert req.url.host == "synthetic-api.ezvizlife.com"
    assert str(req.url).startswith(API_ORIGIN + "/")
    assert SELECTOR_DOMAIN not in req.url.host
    assert RETURNED_WAN not in str(req.url)
    # The access token is a form field only: never in URL or headers.
    assert TOKEN not in str(req.url)
    assert all(TOKEN not in value for value in req.headers.values())
    return req


def assert_no_io(wire: Wire):
    assert wire.auth_requests == []
    assert wire.api_requests == []
    assert wire.factory_origins == []


def facade_outcomes(recorder, entrypoint: str, mode: str):
    """Ordered facade-scope outcomes for one entrypoint/kind/mode triple."""

    return [
        e["outcome"]
        for e in recorder.report()["events"]
        if e.get("kind") == "call" and e.get("entrypoint") == entrypoint and e.get("mode") == mode
    ]


# --------------------------------------------------------------------------
# ddns.get
# --------------------------------------------------------------------------


@MODES
def test_get_sends_both_selectors_verbatim(mode):
    wire = Wire(ok(dict(GET_ROW)))
    resp = invoke(mode, wire, "get", device_serial=SELECTOR_SERIAL, domain=SELECTOR_DOMAIN)
    req = assert_wire_common(wire, PATHS["get"])
    # Exact body: token plus both selectors, case and content preserved.
    assert form_of(req) == {
        "accessToken": TOKEN,
        "deviceSerial": SELECTOR_SERIAL,
        "domain": SELECTOR_DOMAIN,
    }
    assert resp.data["domain"] == RETURNED_WAN
    assert resp.data["subSerial"] == SUB_SERIAL
    # Official UPnP mapping fields and unknown extras survive untouched.
    assert resp.data["upnpMappingMode"] == 1
    assert resp.data["hiddnsHttpPort"] == HIDDNS_HTTP_PORT
    assert resp.data["mappingHiddnsCmdPort"] == MAPPING_CMD_PORT
    assert resp.data["futureExtra"] == {"deep": ["keep", 7]}
    assert resp.raw["data"]["deviceIp"] == DEVICE_IP


@MODES
@pytest.mark.parametrize(
    "selector,sent_key",
    [
        ({"device_serial": SELECTOR_SERIAL}, "deviceSerial"),
        ({"device_serial": SELECTOR_UTF8}, "deviceSerial"),
        ({"domain": SELECTOR_DOMAIN}, "domain"),
    ],
    ids=["serial-only", "serial-utf8", "domain-only"],
)
def test_get_single_selector_alternatives(mode, selector, sent_key):
    wire = Wire(ok(dict(GET_ROW)))
    resp = invoke(mode, wire, "get", **selector)
    req = assert_wire_common(wire, PATHS["get"])
    body = form_of(req)
    assert body == {"accessToken": TOKEN, sent_key: next(iter(selector.values()))}
    omitted = "domain" if sent_key == "deviceSerial" else "deviceSerial"
    assert omitted not in body
    assert resp.data["deviceSerial"] == RESP_SERIAL


# --------------------------------------------------------------------------
# ddns.list / ddns.share_list
# --------------------------------------------------------------------------


@MODES
def test_list_omits_pagination_by_default(mode):
    # The server may answer with a page sibling that differs from any implicit
    # request defaults; everything is preserved verbatim.
    wire = Wire(ok(list(LIST_ROWS), page={"total": 7, "page": 4, "size": 1, "extra": "x"}))
    resp = invoke(mode, wire, "list")
    req = assert_wire_common(wire, PATHS["list"])
    assert form_of(req) == {"accessToken": TOKEN}
    assert resp.data == LIST_ROWS
    assert resp.raw["page"] == {"total": 7, "page": 4, "size": 1, "extra": "x"}


@MODES
@pytest.mark.parametrize(
    "kwargs,sent,omitted",
    [
        ({"page_start": 401}, {"pageStart": "401"}, "pageSize"),
        ({"page_size": 50}, {"pageSize": "50"}, "pageStart"),
    ],
    ids=["start-401-alone", "size-50-alone"],
)
def test_list_single_pagination_field(mode, kwargs, sent, omitted):
    wire = Wire(ok(list(LIST_ROWS)))
    resp = invoke(mode, wire, "list", **kwargs)
    req = assert_wire_common(wire, PATHS["list"])
    body = form_of(req)
    assert body == {"accessToken": TOKEN, **sent}
    assert omitted not in body
    assert resp.data == LIST_ROWS


@MODES
def test_list_explicit_pagination_size_50(mode):
    wire = Wire(ok(list(LIST_ROWS), page={"total": 1, "page": 0, "size": 1}))
    resp = invoke(mode, wire, "list", page_start=3, page_size=50)
    req = assert_wire_common(wire, PATHS["list"])
    assert form_of(req) == {
        "accessToken": TOKEN,
        "pageStart": "3",
        "pageSize": "50",
    }
    # The returned sibling page may legitimately disagree with what was asked.
    assert resp.raw["page"]["size"] == 1


@MODES
def test_list_business_401_raises_api_error(mode):
    recorder = DiagnosticRecorder()
    wire = Wire(json_responder({"code": "401", "msg": "synthetic denied"}))
    with pytest.raises(ApiError) as excinfo:
        invoke(mode, wire, "list", recorder=recorder, page_start=0, page_size=10)
    assert excinfo.value.code == "401"
    assert_wire_common(wire, PATHS["list"])
    events = recorder.report()["events"]
    assert any(e.get("operation") == "ddns.list" for e in events)
    assert facade_outcomes(recorder, "ddns.list", mode) == ["started", "failed"]


@MODES
def test_share_list_uses_its_own_route(mode):
    wire = Wire(ok(list(LIST_ROWS), page={"total": 2, "page": 0, "size": 10}))
    resp = invoke(mode, wire, "share_list", page_start=0, page_size=10)
    req = assert_wire_common(wire, PATHS["share_list"])
    # The shared list is a distinct exact route, not the account list.
    assert req.url.path != PATHS["list"]
    assert form_of(req) == {
        "accessToken": TOKEN,
        "pageStart": "0",
        "pageSize": "10",
    }
    assert resp.data == LIST_ROWS


@MODES
def test_list_empty_and_page_absent(mode):
    wire = Wire(ok([]))
    resp = invoke(mode, wire, "list", page_start=0, page_size=10)
    assert_wire_common(wire, PATHS["list"])
    assert resp.data == []
    # page is optional upstream; nothing is fabricated when it is missing.
    assert resp.raw.get("page") is None


@MODES
@pytest.mark.parametrize("mapping_mode", [0, 1], ids=["upnp-0", "upnp-1"])
def test_list_upnp_rows_extras_and_page_absent(mode, mapping_mode):
    rows = [dict(GET_ROW, upnpMappingMode=mapping_mode)]
    wire = Wire(ok(rows))
    resp = invoke(mode, wire, "list")
    assert_wire_common(wire, PATHS["list"])
    assert resp.data == rows
    # Official UPnP port fields and unrecognized extras pass through verbatim.
    assert resp.data[0]["hiddnsHttpPort"] == HIDDNS_HTTP_PORT
    assert resp.data[0]["mappingHiddnsCmdPort"] == MAPPING_CMD_PORT
    assert resp.data[0]["futureExtra"] == {"deep": ["keep", 7]}
    assert resp.raw.get("page") is None


# --------------------------------------------------------------------------
# Diagnostics scopes
# --------------------------------------------------------------------------


@MODES
@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("get", {"device_serial": SELECTOR_SERIAL}),
        ("list", {"page_size": 10}),
        ("share_list", {"page_start": 0, "page_size": 10}),
    ],
    ids=["get", "list", "share-list"],
)
def test_recorder_scopes_operation_and_facade_outcomes(mode, method, kwargs):
    recorder = DiagnosticRecorder()
    data = dict(GET_ROW) if method == "get" else list(LIST_ROWS)
    wire = Wire(ok(data))
    invoke(mode, wire, method, recorder=recorder, **kwargs)
    assert_wire_common(wire, PATHS[method])
    events = recorder.report()["events"]
    entrypoint = f"ddns.{method}"
    # The inner transport/client.request scope carries the wire operation.
    assert any(e.get("operation") == entrypoint for e in events)
    # The public facade scope emits exactly started -> succeeded, no more.
    assert facade_outcomes(recorder, entrypoint, mode) == ["started", "succeeded"]


# --------------------------------------------------------------------------
# Protocol violations
# --------------------------------------------------------------------------


@MODES
@pytest.mark.parametrize(
    "method,kwargs,payload",
    [
        ("get", {"device_serial": "S"}, {"code": "200", "data": [1, 2]}),
        ("get", {"device_serial": "S"}, {"code": "200", "data": "oops"}),
        ("list", {}, {"code": "200", "data": {"not": "array"}}),
        ("list", {}, {"code": "200", "data": [{"ok": 1}, 5]}),
        ("list", {}, {"code": "200", "data": [], "page": "oops"}),
        ("list", {}, {"code": "200", "data": [], "page": [1]}),
        ("share_list", {}, {"code": "200", "data": None}),
    ],
    ids=[
        "get-array",
        "get-string",
        "list-object",
        "list-odd-row",
        "list-page-string",
        "list-page-array",
        "share-null",
    ],
)
def test_odd_shapes_raise_protocol_error(mode, method, kwargs, payload):
    wire = Wire(json_responder(payload))
    with pytest.raises(ProtocolError):
        invoke(mode, wire, method, **kwargs)
    assert_wire_common(wire, PATHS[method])


# --------------------------------------------------------------------------
# Ordinary failures, redaction, diagnostics
# --------------------------------------------------------------------------


@MODES
def test_get_business_failure_redacts_private_markers(mode):
    recorder = DiagnosticRecorder()
    server_msg = (
        f"failure {SELECTOR_DOMAIN} deviceIp={DEVICE_IP} "
        f"subSerial={SUB_SERIAL} serial={RESP_SERIAL}"
    )
    wire = Wire(json_responder({"code": "49999", "msg": server_msg, "data": dict(GET_ROW)}))
    with pytest.raises(ApiError) as excinfo:
        invoke(mode, wire, "get", recorder=recorder, domain=SELECTOR_DOMAIN)
    exc = excinfo.value
    assert exc.code == "49999"
    assert exc.operation == "ddns.get"
    # The fixed benign wording of the failure text survives scrubbing...
    assert "failure" in str(exc)
    rendered = repr(recorder.report())
    for marker in PRIVATE_MARKERS:
        # ...while every private marker stays out of str, repr and the report.
        assert marker not in str(exc)
        assert marker not in repr(exc)
        assert marker not in rendered
    assert_wire_common(wire, PATHS["get"])
    events = recorder.report()["events"]
    assert any(e.get("operation") == "ddns.get" for e in events)
    assert facade_outcomes(recorder, "ddns.get", mode) == ["started", "failed"]


@MODES
def test_auth_failure_never_reaches_business(mode):
    wire = Wire(
        ok(dict(GET_ROW)),
        auth_payload={"code": "10002", "msg": "synthetic auth denied"},
    )
    with pytest.raises(AuthenticationError) as excinfo:
        invoke(mode, wire, "list", page_start=0, page_size=10)
    assert excinfo.value.code == "10002"
    # Exactly one auth attempt; the business origin is never consulted.
    assert len(wire.auth_requests) == 1
    assert wire.api_requests == []
    assert wire.factory_origins == []


@MODES
def test_response_repr_hides_payload_but_raw_keeps_it(mode):
    recorder = DiagnosticRecorder()
    wire = Wire(ok(dict(GET_ROW), extraTop={"hidden": DEVICE_IP}))
    resp = invoke(
        mode,
        wire,
        "get",
        recorder=recorder,
        device_serial=SELECTOR_SERIAL,
        domain=SELECTOR_DOMAIN,
    )
    assert_wire_common(wire, PATHS["get"])
    # ApiResponse repr must not surface raw/data/msg or any private marker.
    rendered = repr(resp)
    assert "raw" not in rendered
    assert "data" not in rendered
    assert "msg" not in rendered
    for marker in PRIVATE_MARKERS:
        assert marker not in rendered
    events = recorder.report()["events"]
    assert any(e.get("operation") == "ddns.get" for e in events)
    assert facade_outcomes(recorder, "ddns.get", mode) == ["started", "succeeded"]
    blob = repr(events)
    for marker in PRIVATE_MARKERS:
        assert marker not in blob
    # ...while the parsed result still exposes every synthetic identifier.
    assert resp.data["deviceIp"] == DEVICE_IP
    assert resp.data["devicePort"] == DEVICE_PORT
    assert resp.data["subSerial"] == SUB_SERIAL
    assert resp.data["serial"] == RESP_SERIAL
    assert resp.data["deviceHost"] == DEVICE_HOST
    assert resp.raw["data"]["domain"] == RETURNED_WAN
    assert resp.raw["extraTop"] == {"hidden": DEVICE_IP}


# --------------------------------------------------------------------------
# Validation happens before any auth or business I/O
# --------------------------------------------------------------------------


@MODES
@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("get", {}),
        ("get", {"device_serial": 123}),
        ("get", {"domain": 4.5}),
        ("get", {"device_serial": "   "}),
        ("get", {"domain": "\t\n"}),
        ("get", {"domain": "\ud800"}),
        ("get", {"device_serial": "ok", "domain": "\udcffx"}),
        ("list", {"page_size": True}),
        ("list", {"page_size": 0}),
        ("list", {"page_size": 51}),
        ("list", {"page_start": False}),
        ("list", {"page_start": -1}),
        ("list", {"page_start": 0, "page_size": -3}),
        ("share_list", {"page_size": 0}),
        ("share_list", {"page_start": -9}),
    ],
    ids=[
        "get-no-selector",
        "get-serial-nonstring",
        "get-domain-nonstring",
        "get-serial-blank",
        "get-domain-blank",
        "get-domain-surrogate",
        "get-domain-surrogate-with-serial",
        "list-size-bool",
        "list-size-zero",
        "list-size-over-50",
        "list-start-bool",
        "list-start-negative",
        "list-size-negative",
        "share-size-zero",
        "share-start-negative",
    ],
)
def test_validation_fires_before_any_io(mode, method, kwargs):
    wire = Wire(ok(dict(GET_ROW)))
    with pytest.raises(ValueError):
        invoke(mode, wire, method, **kwargs)
    assert_no_io(wire)


@MODES
def test_list_page_start_huge_but_convertible(mode):
    # 10**1000 has exactly 1001 decimal digits and the frozen SDK imposes no
    # overseas upper bound on pageStart. Whether it reaches the wire therefore
    # depends only on this interpreter's int->str conversion limit.
    wire = Wire(ok(list(LIST_ROWS)))
    limit = sys.get_int_max_str_digits()
    if limit and limit < 1001:
        with pytest.raises(ValueError):
            invoke(mode, wire, "list", page_start=10**1000)
        assert_no_io(wire)
    else:
        resp = invoke(mode, wire, "list", page_start=10**1000)
        req = assert_wire_common(wire, PATHS["list"])
        assert form_of(req) == {"pageStart": "1" + "0" * 1000, "accessToken": TOKEN}
        assert resp.data == LIST_ROWS


def _huge_page_start() -> int:
    """A page_start that cannot even be decimal-encoded on this interpreter."""

    limit = sys.get_int_max_str_digits()
    if limit == 0:
        pytest.skip("interpreter int->str digit limit is disabled")
    return 10**limit


@MODES
def test_list_page_start_past_int_str_limit(mode):
    wire = Wire(ok(list(LIST_ROWS)))
    with pytest.raises(ValueError):
        invoke(mode, wire, "list", page_start=_huge_page_start())
    assert_no_io(wire)


@MODES
def test_ddns_rejected_for_china_region_before_io(mode):
    wire = Wire(ok(dict(GET_ROW)))
    with pytest.raises(ValueError):
        invoke(mode, wire, "list", region=RegionConfig.china())
    assert_no_io(wire)
