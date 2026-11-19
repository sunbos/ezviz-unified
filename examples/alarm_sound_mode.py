"""Read an overseas device's alarm sound mode once and print fixed JSON.

Run from the repository root::

    python -m examples.alarm_sound_mode --mode sync
    python -m examples.alarm_sound_mode --mode async

``EZVIZ_APP_KEY``, ``EZVIZ_APP_SECRET`` and ``EZVIZ_DEVICE_SERIAL`` must
be set and ``EZVIZ_REGION`` must be ``overseas``; the standard optional
``EZVIZ_AUTH_BASE_URL`` may override the auth endpoint and a successful
token response supplies ``areaDomain``. A single
``settings.get_alarm_sound_mode(serial)`` call is made per run: it is
one logical query only, no actual sound is played and nothing is
written. ``--help`` prints usage without reading configuration or
touching the network, and importing this module performs no
configuration or network I/O.

The JSON output only exports ``region``, ``mode``, ``status`` and
``alarm_sound_mode``: known modes ``0``, ``1`` and ``2`` mean short,
long and mute per E80, and any other integer is reported as ``null``.
Failures print a fixed error line; raw payloads, URLs and SDK details
are never emitted.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.vod import _header

_USAGE = "usage: python -m examples.alarm_sound_mode --mode sync|async"
_ERROR = "error: alarm sound mode query failed"
_MODES = ("sync", "async")
_KNOWN_ALARM_MODES = frozenset({0, 1, 2})


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
                response = client.settings.get_alarm_sound_mode(serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.settings.get_alarm_sound_mode(serial)

            response = asyncio.run(call_async())
        try:
            mode_raw = response.data["alarmSoundMode"]
        except (KeyError, TypeError) as exc:
            raise ValueError(_ERROR) from exc
        if not isinstance(mode_raw, int) or isinstance(mode_raw, bool):
            raise ValueError(_ERROR)
        mode_code = mode_raw if mode_raw in _KNOWN_ALARM_MODES else None
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "overseas",
        "mode": mode,
        "status": "ok",
        "alarm_sound_mode": mode_code,
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
