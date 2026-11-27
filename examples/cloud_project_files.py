"""Count the files on one page for one fabricated domestic project.

Fully simulated like ``cloud_projects``: no config loader, no environment
reads, no network. The fabricated credentials, canned auth handler and client
kwargs are shared from ``examples.cloud_projects``; only the business route
and its canned rows live here. The example prints only the row count.
"""

import asyncio
import json
import sys

import httpx

from examples.cloud_projects import _APP_KEY, _APP_SECRET, _client_kwargs
from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError

_USAGE = "usage: python -m examples.cloud_project_files --mode sync|async"
_ERROR = "error: cloud project files example failed"
_MODES = ("sync", "async")
_PROJECT_ID = "demo-project"
_PATH = "/api/open/cloud/v1/files"
_ROWS = [
    {
        "projectId": "demo-project",
        "fileId": "demo-file",
        "createTime": None,
        "fileType": 1,
        "fileUrl": "https://synthetic-media.invalid/demo/file",
    }
]


def _business_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == _PATH:
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": "ok", "moreInfo": None}, "data": _ROWS},
        )
    return httpx.Response(404, json={"meta": {"code": 404, "message": "not found"}})


def _demo_sync() -> ApiResponse:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs(_business_handler)) as client:
        return client.cloud_recording_v1.project_files(_PROJECT_ID, page_number=0, page_size=1)


async def _demo_async() -> ApiResponse:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs(_business_handler)) as client:
        return await client.cloud_recording_v1.project_files(
            _PROJECT_ID, page_number=0, page_size=1
        )


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
