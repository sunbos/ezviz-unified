"""Offline contract tests for ``examples/image_configuration.py``."""

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from examples import image_configuration as cli
from ezviz_unified import AsyncClient, Client, DiagnosticRecorder

SERIAL = "MOCKSERIAL01"
TOKEN = "SYNTH_TOKEN"
CANARY = "BIZ-CANARY-9f3e"
AUTH_JSON = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
PATH = {
    "display": "/api/v3/device/display/mode/get",
    "image": "/api/v3/device/video/image/params",
    "white-balance": "/api/v3/device/video/white/balance",
}
USAGE = (
    "usage: python -m examples.image_configuration "
    "--mode sync|async --setting display|image|white-balance\n"
)
ERROR = "error: image configuration query failed\n"


def _env(monkeypatch, region="domestic", serial=SERIAL):
    monkeypatch.setenv("EZVIZ_REGION", region)
    monkeypatch.setenv("EZVIZ_APP_KEY", "mock-key")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "mock-secret")
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)


def _clients(monkeypatch, biz, rec):
    seen = {"auth": [], "biz": []}

    def auth(request):
        seen["auth"].append(request)
        return httpx.Response(200, json=AUTH_JSON)

    def handle(request):
        seen["biz"].append(request)
        return biz(request)

    def wrap(real):
        def factory(*args, **kwargs):
            return real(
                *args,
                auth_transport=httpx.MockTransport(auth),
                transport_factory=lambda origin: httpx.MockTransport(handle),
                diagnostic_recorder=rec,
                **kwargs,
            )

        return factory

    monkeypatch.setattr(cli, "Client", wrap(Client))
    monkeypatch.setattr(cli, "AsyncClient", wrap(AsyncClient))
    return seen


def _statuses(rec, entrypoint, mode):
    return [
        event["outcome"]
        for event in rec.report()["events"]
        if event.get("kind") == "call"
        and event.get("entrypoint") == entrypoint
        and event.get("mode") == mode
    ]


def _payload(setting, mode):
    if setting == "display":
        return {
            "code": "200",
            "data": {"valueInfo": {"mode": "1" if mode == "sync" else 2, "canary": CANARY}},
        }
    return {"meta": {"code": 200}, "data": {"setting": setting, "canary": CANARY}}


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("setting", ["display", "image", "white-balance"])
def test_query(monkeypatch, capsys, mode, setting):
    _env(monkeypatch)
    rec = DiagnosticRecorder()
    seen = _clients(monkeypatch, lambda req: httpx.Response(200, json=_payload(setting, mode)), rec)
    rc = cli.main(["--mode", mode, "--setting", setting])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "setting": setting,
        "status": "ok",
    }
    # Exactly one auth POST and one business GET, both pinned to the domestic host.
    assert len(seen["auth"]) == 1 and len(seen["biz"]) == 1
    auth = seen["auth"][0]
    assert auth.method == "POST"
    assert auth.url.host == "open.ys7.com"
    assert auth.url.path == "/api/lapp/token/get"
    assert parse_qs(auth.content.decode()) == {"appKey": ["mock-key"], "appSecret": ["mock-secret"]}
    request = seen["biz"][0]
    assert request.method == "GET"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == PATH[setting]
    assert request.headers["accessToken"] == TOKEN
    assert request.content == b""
    # The canary, device serial and app secrets are never shown on stdout/stderr.
    combined = captured.out + captured.err
    for secret in (CANARY, SERIAL, "mock-key", "mock-secret", TOKEN):
        assert secret not in combined
    if setting == "display":
        assert request.headers["deviceSerial"] == SERIAL
        assert request.url.params.multi_items() == []
        assert _statuses(rec, "imaging.get_display_mode", mode) == ["started", "succeeded"]
    else:
        assert "deviceSerial" not in request.headers
        assert request.url.params.multi_items() == [("deviceSerial", SERIAL)]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "sync"],
        ["--setting", "display"],
        ["--mode", "sync", "--setting", "display", "--set", "mode=1"],
        ["--bogus"],
    ],
)
def test_usage_errors(monkeypatch, capsys, argv):
    for name in (
        "EZVIZ_REGION",
        "EZVIZ_APP_KEY",
        "EZVIZ_APP_SECRET",
        "EZVIZ_DEVICE_SERIAL",
        "EZVIZ_AUTH_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    # Usage must be printed and returned before any configuration is read.
    monkeypatch.setattr(cli, "load_config", pytest.fail)
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize("argv", [["--help"], ["-h"]])
def test_help(monkeypatch, capsys, argv):
    # --help prints usage without reading configuration or the network.
    monkeypatch.setattr(cli, "load_config", pytest.fail)
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE
    assert captured.err == ""


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "region,serial", [("global", SERIAL), ("overseas", SERIAL), ("domestic", None)]
)
def test_preauth_failures(monkeypatch, capsys, mode, region, serial):
    _env(monkeypatch, region=region, serial=serial)
    seen = _clients(monkeypatch, lambda req: httpx.Response(500), DiagnosticRecorder())
    assert cli.main(["--mode", mode, "--setting", "display"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR
    assert seen == {"auth": [], "biz": []}


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_runtime_failure(monkeypatch, capsys, mode):
    _env(monkeypatch)
    rec = DiagnosticRecorder()

    def biz(request):
        raise httpx.ConnectError("offline")

    seen = _clients(monkeypatch, biz, rec)
    rc = cli.main(["--mode", mode, "--setting", "display"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == ERROR
    assert len(seen["auth"]) == 1 and len(seen["biz"]) == 1
    assert _statuses(rec, "imaging.get_display_mode", mode) == ["started", "failed"]


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())]
)
def test_interrupted(monkeypatch, capsys, mode, exc):
    _env(monkeypatch)

    def biz(request):
        raise exc

    _clients(monkeypatch, biz, DiagnosticRecorder())
    assert cli.main(["--mode", mode, "--setting", "display"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("setting", ["display", "image", "white-balance"])
def test_padded_serial(monkeypatch, capsys, mode, setting):
    padded = f"  {SERIAL} \n"
    _env(monkeypatch, serial=padded)
    seen = _clients(
        monkeypatch,
        lambda req: httpx.Response(200, json=_payload(setting, mode)),
        DiagnosticRecorder(),
    )
    rc = cli.main(["--mode", mode, "--setting", setting])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert padded not in combined and SERIAL not in combined
    if setting == "display":
        assert rc == 1 and captured.out == "" and captured.err == ERROR
        assert seen == {"auth": [], "biz": []}
    else:
        assert rc == 0 and captured.err == ""
        assert len(seen["auth"]) == 1 and len(seen["biz"]) == 1
        # The padded serial is forwarded verbatim, decoded, as the query deviceSerial.
        assert seen["biz"][0].url.params["deviceSerial"] == padded
