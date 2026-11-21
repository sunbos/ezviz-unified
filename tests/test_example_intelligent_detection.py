import asyncio
import json
import os
from urllib.parse import parse_qsl

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.intelligent_detection as cli
from ezviz_unified.settings import AsyncSettings, Settings

USAGE = "usage: python -m examples.intelligent_detection --mode sync|async --action read-scene-switch|read-body-switch|read-face-switch|read-mode|read-advanced-type|write-demo\n"
ERRMSG = "error: intelligent detection example failed\n"
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
        (["--action", "read-scene-switch"], 1, "", USAGE),
        (["--mode", "sync"], 1, "", USAGE),
        (["--mode", "sync", "--action", "0"], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--action", "read-scene-switch", "--action", "write-demo"], 1, "", USAGE),
        (
            ["--mode", "sync", "--action", "read-scene-switch", "--action", "write-demo"],
            1,
            "",
            USAGE,
        ),
        (["--mode", "sync", "--action", "read-scene-switch", "extra"], 1, "", USAGE),
        (
            ["--mode", "sync", "--action", "read-mode", "--token", "FAKE_PRIVATE_ARGUMENT"],
            1,
            "",
            USAGE,
        ),
        (["--action", "read-scene-switch", "--mode", "sync", "--mode", "async"], 1, "", USAGE),
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
@pytest.mark.parametrize(
    "action", ("read-scene-switch", "read-body-switch", "read-face-switch", "read-mode")
)
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
        ("read-scene-switch", {"code": "10031", "msg": "PRIVATE_DENIED"}),
        ("read-mode", {"code": "10031", "msg": "PRIVATE_DENIED"}),
        ("read-scene-switch", {"code": "200", "data": {}}),
        ("read-mode", {"code": "200", "data": {"valueInfo": {"type": True}}}),
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
    assert cli.main(["--mode", mode, "--action", "read-mode"]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


def test_unexpected_error_propagates(rig, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    rig.biz_hook = explode
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync", "--action", "read-scene-switch"])
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
    for name in ("set_intelligent_detection_switch", "select_alarm_detection_mode"):
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
        "intelligent_write": True,
        "mode_write": True,
    }
    assert (
        data["simulated"] is True
        and data["intelligent_write"] is True
        and data["mode_write"] is True
    )
    assert sorted(calls) == [
        ("select_alarm_detection_mode", "domestic"),
        ("set_intelligent_detection_switch", "domestic"),
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
    switch = next(
        r for r in business if r.url.path == "/api/lapp/device/intelligence/detection/switch/set"
    )
    mode_request = next(
        r for r in business if r.url.path == "/api/v3/device/alarm/detect/switch/set"
    )
    assert switch.method == "POST" and mode_request.method == "POST"
    mform = dict(parse_qsl(switch.content.decode()))
    hform = dict(parse_qsl(mode_request.content.decode()))
    assert (
        {"accessToken", "deviceSerial", "enable"}
        <= set(mform)
        <= {"accessToken", "deviceSerial", "enable", "channelNo", "type"}
    )
    assert "accessToken" not in switch.headers
    assert "deviceSerial" not in switch.headers
    assert "accessToken" in mode_request.headers and "deviceSerial" in mode_request.headers
    assert set(hform) == {"type"}
    assert "type" not in mform or mform["type"] in {"302", "304"}
    assert mform["enable"] in {"0", "1"} and hform["type"] in {"1", "5"}


READ_CASES = [
    ("read-scene-switch", None, 0, False),
    ("read-scene-switch", None, 1, True),
    ("read-scene-switch", None, 9, None),
    ("read-body-switch", "302", 0, False),
    ("read-body-switch", "302", 1, True),
    ("read-body-switch", "302", 9, None),
    ("read-face-switch", "304", 0, False),
    ("read-face-switch", "304", 1, True),
    ("read-face-switch", "304", 9, None),
    ("read-mode", None, 1, "human"),
    ("read-mode", None, 5, "pir"),
    ("read-mode", None, 9, None),
]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("action,selector,state,expected", READ_CASES)
def test_read(rig, capsys, monkeypatch, mode, action, selector, state, expected):
    is_mode = action == "read-mode"
    serial = "MiXeD-9" if is_mode else "SYNUNIT"
    method = "GET" if is_mode else "POST"
    path = (
        "/api/v3/device/alarm/detect/switch/get"
        if is_mode
        else "/api/lapp/device/intelligence/detection/switch/status"
    )
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    data = (
        {"valueInfo": {"type": state, "future": "EXT_PRIVATE"}}
        if is_mode
        else {"enable": state, "channelNo": 2, "deviceSerial": "CALLER_PRIVATE"}
    )
    payload = {"code": "200", "msg": "PRIVATE_BACKEND", "data": data}
    rig.biz_hook = lambda request: httpx.Response(200, json=payload)
    assert cli.main(["--mode", mode, "--action", action]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    (line,) = captured.out.splitlines()
    assert captured.out == line + "\n"
    result = json.loads(line)
    key = "mode" if is_mode else "enabled"
    assert result == {key: expected}
    if not is_mode:
        assert result[key] is expected
    [(kind, key, secret, region)] = rig.calls
    assert (kind, key, secret) == (mode, "SynApp", "SynSecret")
    assert region.scope == "domestic"
    assert len(rig.auth_requests) == 1
    (target,) = rig.biz_requests
    for request in (*rig.auth_requests, target):
        assert request.url.scheme == "https" and request.url.host == "open.ys7.com"
    assert target.method == method and target.url.path == path
    assert not target.url.query
    if not is_mode:
        assert target.headers["content-type"].startswith("application/x-www-form-urlencoded")
        expected_form = [("accessToken", "TOK1"), ("deviceSerial", serial)]
        if selector is not None:
            expected_form.append(("type", selector))
        assert sorted(parse_qsl(target.content.decode(), keep_blank_values=True)) == sorted(
            expected_form
        )
        assert "accessToken" not in target.headers
        assert "deviceSerial" not in target.headers
    else:
        assert "content-type" not in target.headers
        assert target.content == b""
        assert target.headers.get_list("accessToken") == ["TOK1"]
        assert target.headers.get_list("deviceSerial") == [serial]
        assert target.headers.get_list("channelNo") == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "off"),
        (4, "motion"),
        (8, "human"),
        (1, None),
        (5, None),
        (-9, None),
    ],
)
def test_read_advanced_type(rig, capsys, monkeypatch, mode, value, expected):
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", "MiXeD-Unit9")
    body = {
        "code": "200",
        "msg": "PRIVATE_BACKEND",
        "data": {
            "valueInfo": {
                "type": value,
                "futurePrivate": "PRIVATE_FUTURE",
            }
        },
    }
    rig.biz_hook = lambda request: httpx.Response(200, json=body)

    exit_code = cli.main(["--mode", mode, "--action", "read-advanced-type"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"type": expected}

    assert len(rig.auth_requests) == 1
    assert len(rig.biz_requests) == 1
    request = rig.biz_requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v3/das/device/detect/switch/get"
    assert request.url.query == b""
    assert request.content == b""
    assert request.headers.get("content-type") is None
    assert request.headers["accessToken"] == "TOK1"
    assert request.headers["deviceSerial"] == "MiXeD-Unit9"
    assert "channelNo" not in request.headers


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())]
)
def test_advanced_interrupt(rig, capsys, mode, exc):
    def cancel(request):
        raise exc

    rig.biz_hook = cancel
    assert cli.main(["--mode", mode, "--action", "read-advanced-type"]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"code": "10031", "msg": "PRIVATE_DENIED"},
        {"code": "200", "data": {"valueInfo": {"type": True}}},
    ],
)
def test_advanced_failure(rig, capsys, mode, body):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode, "--action", "read-advanced-type"]) == 1
    out = capsys.readouterr()
    assert (
        (out.out, out.err) == ("", ERRMSG)
        and len(rig.auth_requests) == 1
        and len(rig.biz_requests) == 1
    )
