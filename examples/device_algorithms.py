"""Query domestic device algorithm entries once, read-only.

Run from the repository root::

    python -m examples.device_algorithms --mode sync --query devices
    python -m examples.device_algorithms --mode async --query supported

``devices`` fetches a single page (``page_start=0``, ``page_size=8``) and
never reads ``EZVIZ_DEVICE_SERIAL`` or follows further pages.
``supported`` requires ``EZVIZ_DEVICE_SERIAL`` to be set and nonblank; the
value is forwarded unchanged and validated by the SDK. ``--help`` prints
usage without reading configuration or touching the network. Importing
this module performs no configuration or network I/O.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client

_USAGE = "usage: python -m examples.device_algorithms --mode sync|async --query devices|supported"
_ERROR = "error: algorithm query failed"
_MODES = ("sync", "async")
_QUERIES = ("devices", "supported")
_METHODS = {"devices": "list_devices", "supported": "list_supported"}


def _query_kwargs(query: str) -> dict:
    if query == "supported":
        serial = os.environ.get("EZVIZ_DEVICE_SERIAL")
        if not serial or not serial.strip():
            raise ValueError(_ERROR)
        return {"device_serial": serial}
    return {"page_start": 0, "page_size": 8}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if (
        len(args) != 4
        or args[0] != "--mode"
        or args[1] not in _MODES
        or args[2] != "--query"
        or args[3] not in _QUERIES
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    mode, query = args[1], args[3]
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        method = _METHODS[query]
        kwargs = _query_kwargs(query)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                getattr(client.device_algorithms, method)(**kwargs)
        else:

            async def call_async() -> None:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await getattr(client.device_algorithms, method)(**kwargs)

            asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    success = {"region": "domestic", "mode": mode, "query": query, "status": "ok"}
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
