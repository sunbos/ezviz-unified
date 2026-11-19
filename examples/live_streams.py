"""List one page of domestic live streams and print fixed JSON.

Run from the repository root::

    python -m examples.live_streams --mode sync
    python -m examples.live_streams --mode async

The configured region must be ``domestic``; it is validated before the
client is created. A single ``live_streams.list(page_start=0,
page_size=1)`` call is made per run: no polling or any other operation.
The endpoint documentation contains a POST request example while the
operation itself is declared as GET; the declared GET is authoritative.
``--help`` prints usage without reading configuration or touching the
network. Importing this module performs no configuration or network I/O.

Failures print a fixed error line; raw statuses, URLs and SDK payloads
are never emitted.
"""

import asyncio
import json
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError

_USAGE = "usage: python -m examples.live_streams --mode sync|async"
_ERROR = "error: live stream list failed"
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
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.live_streams.list(page_start=0, page_size=1)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.live_streams.list(page_start=0, page_size=1)

            response = asyncio.run(call_async())
        try:
            returned_items = len(response.data)
        except TypeError as exc:
            raise ValueError(_ERROR) from exc
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError):
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "returned_items": returned_items,
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
