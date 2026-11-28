"""Count the projects on one page of the domestic cloud-recording 1.0 list.

The example is fully simulated: synthetic credentials plus an in-process
auth/business ``MockTransport`` pair answering a single canned page. The real
config loader is never imported, no environment variable is read and no
network is touched. The example prints only the row count. The sibling
``cloud_project_files`` and ``cloud_tasks`` examples reuse the fabricated
credentials, auth handler and client kwargs defined here.
"""

import asyncio
import json
import sys
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError, RegionConfig

_USAGE = "usage: python -m examples.cloud_projects --mode sync|async"
_ERROR = "error: cloud projects example failed"
_MODES = ("sync", "async")
_APP_KEY = "demo-app-key"
_APP_SECRET = "demo-app-secret"
_ACCESS_TOKEN = "demo-access-token"
_AREA_DOMAIN = "https://open.ys7.com"
_PATH = "/api/open/cloud/v1/projects"
_ROWS = [
    {
        "projectId": "demo-project",
        "projectName": "Demo project",
        "expireDays": 30,
    }
]


def _auth_handler(request: httpx.Request) -> httpx.Response:
    if request.method != "POST" or request.url.path != "/api/lapp/token/get":
        return httpx.Response(404)
    if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return httpx.Response(400)
    form = sorted(parse_qsl(request.content.decode("utf-8"), keep_blank_values=True))
    expected = sorted([("appKey", _APP_KEY), ("appSecret", _APP_SECRET)])
    if request.url.params or form != expected:
        return httpx.Response(400)
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": _ACCESS_TOKEN,
                "expireTime": 9_999_999_999_999,
                "areaDomain": _AREA_DOMAIN,
            },
        },
    )


def _client_kwargs(business_handler) -> dict:
    """True-client kwargs: canned auth transport plus ``business_handler``."""
    return {
        "region": RegionConfig.china(),
        "auth_transport": httpx.MockTransport(_auth_handler),
        "transport_factory": lambda origin: httpx.MockTransport(business_handler),
    }


def _business_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == _PATH:
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": "ok", "moreInfo": None}, "data": _ROWS},
        )
    return httpx.Response(404, json={"meta": {"code": 404, "message": "not found"}})


def _demo_sync() -> ApiResponse:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs(_business_handler)) as client:
        return client.cloud_recording_v1.projects(page_number=0, page_size=1)


async def _demo_async() -> ApiResponse:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs(_business_handler)) as client:
        return await client.cloud_recording_v1.projects(page_number=0, page_size=1)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in _MODES:
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]
    try:
        response = _demo_sync() if mode == "sync" else asyncio.run(_demo_async())
        rows = response.data
        if not isinstance(rows, list):
            raise ValueError(_ERROR)
        print(json.dumps({"items": len(rows)}, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
