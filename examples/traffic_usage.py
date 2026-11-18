"""Query the account traffic summary once, with the sync or async client.

Run from the repository root::

    python -m examples.traffic_usage --mode sync
    python -m examples.traffic_usage --mode async

``--help`` prints usage without reading configuration or touching the
network. Importing this module performs no configuration or network I/O.
"""

import asyncio
import json
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client

_USAGE = "usage: python -m examples.traffic_usage --mode sync|async"
_ERROR = "error: traffic.account_summary failed"
_MODES = ("sync", "async")
_SUCCESS = {
    "region": "domestic",
    "operation": "traffic.account_summary",
    "status": "ok",
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in _MODES:
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        if args[1] == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                client.traffic.account_summary()
        else:

            async def call_async() -> None:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await client.traffic.account_summary()

            asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    print(json.dumps(_SUCCESS, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
