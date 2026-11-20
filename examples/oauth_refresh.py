"""Minimal OAuth refresh-token demo that never touches the network.

Both modes inject an ``httpx.MockTransport`` so the refresh is served
entirely in memory with synthetic Singapore endpoints and demo values.
"""

import asyncio
import json
import sys

import httpx

from ezviz_unified.errors import EzvizError
from ezviz_unified.oauth import AsyncOAuth, OAuth, OAuthRefreshResult

USAGE = "usage: python -m examples.oauth_refresh --mode sync|async"
ERROR = "error: OAuth refresh example failed"

AUTH_ORIGIN = "https://isgpopenauth.ezvizlife.com"
API_ORIGIN = "https://isgpopen.ezvizlife.com"

APP_KEY = "demo-app-key"
OLD_REFRESH_TOKEN = "demo-old-refresh-token"
OPEN_ID = "demo-open-id"
BUNDLE_ID = "com.example.demo"
STATE = "demo-state-123"

FRESH_ACCESS_TOKEN = "demo-fresh-access-token"
FRESH_REFRESH_TOKEN = "demo-fresh-refresh-token"


def _handler(request: httpx.Request) -> httpx.Response:
    """Serve a canned successful refresh response with matching state."""
    return httpx.Response(
        200,
        json={
            "code": "200",
            "msg": "OK",
            "data": {
                "access_token": FRESH_ACCESS_TOKEN,
                "refresh_token": FRESH_REFRESH_TOKEN,
                "state": STATE,
            },
        },
    )


def _refresh_sync() -> OAuthRefreshResult:
    """Call ``OAuth.refresh_token`` through the mock transport."""
    oauth = OAuth(APP_KEY, auth_origin=AUTH_ORIGIN, api_origin=API_ORIGIN)
    return oauth.refresh_token(
        OLD_REFRESH_TOKEN,
        open_id=OPEN_ID,
        bundle_id=BUNDLE_ID,
        state=STATE,
        transport=httpx.MockTransport(_handler),
    )


async def _refresh_async() -> OAuthRefreshResult:
    """Await ``AsyncOAuth.refresh_token`` through the mock transport."""
    oauth = AsyncOAuth(APP_KEY, auth_origin=AUTH_ORIGIN, api_origin=API_ORIGIN)
    return await oauth.refresh_token(
        OLD_REFRESH_TOKEN,
        open_id=OPEN_ID,
        bundle_id=BUNDLE_ID,
        state=STATE,
        transport=httpx.MockTransport(_handler),
    )


def _check(result: OAuthRefreshResult) -> None:
    """Ensure the result carries the expected fresh demo tokens."""
    if result.access_token != FRESH_ACCESS_TOKEN or result.refresh_token != FRESH_REFRESH_TOKEN:
        raise ValueError("refresh result did not contain the expected fresh demo tokens")


def main(argv: list[str] | None = None) -> int:
    """Run the refresh example in ``sync`` or ``async`` mode."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in ("sync", "async"):
        print(USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        result = _refresh_sync() if mode == "sync" else asyncio.run(_refresh_async())
        _check(result)
    except (EzvizError, ValueError, OSError):
        print(ERROR, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(ERROR, file=sys.stderr)
        return 130
    print(json.dumps({"mode": mode, "refreshed": True}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
