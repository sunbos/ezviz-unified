"""Query domestic live concurrency limit rules once, read-only.

Run from the repository root::

    python -m examples.live_limits --mode sync --live-type VTDU
    python -m examples.live_limits --mode async --live-type RTMP

The configured region must be ``domestic``; it is validated before the
client is created. A single ``live_limits.get`` call is made with the
supplied live type -- ``VTDU`` (jsdecode/private stream) or ``RTMP``
(HLS/RTMP/FLV), case-sensitive and passed through verbatim. The call
returns the rules already set on the account, not real-time usage, and
the JSON output only reports that the query succeeded -- no rule values
or entry counts are printed. ``--help`` prints usage without reading
configuration or touching the network. Importing this module performs
no configuration or network I/O.
"""

import asyncio
import json
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client

_USAGE = "usage: python -m examples.live_limits --mode sync|async --live-type VTDU|RTMP"
_ERROR = "error: live limit query failed"
_MODES = ("sync", "async")
_LIVE_TYPES = ("VTDU", "RTMP")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if (
        len(args) != 4
        or args[0] != "--mode"
        or args[1] not in _MODES
        or args[2] != "--live-type"
        or args[3] not in _LIVE_TYPES
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    live_type = args[3]
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                client.live_limits.get(live_type)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await client.live_limits.get(live_type)

            asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "live_type": live_type,
        "status": "ok",
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
