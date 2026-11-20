"""Offline detection sensitivity demo on mock transports; touches zero real devices or networks."""

from __future__ import annotations

import asyncio
import json
import sys
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import AsyncClient, Client, EzvizError, RegionConfig

_APP_KEY = "demo-app-key"
_APP_SECRET = "demo-app-secret"
_ACCESS_TOKEN = "demo-access-token"
_DEVICE_SERIAL = "DEMO-SERIAL"
_AREA_DOMAIN = "https://open.ys7.com"
_USAGE = "usage: python -m examples.detection_sensitivity --mode sync|async"
_ERROR = "error: detection sensitivity example failed"


def _auth_handler(request: httpx.Request) -> httpx.Response:
    if request.method != "POST" or request.url.path != "/api/lapp/token/get":
        return httpx.Response(404)
    if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return httpx.Response(400)
    form = sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))
    expected = sorted([("appKey", _APP_KEY), ("appSecret", _APP_SECRET)])
    if request.url.params or form != expected:
        return httpx.Response(400)
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": _ACCESS_TOKEN,
                "expireTime": 9_999_999_999_999,
                "areaDomain": _AREA_DOMAIN,
            },
        },
    )


def _client_kwargs() -> dict:
    return {
        "region": RegionConfig.china(),
        "auth_transport": httpx.MockTransport(_auth_handler),
        "transport_factory": lambda origin: httpx.MockTransport(_business_handler),
    }


def _business_handler(request: httpx.Request) -> httpx.Response:
    """Return canned domestic responses for the sensitivity endpoints."""
    route = (request.method, request.url.path)
    if route == ("POST", "/api/lapp/device/algorithm/config/get"):
        payload = {"code": "200", "data": [{"channel": "1", "type": "0", "value": 3}]}
    elif route == ("POST", "/api/lapp/device/algorithm/config/set"):
        payload = {"code": "200"}
    elif route == ("GET", "/api/v3/device/detect/config/get"):
        payload = {"code": "200", "data": [{"channel": 1, "type": "3", "value": "70"}]}
    elif route == ("POST", "/api/v3/device/detect/config/set"):
        payload = {"code": "200"}
    else:
        return httpx.Response(404, json={"code": "404"})
    return httpx.Response(200, json=payload)


def _run_sync() -> dict[str, bool]:
    """Call all four sensitivity helpers with a synchronous client."""
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        client.settings.get_motion_sensitivity(_DEVICE_SERIAL)
        client.settings.set_motion_sensitivity(_DEVICE_SERIAL, value=3, channel_no=1)
        client.settings.get_detection_sensitivity(_DEVICE_SERIAL, channel_no="01")
        client.settings.set_detection_sensitivity(
            _DEVICE_SERIAL, channel_no="01", detection_type="3", value="001"
        )
    return {
        "motion_read": True,
        "motion_write": True,
        "detection_read": True,
        "detection_write": True,
    }


async def _run_async() -> dict[str, bool]:
    """Call all four sensitivity helpers with an asynchronous client."""
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        await client.settings.get_motion_sensitivity(_DEVICE_SERIAL)
        await client.settings.set_motion_sensitivity(_DEVICE_SERIAL, value=3, channel_no=1)
        await client.settings.get_detection_sensitivity(_DEVICE_SERIAL, channel_no="01")
        await client.settings.set_detection_sensitivity(
            _DEVICE_SERIAL, channel_no="01", detection_type="3", value="001"
        )
    return {
        "motion_read": True,
        "motion_write": True,
        "detection_read": True,
        "detection_write": True,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the sensitivity example in ``sync`` or ``async`` mode."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in ("sync", "async"):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        result = _run_sync() if mode == "sync" else asyncio.run(_run_async())
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    print(json.dumps({"mode": mode, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
