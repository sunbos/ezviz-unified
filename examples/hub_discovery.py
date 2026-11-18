"""Explicit parent/child HUB discovery example (domestic region only).

The workflow performs at most three logical SDK calls in a fixed order
against two explicitly provided serials:

1. ``hubs.list_subdevices(hub_serial)`` -- the parent's sub-device list.
2. ``hubs.subdevice_info(subdevice_serial)`` -- the child's detail record.
3. ``hubs.channels(subdevice_serial)`` -- the child's channel list.

The example never infers a child serial from the parent list, never
rewrites or concatenates serials, and performs no pairing or control
calls. Output is a fixed JSON whitelist of counts and an identity flag:
IDs, URLs, tokens, raw payloads, offsets and environment values are
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from typing import NoReturn

from ezviz_unified import AsyncClient, Client, EzvizError
from ezviz_unified.models import ApiResponse

from ._common import ConfigError, ExampleConfig, load_config

_DOMESTIC = "domestic"
_HUB_ENV = "EZVIZ_HUB_SERIAL"
_SUB_ENV = "EZVIZ_SUBDEVICE_SERIAL"


class _ArgumentError(Exception):
    """Raised when argparse would otherwise echo raw argv to stderr."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgumentError


def _usable_serial(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any("a" <= char <= "z" for char in value)


def _validate(client: Client | AsyncClient, hub_serial: object, sub_serial: object) -> None:
    """Fail fast on known-illegal inputs before any request is sent."""
    if not _usable_serial(hub_serial) or not _usable_serial(sub_serial):
        raise ValueError("serials must be non-empty UTF-8 strings without ASCII lowercase")
    if client.region.scope != _DOMESTIC:
        raise ValueError("the hubs facade is only supported in the domestic region")


def run_sync(
    client: Client,
    hub_serial: str,
    subdevice_serial: str,
) -> tuple[ApiResponse, ApiResponse, ApiResponse]:
    """Run the three-call workflow on a caller-owned synchronous client.

    Returns ``(list_subdevices, subdevice_info, channels)`` responses and
    stops at the first failure. The caller keeps full ownership of the
    client lifecycle; this function never creates or closes clients.
    """
    _validate(client, hub_serial, subdevice_serial)
    listed = client.hubs.list_subdevices(hub_serial)
    info = client.hubs.subdevice_info(subdevice_serial)
    channels = client.hubs.channels(subdevice_serial)
    return listed, info, channels


async def run_async(
    client: AsyncClient,
    hub_serial: str,
    subdevice_serial: str,
) -> tuple[ApiResponse, ApiResponse, ApiResponse]:
    """Async variant of :func:`run_sync` with identical order and limits."""
    _validate(client, hub_serial, subdevice_serial)
    listed = await client.hubs.list_subdevices(hub_serial)
    info = await client.hubs.subdevice_info(subdevice_serial)
    channels = await client.hubs.channels(subdevice_serial)
    return listed, info, channels


def _identity_consistency(
    list_data: list[object],
    info_data: dict[str, object],
    child_serial: str,
) -> str:
    """Look for the explicit child serial in the detail record and list.

    The detail ``deviceSerial`` is checked first: a missing or non-string
    value degrades the verdict to ``unknown``, and a string different
    from ``child_serial`` yields ``not_matched``. When the detail matches,
    the sub-device list is searched for the target: any dict entry whose
    ``deviceSerial`` is a string equal to ``child_serial`` yields
    ``matched``; sibling devices or unknown rows cannot veto a found
    target. If no list entry names the target, a non-empty list in which
    every item is a dict with a string ``deviceSerial`` yields
    ``not_matched``; otherwise (an empty list or any unknown item) the
    verdict is ``unknown``. Serials are never normalized, rewritten or
    concatenated, and an empty list is not read as "account has no
    devices". This is a response identity check only -- not proof of
    hardware correctness or model coverage.
    """
    detail_serial = info_data.get("deviceSerial")
    if not isinstance(detail_serial, str):
        return "unknown"
    if detail_serial != child_serial:
        return "not_matched"
    all_serials_known = bool(list_data)
    for item in list_data:
        if not isinstance(item, dict):
            all_serials_known = False
            continue
        serial = item.get("deviceSerial")
        if not isinstance(serial, str):
            all_serials_known = False
        elif serial == child_serial:
            return "matched"
    if all_serials_known:
        return "not_matched"
    return "unknown"


def _summarize(
    mode: str,
    responses: tuple[ApiResponse, ApiResponse, ApiResponse],
    child_serial: str,
) -> dict[str, object] | None:
    """Build the whitelisted success report or None on shape violations."""
    listed, info, channels = responses
    list_data, info_data, channel_data = listed.data, info.data, channels.data
    if (
        not isinstance(list_data, list)
        or not isinstance(info_data, dict)
        or not isinstance(channel_data, list)
    ):
        return None
    return {
        "status": "ok",
        "mode": mode,
        "subdevice_count": len(list_data),
        "channel_count": len(channel_data),
        "identity_consistency": _identity_consistency(list_data, info_data, child_serial),
    }


def _env_serial(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ConfigError(name)
    return value


def _fail(category: str) -> int:
    print(json.dumps({"status": "failed", "category": category}), file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m examples.hub_discovery",
        description=(
            "Domestic-only HUB discovery: list a parent's sub-devices, then "
            "read one explicitly named child's detail and channels. At most "
            "three read calls; no pairing, paging or control operations."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("sync", "async"),
        required=True,
        help="Run with the synchronous Client or the asynchronous AsyncClient.",
    )
    return parser


async def _run_async_cli(
    config: ExampleConfig,
    hub_serial: str,
    subdevice_serial: str,
) -> tuple[ApiResponse, ApiResponse, ApiResponse]:
    async with AsyncClient(
        app_key=config.app_key,
        app_secret=config.app_secret,
        region=config.region_config,
    ) as client:
        return await run_async(client, hub_serial, subdevice_serial)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a process exit code, never prints raw data."""
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentError:
        return _fail("input_error")
    try:
        config = load_config()
        hub_serial = _env_serial(_HUB_ENV)
        subdevice_serial = _env_serial(_SUB_ENV)
    except ConfigError:
        return _fail("configuration_error")
    if config.region != _DOMESTIC:
        return _fail("configuration_error")
    try:
        if args.mode == "sync":
            with Client(
                app_key=config.app_key,
                app_secret=config.app_secret,
                region=config.region_config,
            ) as client:
                responses = run_sync(client, hub_serial, subdevice_serial)
        else:
            responses = asyncio.run(_run_async_cli(config, hub_serial, subdevice_serial))
    except EzvizError:
        return _fail("sdk_error")
    except ValueError:
        return _fail("input_error")
    except Exception:  # noqa: BLE001 - unexpected failures stay fully redacted
        return _fail("unexpected_response")
    report = _summarize(args.mode, responses, subdevice_serial)
    if report is None:
        return _fail("unexpected_response")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
