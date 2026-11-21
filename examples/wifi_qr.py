"""Demonstrate domestic Wi-Fi QR generation using an in-memory transport."""

from __future__ import annotations

import asyncio
import json
import sys
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import (
    AsyncClient,
    AsyncStaticTokenProvider,
    Client,
    EzvizError,
    RegionConfig,
    StaticTokenProvider,
)

_ORIGIN = "https://open.ys7.com"
_PATH = "/api/lapp/device/wifi/qrcode"
_ACCESS_TOKEN = "demo-static-token"
_SSID = "synthetic-example-network"
_PASSWORD = "synthetic-example-only!"
_USAGE = "usage: python -m examples.wifi_qr --mode sync|async"
_ERROR = "error: Wi-Fi QR example failed"


def _business_handler(request: httpx.Request) -> httpx.Response:
    expected = sorted([("accessToken", _ACCESS_TOKEN), ("ssid", _SSID), ("password", _PASSWORD)])
    if (
        request.method != "POST"
        or str(request.url) != _ORIGIN + _PATH
        or request.headers.get("Content-Type") != "application/x-www-form-urlencoded"
        or sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True)) != expected
    ):
        return httpx.Response(400)
    return httpx.Response(
        200,
        json={"code": "200", "msg": "ok", "data": {"imageData": "not-qr:synthetic-placeholder!"}},
    )


def _run_sync() -> bool:
    provider = StaticTokenProvider(_ACCESS_TOKEN, expires_at=4102444800.0, api_base_url=_ORIGIN)
    with Client(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_business_handler),
    ) as client:
        result = client.devices.generate_wifi_qr(_SSID, password=_PASSWORD)
        if (
            result.operation != "devices.generate_wifi_qr"
            or result.code != "200"
            or not isinstance(result.data, dict)
            or not isinstance(result.data.get("imageData"), str)
        ):
            raise ValueError(_ERROR)
        return bool(result.data["imageData"])


async def _run_async() -> bool:
    provider = AsyncStaticTokenProvider(
        _ACCESS_TOKEN, expires_at=4102444800.0, api_base_url=_ORIGIN
    )
    async with AsyncClient(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_business_handler),
    ) as client:
        result = await client.devices.generate_wifi_qr(_SSID, password=_PASSWORD)
        if (
            result.operation != "devices.generate_wifi_qr"
            or result.code != "200"
            or not isinstance(result.data, dict)
            or not isinstance(result.data.get("imageData"), str)
        ):
            raise ValueError(_ERROR)
        return bool(result.data["imageData"])


def main(argv: list[str] | None = None) -> int:
    """Run the simulated Wi-Fi QR generation in ``sync`` or ``async`` mode."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in ("sync", "async"):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        if mode == "sync":
            present = _run_sync()
        else:
            present = asyncio.run(_run_async())
        print(json.dumps({"simulated": True, "image_data_present": present}, separators=(",", ":")))
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
