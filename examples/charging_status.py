"""Read a domestic device's charging status once and print fixed JSON.

Run from the repository root::

    python -m examples.charging_status --mode sync
    python -m examples.charging_status --mode async

``EZVIZ_DEVICE_SERIAL`` must be set to a serial acceptable as an HTTP
header value and the configured region must be ``domestic``; both are
validated before the client is created. A single
``devices.charging_status(serial)`` call is made per run: no polling,
percentage/status calls, or any other device operation. The read is
declared read-only, so a token refresh may replay that one logical query
at most once. ``--help`` prints usage without reading configuration or
touching the network. Importing this module performs no configuration or
network I/O.

The JSON output only echoes documented ``powerStatus`` strings ``"0"``
(not charging), ``"1"`` (charging), ``"2"`` (full), ``"3"`` (no battery)
and ``"4"`` (battery fault); any other string is reported as ``null``.
This response carries no battery percentage or sampling timestamp;
``devices.status()`` reports ``data.battryStatus`` separately and the two
readings are not guaranteed to share a sample. Failures print a fixed
error line; raw statuses, URLs and SDK payloads are never emitted.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.charging_status --mode sync|async"
_ERROR = "error: charging status query failed"
_MODES = ("sync", "async")
_KNOWN_POWER_STATUS = frozenset({"0", "1", "2", "3", "4"})


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
        serial = _header("device_serial", os.environ.get("EZVIZ_DEVICE_SERIAL"))
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.devices.charging_status(serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.devices.charging_status(serial)

            response = asyncio.run(call_async())
        try:
            power_status = response.data["valueInfo"]["powerStatus"]
        except (KeyError, TypeError) as exc:
            raise ValueError(_ERROR) from exc
        if not isinstance(power_status, str):
            raise ValueError(_ERROR)
        charging_status = power_status if power_status in _KNOWN_POWER_STATUS else None
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError):
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "charging_status": charging_status,
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
