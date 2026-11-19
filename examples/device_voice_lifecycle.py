"""Fully offline device voice create/delete lifecycle demo.

Exercises the real sync and async clients end to end against fixed
``httpx.MockTransport`` handlers. There is no network access, no file or
environment configuration, no real credentials and no way to switch to a
live transport: both the authentication transport and the per-origin
``transport_factory`` are hard-wired to in-memory mocks.

The demo creates a simulated device voice material and then explicitly
deletes it using the ``voiceId`` returned by the create response. The mock
rejects any request that does not match the scripted method, path,
credentials, or parameter set; the create-then-delete ordering itself is
asserted by the example's tests, not by this mock.

Usage:
    python -m examples.device_voice_lifecycle --mode sync
    python -m examples.device_voice_lifecycle --mode async
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from ezviz_unified import AsyncClient, Client, EzvizError, RegionConfig

_USAGE = "usage: python -m examples.device_voice_lifecycle --mode sync|async"
_ERROR_MESSAGE = "error: simulated device voice workflow failed"
_CANCEL_MESSAGE = "interrupted: simulated device voice workflow cancelled"

_APP_KEY = "SYNTHKEY"
_APP_SECRET = "SYNTHSECRET"
_ACCESS_TOKEN = "SYNTH_TOKEN"
_DEVICE_SERIAL = "SYNTHSERIAL1"
_VOICE_NAME = "synth-tone"
_VOICE_URL = "https://media.invalid/example.wav"
_VOICE_ID = 97531  # synthetic identifier issued by the mock only
_VOICE_PATH = "/api/route/voice/v3/devices/voices"
_AUTH_PATH = "/api/lapp/token/get"


def _reject(request: httpx.Request) -> httpx.Response:
    """Reject any request that does not match the scripted lifecycle."""
    return httpx.Response(
        400,
        json={"code": "-1", "message": "rejected by offline mock"},
        request=request,
    )


def _auth_handler(request: httpx.Request) -> httpx.Response:
    """Serve only POST /api/lapp/token/get with the fixed synthetic token."""
    if request.method != "POST" or request.url.path != _AUTH_PATH:
        return _reject(request)
    if request.url.params:
        return _reject(request)
    form = httpx.QueryParams(request.content.decode("utf-8"))
    if set(form.keys()) != {"appKey", "appSecret"}:
        return _reject(request)
    if form["appKey"] != _APP_KEY or form["appSecret"] != _APP_SECRET:
        return _reject(request)
    # The token endpoint answers with a top-level string ``code``.
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": _ACCESS_TOKEN,
                "expireTime": 9999999999999,
                "areaDomain": "https://open.ys7.com",
            },
        },
        request=request,
    )


def _business_handler(request: httpx.Request) -> httpx.Response:
    """Serve only POST/DELETE on the device voice path with strict params."""
    if request.method not in ("POST", "DELETE") or request.url.path != _VOICE_PATH:
        return _reject(request)
    if request.content:
        # Both write operations send no request body.
        return _reject(request)

    params = request.url.params
    items = params.multi_items()
    expected_keys = {"accessToken", "deviceSerial", "voiceName", "voiceUrl"}
    if request.method == "DELETE":
        expected_keys.add("voiceId")
    # Reject missing, unexpected, or duplicated business keys outright.
    if len(items) != len(expected_keys) or {key for key, _ in items} != expected_keys:
        return _reject(request)
    if params.get("accessToken") != _ACCESS_TOKEN:
        return _reject(request)
    if params.get("deviceSerial") != _DEVICE_SERIAL:
        return _reject(request)
    if params.get("voiceName") != _VOICE_NAME:
        return _reject(request)
    if params.get("voiceUrl") != _VOICE_URL:
        return _reject(request)

    if request.method == "POST":
        # POST must not carry a voiceId (enforced by expected_keys above);
        # the mock issues the fixed identifier under ``voiceInfo``.
        return httpx.Response(
            200,
            json={
                "meta": {"code": 200, "message": "ok"},
                "voiceInfo": {"voiceId": _VOICE_ID},
            },
            request=request,
        )
    # DELETE must reference the identifier issued by the mock's create
    # response, together with the same material name and URL; a successful
    # acknowledgement carries no data payload.
    if params.get("voiceId") != str(_VOICE_ID):
        return _reject(request)
    return httpx.Response(
        200,
        json={"meta": {"code": 200, "message": "ok"}},
        request=request,
    )


def _client_kwargs() -> dict:
    return {
        "region": RegionConfig.china(),
        "auth_transport": httpx.MockTransport(_auth_handler),
        "transport_factory": lambda origin: httpx.MockTransport(_business_handler),
    }


def _run_sync() -> None:
    with Client(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        created = client.device_voice.create(
            _DEVICE_SERIAL, voice_name=_VOICE_NAME, voice_url=_VOICE_URL
        )
        voice_id = created.data["voiceId"]
        client.device_voice.delete(
            _DEVICE_SERIAL, voice_id, voice_name=_VOICE_NAME, voice_url=_VOICE_URL
        )


async def _run_async() -> None:
    async with AsyncClient(_APP_KEY, _APP_SECRET, **_client_kwargs()) as client:
        created = await client.device_voice.create(
            _DEVICE_SERIAL, voice_name=_VOICE_NAME, voice_url=_VOICE_URL
        )
        voice_id = created.data["voiceId"]
        await client.device_voice.delete(
            _DEVICE_SERIAL, voice_id, voice_name=_VOICE_NAME, voice_url=_VOICE_URL
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:]) if argv is None else list(argv)
    if args in (["--help"], ["-h"]):
        print(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--mode" or args[1] not in ("sync", "async"):
        print(_USAGE, file=sys.stderr)
        return 1
    mode = args[1]

    try:
        if mode == "sync":
            _run_sync()
        else:
            asyncio.run(_run_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_CANCEL_MESSAGE, file=sys.stderr)
        return 130
    except EzvizError:
        print(_ERROR_MESSAGE, file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "region": "domestic",
                "mode": mode,
                "status": "ok",
                "simulation": True,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
