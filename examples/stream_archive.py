"""Run one domestic stream archive query with the sync or async client.

    python -m examples.stream_archive --mode sync --kind recording
    python -m examples.stream_archive --mode async --kind index

Env: EZVIZ_STREAM_ID (required), EZVIZ_QUERY_DATE (required for recording,
optional for snapshot), EZVIZ_STREAM_START/EZVIZ_STREAM_STOP as canonical
'YYYY-MM-DD HH:MM:SS' (index). ``--help`` prints usage without reading
configuration or the network; importing this module performs no I/O.
"""

import asyncio
import json
import os
import sys
from datetime import datetime

from examples._common import load_config
from ezviz_unified import AsyncClient, Client

_USAGE = (
    "usage: python -m examples.stream_archive --mode sync|async --kind recording|snapshot|index"
)
_ERROR = "error: stream archive query failed"
_FMT = "%Y-%m-%d %H:%M:%S"


def _stamp(name: str) -> datetime:
    raw = os.environ.get(name, "")
    parsed = datetime.strptime(raw, _FMT)
    if parsed.isoformat(sep=" ", timespec="seconds") != raw:
        raise ValueError(_ERROR)
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    opts = dict(zip(args[::2], args[1::2]))
    mode, kind = opts.get("--mode"), opts.get("--kind")
    if (
        len(args) != 4
        or len(opts) != 2
        or mode not in ("sync", "async")
        or kind not in ("recording", "snapshot", "index")
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    try:
        stream_id = os.environ.get("EZVIZ_STREAM_ID")
        if not stream_id:
            raise ValueError(_ERROR)
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        if kind == "index":
            params = {
                "stream_id": stream_id,
                "start_time": _stamp("EZVIZ_STREAM_START"),
                "stop_time": _stamp("EZVIZ_STREAM_STOP"),
            }
        else:
            date = os.environ.get("EZVIZ_QUERY_DATE")
            if kind == "recording" and date is None:
                raise ValueError(_ERROR)
            params = {
                "stream_id": stream_id,
                "file_type": 1 if kind == "recording" else 2,
                "query_date": date,
            }

        def pick(vod):
            return vod.stream_index_file if kind == "index" else vod.stream_source_files

        if mode == "sync":
            with Client(config.app_key, config.app_secret, region=config.region_config) as client:
                pick(client.vod)(**params)
        else:

            async def run() -> None:
                async with AsyncClient(
                    config.app_key, config.app_secret, region=config.region_config
                ) as client:
                    await pick(client.vod)(**params)

            asyncio.run(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"region": "domestic", "kind": kind, "mode": mode, "status": "ok"},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
