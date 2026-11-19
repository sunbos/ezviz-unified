"""Source-checkout tests for examples/osd_name.py (domestic osd.get_name)."""

import asyncio
import json
from urllib.parse import parse_qsl

import httpx
import pytest

from examples import osd_name as cli
from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

USAGE = "usage: python -m examples.osd_name --mode sync|async"
ERR = "error: OSD query failed\n"
DOMESTIC_ORIGIN = "https://open.ys7.com"
OVERSEAS_AUTH_BASE = "https://synthetic-auth.ezvizlife.com"
SERIAL = "sYnTh-序"
OSD_TEXT = "SYNTH-序 名称!#"
SECRETS = (
    OSD_TEXT,
    SERIAL,
    "SYNTH_TOKEN",
    "SYNTH_META_OK",
    "synthetic-app-key",
    "synthetic-app-secret",
)


def _auth(request):
    return httpx.Response(
        200,
        json={
            "code": "200",
            "data": {
                "accessToken": "SYNTH_TOKEN",
                "expireTime": 9999999999999,
                "areaDomain": DOMESTIC_ORIGIN,
            },
        },
    )


def _biz(osd=OSD_TEXT):
    """1721 success: top-level string code, never a meta envelope."""
    return lambda request: httpx.Response(
        200,
        json={
            "code": "200",
            "msg": "SYNTH_META_OK",
            "data": {"osd": osd},
        },
    )


def _record(bucket, handler):
    def route(request):
        bucket.append(request)
        return handler(request)

    return route


def _form(request):
    return dict(parse_qsl(request.content.decode()))


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


def _client(cls, biz, rec, auth=_auth):
    return cls(
        "mock-key",
        "mock-secret",
        region=RegionConfig.china(),
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
    monkeypatch.setenv("EZVIZ_REGION", "domestic")
    monkeypatch.setenv("EZVIZ_APP_KEY", "synthetic-app-key")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "synthetic-app-secret")
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", SERIAL)


def _check_response(resp):
    assert isinstance(resp, ApiResponse)
    assert resp.operation == "osd.get_name"
    assert type(resp.code) is str and resp.code == "200"
    assert resp.message == "SYNTH_META_OK"
    assert resp.data["osd"] == OSD_TEXT
    assert isinstance(resp.raw, dict)
    assert resp.raw["code"] == "200"
    assert resp.raw["msg"] == "SYNTH_META_OK"
    assert resp.raw["data"]["osd"] == OSD_TEXT
    assert "meta" not in resp.raw


def _check_events(rec, mode, outcomes, expect_osd_attempt):
    events = rec.report()["events"]
    calls = [
        event
        for event in events
        if event.get("kind") == "call" and event.get("entrypoint") == "osd.get_name"
    ]
    # Each public call emits exactly one started + one terminal lifecycle
    # event for the real entrypoint and mode.
    assert [(event.get("mode"), event.get("outcome")) for event in calls] == [
        (mode, outcome) for outcome in outcomes
    ]
    # An attempt is one wire transmission for this operation, not two events.
    attempts = [
        event
        for event in events
        if event.get("kind") == "attempt" and event.get("operation") == "osd.get_name"
    ]
    if expect_osd_attempt:
        assert any(event.get("outcome") == "started" for event in attempts)
    else:
        assert attempts == []


