"""Read an overseas device's working mode week plans once and print fixed JSON.

Run from the repository root::

    python -m examples.working_mode_plans --mode sync
    python -m examples.working_mode_plans --mode async

``EZVIZ_APP_KEY``, ``EZVIZ_APP_SECRET`` and ``EZVIZ_DEVICE_SERIAL`` must
be set and ``EZVIZ_REGION`` must be ``overseas``; the standard optional
``EZVIZ_AUTH_BASE_URL`` may override the auth endpoint and a successful
token response supplies ``areaDomain``. A single
``settings.get_timing_plan(serial)`` call is made per run: it is one
logical query only, nothing is written and there is no polling.
``--help`` prints usage without reading configuration or touching the
network, and importing this module performs no configuration or
network I/O.

The JSON output only exports ``region``, ``mode``, ``status`` and
``week_plan_count`` (the number of week-plan groups, not days or time
slots). Failures print a fixed error line; raw payloads, URLs and SDK
details are never emitted.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.working_mode_plans --mode sync|async"
_ERROR = "error: working mode plan query failed"
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
        if config.region != "overseas":
            raise ValueError(_ERROR)
        serial = _header("device_serial", os.environ.get("EZVIZ_DEVICE_SERIAL"))
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.settings.get_timing_plan(serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.settings.get_timing_plan(serial)

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict):
            raise ValueError(_ERROR)
        week_plans = data.get("weekPlans")
        if not isinstance(week_plans, list):
            raise ValueError(_ERROR)
        success = {
            "region": "overseas",
            "mode": mode,
            "status": "ok",
            "week_plan_count": len(week_plans),
        }
        print(json.dumps(success, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
