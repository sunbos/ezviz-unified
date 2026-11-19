"""Query overseas DDNS entries once, read-only.

Run from the repository root::

    python -m examples.ddns --mode sync --query get
    python -m examples.ddns --mode async --query shared

``get`` forwards the optional ``EZVIZ_DEVICE_SERIAL`` and
``EZVIZ_DDNS_DOMAIN`` selectors unchanged; at least one must be set and the
SDK validates it. ``list`` and ``shared`` fetch a single page
(``page_start=0``, ``page_size=1``) and do not read the selectors.
``--help`` prints usage without reading configuration or touching the
network. Importing this module performs no configuration or network I/O.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client

_USAGE = "usage: python -m examples.ddns --mode sync|async --query get|list|shared"
_ERROR = "error: DDNS query failed"
_MODES = ("sync", "async")
_QUERIES = ("get", "list", "shared")
_METHODS = {"get": "get", "list": "list", "shared": "share_list"}


def _query_kwargs(query: str) -> dict:
    if query == "get":
        device_serial = os.environ.get("EZVIZ_DEVICE_SERIAL")
        domain = os.environ.get("EZVIZ_DDNS_DOMAIN")
        if device_serial is None and domain is None:
            raise ValueError(_ERROR)
        return {"device_serial": device_serial, "domain": domain}
    return {"page_start": 0, "page_size": 1}


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
        if config.region != "overseas":
            raise ValueError(_ERROR)
        method = _METHODS[query]
        kwargs = _query_kwargs(query)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                getattr(client.ddns, method)(**kwargs)
        else:

            async def call_async() -> None:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await getattr(client.ddns, method)(**kwargs)

            asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    success = {"region": "overseas", "mode": mode, "query": query, "status": "ok"}
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
