"""Demonstrate one domestic OTAP image flip on in-memory transports."""

from __future__ import annotations

import asyncio
import json
import sys

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
_PATH = "/api/v3/device/otap/prop"
_ACCESS_TOKEN = "demo-static-token"
_SERIAL = "DEMO-OTAP-UNIT"
_LOCAL_INDEX = "01"
_STYLES = ("LEFTRIGHT", "UPDOWN", "CENTER", "AUTO")
_USAGE = "usage: python -m examples.otap_image_flip --mode sync|async --style LEFTRIGHT|UPDOWN|CENTER|AUTO --enabled true|false"
_ERROR = "error: OTAP image flip example failed"
_HEADERS = {
    "accessToken": _ACCESS_TOKEN,
    "deviceSerial": _SERIAL,
    "localIndex": _LOCAL_INDEX,
    "resourceCategory": "Video",
    "domainIdentifier": "VideoAdjustment",
    "propIdentifier": "ImageFlip",
    "Content-Type": "application/json",
}
_SELECTORS = {
    "local_index": _LOCAL_INDEX,
    "resource_category": "Video",
    "domain_identifier": "VideoAdjustment",
    "property_identifier": "ImageFlip",
}


def _business_handler(request: httpx.Request) -> httpx.Response:
    reject = httpx.Response(400)
    if request.method != "PUT" or str(request.url) != _ORIGIN + _PATH:
        return reject
    for name, value in _HEADERS.items():
        if request.headers.get(name) != value:
            return reject
    try:
        body = json.loads(request.content)
    except ValueError:
        return reject
    if type(body) is not dict or set(body) != {"enabled", "imageFlipStyle"}:
        return reject
    if type(body["enabled"]) is not bool:
        return reject
    if type(body["imageFlipStyle"]) is not str or body["imageFlipStyle"] not in _STYLES:
        return reject
    return httpx.Response(200, json={"meta": {"code": 200, "message": "ok", "moreInfo": None}})


def _run_sync(style: str, enabled: bool) -> None:
    provider = StaticTokenProvider(_ACCESS_TOKEN, expires_at=4102444800.0, api_base_url=_ORIGIN)
    with Client(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_business_handler),
    ) as client:
        result = client.otap.set_property(
            _SERIAL, {"enabled": enabled, "imageFlipStyle": style}, **_SELECTORS
        )
    if (
        result.operation != "otap.set_property"
        or type(result.code) is not int
        or result.code != 200
    ):
        raise ValueError(_ERROR)


async def _run_async(style: str, enabled: bool) -> None:
    provider = AsyncStaticTokenProvider(
        _ACCESS_TOKEN, expires_at=4102444800.0, api_base_url=_ORIGIN
    )
    async with AsyncClient(
        region=RegionConfig.china(),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_business_handler),
    ) as client:
        result = await client.otap.set_property(
            _SERIAL, {"enabled": enabled, "imageFlipStyle": style}, **_SELECTORS
        )
    if (
        result.operation != "otap.set_property"
        or type(result.code) is not int
        or result.code != 200
    ):
        raise ValueError(_ERROR)


def main(argv: list[str] | None = None) -> int:
    """Run a simulated image flip in ``sync`` or ``async`` mode."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    options = dict(zip(args[::2], args[1::2]))
    mode = options.get("--mode")
    style = options.get("--style")
    enabled = options.get("--enabled")
    if (
        len(args) != 6
        or mode not in ("sync", "async")
        or style not in _STYLES
        or enabled not in ("true", "false")
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        if mode == "sync":
            _run_sync(style, enabled == "true")
        else:
            asyncio.run(_run_async(style, enabled == "true"))
        print(json.dumps({"simulated": True, "acknowledged": True}, separators=(",", ":")))
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
