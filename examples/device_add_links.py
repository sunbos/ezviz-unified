"""Read one device add-links page and print fixed JSON.

Run from the repository root::

    python -m examples.device_add_links --mode sync --query records
    python -m examples.device_add_links --mode async --query links

The configured region must be ``domestic``; it is validated before the
client is created. Exactly one call is made per run:
``device_add_links.records(page_size=1)`` for ``--query records`` or
``device_add_links.list(page_size=1)`` for ``--query links``. Running
this example is an explicit opt-in local read that requires developer
account permission; no link is created, opened, sent or inspected,
returned URLs, notes and IDs are never emitted, and no hardware linkage
is claimed. ``--help`` prints usage without reading configuration or
touching the network. Importing this module performs no configuration
or network I/O.

Failures print a fixed error line; raw statuses and SDK payloads are
never emitted.
"""

import asyncio
import json
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError

_USAGE = "usage: python -m examples.device_add_links --mode sync|async --query records|links"
_ERROR = "error: device add lookup failed"
_MODES = ("sync", "async")
_QUERIES = ("records", "links")


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
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = (
                    client.device_add_links.records(page_size=1)
                    if query == "records"
                    else client.device_add_links.list(page_size=1)
                )
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    if query == "records":
                        return await client.device_add_links.records(page_size=1)
                    return await client.device_add_links.list(page_size=1)

            response = asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "query": query,
        "status": "ok",
        "returned_items": len(response.data),
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
