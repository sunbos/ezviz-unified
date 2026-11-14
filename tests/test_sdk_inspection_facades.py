"""Regression: inspection helpers must borrow the client's existing facades."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from ezviz_unified import ApiResponse, AsyncClient, Client, RegionConfig
from ezviz_unified.inspection import inspect_device, inspect_device_async

EXPECTED = [
    "devices.get",
    "devices.channels",
    "devices.status",
    "diagnostics.capabilities",
    "diagnostics.version",
]
CHANNEL_OPS = {"devices.status", "diagnostics.capabilities"}


def _refuse(request):
    raise AssertionError(f"unexpected HTTP request: {request.method} {request.url}")


def _client_kwargs():
    return dict(
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(_refuse),
        transport_factory=lambda origin: httpx.MockTransport(_refuse),
    )


def _facades(async_mode):
    calls = []
    responses = {name: ApiResponse(200, "200", data={"operation": name}) for name in EXPECTED}

    def make(name):
        async def amethod(serial, **kwargs):
            calls.append((name, serial, kwargs))
            return responses[name]

        def method(serial, **kwargs):
            calls.append((name, serial, kwargs))
            return responses[name]

        return amethod if async_mode else method

    devices = SimpleNamespace(
        get=make(EXPECTED[0]), channels=make(EXPECTED[1]), status=make(EXPECTED[2])
    )
    diagnostics = SimpleNamespace(capabilities=make(EXPECTED[3]), version=make(EXPECTED[4]))
    return devices, diagnostics, calls, responses


def _check(calls, responses, result):
    assert [name for name, _, _ in calls] == EXPECTED
    for name, serial, kwargs in calls:
        assert serial == "DEV123"
        if name in CHANNEL_OPS:
            assert kwargs.get("channel") == 7
        else:
            assert "channel" not in kwargs
    assert [row.operation for row in result.results] == EXPECTED
    for row in result.results:
        assert row.response is responses[row.operation]


def _run_sync():
    devices, diagnostics, calls, responses = _facades(False)
    client = Client("test-key", "test-secret", **_client_kwargs())
    try:
        client.devices, client.diagnostics = devices, diagnostics
        result = inspect_device(client, "DEV123", channel=7)
        _check(calls, responses, result)
    finally:
        client.close()


async def _run_async():
    devices, diagnostics, calls, responses = _facades(True)
    client = AsyncClient("test-key", "test-secret", **_client_kwargs())
    try:
        client.devices, client.diagnostics = devices, diagnostics
        result = await inspect_device_async(client, "DEV123", channel=7)
        _check(calls, responses, result)
    finally:
        await client.aclose()


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_inspect_device_borrows_client_facades(async_mode):
    if async_mode:
        asyncio.run(_run_async())
    else:
        _run_sync()
