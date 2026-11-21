import asyncio
import json
import types

import httpx
import pytest
from test_sdk_device_utc import API_HOST, AUTH_BASE, AUTH_HOST, TOKEN, _client

from examples import light_linkage as example
from ezviz_unified import RegionConfig

SERIAL = "MiXeD A-1"
ERROR = "error: light linkage query failed\n"
USAGE = "usage: python -m examples.light_linkage --mode sync|async [--type 301|305]\n"


@pytest.fixture
def h(monkeypatch):
    state = types.SimpleNamespace(
        config=types.SimpleNamespace(
            app_key="mock-key",
            app_secret="mock-secret",
            region="overseas",
            region_config=RegionConfig.overseas(auth_base_url=AUTH_BASE),
        ),
        loads=[],
        built=[],
        seen={"auth": [], "biz": []},
        payload={"body": {"meta": {"code": 200}, "data": {"type": 301, "enable": 1}}},
    )

    def load_config():
        state.loads.append(1)
        return state.config

    def factory(mode):
        def make(app_key, app_secret, *, region):
            assert app_key == state.config.app_key
            assert app_secret == state.config.app_secret
            assert region is state.config.region_config
            state.built.append(mode)
            client, _, state.seen = _client(
                mode, biz=lambda req: httpx.Response(200, json=state.payload), region=region
            )
            return client

        return make

    monkeypatch.setattr(example, "load_config", load_config)
    monkeypatch.setattr(example, "Client", factory("sync"))
    monkeypatch.setattr(example, "AsyncClient", factory("async"))
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", SERIAL)
    return state


def _run(mode):
    return example.main(["--mode", mode])


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "t,e,selector_matches,state",
    [
        (301, 1, True, "on"),
        (301, 0, True, "off"),
        (301, 19, True, "unknown"),
        (2, 1, False, "unknown"),
        (2, 0, False, "unknown"),
        (302, 0, False, "unknown"),
    ],
)
def test_ok(h, capsys, mode, t, e, selector_matches, state):
    h.payload = {
        "body": {
            "meta": {"code": 200, "message": "ok"},
            "data": {
                "type": t,
                "enable": e,
                "subSerial": "SUB_PRIVATE",
                "extra": {"marker": "EXT_PRIVATE"},
            },
        }
    }
    assert _run(mode) == 0
    out = capsys.readouterr()
    assert out.err == ""
    data = json.loads(out.out)
    assert data == {
        "region": "overseas",
        "mode": mode,
        "status": "ok",
        "selector_matches": selector_matches,
        "state": state,
    }
    assert data["selector_matches"] is selector_matches
    for leak in (
        TOKEN,
        SERIAL,
        AUTH_BASE,
        h.config.app_secret,
        "SUB_PRIVATE",
        "EXT_PRIVATE",
    ):
        assert leak not in out.out + out.err
    assert h.loads == [1] and h.built == [mode]
    assert len(h.seen["auth"]) == 1 and len(h.seen["biz"]) == 1
    assert h.seen["auth"][0].url.host == AUTH_HOST
    req = h.seen["biz"][0]
    assert req.method == "GET" and req.url.scheme == "https"
    assert req.url.host == API_HOST
    assert req.url.path == "/api/v3/device/switchStatus/get"
    assert req.url.params.multi_items() == [("type", "301")]
    assert req.content == b"" and "content-type" not in req.headers
    assert req.headers.get_list("accessToken") == [TOKEN]
    assert req.headers.get_list("deviceSerial") == [SERIAL]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"code": 200}, "data": "broken"},
        {"meta": {"code": 200}, "data": {"type": True, "enable": 1}},
        {"meta": {"code": 200}, "data": {"type": 301, "enable": False}},
        {"meta": {"code": 200}, "data": {"type": 301, "enable": 1, "subSerial": None}},
        {"meta": {"code": 10031, "message": "denied"}, "data": {"type": 301, "enable": 1}},
    ],
)
def test_denied(h, capsys, mode, payload):
    h.payload = {"body": payload}
    assert _run(mode) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert h.built == [mode] and len(h.seen["biz"]) == 1


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_cli_help(h, capsys, argv):
    code = example.main(list(argv))
    cap = capsys.readouterr()
    assert code == 0 and cap.out == USAGE and cap.err == ""
    assert h.loads == [] and h.built == []


