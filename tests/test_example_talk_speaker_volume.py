import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from examples import talk_speaker_volume as cli
from ezviz_unified import AsyncClient, Client

USAGE = "usage: python -m examples.talk_speaker_volume --mode sync|async\n"
ERROR = "error: talk speaker volume query failed\n"
APP_KEY = "SYNTHKEY"
APP_SECRET = "SYNTHSECRET"
TOKEN = "SYNTH_TOKEN"
SERIAL = "Ez天Viz-042_MiX"
PADDED_SERIAL = "  Ez天Viz-パッド_SN9  "
CANARY = "CANARY-秘密-001"
DOMESTIC_ORIGIN = "https://open.ys7.com"
AUTH_PATH = "/api/lapp/token/get"
AUTH_PAYLOAD = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": DOMESTIC_ORIGIN,
    },
}


def _env(monkeypatch, region="domestic", serial=SERIAL):
    monkeypatch.setenv("EZVIZ_REGION", region)
    monkeypatch.setenv("EZVIZ_APP_KEY", APP_KEY)
    monkeypatch.setenv("EZVIZ_APP_SECRET", APP_SECRET)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)


def _wire(monkeypatch, biz_handler):
    auth_requests, biz_requests = [], []

    def auth_handler(request):
        auth_requests.append(request)
        return httpx.Response(200, json=AUTH_PAYLOAD)

    def wire_handler(request):
        biz_requests.append(request)
        return biz_handler(request)

    def wrap(cls):
        def wrapper(*args, **kwargs):
            kwargs["auth_transport"] = httpx.MockTransport(auth_handler)
            kwargs["transport_factory"] = lambda origin: httpx.MockTransport(wire_handler)
            return cls(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(cli, "Client", wrap(Client))
    monkeypatch.setattr(cli, "AsyncClient", wrap(AsyncClient))
    return auth_requests, biz_requests


def _origin(url):
    return f"{url.scheme}://{url.host}"


def _generic_failure(capsys):
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("volume,expected", [(0, 0), (55, 55), (100, 100), (137, None)])
def test_success(monkeypatch, capsys, mode, volume, expected):
    _env(monkeypatch)
    payload = {
        "code": "200",
        "msg": "ok",
        "data": {"talkSpeakerVolume": volume, "someExtra": CANARY},
    }
    auth, biz = _wire(monkeypatch, lambda request: httpx.Response(200, json=payload))

    assert cli.main(["--mode", mode]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "reported_percent": expected,
    }
    assert captured.err == ""
    assert len(auth) == 1
    assert len(biz) == 1
    auth_request = auth[0]
    assert auth_request.method == "POST"
    assert _origin(auth_request.url) == DOMESTIC_ORIGIN
    assert auth_request.url.path == AUTH_PATH
    assert auth_request.url.params.multi_items() == []
    assert parse_qs(auth_request.content.decode()) == {
        "appKey": [APP_KEY],
        "appSecret": [APP_SECRET],
    }
    request = biz[0]
    assert request.method == "GET"
    assert _origin(request.url) == DOMESTIC_ORIGIN
    assert request.url.path == "/api/v3/device/talkSpeakerVolume"
    assert request.headers.get_list("accessToken") == [TOKEN]
    assert "accessToken" not in request.url.params
    assert request.url.params.multi_items() == [("deviceSerial", SERIAL)]
    assert request.content == b""
    for leaked in (SERIAL, TOKEN, APP_KEY, APP_SECRET, CANARY):
        assert leaked not in captured.out


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_success_padded_serial_forwarded_verbatim(monkeypatch, capsys, mode):
    _env(monkeypatch, serial=PADDED_SERIAL)
    payload = {
        "code": "200",
        "msg": "ok",
        "data": {"talkSpeakerVolume": 42},
    }
    auth, biz = _wire(monkeypatch, lambda request: httpx.Response(200, json=payload))

    assert cli.main(["--mode", mode]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "reported_percent": 42,
    }
    assert captured.err == ""
    assert len(auth) == 1
    assert len(biz) == 1
    request = biz[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v3/device/talkSpeakerVolume"
    assert request.headers.get_list("accessToken") == [TOKEN]
    # The padded serial travels untouched: not stripped, not the default.
    assert request.url.params.multi_items() == [("deviceSerial", PADDED_SERIAL)]
    assert request.content == b""


@pytest.mark.parametrize(
    "argv",
    [[], ["--mode"], ["--mode", "sideways"], ["--bogus"], ["--set", "50"]],
)
def test_invalid_args(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: pytest.fail("load_config reached"))
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE


@pytest.mark.parametrize("argv", [["--help"], ["-h"]])
def test_help(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: pytest.fail("load_config reached"))
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE
    assert captured.err == ""


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("env", [{"serial": None}, {"region": "overseas"}])
def test_config_rejected(monkeypatch, capsys, mode, env):
    _env(monkeypatch, **env)
    auth, biz = _wire(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert cli.main(["--mode", mode]) == 1
    _generic_failure(capsys)
    assert auth == [] and biz == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "payload",
    [
        {"msg": "missing code"},
        {"code": "500", "msg": "boom", "data": None},
        {"code": "200", "msg": "ok", "data": {"talkSpeakerVolume": True}},
    ],
)
def test_query_failures(monkeypatch, capsys, mode, payload):
    _env(monkeypatch)
    _wire(monkeypatch, lambda request: httpx.Response(200, json=payload))
    assert cli.main(["--mode", mode]) == 1
    _generic_failure(capsys)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
def test_interrupted(monkeypatch, capsys, mode, exc):
    _env(monkeypatch)

    def cancel(request):
        raise exc

    _wire(monkeypatch, cancel)
    assert cli.main(["--mode", mode]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR
    assert "Traceback" not in captured.err
    for leaked in (SERIAL, TOKEN, APP_KEY, APP_SECRET):
        assert leaked not in captured.err
