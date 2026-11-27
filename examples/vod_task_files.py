"""Count the files of one domestic VOD task (doc 3964) on in-memory transports.

The default path is fully simulated: synthetic credentials plus an in-process
auth/business ``MockTransport`` pair answering a single canned page. The real
config loader is never called and no network, environment or device is
touched. The example prints only the row count — it never opens, saves or
follows any returned ``fileUrl``/``coverPicUrl``/``folderNode``.
"""

import asyncio
import json
import sys
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import ApiResponse, AsyncClient, Client, EzvizError, RegionConfig

_USAGE = "usage: python -m examples.vod_task_files --mode sync|async"
_ERROR = "error: vod task files example failed"
_MODES = ("sync", "async")
_APP_KEY = "demo-app-key"
_APP_SECRET = "demo-app-secret"
_ACCESS_TOKEN = "demo-access-token"
_AREA_DOMAIN = "https://open.ys7.com"
_PATH_PREFIX = "/api/service/open/cloud/vod/task/files/"
_TASK_ID = "demo-task"
_ROWS = [
    {
        "folderNode": "demo-node",
        "fileName": "demo-file.mp4",
        "fileUrl": "https://synthetic-media.invalid/demo/file",
        "coverPicUrl": "https://synthetic-media.invalid/demo/cover",
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


def _business_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path.startswith(_PATH_PREFIX):
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": "ok", "moreInfo": None}, "data": _ROWS},
        )
    return httpx.Response(404, json={"meta": {"code": 404, "message": "not found"}})


def _client_kwargs() -> dict:
    return {
        "region": RegionConfig.china(),
        "auth_transport": httpx.MockTransport(_auth_handler),
        "transport_factory": lambda origin: httpx.MockTransport(_business_handler),
    }


def _demo_sync() -> ApiResponse:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        return client.vod.task_files(_TASK_ID)


async def _demo_async() -> ApiResponse:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        return await client.vod.task_files(_TASK_ID)


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
