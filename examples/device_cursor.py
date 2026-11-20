"""Read-only example performing one domestic devices.list_by_id call with page size
1, optionally resuming from an opaque cursor in the EZVIZ_DEVICE_CURSOR
environment variable. Prints only returned_items and has_next.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError
from ezviz_unified.devices import _valid_device_cursor

_USAGE = "usage: python -m examples.device_cursor --mode sync|async"
_ERROR = "error: device cursor query failed"
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
        cursor = os.environ.get("EZVIZ_DEVICE_CURSOR")
        if cursor is not None and not _valid_device_cursor(cursor):
            raise ValueError(_ERROR)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.devices.list_by_id(cursor=cursor, page_size=1)
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.devices.list_by_id(cursor=cursor, page_size=1)

            response = asyncio.run(call_async())
        rows = response.data
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError(_ERROR)
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError(_ERROR)
        raw = response.raw
        page = raw.get("page") if isinstance(raw, dict) else None
        if not isinstance(page, dict) or "nextId" not in page:
            raise ValueError(_ERROR)
        next_id = page["nextId"]
        if next_id is None or next_id == "":
            has_next = False
        elif _valid_device_cursor(next_id):
            has_next = True
        else:
            raise ValueError(_ERROR)
        print(
            json.dumps(
                {"returned_items": len(rows), "has_next": has_next},
                separators=(",", ":"),
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