@pytest.mark.parametrize(
    "argv",
    [[], ["--mode"], ["sync"], ["--mode", "SYNC"], ["--mode", "sync", "extra"], ["--mode", "both"]],
)
def test_cli_usage_error(h, capsys, argv):
    code = example.main(list(argv))
    cap = capsys.readouterr()
    assert code == 1 and cap.out == "" and cap.err == USAGE
    assert h.loads == [] and h.built == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "region,serial",
    [("domestic", SERIAL), ("overseas", None), ("overseas", ""), ("overseas", "   ")],
)
def test_cli_domestic_requires_serial(h, capsys, monkeypatch, mode, region, serial):
    h.config.region = region
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    code = example.main(["--mode", mode])
    cap = capsys.readouterr()
    assert code == 1 and cap.out == "" and cap.err == ERROR
    assert h.loads and h.built == [] and h.seen == {"auth": [], "biz": []}


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "exc,expected",
    [
        (ValueError("SYNTH_PRIVATE"), 1),
        (OSError("SYNTH_PRIVATE"), 1),
        (KeyboardInterrupt(), 130),
        (asyncio.CancelledError(), 130),
    ],
)
def test_cli_load_config_failure(h, capsys, monkeypatch, mode, exc, expected):
    def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(example, "load_config", _boom)
    code = example.main(["--mode", mode])
    cap = capsys.readouterr()
    assert code == expected and cap.out == "" and cap.err == ERROR
    assert "SYNTH_PRIVATE" not in cap.err
    assert h.built == []


