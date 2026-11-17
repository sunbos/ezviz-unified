"""List the public country/region directory (overseas, credentials-free).

Run from the repository root: ``python -m examples.public_areas`` for the
synchronous client or ``python -m examples.public_areas --async`` for the
asynchronous one. Importing this module, showing ``--help`` or passing
invalid arguments never issues a request by itself: a single
``settings.list_areas`` call happens only after the mode is chosen and a
client is constructed.

This example needs no credentials and reads no environment variables. It
targets the documented public origin ``https://open.ezvizlife.com``
directly through ``RegionConfig.overseas(api_base_url=...)``; nothing is
derived from a token, a cluster name or an ``areaDomain``. The response
``data`` array is never dumped: only a fixed count of items is printed
(``status=ok``, ``items=<len>``) when the top level is a list. When the
top level is not a list, a fixed ``unexpected_data_type`` status with
``items=null`` is printed instead and the exit code is 1. No field-level
acceptance is performed — only the top-level type and item count are
reported. No retries, no file writes, no logging.
"""

from __future__ import annotations

import asyncio
import json
import sys

from ezviz_unified import AsyncClient, Client, EzvizError, RegionConfig

_PUBLIC_ORIGIN = "https://open.ezvizlife.com"
_USAGE = "usage: python -m examples.public_areas [--async]"
_FAILURE_MESSAGE = "public areas example failed."
_INTERRUPTED_MESSAGE = "public areas example interrupted."
_UNEXPECTED_TYPE_MESSAGE = "public areas example: response data is not a list."


def _region() -> RegionConfig:
    # Documented public origin only; callers may pick another overseas
    # origin in their own code, but this example never derives one.
    return RegionConfig.overseas(api_base_url=_PUBLIC_ORIGIN)


def _summarize(data: object) -> dict:
    """Bound the directory payload to a fixed count/type summary."""
    if isinstance(data, list):
        return {"status": "ok", "items": len(data)}
    return {"status": "unexpected_data_type", "items": None}


def _print_summary(response) -> int:
    summary = _summarize(response.data)
    print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] != "ok":
        print(_UNEXPECTED_TYPE_MESSAGE, file=sys.stderr)
    return 0 if summary["status"] == "ok" else 1


def _failure(exc: Exception) -> int:
    # Never echo message/payload chains or SDK exception class names.
    print(_FAILURE_MESSAGE, file=sys.stderr)
    return 1


def _sync_main() -> int:
    try:
        with Client(anonymous=True, region=_region()) as client:
            return _print_summary(client.settings.list_areas())
    except EzvizError as exc:
        return _failure(exc)


async def _async_main() -> int:
    try:
        async with AsyncClient(anonymous=True, region=_region()) as client:
            return _print_summary(await client.settings.list_areas())
    except EzvizError as exc:
        return _failure(exc)


def main(argv: list[str] | None = None) -> int:
    """Entry point; parse ``--async`` then issue exactly one directory call."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if args == []:
        return _sync_main()
    if args == ["--async"]:
        return asyncio.run(_async_main())
    print(_USAGE, file=sys.stderr)
    return 2


def run_cli() -> int:
    """CLI boundary: collapse unexpected failures to a fixed exit status."""
    try:
        return main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_INTERRUPTED_MESSAGE, file=sys.stderr)
        return 130
    except Exception:
        print(_FAILURE_MESSAGE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run_cli())
