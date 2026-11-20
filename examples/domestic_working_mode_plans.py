"""Read domestic working mode plans or simulate a Huashu 4G write in memory."""

import asyncio
import json
import os
import re
import sys
from urllib.parse import parse_qsl

import httpx

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError, RegionConfig
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.domestic_working_mode_plans --mode sync|async --action read|write-demo"
_ERROR = "error: domestic working mode plan example failed"
_APP_KEY = "demo-app-key"
_APP_SECRET = "demo-app-secret"
_ACCESS_TOKEN = "demo-access-token"
_DEVICE_SERIAL = "DEMO-SERIAL"
_AREA_DOMAIN = "https://open.ys7.com"


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
    if request.method != "POST" or request.url.path != "/api/v3/device/timing/plan/set":
        return httpx.Response(404)
    if request.url.params:
        return httpx.Response(400)
    if (
        request.headers.get("accessToken") != _ACCESS_TOKEN
        or request.headers.get("deviceSerial") != _DEVICE_SERIAL
    ):
        return httpx.Response(400)
    return httpx.Response(200, json={"code": "200"})


def _demo_sync() -> None:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        result = client.settings.set_huashu_4g_working_mode_plan(
            _DEVICE_SERIAL,
            enabled=True,
            start_time="08:00",
            end_time="09:00",
            week="1",
            stop_after_execution=False,
        )
        if result.operation != "settings.set_huashu_4g_working_mode_plan" or result.code != "200":
            raise ValueError(_ERROR)


async def _demo_async() -> None:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        result = await client.settings.set_huashu_4g_working_mode_plan(
            _DEVICE_SERIAL,
            enabled=True,
            start_time="08:00",
            end_time="09:00",
            week="1",
            stop_after_execution=False,
        )
        if result.operation != "settings.set_huashu_4g_working_mode_plan" or result.code != "200":
            raise ValueError(_ERROR)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    options = dict(zip(args[::2], args[1::2]))
    mode = options.get("--mode")
    action = options.get("--action")
    if len(args) != 4 or mode not in ("sync", "async") or action not in ("read", "write-demo"):
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        if action == "write-demo":
            if mode == "sync":
                _demo_sync()
            else:
                asyncio.run(_demo_async())
            print(json.dumps({"simulated": True, "acknowledged": True}, separators=(",", ":")))
            return 0
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        serial = _header("device_serial", os.environ.get("EZVIZ_DEVICE_SERIAL"))
        channel_raw = os.environ.get("EZVIZ_CHANNEL_NO")
        if channel_raw is None:
            channel = None
        elif re.fullmatch(r"-?[0-9]+", channel_raw) is None:
            raise ValueError(_ERROR)
        else:
            channel = int(channel_raw)
        _raw = os.environ.get("EZVIZ_TIMING_PLAN_TYPE")
        if _raw is None:
            timing_plan_type = None
        elif _raw in ("17", "19"):
            timing_plan_type = int(_raw)
        else:
            raise ValueError(_ERROR)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.settings.get_working_mode_plans(
                    serial, channel=channel, timing_plan_type=timing_plan_type
                )
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.settings.get_working_mode_plans(
                        serial, channel=channel, timing_plan_type=timing_plan_type
                    )

            response = asyncio.run(call_async())
        plans = response.data
        if not isinstance(plans, list):
            raise ValueError(_ERROR)
        print(json.dumps({"plan_count": len(plans)}, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
