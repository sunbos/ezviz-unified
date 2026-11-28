"""Show one domestic cloud-recording 1.0 project (doc 1363), fully simulated.

Synthetic credentials plus the shared in-process auth ``MockTransport`` from
``examples.cloud_projects``; the business transport answers a single canned
project object. The config loader never runs, no environment variable is read
and no network is touched. The example prints only ``{"items":1}``.
"""

import asyncio
import json
import sys

import httpx

from examples.cloud_projects import _APP_KEY, _APP_SECRET, _client_kwargs
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError

_USAGE = "usage: python -m examples.cloud_project --mode sync|async"
_ERROR = "error: cloud project example failed"
_MODES = ("sync", "async")
_PROJECT_ID = "demo_project"
_PATH = "/api/open/cloud/v1/project/" + _PROJECT_ID
_DATA = {"projectId": _PROJECT_ID, "projectName": "Demo project", "expireDays": 30}


def _business_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == _PATH:
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": "ok", "moreInfo": None}, "data": _DATA},
        )
    return httpx.Response(404, json={"meta": {"code": 404, "message": "not found"}})


def _demo_sync() -> ApiResponse:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs(_business_handler)) as client:
        return client.cloud_recording_v1.project(_PROJECT_ID)


async def _demo_async() -> ApiResponse:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs(_business_handler)) as client:
        return await client.cloud_recording_v1.project(_PROJECT_ID)


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
        if not isinstance(response.data, dict):
            raise ValueError(_ERROR)
        print(json.dumps({"items": 1}, separators=(",", ":")))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_ERROR, file=sys.stderr)
        return 130
    except (EzvizError, ValueError, OSError):
        print(_ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
