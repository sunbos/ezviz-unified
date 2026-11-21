"""Run the bounded domestic read verification using explicitly provided transports."""

from __future__ import annotations

import asyncio

from ezviz_unified import (
    AsyncClient,
    AsyncStaticTokenProvider,
    AsyncTokenManager,
    Client,
    RegionConfig,
    StaticTokenProvider,
    TokenManager,
)
from ezviz_unified.settings import _check_serial
from ezviz_unified.vod import _header

from ._cli_transport import _AsyncGuardedTransport, _SyncGuardedTransport
from ._detection_read_guard import DetectionReadGuard

_ORIGIN = "https://open.ys7.com"
_PROFILES = (
    ("scene_switch", "get_intelligent_detection_switch", "int_enable"),
    ("alarm_mode", "get_alarm_detection_mode", "int_mode"),
    ("advanced_type", "get_advanced_alarm_detection_type", "int_mode"),
)


def _factory(guard, gate, factory, wrapper):
    def build(origin):
        if guard.denied or origin != _ORIGIN:
            guard.stop()
            raise ValueError("unexpected transport origin")
        return wrapper(gate, factory())

    return build


def _select_device(response):
    if response.operation != "devices.list" or response.code != "200":
        raise ValueError("unexpected list response")
    rows = response.data
    if not isinstance(rows, list):
        raise ValueError("invalid device list")
    if not rows:
        return None
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("invalid device list")
    serial = _header("device_serial", rows[0].get("deviceSerial"))
    return _check_serial(serial, domestic=True)


def _check_response(response, method_name, shape):
    if response.operation != "settings." + method_name or response.code != "200":
        raise ValueError("unexpected response")
    data = response.data
    if not isinstance(data, dict):
        raise ValueError("unexpected response")
    if shape == "int_enable":
        if type(data.get("enable")) is not int:
            raise ValueError("unexpected response")
        return
    if shape != "int_mode":
        raise ValueError("unexpected response")
    value_info = data.get("valueInfo")
    if not isinstance(value_info, dict) or type(value_info.get("type")) is not int:
        raise ValueError("unexpected response")


def _sync_phase(app_key, app_secret, factory, guard, receipt):
    mode = "sync"
    region = RegionConfig.china()
    gate = guard.for_mode(mode)
    with TokenManager(
        app_key,
        app_secret,
        region=region,
        transport=_SyncGuardedTransport(gate, factory()),
        timeout=10,
        trust_env=False,
    ) as manager:
        try:
            token = manager.get_token()
            if token.api_base_url != _ORIGIN:
                raise ValueError("unexpected token origin")
            guard.bind_token(mode, token.value)
        except (Exception, KeyboardInterrupt, asyncio.CancelledError):
            guard.stop()
            raise
    provider = StaticTokenProvider(
        token.value,
        expires_at=token.expires_at,
        api_base_url=token.api_base_url,
    )
    client = Client(
        region=region,
        token_provider=provider,
        timeout=10,
        trust_env=False,
        transport_factory=_factory(guard, gate, factory, _SyncGuardedTransport),
    )
    try:
        serial = _select_device(client.devices.list(page_start=0, page_size=1))
        if serial is None:
            return None
        guard.bind_device(serial)
        for label, method_name, shape in _PROFILES:
            row = {
                "mode": mode,
                "operation": label,
                "outcome": "failed",
                "shape": "unverified",
            }
            receipt["profiles"].append(row)
            response = getattr(client.settings, method_name)(serial)
            _check_response(response, method_name, shape)
            row["outcome"] = "passed"
            row["shape"] = shape
        return serial
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        guard.stop()
        raise
    finally:
        client.close()
        receipt["sync_closed"] = True


async def _async_phase(app_key, app_secret, factory, guard, receipt, serial):
    mode = "async"
    region = RegionConfig.china()
    gate = guard.for_mode(mode)
    async with AsyncTokenManager(
        app_key,
        app_secret,
        region=region,
        transport=_AsyncGuardedTransport(gate, factory()),
        timeout=10,
        trust_env=False,
    ) as manager:
        try:
            token = await manager.get_token()
            if token.api_base_url != _ORIGIN:
                raise ValueError("unexpected token origin")
            guard.bind_token(mode, token.value)
        except (Exception, KeyboardInterrupt, asyncio.CancelledError):
            guard.stop()
            raise
    provider = AsyncStaticTokenProvider(
        token.value,
        expires_at=token.expires_at,
        api_base_url=token.api_base_url,
    )
    client = AsyncClient(
        region=region,
        token_provider=provider,
        timeout=10,
        trust_env=False,
        transport_factory=_factory(guard, gate, factory, _AsyncGuardedTransport),
    )
    try:
        for label, method_name, shape in _PROFILES:
            row = {
                "mode": mode,
                "operation": label,
                "outcome": "failed",
                "shape": "unverified",
            }
            receipt["profiles"].append(row)
            response = await getattr(client.settings, method_name)(serial)
            _check_response(response, method_name, shape)
            row["outcome"] = "passed"
            row["shape"] = shape
        return serial
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        guard.stop()
        raise
    finally:
        await client.aclose()
        receipt["async_closed"] = True


def _run(app_key, app_secret, sync_factory, async_factory, guard, receipt):
    serial = _sync_phase(app_key, app_secret, sync_factory, guard, receipt)
    if serial is None and guard.spent == 2 and not guard.denied and receipt["sync_closed"]:
        receipt["outcome"] = "no_target"
        guard.stop()
        return
    if (
        serial is None
        or guard.spent != 5
        or guard.denied
        or not receipt["sync_closed"]
        or len(receipt["profiles"]) != 3
        or any(row["outcome"] != "passed" for row in receipt["profiles"])
    ):
        raise ValueError("incomplete sync")
    asyncio.run(_async_phase(app_key, app_secret, async_factory, guard, receipt, serial))
    if (
        guard.spent == 9
        and not guard.denied
        and receipt["async_closed"]
        and len(receipt["profiles"]) == 6
        and all(row["outcome"] == "passed" for row in receipt["profiles"])
    ):
        receipt["outcome"] = "passed"
    else:
        raise ValueError("incomplete")


def verify(app_key: str, app_secret: str, *, sync_factory, async_factory) -> dict:
    receipt = {
        "scope": "domestic_detection_reads",
        "schema_version": 1,
        "outcome": "failed",
        "http_requests": 0,
        "sync_closed": False,
        "async_closed": False,
        "profiles": [],
    }
    if not all(isinstance(value, str) and value.strip() for value in (app_key, app_secret)):
        return receipt
    if not callable(sync_factory) or not callable(async_factory):
        return receipt
    # A synchronous caller owns this loop. Detect an existing loop before spending any request.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return receipt
    try:
        guard = DetectionReadGuard(app_key, app_secret)
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        return receipt
    try:
        _run(app_key, app_secret, sync_factory, async_factory, guard, receipt)
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        guard.stop()
        receipt["outcome"] = "failed"
    finally:
        receipt["http_requests"] = guard.spent
    return receipt
