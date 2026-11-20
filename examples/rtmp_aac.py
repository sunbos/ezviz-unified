"""Query a domestic device's RTMP AAC support once, read-only.

Run from the repository root::

    python -m examples.rtmp_aac --mode sync
    python -m examples.rtmp_aac --mode async

``EZVIZ_DEVICE_SERIAL`` must be nonempty printable ASCII without leading
or trailing spaces, the optional ``EZVIZ_CHANNEL_NO`` must be an ASCII
decimal and the configured region must be ``domestic``; all are checked
before the client is created. A single ``video.get_rtmp_aac_support``
call is made and the JSON output only reports the boolean
``data.enable`` -- the serial and channel stay hidden. ``--help`` prints
usage without reading configuration or touching the network. Importing
this module performs no configuration or network I/O.
"""

import asyncio
import json
import os
import re
import sys

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError

_USAGE = "usage: python -m examples.rtmp_aac --mode sync|async"
_ERROR = "error: RTMP AAC support query failed"
_MODES = ("sync", "async")


def _check_serial(value: object) -> str:
    """Return a serial usable in the AAC device header."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip(" ")
        or any(ord(char) < 0x20 or ord(char) > 0x7E for char in value)
    ):
        raise ValueError(_ERROR)
    return value


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
        if config.region != "domestic":
            raise ValueError(_ERROR)
        serial = _check_serial(os.environ.get("EZVIZ_DEVICE_SERIAL"))
        channel_raw = os.environ.get("EZVIZ_CHANNEL_NO")
        if channel_raw is None:
            channel = None
        elif re.fullmatch(r"[0-9]+", channel_raw) is None:
            raise ValueError(_ERROR)
        else:
            channel = int(channel_raw)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.video.get_rtmp_aac_support(serial, channel=channel)
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.video.get_rtmp_aac_support(serial, channel=channel)

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict) or not isinstance(data.get("enable"), bool):
            raise ValueError(_ERROR)
        supported = data["enable"]
        success = {
            "region": "domestic",
            "mode": mode,
            "status": "ok",
            "supported": supported,
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
