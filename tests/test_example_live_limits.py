import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from examples import live_limits as cli
from ezviz_unified import AsyncClient, Client, DiagnosticRecorder

USAGE = "usage: python -m examples.live_limits --mode sync|async --live-type VTDU|RTMP\n"
ERROR_LINE = "error: live limit query failed\n"
LIMIT_PATH = "/api/v3/mall/online/user/limit/live"
AUTH_PATH = "/api/lapp/token/get"
AUTH_PAYLOAD = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
# A synthetic canary row and an unknown top-level key prove the payload is
# passed through untouched; no real rules or account data are present.
OK_BIZ = {
    "meta": {"code": 200, "message": "ok"},
    "data": [{"liveType": "CANARY", "limitType": 9, "limitNumber": -1}],
    "unknownTopLevel": "synthetic-canary",
}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for name in ("EZVIZ_REGION", "EZVIZ_APP_KEY", "EZVIZ_APP_SECRET", "EZVIZ_AUTH_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def rig(monkeypatch):
    auth_requests, biz_requests = [], []
    recorder = DiagnosticRecorder()
    state = {"biz": lambda request: httpx.Response(200, json=OK_BIZ)}

    def auth_handler(request):
        auth_requests.append(request)
        return httpx.Response(200, json=AUTH_PAYLOAD)

    def biz_handler(request):
        biz_requests.append(request)
        return state["biz"](request)

    def patch(cls):
        def factory(*args, **kwargs):
            kwargs["auth_transport"] = httpx.MockTransport(auth_handler)
            kwargs["transport_factory"] = lambda origin: httpx.MockTransport(biz_handler)
            kwargs["diagnostic_recorder"] = recorder
            return cls(*args, **kwargs)

        return factory

    monkeypatch.setattr(cli, "Client", patch(Client))
    monkeypatch.setattr(cli, "AsyncClient", patch(AsyncClient))
    box = SimpleNamespace(auth=auth_requests, biz=biz_requests, rec=recorder)
    box.set_biz = lambda handler: state.__setitem__("biz", handler)

    def configure(region="domestic"):
        # Exercise the real load_config path with synthetic env credentials.
        # The autouse fixture already cleared all EZVIZ_* variables, so the
        # only configuration visible here is what we set below.
        monkeypatch.setenv("EZVIZ_REGION", region)
        monkeypatch.setenv("EZVIZ_APP_KEY", "mock-key")
        monkeypatch.setenv("EZVIZ_APP_SECRET", "mock-secret")

    box.config = configure
    return box


def _outcomes(box, mode):
    return [
        e.get("outcome")
        for e in box.rec.report()["events"]
        if e.get("kind") == "call"
        and e.get("entrypoint") == "live_limits.get"
        and e.get("mode") == mode
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("live_type", ["VTDU", "RTMP"])
def test_live_limits_get_success(rig, mode, live_type, capsys):
    rig.config()
    rc = cli.main(["--mode", mode, "--live-type", live_type])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out) == {
        "region": "domestic",
        "mode": mode,
        "live_type": live_type,
        "status": "ok",
    }
    # Sensitive settings never appear in the output.
    assert "mock-key" not in out and "mock-secret" not in out
    assert "SYNTH_TOKEN" not in out
    assert len(rig.auth) == 1
    auth = rig.auth[0]
    assert auth.method == "POST"
    assert auth.url.host == "open.ys7.com"
    assert auth.url.path == AUTH_PATH
    assert not auth.url.query
    assert parse_qs(auth.content.decode()) == {
        "appKey": ["mock-key"],
        "appSecret": ["mock-secret"],
    }
    assert len(rig.biz) == 1
    req = rig.biz[0]
    assert req.method == "GET"
    assert req.url.host == "open.ys7.com"
    assert req.url.path == LIMIT_PATH
    assert req.url.params.multi_items() == [("liveType", live_type)]
    assert req.content == b""
    assert req.headers.get_list("accessToken") == ["SYNTH_TOKEN"]
    assert _outcomes(rig, mode) == ["started", "succeeded"]


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help(rig, monkeypatch, capsys, flag):
    monkeypatch.setattr(cli, "load_config", pytest.fail)
    rc = cli.main([flag])
    assert rc == 0
    out, err = capsys.readouterr()
    assert out == USAGE
    assert err == ""


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "sync"],
        ["--live-type", "VTDU"],
        ["--mode", "bogus", "--live-type", "VTDU"],
        ["--mode", "sync", "--live-type", "HLS"],
        ["--live-type", "VTDU", "--mode", "sync"],
        ["--mode", "sync", "--live-type", "VTDU", "--delete"],
        ["--mode", "sync", "--delete", "--live-type", "VTDU"],
    ],
)
def test_invalid_args(rig, monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "load_config", pytest.fail)
    rc = cli.main(argv)
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == USAGE
    assert rig.auth == [] and rig.biz == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_overseas_config_rejected(rig, mode, capsys):
    rig.config("overseas")
    rc = cli.main(["--mode", mode, "--live-type", "VTDU"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ERROR_LINE
    assert rig.auth == [] and rig.biz == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"code": 0, "message": "failed"}, "data": []},
        {"meta": {"code": 10005, "message": "denied"}},
    ],
)
def test_business_failure(rig, mode, payload, capsys):
    rig.config()
    rig.set_biz(lambda request: httpx.Response(200, json=payload))
    rc = cli.main(["--mode", mode, "--live-type", "RTMP"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ERROR_LINE
    assert len(rig.biz) == 1
    assert _outcomes(rig, mode) == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "exc,outcome",
    [
        (KeyboardInterrupt, "interrupted"),
        (asyncio.CancelledError, "cancelled"),
    ],
)
def test_cancellation(rig, mode, exc, outcome, capsys):
    rig.config()

    def boom(request):
        raise exc()

    rig.set_biz(boom)
    rc = cli.main(["--mode", mode, "--live-type", "VTDU"])
    assert rc == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ERROR_LINE
    assert _outcomes(rig, mode) == ["started", outcome]
