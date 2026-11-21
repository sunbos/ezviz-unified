import asyncio
import json
import os
from urllib.parse import parse_qsl

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.tracking_switches as cli
from ezviz_unified.settings import AsyncSettings, Settings

USAGE = "usage: python -m examples.tracking_switches --mode sync|async --action read-motion|read-human|write-demo\n"
ERRMSG = "error: tracking switch example failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_APP_KEY": "SynApp",
    "EZVIZ_APP_SECRET": "SynSecret",
    "EZVIZ_DEVICE_SERIAL": "SYNUNIT",
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
        (["--action", "read-motion"], 1, "", USAGE),
        (["--mode", "sync"], 1, "", USAGE),
        (["--mode", "sync", "--action", "0"], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--action", "read-motion", "--action", "write-demo"], 1, "", USAGE),
        (["--mode", "sync", "--action", "read-motion", "--action", "write-demo"], 1, "", USAGE),
        (["--mode", "sync", "--action", "read-motion", "extra"], 1, "", USAGE),
    ],
)
def test_usage(rig, capsys, monkeypatch, argv, code, so, se):
    monkeypatch.setattr(cli, "load_config", _dead)
    assert cli.main(argv) == code
    out = capsys.readouterr()
    assert (out.out, out.err) == (so, se)
    assert rig.calls == rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize(
    "over,drop",
    [
        ({"EZVIZ_REGION": "overseas"}, None),
        ({}, "EZVIZ_REGION"),
        ({}, "EZVIZ_APP_KEY"),
        ({}, "EZVIZ_APP_SECRET"),
        ({}, "EZVIZ_DEVICE_SERIAL"),
        ({"EZVIZ_DEVICE_SERIAL": "   "}, None),
    ],
)
@pytest.mark.parametrize("action", ("read-motion", "read-human"))
def test_bad_env(rig, capsys, monkeypatch, over, drop, action):
    for k, v in over.items():
        monkeypatch.setenv(k, v)
    if drop:
        monkeypatch.delenv(drop, raising=False)
    assert cli.main(["--mode", "sync", "--action", action]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "action,body",
    [
        ("read-motion", {"code": "10031", "msg": "PRIVATE_DENIED"}),
        ("read-human", {"meta": {"code": 10031, "message": "PRIVATE_DENIED"}}),
        ("read-motion", {"code": "200", "data": {}}),
        ("read-human", {"meta": {"code": 200}, "data": {"enable": True}}),
    ],
)
def test_query_fail(rig, capsys, mode, action, body):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode, "--action", action]) == 1
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
    assert cli.main(["--mode", mode, "--action", "read-human"]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


def test_unexpected_error_propagates(rig, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    rig.biz_hook = explode
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync", "--action", "read-motion"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_write_demo_uses_real_setter_offline(monkeypatch, capsys, mode, reverse_order):
    cls = Settings if mode == "sync" else AsyncSettings
    requests, calls = [], []

    def make_spy(name, original):
        def wrapped(self, *args, **kw):
            calls.append((name, self._client.region.scope))
            return original(self, *args, **kw)

        return wrapped

    class _Env(dict):
        def _trap(self, key):
            assert not str(key).startswith("EZVIZ_")

        def get(self, key, default=None):
            self._trap(key)
            return super().get(key, default)

        def __getitem__(self, key):
            self._trap(key)
            return super().__getitem__(key)

        def _bulk(self, *args, **kwargs):
            raise AssertionError("unexpected environment enumeration")

        __iter__ = keys = items = values = copy = _bulk

        def __contains__(self, key):
            self._trap(key)
            return super().__contains__(key)

    attr = "handle_request" if mode == "sync" else "handle_async_request"
    original_transport = getattr(httpx.MockTransport, attr)
    if mode == "sync":

        def record(self, request):
            requests.append(request)
            return original_transport(self, request)
    else:

        async def record(self, request):
            requests.append(request)
            return await original_transport(self, request)

    monkeypatch.setattr(httpx.MockTransport, attr, record)
    for name in ("set_motion_tracking", "set_human_tracking"):
        monkeypatch.setattr(cls, name, make_spy(name, getattr(cls, name)))
    monkeypatch.setattr(cli, "load_config", _dead)
    monkeypatch.setattr(os, "environ", _Env())
    argv = (
        ["--action", "write-demo", "--mode", mode]
        if reverse_order
        else ["--mode", mode, "--action", "write-demo"]
    )
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    (line,) = captured.out.splitlines()
    assert captured.out == line + "\n"
    data = json.loads(line)
    assert data == {
        "simulated": True,
        "motion_write": True,
        "human_write": True,
    }
    assert (
        data["simulated"] is True and data["motion_write"] is True and data["human_write"] is True
    )
    assert sorted(calls) == [
        ("set_human_tracking", "domestic"),
        ("set_motion_tracking", "domestic"),
    ]
    for req in requests:
        assert req.url.scheme == "https" and req.url.host == "open.ys7.com"
        assert not req.url.query and req.headers["content-type"].startswith(
            "application/x-www-form-urlencoded"
        )
    auth = [r for r in requests if r.method == "POST" and r.url.path == "/api/lapp/token/get"]
    business = [r for r in requests if r.url.path != "/api/lapp/token/get"]
    assert len(auth) in (1, 2) and len(business) == 2
    assert len(requests) == len(auth) + len(business)
    motion = next(r for r in business if r.url.path == "/api/lapp/device/mobile/status/set")
    human = next(r for r in business if r.url.path == "/api/v3/device/switch/human/track")
    assert motion.method == "POST" and human.method == "PUT"
    mform = dict(parse_qsl(motion.content.decode()))
    hform = dict(parse_qsl(human.content.decode()))
    assert (
        {"accessToken", "deviceSerial", "enable"}
        <= set(mform)
        <= {"accessToken", "deviceSerial", "enable", "channelNo"}
    )
    assert "accessToken" not in motion.headers
    assert "deviceSerial" not in motion.headers
    assert "accessToken" in human.headers and "deviceSerial" in human.headers
    assert set(hform) == {"enable"}
    assert mform["enable"] in {"0", "1"} and hform["enable"] in {"0", "1"}


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "action,serial,method,path",
    [
        ("read-motion", "SYNUNIT", "POST", "/api/lapp/device/mobile/status/get"),
        ("read-human", "MiXeD-9", "GET", "/api/v3/device/switch/human/track"),
    ],
)
@pytest.mark.parametrize("state,expected", [(0, False), (1, True), (9, None)])
def test_read(rig, capsys, monkeypatch, mode, action, serial, method, path, state, expected):
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    data = {
        "deviceSerial": "CALLER_PRIVATE",
        "channelNo": 2,
        "enable": state,
        "future": "EXT_PRIVATE",
    }
    if action == "read-motion":
        payload = {"code": "200", "msg": "PRIVATE_BACKEND", "data": data}
    else:
        payload = {"meta": {"code": 200, "message": "PRIVATE_BACKEND"}, "data": data}
    rig.biz_hook = lambda request: httpx.Response(200, json=payload)
    assert cli.main(["--mode", mode, "--action", action]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    (line,) = captured.out.splitlines()
    assert captured.out == line + "\n"
    result = json.loads(line)
    assert result == {"enabled": expected}
    assert result["enabled"] is expected
    [(kind, key, secret, region)] = rig.calls
    assert (kind, key, secret) == (mode, "SynApp", "SynSecret")
    assert region.scope == "domestic"
    assert len(rig.auth_requests) == 1
    (target,) = rig.biz_requests
    for request in (*rig.auth_requests, target):
        assert request.url.scheme == "https" and request.url.host == "open.ys7.com"
    assert target.method == method and target.url.path == path
    assert target.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert not target.url.query
    if action == "read-motion":
        assert sorted(parse_qsl(target.content.decode())) == [
            ("accessToken", "TOK1"),
            ("deviceSerial", serial),
        ]
        assert "accessToken" not in target.headers
        assert "deviceSerial" not in target.headers
    else:
        assert target.content == b""
        assert target.headers.get_list("accessToken") == ["TOK1"]
        assert target.headers.get_list("deviceSerial") == [serial]
        assert target.headers.get_list("channelNo") == []
