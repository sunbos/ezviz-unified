import asyncio
import json
import os
from urllib.parse import parse_qsl

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.recording_files as cli

USAGE = (
    "usage: python -m examples.recording_files --mode sync|async --query cloud|local|component\n"
)
ERRMSG = "error: recording files query failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_DEVICE_SERIAL": "SERIAL_PRIVATE",
    "EZVIZ_APP_KEY": "SynApp",
    "EZVIZ_APP_SECRET": "SynSecret",
}


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    def spy(*args, **kwargs):
        pytest.fail("forbidden external call")

    for target in (
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
        "socket.create_connection",
        "webbrowser.open",
        "webbrowser.open_new",
        "webbrowser.open_new_tab",
    ):
        monkeypatch.setattr(target, spy)


@pytest.fixture()
def rig(monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    state = _Rig()
    monkeypatch.setattr(cli, "Client", state.wrap(cli.Client, "sync"))
    monkeypatch.setattr(cli, "AsyncClient", state.wrap(cli.AsyncClient, "async"))
    return state


def _dead(*args, **kwargs):
    raise AssertionError("load_config must not run")


TIME_START = "2040-02-29 23:58:03"
TIME_STOP = "2040-03-01 00:00:05"


@pytest.mark.parametrize(
    "argv,code,so,se",
    [
        (["-h"], 0, USAGE, ""),
        (["--help"], 0, USAGE, ""),
        ([], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--mode", "sync"], 1, "", USAGE),
        (["--query", "cloud"], 1, "", USAGE),
        (["--mode", "sync", "--query", "unknown"], 1, "", USAGE),
        (["--mode", "sync", "--query", "local", "--query", "cloud"], 1, "", USAGE),
        (["--mode", "sync", "--query", "local", "--mode", "async"], 1, "", USAGE),
        (["--mode"], 1, "", USAGE),
        (["--mode", "bogus"], 1, "", USAGE),
        (["--write"], 1, "", USAGE),
        (["--mode", "sync", "--write"], 1, "", USAGE),
    ],
)
def test_usage(rig, capsys, monkeypatch, argv, code, so, se):
    monkeypatch.setattr(cli, "load_config", _dead)
    assert cli.main(argv) == code
    out = capsys.readouterr()
    assert (out.out, out.err) == (so, se)
    assert rig.calls == rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize(
    "over,drop,query",
    [
        ({"EZVIZ_REGION": "overseas"}, None, "cloud"),
        ({}, "EZVIZ_APP_KEY", "local"),
        ({}, "EZVIZ_APP_SECRET", "cloud"),
        ({}, "EZVIZ_DEVICE_SERIAL", "component"),
        ({}, "EZVIZ_DEVICE_SERIAL", "cloud"),
        ({}, "EZVIZ_DEVICE_SERIAL", "local"),
        ({"EZVIZ_DEVICE_SERIAL": "   "}, None, "cloud"),
        ({}, "EZVIZ_RECORD_START_TIME", "component"),
        ({}, "EZVIZ_RECORD_STOP_TIME", "component"),
        ({"EZVIZ_RECORD_START_TIME": "2041-02-29 00:00:00"}, None, "component"),
        ({"EZVIZ_RECORD_STOP_TIME": "2039-04-30 00:00:00"}, None, "component"),
    ],
)
def test_bad_env(rig, capsys, monkeypatch, over, drop, query):
    monkeypatch.setenv("EZVIZ_RECORD_START_TIME", TIME_START)
    monkeypatch.setenv("EZVIZ_RECORD_STOP_TIME", TIME_STOP)
    for k, v in over.items():
        monkeypatch.setenv(k, v)
    if drop:
        monkeypatch.delenv(drop, raising=False)
    assert cli.main(["--mode", "sync", "--query", query]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"code": "10031", "msg": "PRIVATE_DENIAL"},
        {"code": "200", "data": {"files": [], "isAll": False}},
        {"code": "200", "data": [{}, {}]},
        {"code": "200", "data": {"files": [{}, {}], "isAll": True, "nextFileTime": 0}},
    ],
    ids=["denied", "missing-time", "too-many-flat", "too-many-paged"],
)
@pytest.mark.parametrize("query", ("cloud", "local"))
def test_query_fail(rig, capsys, mode, body, query):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode, "--query", query]) == 1
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
    assert cli.main(["--mode", mode, "--query", "cloud"]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


def test_unexpected_error_propagates(rig, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    rig.biz_hook = explode
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync", "--query", "cloud"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "query,data,expected_rows,has_more",
    [
        ("cloud", [], 0, None),
        ("cloud", [{"fileName": "SYN_CL1", "url": "https://canary.invalid/cl1"}], 1, None),
        ("cloud", {"files": [], "isAll": False, "nextFileTime": 777}, 0, True),
        (
            "cloud",
            {
                "files": [{"fileName": "SYN_CL2", "url": "https://canary.invalid/cl2"}],
                "isAll": True,
                "nextFileTime": 0,
            },
            1,
            False,
        ),
        ("local", [], 0, None),
        ("local", [{"fileName": "SYN_LO1", "url": "https://canary.invalid/lo1"}], 1, None),
        ("local", {"files": [], "isAll": False, "nextFileTime": 888}, 0, True),
        (
            "local",
            {
                "files": [{"fileName": "SYN_LO2", "url": "https://canary.invalid/lo2"}],
                "isAll": True,
                "nextFileTime": 0,
            },
            1,
            False,
        ),
        (
            "component",
            [
                {"fileName": "SYN_CO1", "url": "https://canary.invalid/co1"},
                {"fileName": "SYN_CO2", "url": "https://canary.invalid/co2"},
            ],
            2,
            None,
        ),
        ("component", [], 0, None),
    ],
)
def test_query_success(rig, capsys, monkeypatch, mode, query, data, expected_rows, has_more):
    if query == "component":
        monkeypatch.setenv("EZVIZ_RECORD_START_TIME", TIME_START)
        monkeypatch.setenv("EZVIZ_RECORD_STOP_TIME", TIME_STOP)
    rig.biz_hook = lambda _req: httpx.Response(
        200, json={"code": "200", "msg": "PRIVATE_MSG", "data": data}
    )
    argv = (
        ["--mode", mode, "--query", query] if mode == "sync" else ["--query", query, "--mode", mode]
    )
    assert cli.main(argv) == 0
    out = capsys.readouterr()
    (line,) = out.out.splitlines()
    assert out.out == line + "\n"
    payload = json.loads(line)
    assert payload == {"returned_files": expected_rows, "has_more": has_more}
    assert type(payload["returned_files"]) is int and payload["has_more"] is has_more
    assert out.err == ""
    ((kind, key, secret, region),) = rig.calls
    assert (kind, key, secret) == (mode, "SynApp", "SynSecret")
    assert region.scope == "domestic"
    assert len(rig.auth_requests) == 1
    (req,) = rig.biz_requests
    assert req.method == "POST" and req.url.scheme == "https"
    assert req.url.host == "open.ys7.com" and req.url.path == "/api/lapp/video/by/time"
    assert req.url.query == b""
    assert "authorization" not in req.headers and "accessToken" not in req.headers
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    fields = [("accessToken", "TOK1"), ("deviceSerial", "SERIAL_PRIVATE")]
    if query == "component":
        fields += [("startTime", TIME_START), ("stopTime", TIME_STOP)]
    else:
        fields += [
            ("pageSize", "1"),
            ("recType", {"cloud": "1", "local": "2"}[query]),
            ("version", "2.0"),
        ]
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(fields)
