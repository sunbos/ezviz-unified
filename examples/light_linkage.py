"""Read a single domestic channel or overseas device light linkage state."""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.light_linkage --mode sync|async [--type 301|305]"
_ERROR = "error: light linkage query failed"
_MODES = ("sync", "async")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if not args or len(args) % 2:
        print(_USAGE, file=sys.stderr)
        return 1
    mode = None
    selector = None
    for index in range(0, len(args), 2):
        option = args[index]
        value = args[index + 1]
        if option == "--mode" and mode is None and value in _MODES:
            mode = value
        elif option == "--type" and selector is None and value in ("301", "305"):
            selector = value
        else:
            print(_USAGE, file=sys.stderr)
            return 1
    if mode is None:
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        config = load_config()
        scope = "domestic" if selector is not None else "overseas"
        if scope != config.region:
            raise ValueError(_ERROR)
        serial = _header("device_serial", os.environ.get("EZVIZ_DEVICE_SERIAL"))
        kwargs = {}
        if selector is not None:
            kwargs = {
                "channel_no": _header("channel_no", os.environ.get("EZVIZ_CHANNEL_NO")),
                "linkage_type": selector,
            }
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                if kwargs:
                    response = client.settings.get_channel_light_linkage(serial, **kwargs)
                else:
                    response = client.settings.get_light_linkage(serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    if kwargs:
                        return await client.settings.get_channel_light_linkage(serial, **kwargs)
                    else:
                        return await client.settings.get_light_linkage(serial)

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict):
            raise ValueError(_ERROR)
        linkage_type = data.get("type")
        enable = data.get("enable")
        if type(linkage_type) is not int or type(enable) is not (
            bool if selector is not None else int
        ):
            raise ValueError(_ERROR)
        if "subSerial" in data and not isinstance(data["subSerial"], str):
            raise ValueError(_ERROR)
        selector_matches = linkage_type == (301 if selector is None else int(selector))
        if selector_matches and enable == 1:
            state = "on"
        elif selector_matches and enable == 0:
            state = "off"
        else:
            state = "unknown"
        success = {
            "region": config.region,
            "mode": mode,
            "status": "ok",
            "selector_matches": selector_matches,
            "state": state,
        }
        print(json.dumps(success, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