def test_cli_help(capsys):
    assert cli.main(["--help"]) == 0
    captured = capsys.readouterr()
    assert USAGE in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["--write"],
        ["--mode"],
        ["--mode", "bogus"],
        ["--mode", "sync", "--write"],
        ["extra"],
    ),
)
def test_cli_bad_args(monkeypatch, capsys, argv):
    monkeypatch.delenv("EZVIZ_APP_KEY")
    monkeypatch.delenv("EZVIZ_DEVICE_SERIAL")
    seen = _patch_cli(monkeypatch, _biz())
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert USAGE in captured.err
    assert seen["auth"] == [] and seen["biz"] == []


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_cli_missing_serial(monkeypatch, capsys, mode):
    monkeypatch.delenv("EZVIZ_DEVICE_SERIAL")
    seen = _patch_cli(monkeypatch, _biz())
    assert cli.main(["--mode", mode]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERR
    assert seen["auth"] == [] and seen["biz"] == []


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_cli_overseas_region(monkeypatch, capsys, mode):
    monkeypatch.setenv("EZVIZ_REGION", "overseas")
    monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", OVERSEAS_AUTH_BASE)
    seen = _patch_cli(monkeypatch, _biz())
    assert cli.main(["--mode", mode]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERR
    assert seen["auth"] == [] and seen["biz"] == []


@pytest.mark.parametrize("mode", ("sync", "async"))
@pytest.mark.parametrize("osd", ("", "  ", OSD_TEXT))
def test_cli_success(monkeypatch, capsys, mode, osd):
    seen = _patch_cli(monkeypatch, _biz(osd))
    assert cli.main(["--mode", mode]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "name_present": bool(osd),
    }
    assert captured.err == ""
    for secret in SECRETS:
        assert secret not in captured.out
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    auth = seen["auth"][0]
    assert auth.method == "POST" and not auth.url.query
    assert str(auth.url) == f"{DOMESTIC_ORIGIN}/api/lapp/token/get"
    assert _form(auth) == {
        "appKey": "synthetic-app-key",
        "appSecret": "synthetic-app-secret",
    }
    req = seen["biz"][0]
    assert req.method == "GET"
    assert req.url.host == "open.ys7.com"
    assert req.url.path == "/api/v3/device/osd"
    assert dict(parse_qsl(req.url.query.decode("ascii"), keep_blank_values=True)) == {
        "deviceSerial": SERIAL
    }
    assert req.headers["accessToken"] == "SYNTH_TOKEN"
    assert not req.content


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_cli_business_failure(monkeypatch, capsys, mode):
    def biz(request):
        return httpx.Response(200, json={"code": "10005", "msg": "SYNTH_BIZ_DENY"})

    _patch_cli(monkeypatch, biz)
    assert cli.main(["--mode", mode]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERR


@pytest.mark.parametrize(
    ("mode", "exc"),
    (
        ("sync", KeyboardInterrupt),
        ("async", KeyboardInterrupt),
        ("async", asyncio.CancelledError),
    ),
)
def test_cli_cancelled(monkeypatch, capsys, mode, exc):
    def biz(request):
        raise exc()

    _patch_cli(monkeypatch, biz)
    assert cli.main(["--mode", mode]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERR


def test_diagnostics_sync_success():
    rec = DiagnosticRecorder()
    with _client(Client, _biz(), rec) as client:
        resp = client.osd.get_name(SERIAL)
    _check_response(resp)
    _check_events(rec, "sync", ("started", "succeeded"), True)


def test_diagnostics_async_success():
    rec = DiagnosticRecorder()

    async def run():
        async with _client(AsyncClient, _biz(), rec) as client:
            return await client.osd.get_name(SERIAL)

    resp = asyncio.run(run())
    _check_response(resp)
    _check_events(rec, "async", ("started", "succeeded"), True)


def test_diagnostics_business_failure():
    rec = DiagnosticRecorder()

    def biz(request):
        return httpx.Response(200, json={"code": "10005", "msg": "SYNTH_BIZ_DENY"})

    with pytest.raises(ApiError):
        with _client(Client, biz, rec) as client:
            client.osd.get_name(SERIAL)
    _check_events(rec, "sync", ("started", "failed"), True)


def test_diagnostics_auth_failure():
    rec = DiagnosticRecorder()

    def auth(request):
        return httpx.Response(200, json={"code": "10002", "msg": "SYNTH_AUTH_DENY"})

    with pytest.raises(AuthenticationError):
        with _client(Client, _biz(), rec, auth=auth) as client:
            client.osd.get_name(SERIAL)
    _check_events(rec, "sync", ("started", "failed"), False)


def test_diagnostics_protocol_error():
    rec = DiagnosticRecorder()

    def biz(request):
        return httpx.Response(
            200,
            json={
                "code": "200",
                "msg": "SYNTH_META_OK",
                "data": {"osd": 42},
            },
        )

    with pytest.raises(ProtocolError):
        with _client(Client, biz, rec) as client:
            client.osd.get_name(SERIAL)
    _check_events(rec, "sync", ("started", "failed"), True)
