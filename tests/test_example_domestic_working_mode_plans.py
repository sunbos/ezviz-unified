import asyncio
import json
import os

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.domestic_working_mode_plans as cli
from ezviz_unified.settings import AsyncSettings, Settings

USAGE = "usage: python -m examples.domestic_working_mode_plans --mode sync|async --action read|write-demo\n"
ERRMSG = "error: domestic working mode plan example failed\n"
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
    monkeypatch.delenv("EZVIZ_TIMING_PLAN_TYPE", raising=False)
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
        (["--action", "read"], 1, "", USAGE),
        (["--mode", "sync"], 1, "", USAGE),
        (["--mode", "sync", "--action", "0"], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--action", "read", "--action", "write-demo"], 1, "", USAGE),
        (["--mode", "sync", "--action", "read", "--action", "write-demo"], 1, "", USAGE),
        (["--mode", "sync", "--action", "read", "extra"], 1, "", USAGE),
    ],
)
def test_usage(rig, capsys, monkeypatch, argv, code, so, se):
    monkeypatch.setattr(cli, "load_config", _dead)
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
    assert cli.main(["--mode", "sync", "--action", "read"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"code": "10031", "msg": "PRIVATE_MSG"},
        {"code": "200", "data": {"weekPlans": []}},
        {"code": "200", "data": [{}]},
        {"code": "200", "data": [{"weekPlans": [{"timePlan": [None]}]}]},
    ],
    ids=["denied", "object-not-list", "missing-week-plans", "bad-slot"],
)
def test_query_fail(rig, capsys, mode, body):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode, "--action", "read"]) == 1
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
    assert cli.main(["--mode", mode, "--action", "read"]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


def test_unexpected_error_propagates(rig, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    rig.biz_hook = explode
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync", "--action", "read"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "channel,ptype,count",
    [(None, None, 0), ("-2", "19", 2), ("0", "17", 1)],
    ids=["defaults-empty", "negative-channel-two-rows", "explicit-one-row"],
)
def test_read_ok(rig, capsys, monkeypatch, mode, channel, ptype, count):
    if channel is not None:
        monkeypatch.setenv("EZVIZ_CHANNEL_NO", channel)
    if ptype is not None:
        monkeypatch.setenv("EZVIZ_TIMING_PLAN_TYPE", ptype)
    group = {
        "weekPlans": [
            {
                "weekDay": "6",
                "timePlan": [{"eventArg": "mode:PLAN_PRIVATE", "beginTime": "TIME_PRIVATE"}],
            }
        ],
        "subSerial": "PLAN_DEVICE_PRIVATE",
    }
    rows = [group for _ in range(count)]
    rig.biz_hook = lambda request: httpx.Response(
        200, json={"code": "200", "msg": "SERVER_PRIVATE", "data": rows}
    )
    argv = (
        ["--mode", mode, "--action", "read"]
        if mode == "sync"
        else ["--action", "read", "--mode", mode]
    )
    assert cli.main(argv) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.endswith("\n")
    lines = out.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {"plan_count": count}
    assert type(payload["plan_count"]) is int
    assert len(rig.calls) == 1
    kind, key, secret, region = rig.calls[0]
    assert kind == mode
    assert (key, secret) == ("SynApp", "SynSecret")
    assert region.scope == "domestic"
    assert len(rig.auth_requests) == 1
    assert len(rig.biz_requests) == 1
    req = rig.biz_requests[0]
    assert req.method == "GET"
    assert (req.url.scheme, req.url.host, req.url.path) == (
        "https",
        "open.ys7.com",
        "/api/v3/device/timing/plan/get",
    )
    pairs = list(req.url.params.multi_items())
    expected = {}
    if channel is not None:
        expected["channelNo"] = channel
    if ptype is not None:
        expected["timingPlanType"] = ptype
    assert sorted(pairs) == sorted(expected.items())
    assert req.headers.get_list("accessToken") == ["TOK1"]
    assert req.headers.get_list("deviceSerial") == ["SynUnit"]
    assert not req.content
    assert "content-type" not in req.headers


@pytest.mark.parametrize("ptype", ["", "18", "017", "-17", "x"])
def test_bad_plan_type(rig, capsys, monkeypatch, ptype):
    monkeypatch.setenv("EZVIZ_TIMING_PLAN_TYPE", ptype)
    assert cli.main(["--mode", "sync", "--action", "read"]) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_write_demo_uses_real_setter_offline(monkeypatch, capsys, mode, reverse_order):
    name = "set_huashu_4g_working_mode_plan"
    cls = Settings if mode == "sync" else AsyncSettings
    original = getattr(cls, name)
    scopes, requests = [], []

    def spy(self, *a, **kw):
        scopes.append(self._client.region.scope)
        return original(self, *a, **kw)

    class _Env(dict):
        def _trap(self, key):
            assert not str(key).startswith("EZVIZ_")

        def get(self, key, default=None):
            self._trap(key)
            return super().get(key, default)

        def __getitem__(self, key):
            self._trap(key)
            return super().__getitem__(key)

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
    monkeypatch.setattr(cls, name, spy)
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
    assert data == {"simulated": True, "acknowledged": True}
    assert data["simulated"] is True and data["acknowledged"] is True
    assert scopes == ["domestic"]
    assert [r.url.path for r in requests] == [
        "/api/lapp/token/get",
        "/api/v3/device/timing/plan/set",
    ]
    for req in requests:
        assert req.method == "POST" and req.url.scheme == "https" and req.url.host == "open.ys7.com"
        assert not req.url.query and req.headers["content-type"].startswith(
            "application/x-www-form-urlencoded"
        )
    keys = set(httpx.QueryParams(requests[1].content.decode()))
    assert (
        {"enable", "startTime", "endTime", "week"}
        <= keys
        <= {"enable", "startTime", "endTime", "week", "eventArg"}
    )
