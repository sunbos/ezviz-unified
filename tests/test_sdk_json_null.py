"""Regression tests for the explicit ``JSON_NULL`` root body marker.

Contract (docs/plans/2026-11-15-component-paths.md), independent of the
current implementation state:

- ``JSON_NULL`` is importable from ``ezviz_unified`` and marks a literal
  root-level ``null`` JSON body — four bytes ``b"null"`` with a default
  ``Content-Type: application/json``. ``json=None`` keeps the historical
  "no body" meaning: zero bytes and no Content-Type is added.
- ``header``/``query``/``none`` token locations accept ``JSON_NULL``;
  ``form`` and ``json`` locations reject it before authentication because
  a managed token cannot be injected into a root ``null``. Combining
  ``JSON_NULL`` with ``data`` is likewise rejected pre-auth.
- A caller-supplied Content-Type — in any header case — is never
  overridden, and the caller's headers mapping is never mutated.
- ``0``, ``False``, ``""``, ``[]`` and ``{}`` remain real JSON bodies;
  nested ``None`` still serializes to ``null`` inside a document.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx
import pytest

from ezviz_unified import (
    JSON_NULL,
    AsyncClient,
    Client,
    Operation,
    RegionConfig,
)
from ezviz_unified.auth_models import Token

API_BASE = "https://open.ys7.com"
PATH = "/api/synthetic/null-root"
OK_BODY = {"code": "200", "msg": "ok", "data": {"marker": "null"}}

Handler = Callable[[httpx.Request], httpx.Response]


def _op(token_location: str, read_only: bool = False) -> Operation:
    return Operation(
        f"nullRoot{token_location.title()}",
        "PUT",
        PATH,
        token_location=token_location,
        read_only=read_only,
    )


HEADER_OP = _op("header")
QUERY_OP = _op("query")
NONE_OP = _op("none")
FORM_OP = _op("form")
JSON_OP = _op("json")


# ---------------------------------------------------------------------------
# Doubles and builders (same shape as the path-parameter suite)
# ---------------------------------------------------------------------------


class _CountingProvider:
    def __init__(self) -> None:
        self.can_refresh = True
        self.calls = 0
        self.invalidated: list[str | None] = []

    def get_token(self) -> Token:
        self.calls += 1
        return Token(
            value=f"TOKEN-{self.calls}",
            expires_at=2_000_000.0,
            refresh_at=1_000_000.0,
            api_base_url=API_BASE,
        )

    def invalidate(self, expected_value: str | None = None) -> None:
        self.invalidated.append(expected_value)


class _CountingAsyncProvider(_CountingProvider):
    async def get_token(self) -> Token:  # type: ignore[override]
        self.calls += 1
        return Token(
            value=f"TOKEN-{self.calls}",
            expires_at=2_000_000.0,
            refresh_at=1_000_000.0,
            api_base_url=API_BASE,
        )


def _ok(captured: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=OK_BODY)

    return handler


def _build(kind: str, handler: Handler, anonymous: bool = False):
    cls = Client if kind == "sync" else AsyncClient
    kwargs: dict[str, object] = {
        "region": RegionConfig.china(),
        "transport_factory": lambda origin: httpx.MockTransport(handler),
        # Deterministic clock aligned with the fixture token times
        # (refresh_at=1_000_000 < expires_at=2_000_000): token stays fresh
        # and valid without depending on the real system clock.
        "clock": lambda: 1_000_000.0,
    }
    provider = None
    if anonymous:
        kwargs["anonymous"] = True
    else:
        provider = _CountingProvider() if kind == "sync" else _CountingAsyncProvider()
        kwargs["token_provider"] = provider
    return cls(**kwargs), provider


def _close(kind: str, client) -> None:
    if kind == "sync":
        client.close()
    else:
        asyncio.run(client.aclose())


def _request(kind: str, client, operation: Operation, **kwargs):
    if kind == "sync":
        return client.request(operation, **kwargs)
    return asyncio.run(client.request(operation, **kwargs))


def _content_type(request: httpx.Request) -> str | None:
    return request.headers.get("content-type")


# ---------------------------------------------------------------------------
# Marker identity
# ---------------------------------------------------------------------------


def test_json_null_is_a_public_singleton_marker():
    assert repr(JSON_NULL) == "JSON_NULL"
    assert JSON_NULL is JSON_NULL  # singleton, identity-checked downstream


# ---------------------------------------------------------------------------
# Allowed token locations send a literal b"null" body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "operation",
    [HEADER_OP, QUERY_OP, NONE_OP],
    ids=["header", "query", "none"],
)
def test_json_null_body_on_allowed_token_locations(kind, operation):
    captured: list[httpx.Request] = []
    anonymous = operation.token_location == "none"
    client, provider = _build(kind, _ok(captured), anonymous=anonymous)
    try:
        response = _request(kind, client, operation, json=JSON_NULL)
    finally:
        _close(kind, client)
    assert response.data == {"marker": "null"}
    assert len(captured) == 1
    request = captured[0]
    assert request.content == b"null"
    assert _content_type(request) == "application/json"
    if anonymous:
        assert provider is None  # no provider exists in anonymous mode
    else:
        assert provider.calls == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("operation", [FORM_OP, JSON_OP], ids=["form", "json"])
def test_json_null_rejected_before_auth_for_managed_body_locations(kind, operation):
    """form/json token locations must reject JSON_NULL with zero provider
    and zero business traffic: the token has nowhere to go."""
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        with pytest.raises(ValueError):
            _request(kind, client, operation, json=JSON_NULL)
    finally:
        _close(kind, client)
    assert provider.calls == 0
    assert captured == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_json_null_and_data_are_mutually_exclusive(kind):
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        with pytest.raises(ValueError):
            _request(
                kind,
                client,
                HEADER_OP,
                json=JSON_NULL,
                data={"field": "v"},
            )
    finally:
        _close(kind, client)
    assert provider.calls == 0
    assert captured == []


# ---------------------------------------------------------------------------
# Body-root table: exact wire bytes and Content-Type behaviour
# ---------------------------------------------------------------------------


# (json arg, expected content bytes, expected auto Content-Type)
BODY_ROOTS = [
    pytest.param(JSON_NULL, b"null", "application/json", id="marker-null"),
    pytest.param(None, b"", None, id="none-no-body"),
    pytest.param(0, b"0", "application/json", id="zero"),
    pytest.param(False, b"false", "application/json", id="false"),
    pytest.param("", b'""', "application/json", id="empty-string"),
    pytest.param([], b"[]", "application/json", id="empty-array"),
    pytest.param({}, b"{}", "application/json", id="empty-object"),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("body,expected,content_type", BODY_ROOTS)
def test_body_root_wire_bytes(kind, body, expected, content_type):
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        _request(kind, client, HEADER_OP, json=body)
    finally:
        _close(kind, client)
    assert provider.calls == 1
    assert len(captured) == 1
    request = captured[0]
    assert request.content == expected
    assert _content_type(request) == content_type


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_nested_none_still_serializes_as_json_null(kind):
    """Plain ``None`` inside a document is ordinary JSON ``null``; only the
    root position needs the marker."""
    captured: list[httpx.Request] = []
    client, _ = _build(kind, _ok(captured))
    try:
        _request(
            kind,
            client,
            HEADER_OP,
            json={"outer": None, "list": [1, None]},
        )
    finally:
        _close(kind, client)
    assert len(captured) == 1
    assert captured[0].content == b'{"outer":null,"list":[1,null]}'


# ---------------------------------------------------------------------------
# Caller Content-Type wins in any case; caller headers are never mutated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("header_name", ["Content-Type", "content-type", "CONTENT-TYPE"])
def test_explicit_content_type_is_never_overridden(kind, header_name):
    captured: list[httpx.Request] = []
    headers = {header_name: "application/vnd.eval+json", "X-Trace": "t1"}
    snapshot = dict(headers)
    client, _ = _build(kind, _ok(captured))
    try:
        _request(kind, client, HEADER_OP, json=JSON_NULL, headers=headers)
    finally:
        _close(kind, client)
    assert len(captured) == 1
    request = captured[0]
    assert request.content == b"null"
    assert _content_type(request) == "application/vnd.eval+json"
    assert headers == snapshot  # caller mapping untouched


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_json_null_adds_content_type_without_touching_caller_headers(kind):
    captured: list[httpx.Request] = []
    headers = {"X-Trace": "t2"}
    client, _ = _build(kind, _ok(captured))
    try:
        _request(kind, client, HEADER_OP, json=JSON_NULL, headers=headers)
    finally:
        _close(kind, client)
    assert _content_type(captured[0]) == "application/json"
    assert headers == {"X-Trace": "t2"}


# ---------------------------------------------------------------------------
# Non-null json values on managed body locations keep prior rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_form_location_still_rejects_any_json_body(kind):
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        with pytest.raises(ValueError):
            _request(kind, client, FORM_OP, json={"k": 1})
    finally:
        _close(kind, client)
    assert provider.calls == 0
    assert captured == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_json_location_still_requires_object_body_and_injects_token(kind):
    """A non-mapping json body on a ``json`` token location is rejected;
    a mapping body gets the managed accessToken field."""
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        with pytest.raises(ValueError):
            _request(kind, client, JSON_OP, json=[1, 2])
        response = _request(kind, client, JSON_OP, json={"k": 1})
    finally:
        _close(kind, client)
    assert response.data == {"marker": "null"}
    assert provider.calls == 1
    assert len(captured) == 1
    assert captured[0].content == b'{"k":1,"accessToken":"TOKEN-1"}'
    assert _content_type(captured[0]) == "application/json"
