"""Synthetic, fully offline OAuth login example for the Singapore region.

Builds a login URL and parses a synthetic ``/oauth/success`` redirect
fragment using only local SDK computation. No network, browser,
listener, account configuration, or token persistence is involved and
every value is a demo string.
"""

from __future__ import annotations

import asyncio
import json
import sys
from urllib.parse import urlencode

from ezviz_unified.errors import EzvizError
from ezviz_unified.oauth import AsyncOAuth, OAuth, OAuthGrant

USAGE = "usage: python -m examples.oauth_login --mode sync|async"
_ERROR_MESSAGE = "error: OAuth login example failed"

_AUTH_ORIGIN = "https://isgpopenauth.ezvizlife.com"
_API_ORIGIN = "https://isgpopen.ezvizlife.com"
_APP_KEY = "demo-app-key"
_AREA_ID = 1
_CLIENT_TYPE_PC = 19

_SUCCESS_FRAGMENT = urlencode(
    {
        "access_token": "demo-access-token",
        "areaDomain": _API_ORIGIN,
        "authDomain": _AUTH_ORIGIN,
    }
)
_REDIRECT_URL = f"{_AUTH_ORIGIN}/oauth/success#{_SUCCESS_FRAGMENT}"


def _verify(login_url: str, grant: object) -> None:
    """Fail loudly instead of reporting silent false outcomes."""
    if not login_url:
        raise ValueError("login URL was not built")
    if not isinstance(grant, OAuthGrant) or not grant.access_token:
        raise ValueError("redirect did not produce an OAuthGrant")


def _run_sync() -> None:
    oauth = OAuth(_APP_KEY, auth_origin=_AUTH_ORIGIN, api_origin=_API_ORIGIN)
    login_url = oauth.build_login_url(_AREA_ID, client_type=_CLIENT_TYPE_PC, response_type="token")
    grant = oauth.parse_redirect(_REDIRECT_URL, response_type="token")
    _verify(login_url, grant)


async def _run_async() -> None:
    oauth = AsyncOAuth(_APP_KEY, auth_origin=_AUTH_ORIGIN, api_origin=_API_ORIGIN)
    login_url = await oauth.build_login_url(
        _AREA_ID, client_type=_CLIENT_TYPE_PC, response_type="token"
    )
    grant = await oauth.parse_redirect(_REDIRECT_URL, response_type="token")
    _verify(login_url, grant)


def main(argv: list[str] | None = None) -> int:
    """Run the synthetic OAuth login example."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 1 and args[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in ("sync", "async"):
        print(USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        if mode == "sync":
            _run_sync()
        else:
            asyncio.run(_run_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR_MESSAGE, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR_MESSAGE, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"mode": mode, "login_url_built": True, "redirect_parsed": True},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
