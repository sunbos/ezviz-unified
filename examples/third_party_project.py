"""Query one domestic third-party project and print fixed JSON.

Run from the repository root::

    python -m examples.third_party_project --mode sync --protocol jt808
    python -m examples.third_party_project --mode async --protocol jt808

The configured region must be ``domestic``; it is validated before the
client is created. A single ``third_party_protocols.get_project(protocol)``
call is made per run: no open project, list, registration, host or media
access, polling or any other operation. The server API requires an
enterprise entitlement. ``--help`` prints usage without reading
configuration or touching the network. Importing this module performs no
configuration or network I/O.

Failures print a fixed error line; raw statuses, URLs and SDK payloads
are never emitted.
"""

import asyncio
import json
import sys

from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError

_USAGE = (
    "usage: python -m examples.third_party_project "
    "--mode sync|async --protocol dhreg|tiandy|huawei|jt808"
)
_ERROR = "error: third-party project query failed"
_MODES = ("sync", "async")
_PROTOCOLS = ("dhreg", "tiandy", "huawei", "jt808")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if (
        len(args) != 4
        or args[0] != "--mode"
        or args[1] not in _MODES
        or args[2] != "--protocol"
        or args[3] not in _PROTOCOLS
    ):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    protocol = args[3]
    try:
        config = load_config()
        if config.region != "domestic":
            raise ValueError(_ERROR)
        if mode == "sync":
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
            ) as client:
                client.third_party_protocols.get_project(protocol)
        else:

            async def call_async():
                async with AsyncClient(
                    config.app_key,
                    config.app_secret,
                    region=config.region_config,
                ) as client:
                    await client.third_party_protocols.get_project(protocol)

            asyncio.run(call_async())
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
        "protocol": protocol,
    }
    print(json.dumps(success, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
