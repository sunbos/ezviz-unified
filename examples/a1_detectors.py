"""Read an overseas A1 host's detector counts once and print fixed JSON.

Run from the repository root::

    python -m examples.a1_detectors --mode sync --query detectors
    python -m examples.a1_detectors --mode async --query linked

``EZVIZ_APP_KEY``, ``EZVIZ_APP_SECRET`` and ``EZVIZ_DEVICE_SERIAL`` (the
A1 host) must be set and ``EZVIZ_REGION`` must be ``overseas``; the
standard optional ``EZVIZ_AUTH_BASE_URL`` may override the auth
endpoint. ``EZVIZ_DETECTOR_SERIAL`` is additionally required only for
``--query linked``. Exactly one ``detectors`` call is made per run --
``list``, ``bindable_cameras`` or ``linked_cameras`` -- a single
logical read with no writes, no discovery and no pagination.
``--help`` prints usage without reading configuration or touching the
network, and importing this module performs no configuration or
network I/O.

The JSON output only exports ``region``, ``mode``, ``query``,
``status`` and ``returned_items``: the number of entries in
``response.data``. Individual rows, raw payloads, URLs and SDK details
are never emitted. Failures print one fixed error line.
"""

import asyncio
import json
import os
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.settings import _check_text

_USAGE = (
    "usage: python -m examples.a1_detectors --mode sync|async --query detectors|bindable|linked"
)
_ERROR = "error: A1 detector query failed"
_MODES = ("sync", "async")
_QUERIES = ("detectors", "bindable", "linked")


def _call(detectors, query, a1_serial, detector_serial):
    if query == "detectors":
        return detectors.list(a1_serial)
    if query == "bindable":
        return detectors.bindable_cameras(a1_serial)
    return detectors.linked_cameras(a1_serial, detector_serial)


async def _acall(detectors, query, a1_serial, detector_serial):
    if query == "detectors":
        return await detectors.list(a1_serial)
    if query == "bindable":
        return await detectors.bindable_cameras(a1_serial)
    return await detectors.linked_cameras(a1_serial, detector_serial)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if (
        len(args) != 4
        or args[0] != "--mode"
        or args[2] != "--query"
        or args[1] not in _MODES
        or args[3] not in _QUERIES
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    mode, query = args[1], args[3]
    try:
        config = load_config()
        if config.region != "overseas":
            raise ValueError(_ERROR)
        a1_serial = _check_text(os.environ.get("EZVIZ_DEVICE_SERIAL"), "a1_serial")
        detector_serial = (
            _check_text(os.environ.get("EZVIZ_DETECTOR_SERIAL"), "detector_serial")
            if query == "linked"
            else None
        )
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                response = _call(client.detectors, query, a1_serial, detector_serial)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    return await _acall(client.detectors, query, a1_serial, detector_serial)

            response = asyncio.run(call_async())
        try:
            returned_items = len(response.data)
        except TypeError as exc:
            raise ValueError(_ERROR) from exc
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    success = {
        "region": "overseas",
        "mode": mode,
        "query": query,
        "status": "ok",
        "returned_items": returned_items,
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
