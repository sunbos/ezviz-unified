"""Offline alarm sound demo on mock transports; touches zero real devices or networks."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import AsyncClient, Client, EzvizError, RegionConfig

if TYPE_CHECKING:
    from ezviz_unified import ApiResponse

_APP_KEY = "demo-app-key"
_APP_SECRET = "demo-app-secret"
_ACCESS_TOKEN = "demo-access-token"
_DEVICE_SERIAL = "demo-device-serial"
_AUTH_BASE_URL = "https://demo-auth.ezvizlife.com"
_AREA_DOMAIN = "https://demo-api.ezvizlife.com"
_USAGE = "usage: python -m examples.alarm_sound --mode sync|async"
_ERROR = "error: alarm sound example failed"
_EXPECTED_PARAMS = {
    "/api/v3/device/alarmSound/enabled/set": {"type": "2"},
    "/api/v3/device/audition": {"voiceIndex": "202", "volume": "1"},
}


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
    params = _EXPECTED_PARAMS.get(request.url.path)
    if request.method != "POST" or params is None:
        return httpx.Response(404)
    if (
        sorted(request.url.params.multi_items()) != sorted(params.items())
        or request.headers.get("accessToken") != _ACCESS_TOKEN
        or request.headers.get("deviceSerial") != _DEVICE_SERIAL
        or request.headers.get("content-type") is not None
        or request.content != b""
    ):
        return httpx.Response(400)
    return httpx.Response(200, json={"body": {"meta": {"code": 200, "message": "ok"}}})


def _client_kwargs() -> dict:
    return {
        "region": RegionConfig.overseas(auth_base_url=_AUTH_BASE_URL),
        "auth_transport": httpx.MockTransport(_auth_handler),
        "transport_factory": lambda origin: httpx.MockTransport(_business_handler),
    }


def _check_confirmed(result: ApiResponse, operation_name: str) -> None:
    """Raise ``ValueError`` unless the ack matches the operation and code 200."""
    confirmed = (
        result.operation == "settings." + operation_name
        and isinstance(result.code, int)
        and not isinstance(result.code, bool)
        and result.code == 200
    )
    if not confirmed:
        raise ValueError("unexpected acknowledgement")


def _run_sync() -> tuple[ApiResponse, ApiResponse]:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        mode_result = client.settings.set_alarm_sound_mode(_DEVICE_SERIAL, 2)
        audition_result = client.settings.play_audition(_DEVICE_SERIAL, 202, volume=1)
    return mode_result, audition_result


async def _run_async() -> tuple[ApiResponse, ApiResponse]:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        mode_result = await client.settings.set_alarm_sound_mode(_DEVICE_SERIAL, 2)
        audition_result = await client.settings.play_audition(_DEVICE_SERIAL, 202, volume=1)
    return mode_result, audition_result


def main(argv: list[str] | None = None) -> int:
    """Run the simulated alarm sound flow in ``sync`` or ``async`` mode."""
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
            mode_result, audition_result = _run_sync()
        else:
            mode_result, audition_result = asyncio.run(_run_async())
        _check_confirmed(mode_result, "set_alarm_sound_mode")
        _check_confirmed(audition_result, "play_audition")
        print(
            json.dumps(
                {"mode": mode, "mode_set": True, "audition_acknowledged": True},
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
