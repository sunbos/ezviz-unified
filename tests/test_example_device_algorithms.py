"""Tests for the domestic device-algorithms inventory example CLI."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl

import httpx
import pytest

from examples import device_algorithms as cli
from ezviz_unified import AsyncClient, Client, DiagnosticRecorder

ENV_VARS = (
    "EZVIZ_APP_KEY",
    "EZVIZ_APP_SECRET",
    "EZVIZ_REGION",
    "EZVIZ_DEVICE_SERIAL",
    "EZVIZ_AUTH_BASE_URL",
)
USAGE = "usage: python -m examples.device_algorithms --mode sync|async --query devices|supported"
ENTRY = {
    "devices": "device_algorithms.list_devices",
    "supported": "device_algorithms.list_supported",
}
PATH = {
    "devices": "/api/v3/intelligent/model/device",
    "supported": "/api/v3/intelligent/model/device/support",
}
FORM = {
    "devices": {"pageStart": "0", "pageSize": "8"},
    "supported": {"deviceSerial": "SYNTH-SERIAL"},
}


@pytest.fixture
def harness(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EZVIZ_APP_KEY", "synthetic-app-key")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "synthetic-app-secret")
    monkeypatch.setenv("EZVIZ_REGION", "domestic")
    state = {
        "auth": lambda _r: httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "SYNTH_TOKEN",
                    "expireTime": 9999999999999,
                    "areaDomain": "https://open.ys7.com",
                },
            },
        ),
        "biz": lambda _r: httpx.Response(
            200,
            json={
                "meta": {"code": 200, "message": "ok", "moreInfo": None},
                "data": [{"deviceSerial": "SYNTH-SERIAL"}],
                "extra": 0,
            },
        ),
    }
    seen, rec = {"auth": [], "biz": []}, DiagnosticRecorder()

    def tap(kind):
        def handle(request):
            seen[kind].append(request)
            return state[kind](request)

        return handle

    def make(cls):
        def build(*args, **kwargs):
            return cls(
                *args,
                **kwargs,
                auth_transport=httpx.MockTransport(tap("auth")),
                transport_factory=lambda _o: httpx.MockTransport(tap("biz")),
                diagnostic_recorder=rec,
            )

        return build

    monkeypatch.setattr(cli, "Client", make(Client))
    monkeypatch.setattr(cli, "AsyncClient", make(AsyncClient))
    return SimpleNamespace(seen=seen, state=state, rec=rec)


def _run(argv):
    return cli.main(list(argv))


def _events(rec):
    return [e for e in rec.report()["events"] if isinstance(e, dict)]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("query", ["devices", "supported"])
def test_query_ok(harness, monkeypatch, capsys, mode, query):
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "SYNTH-SERIAL")
    assert _run(["--mode", mode, "--query", query]) == 0
    out, err = capsys.readouterr()
    expected = {"region": "domestic", "mode": mode, "query": query, "status": "ok"}
    assert json.loads(out) == expected and err == ""
    (auth,), (biz,) = harness.seen["auth"], harness.seen["biz"]
    assert auth.method == "POST" and auth.url.host == "open.ys7.com"
    assert auth.url.path == "/api/lapp/token/get"
    assert dict(parse_qsl(auth.content.decode())) == {
        "appKey": "synthetic-app-key",
        "appSecret": "synthetic-app-secret",
    }
    assert biz.method == "GET" and biz.url.host == "open.ys7.com"
    assert biz.url.path == PATH[query] and biz.url.query == b""
    assert biz.headers["accessToken"] == "SYNTH_TOKEN"
    ctype = biz.headers["content-type"].split(";")[0].strip()
    assert ctype == "application/x-www-form-urlencoded"
    assert dict(parse_qsl(biz.content.decode())) == FORM[query]
    assert "SYNTH_TOKEN" not in out + err
    assert "synthetic-app-secret" not in out + err
    events = _events(harness.rec)
    calls = [e for e in events if e.get("kind") == "call" and e.get("entrypoint") == ENTRY[query]]
    assert [e.get("outcome") for e in calls] == ["started", "succeeded"]
    attempts = [e for e in events if e.get("kind") == "attempt"]
    assert attempts and all(e.get("operation") == ENTRY[query] for e in attempts)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_api_error(harness, capsys, mode):
    harness.state["biz"] = lambda _r: httpx.Response(
        200, json={"meta": {"code": 500, "message": "boom", "moreInfo": None}, "data": None}
    )
    assert _run(["--mode", mode, "--query", "devices"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == "error: algorithm query failed\n"
    calls = [
        e
        for e in _events(harness.rec)
        if e.get("kind") == "call" and e.get("entrypoint") == ENTRY["devices"]
    ]
    assert [e.get("outcome") for e in calls] == ["started", "failed"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("region,query", [("domestic", "supported"), ("overseas", "devices")])
def test_rejected_before_auth(harness, monkeypatch, capsys, mode, region, query):
    monkeypatch.setenv("EZVIZ_REGION", region)
    assert _run(["--mode", mode, "--query", query]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == "error: algorithm query failed\n"
    assert harness.seen == {"auth": [], "biz": []}


@pytest.mark.parametrize(
    "argv,rc,stream",
    [
        (["--help"], 0, "out"),
        (["-h"], 0, "out"),
        ([], 1, "err"),
        (["--mode", "sync"], 1, "err"),
        (["--mode", "bogus", "--query", "devices"], 1, "err"),
        (["--mode", "sync", "--query", "bogus"], 1, "err"),
        (["--mode", "sync", "--query", "devices", "--extra"], 1, "err"),
    ],
)
def test_usage(harness, monkeypatch, capsys, argv, rc, stream):
    monkeypatch.setattr(cli, "load_config", lambda *_a, **_k: pytest.fail("load_config called"))
    assert _run(argv) == rc
    out, err = capsys.readouterr()
    shown, other = (out, err) if stream == "out" else (err, out)
    assert shown.splitlines()[0] == USAGE and other == ""
    assert harness.seen == {"auth": [], "biz": []}


@pytest.mark.parametrize(
    "mode,exc",
    [("sync", KeyboardInterrupt), ("async", KeyboardInterrupt), ("async", asyncio.CancelledError)],
)
def test_aborted(harness, capsys, mode, exc):
    def broken(_request):
        raise exc

    harness.state["biz"] = broken
    assert _run(["--mode", mode, "--query", "devices"]) == 130
    out, err = capsys.readouterr()
    assert out == "" and err == "error: algorithm query failed\n"
    assert "SYNTH_TOKEN" not in out + err
