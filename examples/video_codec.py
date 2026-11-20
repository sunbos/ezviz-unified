"""Query a domestic device's video codec once, read-only.

Run from the repository root::

    python -m examples.video_codec --mode sync --stream 1
    python -m examples.video_codec --mode async --stream 2

``EZVIZ_DEVICE_SERIAL`` must be nonempty printable ASCII without leading or
trailing spaces, the optional ``EZVIZ_CHANNEL_NO`` must be an ASCII decimal,
optionally negative, and the configured region must be ``domestic``; all are
checked before the client is created. A single ``video.get_video_codec`` call is
made and the JSON output only reports the video code and its label -- the serial
and channel stay hidden. ``--help`` prints usage without reading configuration or
touching the network. Importing this module performs no configuration or network
I/O.
"""

import asyncio
import json
import os
import re
import sys

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError

_USAGE = "usage: python -m examples.video_codec --mode sync|async --stream 1|2"
_ERROR = "error: video codec query failed"
_CODEC_LABELS = {
    0: "private_h264",
    1: "standard_h264",
    2: "standard_mpeg4",
    3: "standard_mpeg2",
    4: "mjpeg",
    5: "standard_h265",
    6: "smart264",
    7: "smart265",
}


def _check_serial(value: object) -> str:
    """Return a serial usable in the device serial header."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip(" ")
        or any(ord(char) < 0x20 or ord(char) > 0x7E for char in value)
    ):
        raise ValueError(_ERROR)
    return value


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    options = dict(zip(args[::2], args[1::2]))
    mode = options.get("--mode")
    stream_text = options.get("--stream")
    if len(args) != 4 or mode not in ("sync", "async") or stream_text not in ("1", "2"):
        print(_USAGE, file=sys.stderr)
        return 1
    stream = int(stream_text)
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        serial = _check_serial(os.environ.get("EZVIZ_DEVICE_SERIAL"))
        channel_raw = os.environ.get("EZVIZ_CHANNEL_NO")
        if channel_raw is None:
            channel = None
        elif re.fullmatch(r"-?[0-9]+", channel_raw) is None:
            raise ValueError(_ERROR)
        else:
            channel = int(channel_raw)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = client.video.get_video_codec(serial, stream=stream, channel=channel)
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await client.video.get_video_codec(
                        serial, stream=stream, channel=channel
                    )

            response = asyncio.run(call_async())
        data = response.data
        if not isinstance(data, dict) or type(data.get("videoCode")) is not int:
            raise ValueError(_ERROR)
        code = data["videoCode"]
        label = _CODEC_LABELS.get(code)
        if label is None:
            print(json.dumps({"video_code": None, "codec": "unknown"}, separators=(",", ":")))
        else:
            print(json.dumps({"video_code": code, "codec": label}, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
