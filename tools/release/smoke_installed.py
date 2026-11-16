"""Run with the clean installation's Python -I; uses only mock HTTP."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx

import ezviz_unified as sdk


def auth(request):
    assert request.method == "POST"
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": "SMOKE_TOKEN",
                "expireTime": 2_000_000,
                "areaDomain": "https://isgpopen.ezviz.com",
            },
        },
    )


def business(request):
    assert request.url.host == "isgpopen.ezviz.com"
    if request.url.path == "/api/lapp/device/list":
        assert request.method == "POST"
        assert parse_qs(request.content.decode())["accessToken"] == ["SMOKE_TOKEN"]
        return httpx.Response(200, json={"code": "200", "data": []})
    assert request.url.path == "/api/v3/das/device/local/video/query"
    assert request.method == "GET" and request.content == b""
    assert request.headers["accessToken"] == "SMOKE_TOKEN"
    assert request.url.params["startTime"] == "2024-01-01 00:00:00"
    return httpx.Response(200, json={"meta": {"code": 200, "message": "ok"}, "data": []})


def options():
    return {
        "region": sdk.RegionConfig.overseas(),
        "clock": lambda: 1000,
        "auth_transport": httpx.MockTransport(auth),
        "transport_factory": lambda origin: httpx.MockTransport(business),
    }


def _assert_diagnostic_report(recorder, mode):
    report = recorder.report()
    assert report["schema_version"] == 1
    assert report["scope"] == "local_sdk_diagnostics"
    assert report["recording_errors"] == 0 and report["dropped_events"] == 0
    for entry in ("devices.list", "recordings.local"):
        matched = [e for e in report["events"] if e["entrypoint"] == entry]
        succeeded = [e for e in matched if e["outcome"] == "succeeded"]
        assert len(succeeded) == 1, f"{entry}/{mode} missing succeeded terminal event"
        assert all(e["mode"] == mode and e["region"] == "overseas" for e in matched)
    payload = recorder.to_json()
    assert json.loads(payload) == report
    assert all(s not in payload for s in ("SMOKE_KEY", "SMOKE_SECRET", "SMOKE_TOKEN", "DEVICE"))


async def asynchronous():
    recorder = sdk.DiagnosticRecorder()
    async with sdk.AsyncClient(
        "SMOKE_KEY", "SMOKE_SECRET", diagnostic_recorder=recorder, **options()
    ) as client:
        assert (await client.devices.list()).data == []
        assert (await client.recordings.local("DEVICE", start_time=datetime(2024, 1, 1))).data == []
    assert client.is_closed
    _assert_diagnostic_report(recorder, "async")


def main():
    assert Path(sdk.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "SDK loaded outside installation"
    )
    recorder = sdk.DiagnosticRecorder()
    with sdk.Client(
        "SMOKE_KEY", "SMOKE_SECRET", diagnostic_recorder=recorder, **options()
    ) as client:
        assert client.devices.list().data == []
        assert client.recordings.local("DEVICE", start_time=datetime(2024, 1, 1)).data == []
    assert client.is_closed
    _assert_diagnostic_report(recorder, "sync")
    asyncio.run(asynchronous())
    print("Installed SDK sync/async mock smoke passed")


if __name__ == "__main__":
    main()
