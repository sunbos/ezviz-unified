"""Offline wire and privacy coverage for the single-page domestic GB license query."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_third_party_protocols import Probe, _events

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

OP = "list_gb_licenses"
PATH = "/api/v3/device/register/gb/license/list"
PRODUCT = " GB_PRIVATE+/%&= 项 "
KW = {"page_index": 8, "page_size": 17}
FIRST = {
    "deviceLicense": "LICENSE_PRIVATE",
    "deviceId": "DEVICE_PRIVATE",
    "devlogicId": "LOGIC_PRIVATE",
    "idType": "ID_TYPE_PRIVATE",
    "deviceName": "NAME_PRIVATE",
    "productKey": "REPLY_KEY_PRIVATE",
    "activatedTime": None,
    "reserve": None,
    "activatedStatus": "STATUS_PRIVATE",
    "disableStatus": "DISABLE_PRIVATE",
    "createTime": "TIME_PRIVATE",
    "future": {"tag": "EXT_PRIVATE"},
}
ROWS = [FIRST, {}, {"idType": "unknown-order", "future": None}]
OK = {
    "meta": {"code": 200, "message": "ok", "moreInfo": {"opaque": "META_PRIVATE"}},
    "data": ROWS,
    "rootExtra": "ROOT_PRIVATE",
}
STALE = {"meta": {"code": 10002, "message": "STALE_PRIVATE"}}


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def _ev(p, mode, name):
    return _events(p, name, "call", mode), _events(p, name, "attempt", mode)


def test_closed_client_never_authenticates(mode):
    async def scenario():
        client, auth_calls, origins = make_client(mode, lambda r: pytest.fail("unexpected request"))
        await close(client)
        await close(client)
        assert client.is_closed
        with pytest.raises(TransportError, match="closed"):
            await resolve(client.third_party_protocols.list_gb_licenses(PRODUCT, **KW))
        assert auth_calls == origins == []

    asyncio.run(scenario())


def test_permission_denied_10031(mode):
    p = Probe({"meta": {"code": 10031, "message": "PRIVATE_DENIAL"}})
    with pytest.raises(PermissionDeniedError) as err:
        p.call(mode, OP, PRODUCT, **KW)
    assert err.value.code == 10031 and err.value.http_status == 200
    assert len(p.auth) == len(p.biz) == 1


def test_read_timeout_maps_transport_error(mode):
    p = Probe(httpx.ReadTimeout("private timeout"))
    with pytest.raises(TransportError) as excinfo:
        p.call(mode, OP, PRODUCT, **KW)
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(p.auth) == len(p.biz) == 1


def test_async_cancel():
    import asyncio

    probe = Probe(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        probe.call("async", OP, PRODUCT, **KW)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, "async", OP) == (["started", "cancelled"], ["started", "cancelled"])


def test_get_refresh(mode):
    probe = Probe(STALE, OK)
    assert probe.call(mode, OP, PRODUCT, **KW).raw == OK
    check_replayed_form(probe)
    assert _ev(probe, mode, OP) == (
        ["started", "succeeded"],
        ["started", "failed", "started", "succeeded"],
    )


def test_get_refresh_exhausted(mode):
    probe = Probe(STALE, STALE)
    with pytest.raises(AuthenticationError):
        probe.call(mode, OP, PRODUCT, **KW)
    check_replayed_form(probe)
    assert _ev(probe, mode, OP) == (
        ["started", "failed"],
        ["started", "failed", "started", "failed"],
    )


def test_auth_failure(mode):
    p = Probe()

    def deny(request):
        p.auth.append(request)
        return httpx.Response(403, json={"code": "10031", "msg": "PRIVATE_DENIAL"})

    p._auth = deny
    with pytest.raises(ApiError) as exc:
        p.call(mode, OP, PRODUCT, **KW)
    assert len(p.auth) == 1
    assert p.biz == []
    blob = repr(exc.value) + " " + str(exc.value)
    assert PRODUCT not in blob
    assert "TOK1" not in blob


@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, ok, caplog):
    payload = {
        **OK,
        "meta": {
            "code": 200 if ok else "PRIVATE_BACKEND_CODE",
            "message": "PRIVATE_UPSTREAM_MSG",
            "moreInfo": {"private": "META_PRIVATE"},
        },
    }
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(wire)
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.call(mode, OP, PRODUCT, **KW)
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.call(mode, OP, PRODUCT, **KW)
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [r.getMessage() for r in caplog.records if r.name == "httpx" and PATH in r.getMessage()]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        "GB_PRIVATE",
        "LICENSE_PRIVATE",
        "DEVICE_PRIVATE",
        "LOGIC_PRIVATE",
        "ID_TYPE_PRIVATE",
        "NAME_PRIVATE",
        "REPLY_KEY_PRIVATE",
        "STATUS_PRIVATE",
        "DISABLE_PRIVATE",
        "TIME_PRIVATE",
        "EXT_PRIVATE",
        "META_PRIVATE",
        "ROOT_PRIVATE",
        "TOK1",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
    ):
        assert marker not in blob


def check_replayed_form(probe):
    assert len(probe.auth) == 2 and len(probe.biz) == 2
    fields = {"productKey": PRODUCT, "pageIndex": "8", "pageSize": "17"}
    _form(probe.biz[0], "TOK1", fields)
    _form(probe.biz[1], "TOK2", fields)


def _form(req, token, fields):
    assert req.method == "GET"
    assert req.url.scheme == "https" and req.url.host == "open.ys7.com"
    assert req.url.path == PATH
    assert req.url.query == b""
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(
        [("accessToken", token), *fields.items()]
    )
    for header in (
        "authorization",
        "accessToken",
        "productKey",
        "pageIndex",
        "pageSize",
        "deviceSerial",
    ):
        assert header not in req.headers


@pytest.mark.parametrize(
    ("kwargs", "hints"),
    [
        ({}, {}),
        ({"page_index": None, "page_size": None}, {}),
        ({"page_index": 0}, {"pageIndex": "0"}),
        ({"page_index": 123456}, {"pageIndex": "123456"}),
        ({"page_size": 1}, {"pageSize": "1"}),
        ({"page_size": 50}, {"pageSize": "50"}),
        (KW, {"pageIndex": "8", "pageSize": "17"}),
    ],
)
@pytest.mark.parametrize("data", [[], ROWS])
def test_gb_wire_success(mode, kwargs, hints, data):
    p = Probe({**OK, "data": data})
    result = p.call(mode, OP, PRODUCT, **kwargs)
    assert len(p.auth) == 1
    assert p.auth[0].method == "POST"
    assert p.auth[0].url.host == "open.ys7.com"
    assert p.auth[0].url.path == "/api/lapp/token/get"
    assert len(p.biz) == 1
    _form(p.biz[0], "TOK1", {"productKey": PRODUCT, **hints})
    assert result.code == 200 and type(result.code) is int
    assert result.message == "***"
    assert result.raw == {**OK, "data": data}
    assert result.data == data
    assert _ev(p, mode, OP) == (["started", "succeeded"], ["started", "succeeded"])


INVALID_CALLS = [
    ((), {}, TypeError),
    (("",), {}, ValueError),
    ((" \t\r\n",), {}, ValueError),
    ((b"GB_PRIVATE",), {}, ValueError),
    ((True,), {}, ValueError),
    ((None,), {}, ValueError),
    (("\ud800GB_PRIVATE",), {}, ValueError),
    ((PRODUCT,), {"page_index": -1}, ValueError),
    ((PRODUCT,), {"page_index": 0.5}, ValueError),
    ((PRODUCT,), {"page_index": True}, ValueError),
    ((PRODUCT,), {"page_index": "1"}, ValueError),
    ((PRODUCT,), {"page_size": 0}, ValueError),
    ((PRODUCT,), {"page_size": 51}, ValueError),
    ((PRODUCT,), {"page_size": False}, ValueError),
    ((PRODUCT,), {"page_size": 25.0}, ValueError),
    ((PRODUCT,), {"page_size": "10"}, ValueError),
    ((PRODUCT, 1, 10), {}, TypeError),
    ((PRODUCT,), {"pageStart": 1}, TypeError),
    ((PRODUCT,), {"protocol": "gb"}, TypeError),
    ((PRODUCT,), {"device_serial": "DS_PRIVATE"}, TypeError),
    ((PRODUCT,), {"productKey": "PK_PRIVATE2"}, TypeError),
]

BAD_RESPONSES = [
    ({"meta": OK["meta"]}, ProtocolError),
    ({**OK, "data": None}, ProtocolError),
    ({**OK, "data": {}}, ProtocolError),
    ({**OK, "data": ["row"]}, ProtocolError),
    ({**OK, "data": [None]}, ProtocolError),
    ({**OK, "data": [*ROWS, None]}, ProtocolError),
    ({"code": 200, "data": ROWS}, ProtocolError),
    ({"code": "200", "data": ROWS}, ProtocolError),
    ({"body": {"meta": {"code": 200}, "data": []}}, ProtocolError),
    ({**OK, "meta": {**OK["meta"], "code": True}}, ProtocolError),
    ({**OK, "meta": {**OK["meta"], "code": 200.0}}, ProtocolError),
    ({**OK, "meta": {**OK["meta"], "code": "200"}}, ApiError),
]


@pytest.mark.parametrize("args,kwargs,exc", INVALID_CALLS)
def test_invalid_inputs(mode, args, kwargs, exc):
    p = Probe()
    with pytest.raises(exc):
        p.call(mode, OP, *args, **kwargs)
    assert p.auth == p.biz == []
    assert _events(p, OP, "attempt", mode) == []


@pytest.mark.parametrize("body,exc", BAD_RESPONSES)
def test_invalid_response(mode, body, exc):
    p = Probe(body)
    with pytest.raises(exc):
        p.call(mode, OP, PRODUCT, **KW)
    assert len(p.auth) == len(p.biz) == 1
    assert _events(p, OP, "call", mode) == ["started", "failed"]


def test_region_precedes_input_validation(mode):
    messages = []
    for key in (PRODUCT, object()):
        p = Probe()
        with pytest.raises(ValueError) as error:
            p.call(mode, OP, key, region=RegionConfig.overseas())
        assert p.auth == p.biz == []
        assert _ev(p, mode, OP) == (["started", "failed"], [])
        messages.append(str(error.value))
    assert messages[0] == messages[1]


@pytest.mark.parametrize(
    "status,code,exc", [(403, 20018, PermissionDeniedError), (200, 60020, ApiError)]
)
def test_nonretryable_business_failures(mode, status, code, exc):
    p = Probe(httpx.Response(status, json={"meta": {"code": code, "message": "PRIVATE_DENIAL"}}))
    with pytest.raises(exc) as error:
        p.call(mode, OP, PRODUCT, **KW)
    assert type(error.value) is exc and error.value.code == code
    assert error.value.message == "***"
    assert len(p.auth) == len(p.biz) == 1
    assert _ev(p, mode, OP) == (["started", "failed"], ["started", "failed"])
