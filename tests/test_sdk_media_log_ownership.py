"""Regression tests: SDK-owned vs native request markers in httpx INFO logs.

Ported from successful local synthetic probes; all traffic stays in-process on
httpx.MockTransport (the .invalid hosts are never resolved).
"""

import asyncio
import logging

import httpx
import pytest

from ezviz_unified import ResponsePolicy
from ezviz_unified.transport import AsyncTransport, SyncTransport

BASE_URL = "https://unit.invalid"
TIMEOUT = 5
POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    success_values=(200,),
    redact_media_urls=True,
)


def _reply(marker: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"meta": {"code": 200, "message": "ok"}, "data": {}},
        extensions={"reason_phrase": f"rtmps://media.invalid/{marker}".encode()},
    )


def _httpx_info(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    ]


@pytest.mark.parametrize("sync_mode", [False, True], ids=["async", "sync"])
def test_owned_request_marker_hidden_native_visible(caplog, sync_mode):
    """Native GET to the same URL string keeps its marker; the forwarded
    SDK-owned request's marker is hidden in httpx INFO logs."""
    caplog.set_level(logging.INFO, logger="httpx")
    calls = []

    def origin_for(owned):
        def origin(incoming):
            marker = "FORWARDED_PRIVATE" if incoming is owned else "NATIVE_PUBLIC"
            calls.append(marker)
            return _reply(marker)

        return httpx.MockTransport(origin)

    async def arun() -> None:
        async def business(req):
            async with httpx.AsyncClient(transport=origin_for(req)) as raw:
                await asyncio.create_task(raw.get(str(req.url)))  # new URL object
                return await asyncio.create_task(raw.send(req))  # owned request

        async with AsyncTransport(BASE_URL, transport=httpx.MockTransport(business)) as sdk:
            await sdk.request("GET", "/same-path", policy=POLICY)

    def srun() -> None:
        def business(req):
            with httpx.Client(transport=origin_for(req)) as raw:
                raw.get(str(req.url))
                return raw.send(req)

        with SyncTransport(BASE_URL, transport=httpx.MockTransport(business)) as sdk:
            sdk.request("GET", "/same-path", policy=POLICY)

    if sync_mode:
        srun()
    else:
        asyncio.run(arun())

    assert sorted(calls) == ["FORWARDED_PRIVATE", "NATIVE_PUBLIC"]
    assert any("NATIVE_PUBLIC" in m for m in _httpx_info(caplog))
    assert "FORWARDED_PRIVATE" not in caplog.text


def test_inherited_task_after_cancel_keeps_native_marker(caplog):
    """A task spawned inside the handler inherits context but owns no request:
    after the owning request is cancelled and the transport closed, its httpx
    INFO reason marker must remain visible."""
    caplog.set_level(logging.INFO, logger="httpx")

    async def run() -> None:
        entered = asyncio.Event()
        release = asyncio.get_running_loop().create_future()
        child = None

        async def business(req):
            nonlocal child

            async def later() -> None:
                entered.set()
                await release
                mock = httpx.MockTransport(lambda r: _reply("OUTSIDE_CANARY"))
                async with httpx.AsyncClient(transport=mock) as raw:
                    await raw.get(str(req.url))

            child = asyncio.create_task(later())
            await asyncio.Future()  # hang until the owning request is cancelled

        async with AsyncTransport(BASE_URL, transport=httpx.MockTransport(business)) as sdk:
            task = asyncio.create_task(sdk.request("GET", "/same-path", policy=POLICY))
            await asyncio.wait_for(entered.wait(), timeout=TIMEOUT)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        release.set_result(None)
        await asyncio.wait_for(child, timeout=TIMEOUT)

    asyncio.run(run())
    assert any("OUTSIDE_CANARY" in m for m in _httpx_info(caplog))
