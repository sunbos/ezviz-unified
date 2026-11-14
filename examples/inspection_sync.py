"""Run the read-only single-device inspection with the synchronous client.

Run from the repository root: ``python -m examples.inspection_sync``.
Importing this module reads no configuration and performs no network I/O.
"""

import json
import sys

from examples._common import ConfigError, load_config, print_failure
from examples._inspection import load_target
from ezviz_unified import Client, EzvizError, inspect_device


def main() -> int:
    try:
        config = load_config()
        target = load_target(config.region)
        with Client(config.app_key, config.app_secret, region=config.region_config) as client:
            report = inspect_device(client, target.device_serial, channel=target.channel)
    except (ConfigError, EzvizError) as exc:
        print_failure(exc)
        return 1
    print(json.dumps(report.public_summary()))
    return 0 if report.all_succeeded else 2


if __name__ == "__main__":
    sys.exit(main())
