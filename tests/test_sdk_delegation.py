"""Behavior tests for the delegation facades over real client transports.

``client.delegation`` attachment belongs to the integration task, so the
facades are instantiated directly against a real ``Client`` /
``AsyncClient`` backed by ``httpx.MockTransport``. Authentication,
header-token injection, form encoding, the meta envelope, the single
read retry, write no-replay, and ``sign`` redaction are exercised on the
wire; no real grant or relieve call is ever issued.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest

from ezviz_unified import ApiResponse, AsyncClient, Client, RegionConfig
from ezviz_unified.delegation import AsyncDelegation, Delegation
from ezviz_unified.errors import (
    _SENSITIVE_KEYS,
    ApiError,
    AuthenticationError,
    ProtocolError,
    TransportError,
    redact,
)

_SERIAL = "EVAL_DEVICE"
_B2_KEY = "B2-RECIPIENT-KEY"
_OWNER_KEY = "B1-OWNER-KEY"
_SIGN = "owner-app#0123456789abcdef0123456789abcdef#fedcba9876543210"
_LIST_PATH = "/api/v3/lapp/trust/sign/resource/list"

KINDS = pytest.mark.parametrize("kind", ["sync", "async"])


def _region(scope: str) -> RegionConfig:
    return RegionConfig.china() if scope == "domestic" else RegionConfig.overseas()


def _meta_ok(data: object) -> httpx.Response:
    return httpx.Response(
        200, json={"meta": {"code": 200, "message": "Operation succeeded."}, "data": data}
    )


def _meta_error(code: int, message: str) -> httpx.Response:
    return httpx.Response(200, json={"meta": {"code": code, "message": message}, "data": None})


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.content.decode("utf-8")))


class _Wire:
    """Mock plumbing: auth issues TOKEN<n>; business records each request."""

    def __init__(self, scope: str = "domestic") -> None:
        self.scope = scope
        self.auth_requests = 0
        self.business_requests: list[httpx.Request] = []
        self.handler = lambda request, count: _meta_ok(None)

    def auth(self, request: httpx.Request) -> httpx.Response:
        self.auth_requests += 1
        data: dict[str, object] = {
            "accessToken": f"TOKEN{self.auth_requests}",
            "expireTime": 9_999_999_999_999,
        }
        if self.scope == "overseas":
            data["areaDomain"] = "https://isgpopen.ezviz.com"
        return httpx.Response(200, json={"code": "200", "data": data})

    def business(self, request: httpx.Request) -> httpx.Response:
        self.business_requests.append(request)
        return self.handler(request, len(self.business_requests))

    def client(self, cls: type[Client] | type[AsyncClient]) -> Client | AsyncClient:
        return cls(
            "SDK-APP-KEY",
            "SDK-APP-SECRET",
            region=_region(self.scope),
            clock=lambda: 1000,
            auth_transport=httpx.MockTransport(self.auth),
            transport_factory=lambda origin: httpx.MockTransport(self.business),
        )


def _drive(kind: str, wire: _Wire, *steps: tuple) -> list[ApiResponse]:
    """Run (name, args, kwargs) facade calls; return their ApiResponses."""
    if kind == "sync":
        client = wire.client(Client)
        try:
            facade = Delegation(client)
            return [getattr(facade, name)(*args, **kwargs) for name, args, kwargs in steps]
        finally:
            client.close()

    async def run() -> list[ApiResponse]:
        client = wire.client(AsyncClient)
        try:
            facade = AsyncDelegation(client)
            out: list[ApiResponse] = []
            for name, args, kwargs in steps:
                out.append(await getattr(facade, name)(*args, **kwargs))
            return out
        finally:
            await client.aclose()

    return asyncio.run(run())


@KINDS
@pytest.mark.parametrize(
    "case",
    [
        (
            "apply_code",
            (_OWNER_KEY,),
            {},
            "/api/v3/lapp/trust/sign/apply",
            {"appKey": _OWNER_KEY},
            {},
            {"deviceSerial", "localIndex", "sign"},
        ),
        (
            "authorize_account",
            (_SIGN,),
            {},
            "/api/v3/lapp/trust/sign/userAuthorize",
            {"sign": _SIGN},
            {},
            {"deviceSerial", "localIndex", "appKey"},
        ),
        (
            "list_resources",
            (_SIGN,),
            {
                "device_serial": _SERIAL,
                "authorization_type": "authorize",
                "page_start": 0,
                "page_size": 20,
            },
            _LIST_PATH,
            {
                "sign": _SIGN,
                "authorizeType": "authorize",
                "pageStart": "0",
                "pageSize": "20",
            },
            {"deviceSerial": _SERIAL},
            {"localIndex", "appKey"},
        ),
        (
            "authorize_device",
            (_SIGN, _SERIAL),
            {"state": "1", "local_index": "3"},
            "/api/v3/lapp/trust/sign/resource/authorize",
            {"sign": _SIGN, "state": "1"},
            {"deviceSerial": _SERIAL, "localIndex": "3"},
            {"appKey"},
        ),
        (
            "relieve_device",
            (_B2_KEY, _SERIAL, "1"),
            {"local_index": "3"},
            "/api/v3/lapp/trust/sign/resource/relieve",
            {"appKey": _B2_KEY, "state": "1"},
            {"deviceSerial": _SERIAL, "localIndex": "3"},
            {"sign"},
        ),
    ],
    ids=["apply_code", "authorize_account", "list_resources", "authorize_device", "relieve_device"],
)
def test_wire_contracts(kind, case):
    """Each method posts to its endpoint with header token and exact form/headers."""
    name, args, kwargs, path, form, headers, absent = case
    wire = _Wire("domestic")
    (result,) = _drive(kind, wire, (name, args, kwargs))
    assert wire.auth_requests == 1 and len(wire.business_requests) == 1
    request = wire.business_requests[0]
    assert request.method == "POST" and request.url.path == path
    assert request.headers["accessToken"] == "TOKEN1"
    assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    sent = _form(request)
    assert sent == form and "accessToken" not in sent
    for key, value in headers.items():
        assert request.headers[key] == value
    for key in absent:
        assert key not in request.headers and key not in sent
    assert result.code == 200 and result.operation == f"delegation.{name}"


@KINDS
def test_optional_fields_stay_absent_until_given(kind):
    """None-valued options never reach the wire; defaults belong to the platform."""
    wire = _Wire("domestic")
    _drive(
        kind,
        wire,
        ("list_resources", (_SIGN,), {}),
        ("authorize_device", (_SIGN, _SERIAL), {}),
        ("relieve_device", (_B2_KEY, _SERIAL, "1"), {}),
    )
    list_req, auth_req, relieve_req = wire.business_requests
    assert _form(list_req) == {"sign": _SIGN}
    assert "deviceSerial" not in list_req.headers
    assert _form(auth_req) == {"sign": _SIGN}
    assert "localIndex" not in auth_req.headers
    assert _form(relieve_req) == {"appKey": _B2_KEY, "state": "1"}
    assert "localIndex" not in relieve_req.headers


@KINDS
def test_undeclared_page_bounds_are_not_invented(kind):
    """Docs declare only max 20; lower bounds are left to the platform."""
    wire = _Wire("domestic")
    _drive(
        kind,
        wire,
        ("list_resources", (_SIGN,), {"page_start": -2, "page_size": 0}),
    )
    assert _form(wire.business_requests[0]) == {
        "sign": _SIGN,
        "pageStart": "-2",
        "pageSize": "0",
    }


@KINDS
def test_response_payload_is_preserved_verbatim(kind):
    """Nulls, empty cameraInfo, unknown keys and data:false survive untouched."""
    listing = [
        {
            "deviceSerial": _SERIAL,
            "deviceName": "gate",
            "isEncrypt": 1,
            "isPermission": 0,
            "isAble": 1,
            "deviceTagType": "shared",
            "capturePic": None,
            "cameraInfo": [],
            "undocumented": {"nested": None},
        }
    ]
    wire = _Wire("domestic")
    responses = iter([_meta_ok(listing), _meta_ok(False)])
    wire.handler = lambda request, count: next(responses)
    listed, granted = _drive(
        kind,
        wire,
        ("list_resources", (_SIGN,), {}),
        ("authorize_account", (_SIGN,), {}),
    )
    assert listed.data == listing and listed.data[0]["capturePic"] is None
    assert granted.data is False  # meta.code==200 must not overwrite data:false


_OVERSEAS_STEPS = [
    ("apply_code", (_OWNER_KEY,), {}),
    ("authorize_account", (_SIGN,), {}),
    ("list_resources", (_SIGN,), {}),
    ("authorize_device", (_SIGN, _SERIAL), {}),
    ("relieve_device", (_B2_KEY, _SERIAL, "1"), {}),
]


@KINDS
@pytest.mark.parametrize(
    "step",
    _OVERSEAS_STEPS,
    ids=["apply_code", "authorize_account", "list_resources", "authorize_device", "relieve_device"],
)
def test_overseas_calls_fail_before_any_io(kind, step):
    """No overseas doc exists: rejection precedes auth and business traffic."""
    wire = _Wire("overseas")
    with pytest.raises(ValueError):
        _drive(kind, wire, step)
    assert wire.auth_requests == 0 and wire.business_requests == []


_INVALID_STEPS = [
    (("apply_code", (" ",), {}), "blank-owner-key"),
    (("apply_code", (123,), {}), "nonstr-owner-key"),
    (("authorize_account", ("",), {}), "blank-sign"),
    (("list_resources", (_SIGN,), {"device_serial": "通道一"}), "header-nonascii"),
    (("list_resources", (_SIGN,), {"device_serial": "SER\nBAD"}), "header-crlf"),
    (("list_resources", (_SIGN,), {"authorization_type": " "}), "blank-opttype"),
    (("list_resources", (_SIGN,), {"authorization_type": 1}), "nonstr-opttype"),
    (("list_resources", (_SIGN,), {"page_size": 21}), "page-size-over-max"),
    (("list_resources", (_SIGN,), {"page_size": True}), "page-size-bool"),
    (("list_resources", (_SIGN,), {"page_size": "20"}), "page-size-str"),
    (("list_resources", (_SIGN,), {"page_start": 0.5}), "page-start-float"),
    (("authorize_device", (_SIGN, " "), {}), "blank-serial"),
    (("authorize_device", (_SIGN, _SERIAL), {"state": "\t"}), "blank-optstate"),
    (("authorize_device", (_SIGN, _SERIAL), {"local_index": "1\x00"}), "header-nul"),
    (("relieve_device", (_B2_KEY, _SERIAL, None), {}), "missing-state"),
    (("relieve_device", (_B2_KEY, _SERIAL, " "), {}), "blank-state"),
    (("relieve_device", (" ", _SERIAL, "1"), {}), "blank-recipient"),
]


@KINDS
@pytest.mark.parametrize("step", [s for s, _ in _INVALID_STEPS], ids=[i for _, i in _INVALID_STEPS])
def test_invalid_input_is_rejected_before_auth(kind, step):
    """Source-limit validation raises locally: zero auth, zero business I/O."""
    wire = _Wire("domestic")
    with pytest.raises(ValueError):
        _drive(kind, wire, step)
    assert wire.auth_requests == 0 and wire.business_requests == []


@KINDS
def test_business_error_is_typed_and_never_leaks_sign(kind):
    """HTTP 200 + meta.code 10005 raises a typed error; the sign is masked."""
    wire = _Wire("domestic")
    wire.handler = lambda request, count: httpx.Response(
        200,
        json={
            "meta": {"code": 10005, "message": f"credential {_SIGN} rejected"},
            "data": {"echo": _SIGN, "marker": "raw-secret-marker"},
        },
    )
    with pytest.raises(ApiError) as excinfo:
        _drive(kind, wire, ("authorize_account", (_SIGN,), {}))
    exc = excinfo.value
    rendered = f"{exc!s} {exc!r}"
    assert str(exc.code) == "10005" and exc.http_status == 200
    assert exc.operation == "delegation.authorize_account"
    assert not isinstance(exc, AuthenticationError)
    assert _SIGN not in rendered and "raw-secret-marker" not in rendered
    assert "***" in rendered
    # Redaction is diagnostic-only; the wire still carried the verbatim sign.
    assert _form(wire.business_requests[0]) == {"sign": _SIGN}


@KINDS
def test_list_resources_retries_once_after_token_expiry(kind):
    """The single read-only op may refresh the token once and reissue."""
    wire = _Wire("domestic")
    wire.handler = lambda request, count: (
        _meta_error(10002, "token expired") if count == 1 else _meta_ok([])
    )
    (result,) = _drive(kind, wire, ("list_resources", (_SIGN,), {}))
    assert result.data == []
    assert wire.auth_requests == 2 and len(wire.business_requests) == 2
    first, second = wire.business_requests
    assert first.headers["accessToken"] == "TOKEN1"
    assert second.headers["accessToken"] == "TOKEN2"
    assert first.url.path == second.url.path == _LIST_PATH


@KINDS
@pytest.mark.parametrize(
    "step",
    [
        ("apply_code", (_OWNER_KEY,), {}),
        ("authorize_account", (_SIGN,), {}),
        ("authorize_device", (_SIGN, _SERIAL), {}),
        ("relieve_device", (_B2_KEY, _SERIAL, "1"), {}),
    ],
    ids=["apply_code", "authorize_account", "authorize_device", "relieve_device"],
)
def test_writes_are_never_replayed_after_refresh(kind, step):
    """A 10002 on a write surfaces immediately: one request, no replay."""
    wire = _Wire("domestic")
    wire.handler = lambda request, count: _meta_error(10002, "token expired")
    with pytest.raises(AuthenticationError) as excinfo:
        _drive(kind, wire, step)
    assert excinfo.value.invalid_token
    assert len(wire.business_requests) == 1 and wire.auth_requests == 1


@KINDS
def test_network_failure_raises_transport_error(kind):
    """Socket-level failure maps to TransportError, never a business code."""
    wire = _Wire("domestic")

    def boom(request, count):
        raise httpx.ConnectError("refused", request=wire.business_requests[-1])

    wire.handler = boom
    with pytest.raises(TransportError) as excinfo:
        _drive(kind, wire, ("apply_code", (_OWNER_KEY,), {}))
    assert excinfo.value.reason == "network"
    assert len(wire.business_requests) == 1


@KINDS
def test_missing_meta_fields_raise_protocol_error(kind):
    """A non-meta envelope violates the declared response schema."""
    wire = _Wire("domestic")
    wire.handler = lambda request, count: httpx.Response(200, json={"code": "200", "data": []})
    with pytest.raises(ProtocolError):
        _drive(kind, wire, ("list_resources", (_SIGN,), {}))


def test_sign_key_is_registered_for_redaction():
    """The errors.py patch masks sign= diagnostics without touching payloads."""
    assert "sign" in _SENSITIVE_KEYS
    assert redact("GET /x?sign=abc#1#2 HTTP/1.1") == "GET /x?sign=*** HTTP/1.1"
    assert "abc#1#2" not in redact("sign=abc#1#2")
