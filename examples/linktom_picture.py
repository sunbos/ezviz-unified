"""Demonstrate one overseas Linktom picture lookup on in-memory transports."""

from __future__ import annotations

import asyncio
import json
import sys
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import (
    AsyncClient,
    AsyncStaticTokenProvider,
    Client,
    EzvizError,
    RegionConfig,
    StaticTokenProvider,
)

_ORIGIN = "https://synthetic-api.ezvizlife.com"
_PATH = "/api/lapp/device/uuid/picture"
_ACCESS_TOKEN = "demo-static-token"
_CAPTURE_UUID = "synthetic-existing-capture"
_SIZE = 640
_USAGE = "usage: python -m examples.linktom_picture --mode sync|async"
_ERROR = "error: Linktom picture lookup example failed"


def _business_handler(request: httpx.Request) -> httpx.Response:
    expected = sorted(
        [("accessToken", _ACCESS_TOKEN), ("uuid", _CAPTURE_UUID), ("size", str(_SIZE))]
    )
    if (
        request.method != "POST"
        or str(request.url) != _ORIGIN + _PATH
        or request.headers.get("Content-Type") != "application/x-www-form-urlencoded"
        or sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True)) != expected
    ):
        return httpx.Response(400)
    return httpx.Response(
        200, json={"code": "200", "msg": "ok", "data": {"picUrl": "synthetic-reference"}}
    )


def _run_sync() -> bool:
    provider = StaticTokenProvider(_ACCESS_TOKEN, expires_at=4102444800.0, api_base_url=_ORIGIN)
    with Client(
        region=RegionConfig.overseas(api_base_url=_ORIGIN),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_business_handler),
    ) as client:
        result = client.snapshots.get_by_uuid(_CAPTURE_UUID, size=_SIZE)
        if (
            result.operation != "snapshots.get_by_uuid"
            or result.code != "200"
            or not isinstance(result.data, dict)
            or not isinstance(result.data.get("picUrl"), str)
        ):
            raise ValueError(_ERROR)
        return bool(result.data["picUrl"])


async def _run_async() -> bool:
    provider = AsyncStaticTokenProvider(
        _ACCESS_TOKEN, expires_at=4102444800.0, api_base_url=_ORIGIN
    )
    async with AsyncClient(
        region=RegionConfig.overseas(api_base_url=_ORIGIN),
        token_provider=provider,
        transport_factory=lambda origin: httpx.MockTransport(_business_handler),
    ) as client:
        result = await client.snapshots.get_by_uuid(_CAPTURE_UUID, size=_SIZE)
        if (
            result.operation != "snapshots.get_by_uuid"
            or result.code != "200"
            or not isinstance(result.data, dict)
            or not isinstance(result.data.get("picUrl"), str)
        ):
            raise ValueError(_ERROR)
        return bool(result.data["picUrl"])


def main(argv: list[str] | None = None) -> int:
    """Run the simulated Linktom picture lookup in ``sync`` or ``async`` mode."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in ("sync", "async"):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        if mode == "sync":
            present = _run_sync()
        else:
            present = asyncio.run(_run_async())
        print(
            json.dumps({"simulated": True, "picture_ref_present": present}, separators=(",", ":"))
        )
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
