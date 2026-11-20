import asyncio
import json
import os

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.video_codec as cli

USAGE = "usage: python -m examples.video_codec --mode sync|async --stream 1|2\n"
ERRMSG = "error: video codec query failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_APP_KEY": "SynApp",
    "EZVIZ_APP_SECRET": "SynSecret",
    "EZVIZ_DEVICE_SERIAL": "SynUnit",
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
    monkeypatch.delenv("EZVIZ_CHANNEL_NO", raising=False)
    state = _Rig()
    monkeypatch.setattr(cli, "Client", state.wrap(cli.Client, "sync"))
    monkeypatch.setattr(cli, "AsyncClient", state.wrap(cli.AsyncClient, "async"))
    return state


def _codec(code=1):
    def hook(request):
        return httpx.Response(
            200, json={"meta": {"code": 200, "message": "ok"}, "data": {"videoCode": code}}
        )

    return hook


def _dead(*args, **kwargs):
    raise AssertionError("load_config must not run")


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
        (["--stream", "1"], 1, "", USAGE),
        (["--mode", "sync"], 1, "", USAGE),
        (["--mode", "sync", "--stream", "0"], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--stream", "1", "--stream", "2"], 1, "", USAGE),
        (["--mode", "sync", "--stream", "1", "--stream", "2"], 1, "", USAGE),
        (["--mode", "sync", "--stream", "1", "extra"], 1, "", USAGE),
    ],
)
def test_usage(rig, capsys, monkeypatch, argv, code, so, se):
    monkeypatch.setattr(cli, "load_config", _dead)
    rig.biz_hook = _codec()
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
        ({}, None, "+1"),
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
    rig.biz_hook = _codec()
    assert cli.main(["--mode", "sync", "--stream", "1"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"meta": {"code": 10031, "message": "denied"}, "data": {"videoCode": 1}},
        {"meta": {"code": 200, "message": "ok"}, "data": {"videoCode": "1"}},
        {"meta": {"code": 200, "message": "ok"}, "data": {}},
    ],
    ids=["code10031", "badtype", "missing"],
)
def test_query_fail(rig, capsys, mode, body):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode, "--stream", "1"]) == 1
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
    assert cli.main(["--mode", mode, "--stream", "1"]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


def test_unexpected_error_propagates(rig, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    rig.biz_hook = explode
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync", "--stream", "1"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize("channel", [None, "-2"])
@pytest.mark.parametrize(
    "code, expected_number, expected_label",
    [
        (0, 0, "private_h264"),
        (1, 1, "standard_h264"),
        (2, 2, "standard_mpeg4"),
        (3, 3, "standard_mpeg2"),
        (4, 4, "mjpeg"),
        (5, 5, "standard_h265"),
        (6, 6, "smart264"),
        (7, 7, "smart265"),
        (99, None, "unknown"),
        (-7, None, "unknown"),
    ],
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_query_ok(rig, capsys, monkeypatch, mode, code, expected_number, expected_label, channel):
    if channel is not None:
        monkeypatch.setenv("EZVIZ_CHANNEL_NO", channel)
    rig.biz_hook = _codec(code)
    stream = "1" if code % 2 == 0 else "2"
    if channel is None:
        argv = ["--mode", mode, "--stream", stream]
    else:
        argv = ["--stream", stream, "--mode", mode]
    assert cli.main(argv) == 0
    out = capsys.readouterr()
    (line,) = out.out.splitlines()
    assert out.out == line + "\n" and out.err == "" and "TOK1" not in line
    payload = json.loads(line)
    assert payload == {"video_code": expected_number, "codec": expected_label}
    if expected_number is None:
        assert payload["video_code"] is None
    assert type(payload["video_code"]) is int or payload["video_code"] is None
    assert type(payload["codec"]) is str
    [(kind, key, secret, region)] = rig.calls
    assert (kind, key, secret, region.scope) == (mode, "SynApp", "SynSecret", "domestic")
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1
    req = rig.biz_requests[0]
    assert (req.method, req.url.host, req.url.path) == (
        "GET",
        "open.ys7.com",
        "/api/v3/das/device/video/encode",
    )
    assert req.content == b"" and req.headers.get("content-type") is None
    assert req.url.query == f"streamType={stream}".encode()
    assert req.headers.get("accessToken") == "TOK1" and req.headers.get("deviceSerial") == "SynUnit"
    assert req.headers.get("channelNo") == channel
    assert req.headers.get("localIndex") is None
