"""Run the read-only single-device inspection with the asynchronous client.

Run from the repository root: ``python -m examples.inspection_async``.
Importing this module reads no configuration and performs no network I/O.
"""

import asyncio
import json
import sys

from examples._common import ConfigError, load_config, print_failure
from examples._inspection import load_target
from ezviz_unified import AsyncClient, EzvizError, inspect_device_async


async def main() -> int:
    try:
        config = load_config()
        target = load_target(config.region)
        async with AsyncClient(
            config.app_key, config.app_secret, region=config.region_config
        ) as client:
            report = await inspect_device_async(
                client, target.device_serial, channel=target.channel
            )
    except (ConfigError, EzvizError) as exc:
        print_failure(exc)
        return 1
    print(json.dumps(report.public_summary()))
    return 0 if report.all_succeeded else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
