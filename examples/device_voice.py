"""Query a domestic device's voice list once, read-only.

Run from the repository root::

    python -m examples.device_voice --mode sync
    python -m examples.device_voice --mode async

``EZVIZ_DEVICE_SERIAL`` must be set to a nonblank serial and the configured
region must be ``domestic``; both are validated before the client is
created. A single ``device_voice.list`` call is made and the JSON output
only reports whether any voices were returned -- no serial, voice names,
or voice URLs are printed. ``--help`` prints usage without reading
configuration or touching the network. Importing this module performs no
configuration or network I/O.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client

_USAGE = "usage: python -m examples.device_voice --mode sync|async"
_ERROR = "error: device voice list failed"
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
        if serial is None or not serial.strip():
            raise ValueError(_ERROR)
        serial.encode("utf-8")
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.device_voice.list(serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.device_voice.list(serial)

            response = asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "has_voices": bool(response.data),
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
