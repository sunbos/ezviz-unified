"""Offline motion area demo on mock transports; touches zero real devices or networks."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import AsyncClient, Client, EzvizError, RegionConfig

if TYPE_CHECKING:
    from ezviz_unified import ApiResponse

_CN_APP_KEY = "demo-cn-app-key"
_CN_APP_SECRET = "demo-cn-app-secret"
_CN_ACCESS_TOKEN = "demo-cn-access-token"
_CN_DEVICE_SERIAL = "demo-cn-device-serial"
_CN_CHANNEL_NO = "1"
_CN_AREA = "demo-cn-motion-area"
_CN_API_ORIGIN = "https://open.ys7.com"

_OS_APP_KEY = "demo-os-app-key"
_OS_APP_SECRET = "demo-os-app-secret"
_OS_ACCESS_TOKEN = "demo-os-access-token"
_OS_DEVICE_SERIAL = "demo-os-device-serial"
_OS_CHANNEL_NO = "1"
_OS_AREA = "demo-os-motion-area"
_OS_AUTH_BASE_URL = "https://demo-auth.ezvizlife.com"
_OS_API_ORIGIN = "https://demo-api.ezvizlife.com"

_GET_PATH = "/api/v3/device/motion/detect/get"
_SET_PATH = "/api/v3/device/motion/detect/set"
_USAGE = "usage: python -m examples.motion_area --mode sync|async"
_ERROR = "error: motion area example failed"


def _origin(request: httpx.Request) -> str:
    return f"{request.url.scheme}://{request.url.host}"


def _make_auth_handler(
    app_key: str,
    app_secret: str,
    access_token: str,
    area_domain: str,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != "/api/lapp/token/get":
            return httpx.Response(404)
        if not request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            return httpx.Response(400)
        form = sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))
        expected = sorted([("appKey", app_key), ("appSecret", app_secret)])
        if request.url.params or form != expected:
            return httpx.Response(400)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": access_token,
                    "expireTime": 9_999_999_999_999,
                    "areaDomain": area_domain,
                },
            },
        )

    return handler


def _domestic_business_handler(request: httpx.Request) -> httpx.Response:
    if _origin(request) != _CN_API_ORIGIN:
        return httpx.Response(404)
    if (
        request.headers.get("accessToken") != _CN_ACCESS_TOKEN
        or request.headers.get("deviceSerial") != _CN_DEVICE_SERIAL
        or request.headers.get("channelNo") != _CN_CHANNEL_NO
    ):
        return httpx.Response(400)
    if request.method == "GET" and request.url.path == _GET_PATH:
        if (
            request.url.params
            or request.headers.get("content-type") is not None
            or request.content != b""
        ):
            return httpx.Response(400)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "area": [0, 0, 1, 1],
                    "rows": 2,
                    "columns": 2,
                    "channelNo": 1,
                },
            },
        )
    if request.method == "POST" and request.url.path == _SET_PATH:
        if request.url.params or not request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            return httpx.Response(400)
        form = sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))
        if form != [("area", _CN_AREA)]:
            return httpx.Response(400)
        return httpx.Response(200, json={"code": "200"})
    return httpx.Response(404)


def _overseas_business_handler(request: httpx.Request) -> httpx.Response:
    if _origin(request) != _OS_API_ORIGIN:
        return httpx.Response(404)
    if (
        request.headers.get("accessToken") != _OS_ACCESS_TOKEN
        or request.headers.get("deviceSerial") != _OS_DEVICE_SERIAL
        or request.headers.get("channelNo") != _OS_CHANNEL_NO
        or request.headers.get("content-type") is not None
        or request.content != b""
    ):
        return httpx.Response(400)
    if request.method != "POST" or request.url.path != _SET_PATH:
        return httpx.Response(404)
    if sorted(request.url.params.multi_items()) != [("area", _OS_AREA)]:
        return httpx.Response(400)
    return httpx.Response(200, json={"body": {"meta": {"code": 200, "message": "ok"}}})


def _domestic_client_kwargs() -> dict:
    return {
        "region": RegionConfig.china(),
        "auth_transport": httpx.MockTransport(
            _make_auth_handler(_CN_APP_KEY, _CN_APP_SECRET, _CN_ACCESS_TOKEN, _CN_API_ORIGIN)
        ),
        "transport_factory": lambda origin: httpx.MockTransport(_domestic_business_handler),
    }


def _overseas_client_kwargs() -> dict:
    return {
        "region": RegionConfig.overseas(auth_base_url=_OS_AUTH_BASE_URL),
        "auth_transport": httpx.MockTransport(
            _make_auth_handler(_OS_APP_KEY, _OS_APP_SECRET, _OS_ACCESS_TOKEN, _OS_API_ORIGIN)
        ),
        "transport_factory": lambda origin: httpx.MockTransport(_overseas_business_handler),
    }


def _check_domestic(result: ApiResponse, operation_name: str) -> None:
    """Raise ``ValueError`` unless the ack matches the operation and code ``"200"``."""
    confirmed = (
        result.operation == "settings." + operation_name
        and isinstance(result.code, str)
        and result.code == "200"
    )
    if not confirmed:
        raise ValueError("unexpected acknowledgement")


def _check_overseas(result: ApiResponse, operation_name: str) -> None:
    """Raise ``ValueError`` unless the ack matches the operation and code ``200``."""
    confirmed = (
        result.operation == "settings." + operation_name
        and isinstance(result.code, int)
        and not isinstance(result.code, bool)
        and result.code == 200
    )
    if not confirmed:
        raise ValueError("unexpected acknowledgement")


def _check_domestic_read(result: ApiResponse) -> None:
    _check_domestic(result, "get_motion_area")
    data = result.data
    area = data.get("area") if isinstance(data, dict) else None
    if not isinstance(area, list) or any(
        not isinstance(value, int) or isinstance(value, bool) for value in area
    ):
        raise ValueError("unexpected motion area payload")


def _run_sync() -> tuple[ApiResponse, ApiResponse, ApiResponse]:
    with Client(_CN_APP_KEY, _CN_APP_SECRET, **_domestic_client_kwargs()) as client:
        read_result = client.settings.get_motion_area(_CN_DEVICE_SERIAL, channel_no=_CN_CHANNEL_NO)
        write_result = client.settings.set_motion_area(
            _CN_DEVICE_SERIAL, channel_no=_CN_CHANNEL_NO, area=_CN_AREA
        )
    with Client(_OS_APP_KEY, _OS_APP_SECRET, **_overseas_client_kwargs()) as client:
        overseas_result = client.settings.set_motion_area(
            _OS_DEVICE_SERIAL, channel_no=_OS_CHANNEL_NO, area=_OS_AREA
        )
    return read_result, write_result, overseas_result


async def _run_async() -> tuple[ApiResponse, ApiResponse, ApiResponse]:
    async with AsyncClient(_CN_APP_KEY, _CN_APP_SECRET, **_domestic_client_kwargs()) as client:
        read_result = await client.settings.get_motion_area(
            _CN_DEVICE_SERIAL, channel_no=_CN_CHANNEL_NO
        )
        write_result = await client.settings.set_motion_area(
            _CN_DEVICE_SERIAL, channel_no=_CN_CHANNEL_NO, area=_CN_AREA
        )
    async with AsyncClient(_OS_APP_KEY, _OS_APP_SECRET, **_overseas_client_kwargs()) as client:
        overseas_result = await client.settings.set_motion_area(
            _OS_DEVICE_SERIAL, channel_no=_OS_CHANNEL_NO, area=_OS_AREA
        )
    return read_result, write_result, overseas_result


def main(argv: list[str] | None = None) -> int:
    """Run the simulated motion area flow in ``sync`` or ``async`` mode."""
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
            read_result, write_result, overseas_result = _run_sync()
        else:
            read_result, write_result, overseas_result = asyncio.run(_run_async())
        _check_domestic_read(read_result)
        _check_domestic(write_result, "set_motion_area")
        _check_overseas(overseas_result, "set_motion_area")
        print(
            json.dumps(
                {
                    "mode": mode,
                    "domestic_read": True,
                    "domestic_write": True,
                    "overseas_write": True,
                },
                separators=(",", ":"),
            )
        )
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
