"""Read one domestic detection setting or simulate both writes on in-memory transports."""

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

_USAGE = "usage: python -m examples.intelligent_detection --mode sync|async --action read-scene-switch|read-body-switch|read-face-switch|read-mode|write-demo"
_ERROR = "error: intelligent detection example failed"
_APP_KEY = "demo-app-key"
_APP_SECRET = "demo-app-secret"
_ACCESS_TOKEN = "demo-access-token"
_DEVICE_SERIAL = "DEMO-SERIAL"
_AREA_DOMAIN = "https://open.ys7.com"


_SWITCH_TYPES = {"read-scene-switch": None, "read-body-switch": "302", "read-face-switch": "304"}


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
    allowed_paths = (
        "/api/lapp/device/intelligence/detection/switch/set",
        "/api/v3/device/alarm/detect/switch/set",
    )
    if request.method == "POST" and request.url.path in allowed_paths:
        return httpx.Response(200, json={"code": "200", "msg": "ok"})
    return httpx.Response(404, json={"code": "404", "msg": "not found"})


def _demo_sync() -> None:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        intelligent = client.settings.set_intelligent_detection_switch(
            _DEVICE_SERIAL, enabled=True, detection_type="302", channel_no="01"
        )
        selection = client.settings.select_alarm_detection_mode(_DEVICE_SERIAL, detection_type="5")
        if (
            intelligent.operation != "settings.set_intelligent_detection_switch"
            or intelligent.code != "200"
        ):
            raise ValueError(_ERROR)
        if selection.operation != "settings.select_alarm_detection_mode" or selection.code != "200":
            raise ValueError(_ERROR)


async def _demo_async() -> None:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        intelligent = await client.settings.set_intelligent_detection_switch(
            _DEVICE_SERIAL, enabled=True, detection_type="302", channel_no="01"
        )
        selection = await client.settings.select_alarm_detection_mode(
            _DEVICE_SERIAL, detection_type="5"
        )
        if (
            intelligent.operation != "settings.set_intelligent_detection_switch"
            or intelligent.code != "200"
        ):
            raise ValueError(_ERROR)
        if selection.operation != "settings.select_alarm_detection_mode" or selection.code != "200":
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
        or action
        not in (
            "read-scene-switch",
            "read-body-switch",
            "read-face-switch",
            "read-mode",
            "write-demo",
        )
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
                    {"simulated": True, "intelligent_write": True, "mode_write": True},
                    separators=(",", ":"),
                )
            )
            return 0
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        raw = os.environ.get("EZVIZ_DEVICE_SERIAL")
        if action in _SWITCH_TYPES:
            serial = _check_serial(_check_text(raw, "device_serial"), domestic=True)
        else:
            serial = _header("device_serial", raw)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                if action in _SWITCH_TYPES:
                    response = client.settings.get_intelligent_detection_switch(
                        serial, detection_type=_SWITCH_TYPES[action]
                    )
                else:
                    response = client.settings.get_alarm_detection_mode(serial)
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    if action in _SWITCH_TYPES:
                        return await client.settings.get_intelligent_detection_switch(
                            serial, detection_type=_SWITCH_TYPES[action]
                        )
                    return await client.settings.get_alarm_detection_mode(serial)

            response = asyncio.run(call_async())
        data = getattr(response, "data", None)
        if not isinstance(data, dict):
            raise ValueError(_ERROR)
        if action == "read-mode":
            value_info = data.get("valueInfo")
            if not isinstance(value_info, dict):
                raise ValueError(_ERROR)
            mode_type = value_info.get("type")
            if isinstance(mode_type, bool) or not isinstance(mode_type, int):
                raise ValueError(_ERROR)
            mode = "human" if mode_type == 1 else "pir" if mode_type == 5 else None
            print(json.dumps({"mode": mode}, separators=(",", ":")))
        elif action in ("read-scene-switch", "read-body-switch", "read-face-switch"):
            enable = data.get("enable")
            if isinstance(enable, bool) or not isinstance(enable, int):
                raise ValueError(_ERROR)
            enabled = True if enable == 1 else False if enable == 0 else None
            print(json.dumps({"enabled": enabled}, separators=(",", ":")))
        else:
            raise ValueError(_ERROR)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