@pytest.mark.parametrize(
    "mode,selector,returned,enable,match,state",
    [
        (mode, selector, returned, enable, match, state)
        for mode in ("sync", "async")
        for selector, returned, enable, match, state in [
            ("301", 301, True, True, "on"),
            ("301", 301, False, True, "off"),
            ("305", 305, True, True, "on"),
            ("305", 305, False, True, "off"),
            ("301", 305, True, False, "unknown"),
            ("305", 301, False, False, "unknown"),
            ("301", -9, True, False, "unknown"),
        ]
    ],
)
@pytest.mark.usefixtures("domestic_auth_reply")
def test_domestic_channel_query(
    h, capsys, monkeypatch, mode, selector, returned, enable, match, state
):
    h.config.region = "domestic"
    h.config.region_config = RegionConfig.china()
    monkeypatch.setenv("EZVIZ_CHANNEL_NO", "0007")
    h.payload = {
        "code": "200",
        "msg": "PRIVATE_UPSTREAM",
        "data": {
            "type": returned,
            "enable": enable,
            "subSerial": "SUB_PRIVATE",
            "future": "EXT_PRIVATE",
        },
    }

    if mode == "async":
        argv = ["--type", selector, "--mode", mode]
    else:
        argv = ["--mode", mode, "--type", selector]
    assert example.main(argv) == 0

    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out
    result = json.loads(captured.out)
    assert result == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "selector_matches": match,
        "state": state,
    }
    assert result["selector_matches"] is match
    combined = captured.out + captured.err
    for secret in (
        TOKEN,
        SERIAL,
        h.config.app_secret,
        "SUB_PRIVATE",
        "EXT_PRIVATE",
        "PRIVATE_UPSTREAM",
    ):
        assert secret not in combined

    assert h.loads == [1]
    assert h.built == [mode]

    assert len(h.seen["auth"]) == 1
    assert len(h.seen["biz"]) == 1
    auth = h.seen["auth"][0]
    biz = h.seen["biz"][0]
    assert auth.url.scheme == "https"
    assert auth.url.host == "open.ys7.com"
    assert biz.url.scheme == "https"
    assert biz.url.host == "open.ys7.com"
    assert biz.method == "GET"
    assert biz.url.path == "/api/v3/device/switchStatus/get"
    assert list(biz.url.params.multi_items()) == [("type", selector)]
    assert biz.content == b""
    assert "content-type" not in biz.headers
    assert biz.headers["accessToken"] == TOKEN
    assert biz.headers["deviceSerial"] == SERIAL
    assert biz.headers["channelNo"] == "0007"


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "sync", "--type"],
        ["--mode", "sync", "--type", "302"],
        ["--mode", "sync", "--type", "0305"],
        ["--mode", "sync", "--type", "305", "--type", "305"],
        ["--type", "305", "--mode", "sync", "--mode", "sync"],
        ["--mode", "sync", "--type", "305", "unexpected"],
        ["--mode", "sync", "--type", "305", "--serial", SERIAL],
    ],
    ids=[
        "type-missing-value",
        "type-unsupported-302",
        "type-leading-zero",
        "type-repeated",
        "mode-repeated",
        "extra-argument",
        "extra-credential-option",
    ],
)
@pytest.mark.usefixtures("domestic_auth_reply")
def test_domestic_selector_usage(h, capsys, argv):
    h.config.region = "domestic"
    h.config.region_config = RegionConfig.china()

    assert example.main(argv) == 1

    assert capsys.readouterr() == ("", USAGE)
    assert h.loads == h.built == []
    assert h.seen == {"auth": [], "biz": []}


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "region,channel,serial",
    [
        ("overseas", "0007", SERIAL),
        ("domestic", None, SERIAL),
        ("domestic", "0007", None),
    ],
    ids=["overseas-valid", "domestic-missing-channel", "domestic-missing-serial"],
)
@pytest.mark.usefixtures("domestic_auth_reply")
def test_domestic_config_error(h, capsys, monkeypatch, mode, region, channel, serial):
    h.config.region = region
    h.config.region_config = {
        "domestic": RegionConfig.china(),
        "overseas": RegionConfig.overseas(),
    }[region]

    if channel is None:
        monkeypatch.delenv("EZVIZ_CHANNEL_NO", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_CHANNEL_NO", channel)
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)

    assert example.main(["--type", "305", "--mode", mode]) == 1

    assert capsys.readouterr() == ("", ERROR)
    assert h.seen == {"auth": [], "biz": []}


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "payload",
    [
        {"code": "10031", "msg": "PRIVATE_DENIED"},
        {"code": "200", "data": {"type": 301, "enable": 1}},
        {"body": {"meta": {"code": 200}, "data": {"type": 301, "enable": True}}},
    ],
)
@pytest.mark.usefixtures("domestic_auth_reply")
def test_domestic_cli_failed_response(h, capsys, monkeypatch, mode, payload):
    h.config.region = "domestic"
    h.config.region_config = RegionConfig.china()
    monkeypatch.setenv("EZVIZ_CHANNEL_NO", "0007")
    h.payload = payload
    assert example.main(["--mode", mode, "--type", "301"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert h.built == [mode] and len(h.seen["biz"]) == 1


@pytest.mark.parametrize(
    ("mode", "exc"),
    [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())],
)
@pytest.mark.usefixtures("domestic_auth_reply")
def test_domestic_interrupt(h, capsys, monkeypatch, mode, exc):
    h.config.region = "domestic"
    h.config.region_config = RegionConfig.china()
    monkeypatch.setenv("EZVIZ_CHANNEL_NO", "0007")

    def business(req):
        raise exc

    def make(key, secret, *, region):
        client, _, h.seen = _client(mode, biz=business, region=region)
        return client

    monkeypatch.setattr(example, "Client" if mode == "sync" else "AsyncClient", make)

    assert example.main(["--mode", mode, "--type", "305"]) == 130
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", ERROR)
    assert len(h.seen["auth"]) == len(h.seen["biz"]) == 1


@pytest.fixture
def domestic_auth_reply(monkeypatch):
    def auth_ok(request):
        assert request.url.host == "open.ys7.com"
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": TOKEN,
                    "expireTime": 9999999999999,
                    "areaDomain": "https://open.ys7.com",
                },
            },
        )

    monkeypatch.setattr("test_sdk_device_utc._auth_ok", auth_ok)
