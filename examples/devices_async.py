"""Read the first page of the device list with the asynchronous client.

Run from the repository root: ``python -m examples.devices_async``.
Importing this module reads no configuration and performs no network I/O.
"""

import asyncio
import sys

from examples._common import load_config, print_failure, print_page_summary
from ezviz_unified import AsyncClient, EzvizError


async def main() -> int:
    try:
        config = load_config()
        async with AsyncClient(
            config.app_key, config.app_secret, region=config.region_config
        ) as client:
            items = (await client.devices.list(page_start=0, page_size=1)).data
        if not isinstance(items, list):
            raise ValueError("device list payload is not a list")
    except (EzvizError, ValueError) as exc:
        print_failure(exc)
        return 1
    print_page_summary(config, items)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
