"""Query a domestic device's night vision setting once, read-only.

Run from the repository root::

    python -m examples.night_vision --mode sync
    python -m examples.night_vision --mode async

``EZVIZ_DEVICE_SERIAL`` must be set to a serial that is a valid SDK path
segment, ``EZVIZ_CHANNEL_NO`` to a decimal integer (zero and negative
values are accepted) and the configured region must be ``domestic``; all
three are validated before the client is created. A single
``illumination.get_night_vision`` call is made and the JSON output only
reports whether ``data.valueInfo`` arrived as a string or an object --
its contents, the serial and the channel stay hidden. ``--help`` prints
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
from ezviz_unified._path import _check_value

_USAGE = "usage: python -m examples.night_vision --mode sync|async"
_ERROR = "error: night vision query failed"
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
        if config.region != "domestic":
            raise ValueError(_ERROR)
        serial = os.environ.get("EZVIZ_DEVICE_SERIAL")
        if not isinstance(serial, str):
            raise ValueError(_ERROR)
        _check_value(serial)
        channel_raw = os.environ.get("EZVIZ_CHANNEL_NO")
        if channel_raw is None or re.fullmatch(r"[+-]?[0-9]+", channel_raw) is None:
            raise ValueError(_ERROR)
        channel_no = int(channel_raw)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.illumination.get_night_vision(serial, channel_no)
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.illumination.get_night_vision(serial, channel_no)

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict):
            raise ValueError(_ERROR)
        value_info = data.get("valueInfo")
        if isinstance(value_info, str):
            value_info_type = "string"
        elif isinstance(value_info, dict):
            value_info_type = "object"
        else:
            raise ValueError(_ERROR)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "value_info_type": value_info_type,
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
