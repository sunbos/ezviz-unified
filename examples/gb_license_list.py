"""
Read one page of license rows for the domestic GB project configured by
EZVIZ_PRODUCT_KEY without displaying registration records or secrets.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError
from ezviz_unified.settings import _check_text

_USAGE = "usage: python -m examples.gb_license_list --mode sync|async"
_ERROR = "error: GB license list query failed"
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
        product_key = _check_text(os.environ.get("EZVIZ_PRODUCT_KEY"), "product_key")
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.third_party_protocols.list_gb_licenses(
                    product_key, page_index=0, page_size=1
                )
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.third_party_protocols.list_gb_licenses(
                        product_key, page_index=0, page_size=1
                    )

            response = asyncio.run(call_async())
        rows = response.data
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError(_ERROR)
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError(_ERROR)
        print(json.dumps({"returned_items": len(rows)}, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
