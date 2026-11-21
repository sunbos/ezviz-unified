"""Demonstrate one overseas detector display-name update on in-memory transports."""

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
_DEVICE_SERIAL = "demo-device-serial"
_AUTH_BASE_URL = "https://demo-auth.ezvizlife.com"
_AREA_DOMAIN = "https://demo-api.ezvizlife.com"
_USAGE = "usage: python -m examples.a1_detector_rename --mode sync|async"
_ERROR = "error: A1 detector rename example failed"
_DETECTOR_SERIAL = "demo-detector"
_NEW_NAME = "Demo detector"


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


def _business_handler(request: httpx.Request) -> httpx.Response:
    if (
        request.method != "POST"
        or request.url.path != "/api/lapp/detector/name/change"
        or request.url.query
    ):
        return httpx.Response(404, json={"code": "404", "msg": "not found"})
    if request.headers.get("Content-Type") != "application/x-www-form-urlencoded":
        return httpx.Response(400, json={"code": "400", "msg": "bad request"})
    params = sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))
    expected = sorted(
        [
            ("accessToken", _ACCESS_TOKEN),
            ("deviceSerial", _DEVICE_SERIAL),
            ("detectorSerial", _DETECTOR_SERIAL),
            ("newName", _NEW_NAME),
        ]
    )
    if params != expected:
        return httpx.Response(400, json={"code": "400", "msg": "bad request"})
    return httpx.Response(200, json={"code": "200", "msg": "ok"})


def _client_kwargs() -> dict:
    return {
        "region": RegionConfig.overseas(auth_base_url=_AUTH_BASE_URL),
        "auth_transport": httpx.MockTransport(_auth_handler),
        "transport_factory": lambda origin: httpx.MockTransport(_business_handler),
    }


def _run_sync() -> None:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        result = client.detectors.rename(_DEVICE_SERIAL, _DETECTOR_SERIAL, new_name=_NEW_NAME)
        if result.operation != "detectors.rename" or result.code != "200":
            raise ValueError(_ERROR)


async def _run_async() -> None:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        result = await client.detectors.rename(_DEVICE_SERIAL, _DETECTOR_SERIAL, new_name=_NEW_NAME)
        if result.operation != "detectors.rename" or result.code != "200":
            raise ValueError(_ERROR)


def main(argv: list[str] | None = None) -> int:
    """Run the simulated detector rename in ``sync`` or ``async`` mode."""
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
            _run_sync()
        else:
            asyncio.run(_run_async())
        print(json.dumps({"simulated": True, "renamed": True}, separators=(",", ":")))
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
