"""Offline simulation of the domestic live stream lifecycle.

Drives ``create_device`` -> ``update_window`` -> ``set_status`` -> ``delete``
entirely against ``httpx.MockTransport`` handlers. It never contacts a real
endpoint, uses no real credentials, and produces no playable media address;
the stream exists only inside the mock.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from urllib.parse import parse_qsl

import httpx

from ezviz_unified import Client, EzvizError, RegionConfig

USAGE = "usage: python -m examples.live_stream_lifecycle"

HOST = "open.ys7.com"
_TOKEN_PATH = "/api/lapp/token/get"
_STREAM_PATH = "/api/service/media/streammanage/stream"
_STATUS_PATH = "/api/service/media/streammanage/stream/status"

# Synthetic fixture values only; no real credential, ID, or media address.
_STREAM_ID = "SYNTH-STREAM-0001"
_TOKEN_BODY = {
    "code": "200",  # auth endpoint returns a top-level string code
    "data": {
        "accessToken": "synthetic-access-token",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}


def _meta(data=None) -> httpx.Response:
    body = {"meta": {"code": 200, "message": "ok"}}
    if data is not None:
        body["data"] = data
    return httpx.Response(200, json=body)


def _reject() -> httpx.Response:
    return httpx.Response(200, json={"meta": {"code": 400, "message": "unexpected"}})


def _origin(request: httpx.Request) -> bool:
    return request.url.scheme == "https" and request.url.host == HOST


def _pairs(raw: bytes) -> list[tuple[str, str]]:
    return parse_qsl(raw.decode("utf-8"), keep_blank_values=True)


def _values(pairs: list[tuple[str, str]], key: str) -> list[str]:
    return [value for name, value in pairs if name == key]


def _selector(request: httpx.Request) -> bool:
    """Raw query must hold exactly one streamId equal to the fixture value."""
    return _values(_pairs(request.url.query), "streamId") == [_STREAM_ID]


def auth(request: httpx.Request) -> httpx.Response:
    """Only the token endpoint; never forwards to business routes."""
    if request.method != "POST" or not _origin(request) or request.url.path != _TOKEN_PATH:
        return httpx.Response(400, json={"code": "400", "msg": "unexpected auth request"})
    return httpx.Response(200, json=dict(_TOKEN_BODY))


def biz(request: httpx.Request) -> httpx.Response:
    """Exactly the four lifecycle routes; anything else is a protocol break."""
    if not _origin(request) or request.url.path == _TOKEN_PATH:
        return _reject()
    method, path, query = request.method, request.url.path, _pairs(request.url.query)
    if method == "POST" and path == _STREAM_PATH:
        form = _pairs(request.content)
        if (
            _values(form, "accessType") != ["1"]
            or _values(form, "startTime") != ["2026-01-02 00:00:00"]
            or _values(form, "endTime") != ["2026-01-03 00:00:00"]
            or request.headers.get_list("deviceSerial") != ["ExampleDevice"]
            or _values(query, "streamId")
        ):
            return _reject()
        return _meta({"streamId": _STREAM_ID})
    if method in ("PUT", "DELETE") and path == _STREAM_PATH and _selector(request):
        return _meta()
    if method == "PUT" and path == _STATUS_PATH:
        if _selector(request) and _values(query, "status") == ["0"]:
            return _meta()
    return _reject()


def _run() -> None:
    with Client(
        "SYNTH_APP",
        "SYNTH_SECRET",
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
    ) as client:
        result = client.live_streams.create_device(
            "ExampleDevice",
            start_time=datetime(2026, 1, 2),
            end_time=datetime(2026, 1, 3),
        )
        sid = result.data["streamId"]
        client.live_streams.update_window(
            sid,
            start_time=datetime(2026, 1, 3),
            end_time=datetime(2026, 1, 4),
        )
        client.live_streams.set_status(sid, 0)
        client.live_streams.delete(sid)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--help"] or args == ["-h"]:
        print(USAGE)
        return 0
    if args:
        print(USAGE, file=sys.stderr)
        return 1
    try:
        _run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("error: interrupted", file=sys.stderr)
        return 130
    except (EzvizError, ValueError):
        print("error: simulated live stream lifecycle failed", file=sys.stderr)
        return 1
    print(json.dumps({"region": "domestic", "status": "ok", "simulation": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
