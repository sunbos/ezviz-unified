"""Regression tests for hardened timeout and provider-contract validation.

* Oversized integer timeouts raise the fixed ``ValueError`` instead of
  ``OverflowError`` on all four public constructors.
* Both clients reject external providers whose ``invalidate`` is a
  coroutine function, at construction time and without probing it.
* ``AsyncClient`` raises a fixed ``TypeError`` when ``get_token`` returns
  a non-awaitable, while a plain function returning ``asyncio.Future``
  remains a valid provider.

All fixtures are synthetic, built on ``httpx.MockTransport`` and real
client construction (no ``__new__`` shortcuts, no signature guessing).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ezviz_unified import AsyncClient, Client, RegionConfig, Token
from ezviz_unified.transport import AsyncTransport, SyncTransport

_TIMEOUT_MESSAGE = "timeout must be a finite positive number of seconds"
_AWAITABLE_MESSAGE = "async token_provider get_token must return an awaitable"


def _token(value: str) -> Token:
    return Token(value, 2000, 1940, "https://open.ys7.com")


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda timeout: Client("K", "S", region=RegionConfig.china(), timeout=timeout),
            id="client",
        ),
        pytest.param(
            lambda timeout: AsyncClient("K", "S", region=RegionConfig.china(), timeout=timeout),
            id="async_client",
        ),
        pytest.param(
            lambda timeout: SyncTransport("https://open.ys7.com", timeout=timeout),
            id="sync_transport",
        ),
        pytest.param(
            lambda timeout: AsyncTransport("https://open.ys7.com", timeout=timeout),
            id="async_transport",
        ),
    ],
)
def test_oversized_timeout_raises_fixed_value_error(build):
    with pytest.raises(ValueError) as caught:
        build(10**400)
    assert not isinstance(caught.value, OverflowError)
    assert str(caught.value) == _TIMEOUT_MESSAGE


class _SyncGetAsyncInvalidateProvider:
    """Structural sync provider whose ``invalidate`` is wrongly a coroutine."""

    can_refresh = True

    def __init__(self) -> None:
        self.get_calls = 0
        self.invalidate_calls = 0

    def get_token(self):
        self.get_calls += 1
        return _token("T")

    async def invalidate(self, expected_value=None):
        self.invalidate_calls += 1


class _AsyncGetAsyncInvalidateProvider(_SyncGetAsyncInvalidateProvider):
    async def get_token(self):
        self.get_calls += 1
        return _token("T")


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_coroutine_invalidate_provider_is_rejected_at_construction(kind):
    provider = (
        _SyncGetAsyncInvalidateProvider() if kind == "sync" else _AsyncGetAsyncInvalidateProvider()
    )
    factory_calls = []
    cls = Client if kind == "sync" else AsyncClient
    with pytest.raises(TypeError):
        cls(
            region=RegionConfig.china(),
            token_provider=provider,
            clock=lambda: 1000,
            transport_factory=lambda origin: factory_calls.append(origin),
        )
    assert provider.get_calls == 0
    assert provider.invalidate_calls == 0
    assert factory_calls == []


def test_async_client_rejects_non_awaitable_get_token_result():
    class Provider:
        can_refresh = False

        def __init__(self) -> None:
            self.get_calls = 0
            self.invalidated = []

        def get_token(self):
            self.get_calls += 1
            return _token("PRIVATE")  # a plain Token is not awaitable

        def invalidate(self, expected_value=None):
            self.invalidated.append(expected_value)

    provider = Provider()
    factory_calls = []
    requests = []

    def business(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "200", "data": []})

    def factory(origin):
        factory_calls.append(origin)
        return httpx.MockTransport(business)

    async def scenario():
        client = AsyncClient(
            region=RegionConfig.china(),
            token_provider=provider,
            clock=lambda: 1000,
            transport_factory=factory,
        )
        try:
            assert provider.get_calls == 0
            with pytest.raises(TypeError) as caught:
                await client.devices.list()
            assert str(caught.value) == _AWAITABLE_MESSAGE
            assert "PRIVATE" not in str(caught.value)
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert provider.get_calls == 1
    assert factory_calls == []
    assert requests == []


def test_async_provider_function_returning_future_still_works():
    class Provider:
        can_refresh = False

        def __init__(self) -> None:
            self.get_calls = 0
            self.invalidated = []

        def get_token(self):
            self.get_calls += 1
            future = asyncio.get_running_loop().create_future()
            future.set_result(_token("PRIVATE"))
            return future

        def invalidate(self, expected_value=None):
            self.invalidated.append(expected_value)

    provider = Provider()
    requests = []

    def business(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "200", "data": []})

    async def scenario():
        client = AsyncClient(
            region=RegionConfig.china(),
            token_provider=provider,
            clock=lambda: 1000,
            transport_factory=lambda origin: httpx.MockTransport(business),
        )
        try:
            assert provider.get_calls == 0  # construction stays lazy
            assert (await client.devices.list()).data == []
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert provider.get_calls == 1
    assert len(requests) == 1
