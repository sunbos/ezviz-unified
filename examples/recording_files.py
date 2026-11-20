"""Query one domestic recording metadata response without fetching media."""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError
from ezviz_unified.recordings import _require_recording_data, _stored_serial, _stored_text_time

_USAGE = "usage: python -m examples.recording_files --mode sync|async --query cloud|local|component"
_ERROR = "error: recording files query failed"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    options = dict(zip(args[::2], args[1::2]))
    mode = options.get("--mode")
    query = options.get("--query")
    if (
        len(args) != 4
        or mode not in ("sync", "async")
        or query not in ("cloud", "local", "component")
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        serial = _stored_serial(os.environ.get("EZVIZ_DEVICE_SERIAL"))
        record_start_time = None
        record_stop_time = None
        if query == "component":
            record_start_time = _stored_text_time(
                os.environ.get("EZVIZ_RECORD_START_TIME"), "EZVIZ_RECORD_START_TIME"
            )
            record_stop_time = _stored_text_time(
                os.environ.get("EZVIZ_RECORD_STOP_TIME"), "EZVIZ_RECORD_STOP_TIME"
            )
            if record_start_time > record_stop_time:
                raise ValueError(_ERROR)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                if query == "component":
                    response = client.recordings.by_text_time(
                        device_serial=serial,
                        start_time=record_start_time,
                        stop_time=record_stop_time,
                    )
                else:
                    response = client.recordings.by_time(
                        serial, rec_type=(1 if query == "cloud" else 2), version="2.0", page_size=1
                    )
        else:

            async def call_async() -> ApiResponse:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    if query == "component":
                        response = await client.recordings.by_text_time(
                            device_serial=serial,
                            start_time=record_start_time,
                            stop_time=record_stop_time,
                        )
                    else:
                        response = await client.recordings.by_time(
                            serial,
                            rec_type=(1 if query == "cloud" else 2),
                            version="2.0",
                            page_size=1,
                        )
                    return response

            response = asyncio.run(call_async())
        response = _require_recording_data(response, allow_file_page=query != "component")
        data = response.data
        if isinstance(data, list):
            rows, has_more = data, None
        else:
            rows, has_more = data["files"], not data["isAll"]
        if query != "component" and len(rows) > 1:
            raise ValueError(_ERROR)
        print(
            json.dumps({"returned_files": len(rows), "has_more": has_more}, separators=(",", ":"))
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
