"""Query passenger flow daily or hourly counts once, read-only.

Run from the repository root::

    python -m examples.passenger_flow --mode sync --report daily
    python -m examples.passenger_flow --mode async --report hourly

Requires local ``EZVIZ_DEVICE_SERIAL`` and ``EZVIZ_CHANNEL``. ``daily``
accepts optional ``EZVIZ_FLOW_DAY`` as an aware local-midnight ISO datetime;
``hourly`` accepts optional ``EZVIZ_FLOW_DATE_MS`` as a signed decimal
integer. ``--help`` prints usage without reading configuration or touching
the network. Importing this module performs no configuration or network I/O.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime

from examples._common import load_config
from examples._inspection import load_target
from ezviz_unified import AsyncClient, Client

_USAGE = "usage: python -m examples.passenger_flow --mode sync|async --report daily|hourly"
_ERROR = "error: passenger flow query failed"
_MODES = ("sync", "async")
_REPORTS = ("daily", "hourly")
_DATE_MS_RE = re.compile(r"[+-]?[0-9]+")


def _report_kwargs(report: str) -> dict:
    if report == "daily":
        raw = os.environ.get("EZVIZ_FLOW_DAY")
        return {"day": None if raw is None else datetime.fromisoformat(raw)}
    raw = os.environ.get("EZVIZ_FLOW_DATE_MS")
    if raw is not None and _DATE_MS_RE.fullmatch(raw) is None:
        raise ValueError(_ERROR)
    return {"date_ms": None if raw is None else int(raw)}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if (
        len(args) != 4
        or args[0] != "--mode"
        or args[1] not in _MODES
        or args[2] != "--report"
        or args[3] not in _REPORTS
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    mode, report = args[1], args[3]
    try:
        if "EZVIZ_DEVICE_SERIAL" not in os.environ or "EZVIZ_CHANNEL" not in os.environ:
            raise ValueError(_ERROR)
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        target = load_target("domestic")
        kwargs = _report_kwargs(report)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                getattr(client.passenger_flow, report)(
                    target.device_serial, target.channel, **kwargs
                )
        else:

            async def call_async() -> None:
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await getattr(client.passenger_flow, report)(
                        target.device_serial, target.channel, **kwargs
                    )

            asyncio.run(call_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    success = {"region": "domestic", "mode": mode, "report": report, "status": "ok"}
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
