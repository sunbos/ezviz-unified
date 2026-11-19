"""Source-checkout tests for examples/device_timezone.py and devices.get_utc."""

import asyncio
import json
from urllib.parse import parse_qsl

import httpx
import pytest

from examples import device_timezone as cli
from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    Client,
    DiagnosticRecorder,
    RegionConfig,
)

USAGE = "usage: python -m examples.device_timezone --mode sync|async"
ERR = "error: device timezone query failed\n"
AUTH_BASE = "https://synthetic-auth.ezvizlife.com"
DATA = {
    "timeZone": "SYNTH/Zone",
    "wifiSsid": "SYNTH_SSID",
    "deviceSerial": "sYnTh-序",
    "futureField": {"marker": "SYNTH_PRIVATE"},
}
SECRETS = ("SYNTH/Zone", "SYNTH_SSID", "sYnTh-序", "SYNTH_PRIVATE")


def _auth(request):
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": "SYNTH_TOKEN",
                "expireTime": 9999999999999,
                "areaDomain": "https://synthetic-api.ezvizlife.com",
            },
        },
    )


def _biz(data=DATA):
    return lambda request: httpx.Response(200, json={"code": "200", "msg": "ok", "data": data})


def _record(bucket, handler):
    def route(request):
        bucket.append(request)
        return handler(request)

    return route


def _form(request):
    return dict(parse_qsl(request.content.decode()))


def _client(cls, biz, rec=None, region=None):
    region = region or RegionConfig.overseas(auth_base_url=AUTH_BASE)
    auth = (
        _auth
        if region.scope == "overseas"
        else lambda request: httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "SYNTH_TOKEN",
                    "expireTime": 9999999999999,
                    "areaDomain": "https://open.ys7.com",
                },
            },
        )
    )
    return cls(
        "mock-key",
        "mock-secret",
        region=region,
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
        diagnostic_recorder=rec,
    )


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in (
        "EZVIZ_REGION",
        "EZVIZ_APP_KEY",
        "EZVIZ_APP_SECRET",
        "EZVIZ_DEVICE_SERIAL",
        "EZVIZ_AUTH_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EZVIZ_REGION", "overseas")
    monkeypatch.setenv("EZVIZ_APP_KEY", "synthetic-app-key")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "synthetic-app-secret")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "sYnTh-序")
    monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", AUTH_BASE)


def _patch_cli(monkeypatch, biz, auth=_auth):
    seen = {"auth": [], "biz": []}
    for name, cls in (("Client", Client), ("AsyncClient", AsyncClient)):

        def build(*args, _cls=cls, **kwargs):
            kwargs["auth_transport"] = httpx.MockTransport(_record(seen["auth"], auth))
            kwargs["transport_factory"] = lambda origin: httpx.MockTransport(
                _record(seen["biz"], biz)
            )
            return _cls(*args, **kwargs)

        monkeypatch.setattr(cli, name, build)
    return seen


@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize(
    "data, expected",
    (
        (DATA, True),
        ({"wifiSsid": "SYNTH_SSID"}, False),
        ({"wifiSsid": "SYNTH_SSID", "timeZone": "  "}, False),
        ({"wifiSsid": "SYNTH_SSID", "timeZone": 42}, False),
    ),
)
def test_cli_success(monkeypatch, capsys, mode, data, expected):
    seen = _patch_cli(monkeypatch, _biz(data))
    assert cli.main(["--mode", mode]) == 0
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    auth = seen["auth"][0]
    assert auth.method == "POST" and not auth.url.query
    assert str(auth.url) == "https://synthetic-auth.ezvizlife.com/api/lapp/token/get"
    assert _form(auth) == {
        "appKey": "synthetic-app-key",
        "appSecret": "synthetic-app-secret",
    }
    biz = seen["biz"][0]
    assert biz.method == "POST" and not biz.url.query
    assert str(biz.url) == "https://synthetic-api.ezvizlife.com/api/lapp/device/info"
    assert _form(biz) == {
        "accessToken": "SYNTH_TOKEN",
        "deviceSerial": "sYnTh-序",
        "version": "utc",
    }
    out = capsys.readouterr()
    assert json.loads(out.out) == {
        "region": "overseas",
        "mode": mode,
        "status": "ok",
        "time_zone_present": expected,
    }
    assert out.err == "" and not any(s in out.out for s in SECRETS)


@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize(
    "overrides",
    ({"EZVIZ_DEVICE_SERIAL": None}, {"EZVIZ_REGION": "domestic", "EZVIZ_AUTH_BASE_URL": None}),
)
def test_cli_rejected_before_requests(monkeypatch, capsys, overrides, mode):
    for name, value in overrides.items():
        monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(
            name, value
        )
    seen = _patch_cli(monkeypatch, _biz())
    assert cli.main(["--mode", mode]) == 1
    assert seen == {"auth": [], "biz": []}
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERR


@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize(
    "biz, rc",
    (
        (lambda r: httpx.Response(200, json={"code": "500", "msg": "x"}), 1),
        (lambda r: (_ for _ in ()).throw(KeyboardInterrupt()), 130),
    ),
)
def test_cli_failure(monkeypatch, capsys, mode, biz, rc):
    _patch_cli(monkeypatch, biz)
    assert cli.main(["--mode", mode]) == rc
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERR


def test_cli_help_and_usage_errors(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: pytest.fail("load_config called"))
    for flag in (["-h"], ["--help"]):
        assert cli.main(flag) == 0
        out = capsys.readouterr()
        assert out.out == USAGE + "\n" and out.err == ""
    for argv in ([], ["sync"], ["--mode"], ["--mode", "bogus"], ["--mode", "sync", "x"]):
        assert cli.main(argv) == 1
        out = capsys.readouterr()
        assert out.out == "" and out.err == USAGE + "\n"


def test_devices_get_utc_passthrough_and_diagnostics():
    rec, seen = DiagnosticRecorder(), []
    for region in (RegionConfig.overseas(auth_base_url=AUTH_BASE), RegionConfig.china()):
        with _client(Client, _record(seen, _biz()), rec, region) as client:
            client.devices.get("sYnTh-序")
    assert len(seen) == 2 and all("version" not in _form(r) for r in seen)
    with _client(Client, _record(seen, _biz()), rec) as client:
        resp = client.devices.get_utc("sYnTh-序")
    assert isinstance(resp, ApiResponse) and resp.data == DATA
    assert _form(seen[-1]) == {
        "accessToken": "SYNTH_TOKEN",
        "deviceSerial": "sYnTh-序",
        "version": "utc",
    }
    assert not any(s in repr(resp) + str(rec.report()) for s in SECRETS)
    with pytest.raises(ApiError):
        with _client(
            Client, lambda r: httpx.Response(200, json={"code": "500", "msg": "x"}), rec
        ) as client:
            client.devices.get_utc("sYnTh-序")
    events = rec.report()["events"]
    outcomes = [
        e.get("outcome")
        for e in events
        if e.get("kind") == "call" and e.get("entrypoint") == "devices.get_utc"
    ]
    assert outcomes == ["started", "succeeded", "started", "failed"]
    attempts = [
        e for e in events if e.get("kind") == "attempt" and e.get("operation") == "devices.get_utc"
    ]
    assert [e.get("outcome") for e in attempts] == [
        "started",
        "succeeded",
        "started",
        "failed",
    ]


def test_devices_get_utc_async():
    async def run():
        async with _client(AsyncClient, _biz(), DiagnosticRecorder()) as client:
            return await client.devices.get_utc("sYnTh-序")

    assert asyncio.run(run()).data == DATA
