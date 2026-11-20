"""Read an overseas device's light linkage state once and print fixed JSON.

Run from the repository root with ``--mode sync`` or ``--mode async``;
``--help`` prints usage without loading configuration.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.light_linkage --mode sync|async"
_ERROR = "error: light linkage query failed"
_MODES = ("sync", "async")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in _MODES:
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        config = load_config()
        if config.region != "overseas":
            raise ValueError(_ERROR)
        serial = _header("device_serial", os.environ.get("EZVIZ_DEVICE_SERIAL"))
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.settings.get_light_linkage(serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.settings.get_light_linkage(serial)

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict):
            raise ValueError(_ERROR)
        linkage_type = data.get("type")
        enable = data.get("enable")
        if type(linkage_type) is not int or type(enable) is not int:
            raise ValueError(_ERROR)
        if "subSerial" in data and not isinstance(data["subSerial"], str):
            raise ValueError(_ERROR)
        selector_matches = linkage_type == 301
        if selector_matches and enable == 1:
            state = "on"
        elif selector_matches and enable == 0:
            state = "off"
        else:
            state = "unknown"
        success = {
            "region": "overseas",
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
