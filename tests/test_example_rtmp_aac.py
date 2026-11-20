import asyncio
import json

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.rtmp_aac as cli

USAGE = "usage: python -m examples.rtmp_aac --mode sync|async\n"
ERRMSG = "error: RTMP AAC support query failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_APP_KEY": "SynApp",
    "EZVIZ_APP_SECRET": "SynSecret",
    "EZVIZ_DEVICE_SERIAL": "SynUnit",
}


@pytest.fixture()
def rig(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    monkeypatch.delenv("EZVIZ_CHANNEL_NO", raising=False)
    state = _Rig()
    monkeypatch.setattr(cli, "Client", state.wrap(cli.Client, "sync"))
    monkeypatch.setattr(cli, "AsyncClient", state.wrap(cli.AsyncClient, "async"))
    return state


def _aac(enable=True):
    def hook(request):
        return httpx.Response(
            200, json={"meta": {"code": 200, "message": "ok"}, "data": {"enable": enable}}
        )

    return hook


def _dead(*args, **kwargs):
    raise AssertionError("load_config must not run")


@pytest.mark.parametrize("channel", [None, "0", "7"])
@pytest.mark.parametrize("supported", [True, False])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_query_ok(rig, capsys, monkeypatch, mode, supported, channel):
    if channel is not None:
        monkeypatch.setenv("EZVIZ_CHANNEL_NO", channel)
    rig.biz_hook = _aac(supported)
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    (line,) = out.out.splitlines()
    assert out.out == line + "\n" and out.err == "" and "TOK1" not in line
    payload = json.loads(line)
    assert payload == {"region": "domestic", "mode": mode, "status": "ok", "supported": supported}
    assert type(payload["supported"]) is bool
    [(kind, key, secret, region)] = rig.calls
    assert (kind, key, secret, region.scope) == (mode, "SynApp", "SynSecret", "domestic")
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1
    req = rig.biz_requests[0]
    assert (req.method, req.url.host, req.url.path) == (
        "GET",
        "open.ys7.com",
        "/api/service/media/aac/transfer",
    )
    assert req.url.query == req.content == b""
    assert req.headers.get("accessToken") == "TOK1" and req.headers.get("deviceSerial") == "SynUnit"
    assert req.headers.get("localIndex") == channel


@pytest.mark.parametrize(
    "argv,code,so,se",
    [
        (["-h"], 0, USAGE, ""),
        (["--help"], 0, USAGE, ""),
        ([], 1, "", USAGE),
        (["--mode"], 1, "", USAGE),
        (["--mode", "bogus"], 1, "", USAGE),
        (["--write"], 1, "", USAGE),
        (["--mode", "sync", "--write"], 1, "", USAGE),
    ],
)
def test_usage(rig, capsys, monkeypatch, argv, code, so, se):
    monkeypatch.setattr(cli, "load_config", _dead)
    rig.biz_hook = _aac()
    assert cli.main(argv) == code
    out = capsys.readouterr()
    assert (out.out, out.err) == (so, se)
    assert rig.calls == rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize(
    "over,drop,channel",
    [
        ({"EZVIZ_REGION": "moon"}, None, None),
        ({}, "EZVIZ_REGION", None),
        ({}, "EZVIZ_APP_KEY", None),
        ({"EZVIZ_REGION": "overseas"}, None, None),
        ({"EZVIZ_DEVICE_SERIAL": " SN12345"}, None, None),
        ({"EZVIZ_DEVICE_SERIAL": "SN12345 "}, None, None),
        ({"EZVIZ_DEVICE_SERIAL": ""}, None, None),
        ({}, "EZVIZ_APP_SECRET", None),
        ({}, "EZVIZ_DEVICE_SERIAL", None),
        ({}, None, "-1"),
        ({}, None, "abc"),
        ({}, None, "1_0"),
    ],
)
def test_bad_env(rig, capsys, monkeypatch, over, drop, channel):
    for k, v in over.items():
        monkeypatch.setenv(k, v)
    if drop:
        monkeypatch.delenv(drop, raising=False)
    if channel is not None:
        monkeypatch.setenv("EZVIZ_CHANNEL_NO", channel)
    rig.biz_hook = _aac()
    assert cli.main(["--mode", "sync"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.calls == rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"meta": {"code": 10031, "message": "denied"}, "data": {"enable": True}},
        {"meta": {"code": 200, "message": "ok"}, "data": {"enable": "yes"}},
        {"meta": {"code": 200, "message": "ok"}, "data": {}},
    ],
    ids=["code10031", "badtype", "missing"],
)
def test_query_fail(rig, capsys, mode, body):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode]) == 1
    out = capsys.readouterr()
    assert (
        (out.out, out.err) == ("", ERRMSG)
        and len(rig.auth_requests) == 1
        and len(rig.biz_requests) == 1
    )


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())]
)
def test_interrupt(rig, capsys, mode, exc):
    def cancel(request):
        raise exc

    rig.biz_hook = cancel
    assert cli.main(["--mode", mode]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1
