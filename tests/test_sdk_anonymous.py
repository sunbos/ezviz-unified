"""Spec-driven tests for anonymous mode and public (``token_location="none"``) operations.

Covers the 2026-11-15 overseas plan rules:
- a public operation performs exactly one business request and never injects
  ``accessToken`` into headers, query, form, or JSON payloads;
- a public operation never calls the token provider, even on authenticated
  clients, and a business ``10002`` raises ``AuthenticationError`` unretried;
- ``anonymous=True`` forbids credentials/token_provider/auth_transport,
  requires a real bool plus an explicit ``RegionConfig.api_base_url``, and
  fails authenticated operations with ``ValueError`` before any network I/O;
- authenticated clients calling public operations still need ``api_base_url``
  and must fail before touching the token provider when it is missing;
- reserved parameter keys stay rejected, closed clients refuse requests with
  ``TransportError(reason="closed")``, and async cancel/``aclose`` behave
  against a real blocking ``MockTransport``.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx
import pytest

from ezviz_unified import (
    AsyncClient,
    AuthenticationError,
    Client,
    Operation,
    RegionConfig,
    TransportError,
)

API_BASE = "https://isgpopen.ezvizlife.com"
PUBLIC_PATH = "/api/lapp/timezone/list"

OK_BODY = {"code": "200", "msg": "ok", "data": {"marker": "public"}}
EXPIRED_BODY = {"code": "10002", "msg": "accessToken expired", "data": None}
EXPECTED_DATA = {"marker": "public"}

PUBLIC_LIST = Operation(
    "timezoneList",
    "POST",
    PUBLIC_PATH,
    token_location="none",
    read_only=True,
    regions=frozenset({"overseas"}),
)
AUTH_SECURE = Operation(
    "secureOperation",
    "POST",
    "/api/lapp/device/secure",
    token_location="form",
    regions=frozenset({"overseas"}),
)

Handler = Callable[[httpx.Request], httpx.Response]
WAIT_TIMEOUT = 5


def _region() -> RegionConfig:
    return RegionConfig.overseas(api_base_url=API_BASE)


def _capture(captured: list) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=OK_BODY)

    return handler


def _forbidden(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError("no network request was expected")


def _sync_client(handler, **kwargs) -> Client:
    return Client(
        region=_region(),
        transport_factory=lambda origin: httpx.MockTransport(handler),
        **kwargs,
    )


def _async_client(handler, **kwargs) -> AsyncClient:
    return AsyncClient(
        region=_region(),
        transport_factory=lambda origin: httpx.MockTransport(handler),
        **kwargs,
    )


class _GuardSyncProvider:
    """Provider double that fails the test if the SDK touches it."""

    can_refresh = True

    def get_token(self):
        raise AssertionError("public operations must not call get_token")

    def invalidate(self, expected_value=None):
        raise AssertionError("public operations must not call invalidate")


class _GuardAsyncProvider:
    """Async provider double; only ``get_token`` is a coroutine."""

    can_refresh = True

    async def get_token(self):
        raise AssertionError("public operations must not call get_token")

    def invalidate(self, expected_value=None):
        raise AssertionError("public operations must not call invalidate")


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_public_request_sends_single_tokenless_call(kind):
    captured = []
    if kind == "sync":
        client = _sync_client(_capture(captured), anonymous=True)
        try:
            response = client.request(PUBLIC_LIST)
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_capture(captured), anonymous=True)
            try:
                return await client.request(PUBLIC_LIST)
            finally:
                await client.aclose()

        response = asyncio.run(run())
    assert response.data == EXPECTED_DATA
    assert len(captured) == 1
    request = captured[0]
    assert request.url.path == PUBLIC_PATH
    header_names = {key.lower() for key in request.headers}
    query_names = {key.lower() for key in request.url.params.keys()}
    payload = request.content.decode("utf-8", "replace").lower()
    assert "accesstoken" not in header_names
    assert "accesstoken" not in query_names
    assert "accesstoken" not in payload


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_public_request_never_touches_token_provider(kind):
    captured = []
    provider = _GuardSyncProvider() if kind == "sync" else _GuardAsyncProvider()
    if kind == "sync":
        client = _sync_client(_capture(captured), token_provider=provider)
        try:
            response = client.request(PUBLIC_LIST)
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_capture(captured), token_provider=provider)
            try:
                return await client.request(PUBLIC_LIST)
            finally:
                await client.aclose()

        response = asyncio.run(run())
    assert response.data == EXPECTED_DATA
    assert len(captured) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_public_10002_raises_authentication_error_without_replay(kind):
    captured = []

    def expired(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=EXPIRED_BODY)

    if kind == "sync":
        client = _sync_client(expired, anonymous=True)
        try:
            with pytest.raises(AuthenticationError):
                client.request(PUBLIC_LIST)
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(expired, anonymous=True)
            try:
                with pytest.raises(AuthenticationError):
                    await client.request(PUBLIC_LIST)
            finally:
                await client.aclose()

        asyncio.run(run())
    assert len(captured) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_anonymous_rejects_authenticated_operation_before_network(kind):
    captured = []
    if kind == "sync":
        client = _sync_client(_capture(captured), anonymous=True)
        try:
            with pytest.raises(ValueError):
                client.request(AUTH_SECURE)
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_capture(captured), anonymous=True)
            try:
                with pytest.raises(ValueError):
                    await client.request(AUTH_SECURE)
            finally:
                await client.aclose()

        asyncio.run(run())
    assert captured == []


@pytest.mark.parametrize(
    "extra",
    [
        {"app_key": "app-key", "app_secret": "app-secret"},
        {"token_provider": _GuardSyncProvider()},
        {"auth_transport": httpx.MockTransport(_forbidden)},
    ],
    ids=["credentials", "token_provider", "auth_transport"],
)
@pytest.mark.parametrize("client_cls", [Client, AsyncClient])
def test_anonymous_rejects_auth_mixins(client_cls, extra):
    with pytest.raises(ValueError):
        client_cls(
            region=_region(),
            anonymous=True,
            transport_factory=lambda origin: httpx.MockTransport(_forbidden),
            **extra,
        )


@pytest.mark.parametrize("flag", ["yes", 1, object()], ids=["str", "int", "object"])
@pytest.mark.parametrize("client_cls", [Client, AsyncClient])
def test_anonymous_flag_must_be_a_real_bool(client_cls, flag):
    with pytest.raises(ValueError):
        client_cls(
            region=_region(),
            anonymous=flag,
            transport_factory=lambda origin: httpx.MockTransport(_forbidden),
        )


@pytest.mark.parametrize("client_cls", [Client, AsyncClient])
def test_anonymous_requires_explicit_api_base_url(client_cls):
    with pytest.raises(ValueError):
        client_cls(
            region=RegionConfig.overseas(),
            anonymous=True,
            transport_factory=lambda origin: httpx.MockTransport(_forbidden),
        )


@pytest.mark.parametrize("client_cls", [Client, AsyncClient])
def test_default_anonymous_false_still_requires_credentials(client_cls):
    with pytest.raises(ValueError):
        client_cls(
            region=_region(),
            transport_factory=lambda origin: httpx.MockTransport(_forbidden),
        )


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_authenticated_public_request_without_api_base_fails_before_provider(kind):
    provider = _GuardSyncProvider() if kind == "sync" else _GuardAsyncProvider()
    if kind == "sync":
        client = Client(
            region=RegionConfig.overseas(),
            token_provider=provider,
            transport_factory=lambda origin: httpx.MockTransport(_forbidden),
        )
        try:
            with pytest.raises(ValueError):
                client.request(PUBLIC_LIST)
        finally:
            client.close()
    else:

        async def run():
            client = AsyncClient(
                region=RegionConfig.overseas(),
                token_provider=provider,
                transport_factory=lambda origin: httpx.MockTransport(_forbidden),
            )
            try:
                with pytest.raises(ValueError):
                    await client.request(PUBLIC_LIST)
            finally:
                await client.aclose()

        asyncio.run(run())


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_public_request_rejects_reserved_access_token_param(kind):
    captured = []
    if kind == "sync":
        client = _sync_client(_capture(captured), anonymous=True)
        try:
            with pytest.raises(ValueError):
                client.request(PUBLIC_LIST, params={"accessToken": "injected"})
        finally:
            client.close()
    else:

        async def run():
            client = _async_client(_capture(captured), anonymous=True)
            try:
                with pytest.raises(ValueError):
                    await client.request(PUBLIC_LIST, params={"accessToken": "injected"})
            finally:
                await client.aclose()

        asyncio.run(run())
    assert captured == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_closed_client_rejects_public_request(kind):
    captured = []

    async def run_async():
        client = _async_client(_capture(captured), anonymous=True)
        await client.aclose()
        with pytest.raises(TransportError) as excinfo:
            await client.request(PUBLIC_LIST)
        return excinfo.value

    if kind == "sync":
        client = _sync_client(_capture(captured), anonymous=True)
        client.close()
        with pytest.raises(TransportError) as excinfo:
            client.request(PUBLIC_LIST)
        error = excinfo.value
    else:
        error = asyncio.run(run_async())
    assert getattr(error, "reason", None) == "closed"
    assert captured == []


def test_async_cancelled_public_request_propagates_and_aclose_completes():
    captured = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=WAIT_TIMEOUT)
        return httpx.Response(200, json=OK_BODY)

    async def run():
        client = _async_client(handler, anonymous=True)
        try:
            task = asyncio.create_task(client.request(PUBLIC_LIST))
            await asyncio.wait_for(entered.wait(), timeout=WAIT_TIMEOUT)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        await asyncio.wait_for(client.aclose(), timeout=WAIT_TIMEOUT)

    asyncio.run(run())
    assert len(captured) == 1


def test_async_aclose_waits_for_in_flight_public_request():
    captured = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=WAIT_TIMEOUT)
        return httpx.Response(200, json=OK_BODY)

    async def run():
        client = _async_client(handler, anonymous=True)
        request_task = asyncio.create_task(client.request(PUBLIC_LIST))
        await asyncio.wait_for(entered.wait(), timeout=WAIT_TIMEOUT)
        close_task = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        assert not close_task.done()
        release.set()
        response = await asyncio.wait_for(request_task, timeout=WAIT_TIMEOUT)
        await asyncio.wait_for(close_task, timeout=WAIT_TIMEOUT)
        return response

    response = asyncio.run(run())
    assert response.data == EXPECTED_DATA
    assert len(captured) == 1
