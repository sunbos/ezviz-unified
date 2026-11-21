"""Read one domestic tracking switch or demonstrate both settings on in-memory transports."""

import asyncio
import json
import os
import sys
from urllib.parse import parse_qsl

import httpx

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError, RegionConfig
from ezviz_unified.settings import _check_serial, _check_text
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.tracking_switches --mode sync|async --action read-motion|read-human|write-demo"
_ERROR = "error: tracking switch example failed"
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
    if request.method == "POST" and request.url.path == "/api/lapp/device/mobile/status/set":
        return httpx.Response(200, json={"code": "200", "msg": "ok"})
    if request.method == "PUT" and request.url.path == "/api/v3/device/switch/human/track":
        return httpx.Response(200, json={"meta": {"code": 200, "message": "ok"}, "data": None})
    return httpx.Response(404, json={"code": "404", "msg": "not found"})


def _demo_sync() -> None:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        motion = client.settings.set_motion_tracking(_DEVICE_SERIAL, enabled=False, channel_no="01")
        human = client.settings.set_human_tracking(_DEVICE_SERIAL, enabled=False)
        if motion.operation != "settings.set_motion_tracking" or motion.code != "200":
            raise ValueError(_ERROR)
        if human.operation != "settings.set_human_tracking" or human.code != 200:
            raise ValueError(_ERROR)


async def _demo_async() -> None:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        motion = await client.settings.set_motion_tracking(
            _DEVICE_SERIAL, enabled=False, channel_no="01"
        )
        human = await client.settings.set_human_tracking(_DEVICE_SERIAL, enabled=False)
        if motion.operation != "settings.set_motion_tracking" or motion.code != "200":
            raise ValueError(_ERROR)
        if human.operation != "settings.set_human_tracking" or human.code != 200:
            raise ValueError(_ERROR)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    options = dict(zip(args[::2], args[1::2]))
    mode = options.get("--mode")
    action = options.get("--action")
    if (
        len(args) != 4
        or mode not in ("sync", "async")
        or action not in ("read-motion", "read-human", "write-demo")
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        if action == "write-demo":
            if mode == "sync":
                _demo_sync()
            else:
                asyncio.run(_demo_async())
            print(
                json.dumps(
                    {"simulated": True, "motion_write": True, "human_write": True},
                    separators=(",", ":"),
                )
            )
            return 0
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        raw = os.environ.get("EZVIZ_DEVICE_SERIAL")
        if action == "read-motion":
            serial = _check_serial(_check_text(raw, "device_serial"), domestic=True)
        else:
            serial = _header("device_serial", raw)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                if action == "read-motion":
                    response = client.settings.get_motion_tracking(serial)
                else:
                    response = client.settings.get_human_tracking(serial)
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    if action == "read-motion":
                        return await client.settings.get_motion_tracking(serial)
                    return await client.settings.get_human_tracking(serial)

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict) or type(data.get("enable")) is not int:
            raise ValueError(_ERROR)
        value = data["enable"]
        enabled = bool(value) if value in (0, 1) else None
        print(json.dumps({"enabled": enabled}, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
