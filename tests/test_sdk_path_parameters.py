"""Regression tests for whole-segment ``{name}`` path parameters (P003 SDK side).

Contract (docs/plans/2026-11-15-component-paths.md), independent of the
current implementation state:

- ``Operation.path`` accepts placeholders only as complete ``{name}``
  segments where *name* is an ASCII identifier; repeats are allowed. All
  other brace shapes and static ``.``/``..`` segments — including
  percent-encoded spellings — are rejected at construction while the
  existing token_location/regions/read_only/policy checks keep working.
- ``request(..., path_params=...)`` keys must equal the template names
  exactly; non-mapping input, non-string keys/values, blank values, raw
  ``/``/``\\``, lone ``.``/``..``, C0/C1 controls and lone surrogates are
  ``ValueError`` before any provider or business traffic, and error
  text/chains never echo the supplied marker.
- Values are unencoded single segments encoded once on the wire; expected
  paths below are literal ``raw_path`` assertions written by hand, never
  produced by SDK rendering helpers.
- A single rendered path snapshot serves both attempts of a read-only
  10002 retry, raw/encoded values join the request redaction context, and
  public (``token_location="none"``) operations bind ``path_params`` with
  no provider at all.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    Operation,
    RegionConfig,
    TransportError,
)
from ezviz_unified.auth_models import Token

API_BASE = "https://open.ys7.com"
TEMPLATE = "/api/v3/otap/prop/{deviceSerial}/Video/1"
OK_BODY = {"code": "200", "msg": "ok", "data": {"marker": "path"}}

Handler = Callable[[httpx.Request], httpx.Response]

READ_PATH_OP = Operation("readPathOp", "GET", TEMPLATE, token_location="header", read_only=True)
WRITE_PATH_OP = Operation("writePathOp", "PUT", TEMPLATE, token_location="header", read_only=False)
PUBLIC_PATH_OP = Operation("publicPathOp", "GET", TEMPLATE, token_location="none", read_only=True)
FIXED_OP = Operation("fixedOp", "GET", "/api/lapp/timezone/list", token_location="none")


# ---------------------------------------------------------------------------
# Doubles and builders
# ---------------------------------------------------------------------------


class _CountingProvider:
    """Sync provider: returns Token objects and counts every touch."""

    def __init__(self, prefix: str = "TOKEN") -> None:
        self.can_refresh = True
        self.calls = 0
        self.invalidated: list[str | None] = []
        self._prefix = prefix

    def get_token(self) -> Token:
        self.calls += 1
        return Token(
            value=f"{self._prefix}-{self.calls}",
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
            value=f"{self._prefix}-{self.calls}",
            expires_at=2_000_000.0,
            refresh_at=1_000_000.0,
            api_base_url=API_BASE,
        )


class _GuardProvider:
    """Fails the moment a public/anonymous flow touches the provider."""

    can_refresh = True

    def get_token(self):
        raise AssertionError("public path operation must not call get_token")

    def invalidate(self, expected_value=None):
        raise AssertionError("public path operation must not call invalidate")


class _GuardAsyncProvider(_GuardProvider):
    async def get_token(self):  # type: ignore[override]
        raise AssertionError("public path operation must not call get_token")


def _ok(captured: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=OK_BODY)

    return handler


def _build(kind: str, handler: Handler, provider=None, anonymous: bool = False):
    """Real client on MockTransport; returns (client, provider)."""
    cls = Client if kind == "sync" else AsyncClient
    kwargs: dict[str, object] = {
        "region": RegionConfig.china(),
        "transport_factory": lambda origin: httpx.MockTransport(handler),
        # Deterministic clock aligned with the fixture token times
        # (refresh_at=1_000_000 < expires_at=2_000_000): token stays fresh
        # and valid without depending on the real system clock.
        "clock": lambda: 1_000_000.0,
    }
    if anonymous:
        kwargs["anonymous"] = True
    else:
        kwargs["token_provider"] = provider or (
            _CountingProvider() if kind == "sync" else _CountingAsyncProvider()
        )
    return cls(**kwargs), kwargs.get("token_provider")


def _close(kind: str, client):
    if kind == "sync":
        client.close()
    else:
        asyncio.run(client.aclose())


def _request(kind: str, client, operation: Operation, **kwargs):
    if kind == "sync":
        return client.request(operation, **kwargs)
    return asyncio.run(client.request(operation, **kwargs))


def _raw_path(request: httpx.Request) -> str:
    """Path portion of the wire bytes (query stripped) — the encoded view."""
    raw = request.url.raw_path.decode("ascii")
    return raw.split("?", 1)[0]


# ---------------------------------------------------------------------------
# Operation template parsing
# ---------------------------------------------------------------------------


def test_template_metadata_reports_names_and_keeps_verbatim_path():
    operation = Operation("meta", "GET", TEMPLATE)
    assert operation.path == TEMPLATE
    assert operation.path_params == ("deviceSerial",)
    assert "EVALDEV" not in operation.path


def test_repeated_placeholder_name_is_allowed():
    operation = Operation("dup", "GET", "/api/{seg}/x/{seg}")
    assert operation.path_params == ("seg", "seg")


@pytest.mark.parametrize(
    "path",
    [
        "/api/}x{/y",  # reversed braces
        "/api/prefix{x}/y",  # embedded placeholder
        "/api/{x}suffix/y",
        "/api/{{x}}/y",  # nested braces
        "/api/{x!r}/y",  # conversion
        "/api/{x:>10}/y",  # format spec
        "/api/{}/y",  # empty placeholder
        "/api/{x/y",  # unbalanced open
        "/api/x}/y",  # unbalanced close
        "/api/{na-me}/y",  # non-identifier
        "/api/{设备}/y",  # non-ASCII name
        "/api/./{x}/y",  # static dot segment
        "/api/../{x}/y",
        "/api/%2e/{x}/y",  # encoded static dot segment
        "/api/.%2E/{x}/y",
        "/api/%2E%2e/{x}/y",
        "/api/%2e%2E/{x}/y",
    ],
)
def test_malformed_templates_rejected(path):
    with pytest.raises(ValueError):
        Operation("bad", "GET", path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": " "},
        {"method": "get"},
        {"path": "https://evil.example"},
        {"path": "//evil.example"},
        {"path": "/p\nath"},
        {"path": "/p\\ath"},
        {"regions": set()},
        {"regions": {"unverified"}},
        {"token_location": "cookie"},
        {"read_only": 1},
        {"policy": object()},
    ],
)
def test_existing_operation_validation_still_holds(kwargs):
    values = {"name": "op", "method": "GET", "path": TEMPLATE, **kwargs}
    with pytest.raises((ValueError, TypeError)):
        Operation(**values)


def test_fixed_path_operations_unchanged():
    assert FIXED_OP.path_params is None
    captured: list[httpx.Request] = []
    client = _build("sync", _ok(captured), anonymous=True)[0]
    try:
        with pytest.raises(ValueError):
            client.request(FIXED_OP, path_params={"deviceSerial": "EV1"})
        response = client.request(FIXED_OP)
    finally:
        client.close()
    assert response.data == {"marker": "path"}
    assert len(captured) == 1
    assert _raw_path(captured[0]) == "/api/lapp/timezone/list"


# ---------------------------------------------------------------------------
# Wire rendering: literal raw_path expectations
# ---------------------------------------------------------------------------


VALID_SEGMENTS = [
    ("MiX_123", "MiX_123"),  # case preserved verbatim
    ("a%2Fb", "a%252Fb"),  # raw '%' is data, encoded exactly once
    (" A ", "%20A%20"),  # non-blank edge whitespace kept, then encoded
    ("设备A1", "%E8%AE%BE%E5%A4%87A1"),  # UTF-8 non-ASCII
    ("a+b", "a%2Bb"),
    ("a?b", "a%3Fb"),
    ("a#b", "a%23b"),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("value,expected", VALID_SEGMENTS)
def test_valid_segment_encodes_once(kind, value, expected):
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        response = _request(kind, client, READ_PATH_OP, path_params={"deviceSerial": value})
    finally:
        _close(kind, client)
    assert response.data == {"marker": "path"}
    assert len(captured) == 1
    assert provider.calls == 1
    request = captured[0]
    assert _raw_path(request) == f"/api/v3/otap/prop/{expected}/Video/1"
    assert "accesstoken" in {key.lower() for key in request.headers}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_repeated_placeholder_uses_one_key(kind):
    operation = Operation("dup", "GET", "/api/{seg}/x/{seg}", token_location="none")
    captured: list[httpx.Request] = []
    client = _build(kind, _ok(captured), anonymous=True)[0]
    try:
        _request(kind, client, operation, path_params={"seg": "v 1"})
    finally:
        _close(kind, client)
    assert len(captured) == 1
    assert _raw_path(captured[0]) == "/api/v%201/x/v%201"


# ---------------------------------------------------------------------------
# Pre-auth rejections: zero provider calls, zero business calls, no leaks
# ---------------------------------------------------------------------------

BAD_VALUES = [
    pytest.param({"deviceSerial": ""}, id="empty"),
    pytest.param({"deviceSerial": "   "}, id="blank"),
    pytest.param({"deviceSerial": "a/b"}, id="raw-slash"),
    pytest.param({"deviceSerial": "a\\b"}, id="backslash"),
    pytest.param({"deviceSerial": "."}, id="dot"),
    pytest.param({"deviceSerial": ".."}, id="dotdot"),
    pytest.param({"deviceSerial": "a\tb"}, id="c0-control"),
    pytest.param({"deviceSerial": "a\x85b"}, id="c1-control"),
    pytest.param({"deviceSerial": "a\x7fb"}, id="del"),
    pytest.param({"deviceSerial": "EV\udcff"}, id="lone-surrogate"),
]

BAD_MAPPINGS = [
    pytest.param("EV123", id="non-mapping"),
    pytest.param({}, id="missing-key"),
    pytest.param({"serial": "EV1"}, id="wrong-key"),
    pytest.param({"deviceSerial": "EV1", "rogue": "x"}, id="extra-key"),
    pytest.param({7: "EV1"}, id="non-string-key"),
    pytest.param({"deviceSerial": 7}, id="non-string-value"),
    *BAD_VALUES,
]

MARKER = "PRIVATE-EVAL-MARKER"


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("path_params", BAD_MAPPINGS)
def test_invalid_path_params_rejected_before_any_io(kind, path_params):
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        with pytest.raises(ValueError):
            _request(kind, client, READ_PATH_OP, path_params=path_params)
    finally:
        _close(kind, client)
    assert provider.calls == 0
    assert provider.invalidated == []
    assert captured == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_rejection_error_never_carries_the_value(kind):
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    try:
        with pytest.raises(ValueError) as excinfo:
            _request(
                kind,
                client,
                READ_PATH_OP,
                path_params={"deviceSerial": MARKER + "\udcff"},
            )
        error = excinfo.value
        assert MARKER not in str(error)
        assert MARKER not in repr(error)
        assert error.__cause__ is None
        assert error.__context__ is None
    finally:
        _close(kind, client)
    assert provider.calls == 0
    assert captured == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_reserved_and_region_gates_apply_to_templated_operations(kind):
    """accessToken smuggling and region mismatches fail before the wire."""
    captured: list[httpx.Request] = []
    client, provider = _build(kind, _ok(captured))
    overseas_only = Operation(
        "otherRegion",
        "GET",
        TEMPLATE,
        token_location="none",
        regions=frozenset({"overseas"}),
    )
    try:
        with pytest.raises(ValueError):
            _request(
                kind,
                client,
                READ_PATH_OP,
                path_params={"deviceSerial": "EV1"},
                params={"accessToken": "smuggled"},
            )
        with pytest.raises(ValueError):
            _request(
                kind,
                client,
                overseas_only,
                path_params={"deviceSerial": "EV1"},
            )
    finally:
        _close(kind, client)
    assert provider.calls == 0
    assert captured == []


# ---------------------------------------------------------------------------
# Retry snapshot, redaction, anonymous public operations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_read_only_retry_uses_frozen_path_snapshot(kind):
    """The second attempt carries the same rendered path even though the
    caller's Mapping was mutated between attempts; tokens differ per try."""
    responses = iter(
        [
            httpx.Response(200, json={"code": "10002", "msg": "expired"}),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    captured: list[httpx.Request] = []
    bound = {"deviceSerial": "FIRST"}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        bound["deviceSerial"] = "SECOND"  # mutate after each attempt
        return next(responses)

    client, provider = _build(kind, handler)
    try:
        response = _request(kind, client, READ_PATH_OP, path_params=bound)
    finally:
        _close(kind, client)
    assert response.data == {"marker": "path"}
    assert len(captured) == 2
    assert provider.calls == 2
    assert provider.invalidated == ["TOKEN-1"]
    for request in captured:
        assert _raw_path(request) == "/api/v3/otap/prop/FIRST/Video/1"
    # Different tokens on each attempt confirm a real retry happened.
    tokens = [r.headers["accessToken"] for r in captured]
    assert tokens == ["TOKEN-1", "TOKEN-2"]


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_write_operation_never_retries_and_still_sends_rendered_path(kind):
    responses = iter(
        [
            httpx.Response(200, json={"code": "10002", "msg": "expired"}),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return next(responses)

    client, provider = _build(kind, handler)
    try:
        with pytest.raises(AuthenticationError):
            _request(kind, client, WRITE_PATH_OP, path_params={"deviceSerial": "EV9"})
    finally:
        _close(kind, client)
    assert len(captured) == 1
    assert provider.calls == 1
    assert _raw_path(captured[0]) == "/api/v3/otap/prop/EV9/Video/1"


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_business_error_echoing_device_marker_is_redacted(kind):
    """A response that echoes the raw and encoded serial must surface an
    ApiError whose message/str/repr carries neither form."""
    raw = "EVALDEV001"
    encoded = "EVALDEV001"
    echo = f"device {raw} failed at segment {encoded}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "20018", "msg": echo})

    client, provider = _build(kind, handler)
    try:
        with pytest.raises(ApiError) as excinfo:
            _request(kind, client, READ_PATH_OP, path_params={"deviceSerial": raw})
        error = excinfo.value
        assert raw not in str(error)
        assert raw not in repr(error)
        assert raw not in (error.message or "")
    finally:
        _close(kind, client)
    assert provider.calls == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_encoded_value_also_redacted_from_error(kind):
    raw = "DEV ICE"  # encodes to DEV%20ICE
    encoded = "DEV%20ICE"
    echo = f"bad segment {raw} / {encoded}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "20018", "msg": echo})

    client, _ = _build(kind, handler)
    try:
        with pytest.raises(ApiError) as excinfo:
            _request(kind, client, READ_PATH_OP, path_params={"deviceSerial": raw})
        error = excinfo.value
        assert raw not in str(error) and encoded not in str(error)
        assert raw not in repr(error) and encoded not in repr(error)
    finally:
        _close(kind, client)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_httpx_logs_do_not_leak_path_marker(kind, caplog):
    raw = "EVLOGMARK1"
    captured: list[httpx.Request] = []
    client, _ = _build(kind, _ok(captured))
    try:
        with caplog.at_level(logging.DEBUG, logger="httpx"):
            _request(kind, client, READ_PATH_OP, path_params={"deviceSerial": raw})
    finally:
        _close(kind, client)
    for record in caplog.records:
        if record.name == "httpx":
            assert raw not in record.getMessage()


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_transport_failure_raises_clean_error_without_marker(kind):
    """A simulated transport failure must not chain the httpx exception
    (which embeds the full encoded URL) nor echo the serial."""

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, provider = _build(kind, unreachable)
    try:
        with pytest.raises(TransportError) as excinfo:
            _request(
                kind,
                client,
                READ_PATH_OP,
                path_params={"deviceSerial": "EVSECRET9"},
            )
        error = excinfo.value
        assert "EVSECRET9" not in str(error)
        assert "EVSECRET9" not in repr(error)
        assert "EV%20SECRET9" not in str(error)
        assert error.__context__ is None
        assert error.__cause__ is None
    finally:
        _close(kind, client)
    assert provider.calls == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_public_operation_binds_path_params_without_any_provider(kind):
    captured: list[httpx.Request] = []
    guard = _GuardProvider() if kind == "sync" else _GuardAsyncProvider()
    client, _ = _build(kind, _ok(captured), provider=guard)
    try:
        response = _request(kind, client, PUBLIC_PATH_OP, path_params={"deviceSerial": "EV PUB"})
    finally:
        _close(kind, client)
    assert response.data == {"marker": "path"}
    assert len(captured) == 1
    request = captured[0]
    assert _raw_path(request) == "/api/v3/otap/prop/EV%20PUB/Video/1"
    assert "accesstoken" not in {key.lower() for key in request.headers}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_anonymous_client_binds_path_params(kind):
    captured: list[httpx.Request] = []
    client, _ = _build(kind, _ok(captured), anonymous=True)
    try:
        response = _request(kind, client, PUBLIC_PATH_OP, path_params={"deviceSerial": "EV1"})
    finally:
        _close(kind, client)
    assert response.data == {"marker": "path"}
    assert len(captured) == 1
    assert _raw_path(captured[0]) == "/api/v3/otap/prop/EV1/Video/1"
