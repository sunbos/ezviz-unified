"""Query domestic image configuration once, read-only.

Run from the repository root::

    python -m examples.image_configuration --mode sync --setting display
    python -m examples.image_configuration --mode async --setting image
    python -m examples.image_configuration --mode sync --setting white-balance

The configured region must be ``domestic``; it is validated before the
client is created. A nonblank ``EZVIZ_DEVICE_SERIAL`` environment
variable is required before the client is constructed. Exactly one read
call is made: ``imaging.get_display_mode`` for ``--setting display``,
or ``imaging.get`` with ``ImageSetting.IMAGE_PARAMS`` /
``ImageSetting.WHITE_BALANCE`` for ``--setting image`` /
``--setting white-balance`` -- no fallback or extra I/O. The JSON
output only reports that the query succeeded -- no image values or the
device serial are printed. ``--help`` prints usage without reading
configuration or touching the network. Importing this module performs
no configuration or network I/O.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, ImageSetting

_USAGE = "usage: python -m examples.image_configuration --mode sync|async --setting display|image|white-balance"
_ERROR = "error: image configuration query failed"
_MODES = ("sync", "async")
_SETTINGS = ("display", "image", "white-balance")


def _query_sync(imaging, serial: str, setting: str):
    if setting == "display":
        return imaging.get_display_mode(serial)
    if setting == "image":
        return imaging.get(serial, ImageSetting.IMAGE_PARAMS)
    return imaging.get(serial, ImageSetting.WHITE_BALANCE)


async def _query_async(imaging, serial: str, setting: str):
    if setting == "display":
        return await imaging.get_display_mode(serial)
    if setting == "image":
        return await imaging.get(serial, ImageSetting.IMAGE_PARAMS)
    return await imaging.get(serial, ImageSetting.WHITE_BALANCE)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if (
        len(args) != 4
        or args[0] != "--mode"
        or args[1] not in _MODES
        or args[2] != "--setting"
        or args[3] not in _SETTINGS
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    setting = args[3]
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        serial = os.environ.get("EZVIZ_DEVICE_SERIAL", "")
        if not serial.strip():
            raise ValueError(_ERROR)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                _query_sync(client.imaging, serial, setting)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await _query_async(client.imaging, serial, setting)

            asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "domestic",
        "mode": mode,
        "setting": setting,
        "status": "ok",
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
