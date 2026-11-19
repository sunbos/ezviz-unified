"""Synthetic-transport contract tests for examples.alarm_sound_mode."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from examples import alarm_sound_mode as example
from ezviz_unified import AsyncClient, Client, RegionConfig

SERIAL = "Mixed-SN"
USAGE = "usage: python -m examples.alarm_sound_mode --mode sync|async\n"
ERROR = "error: alarm sound mode query failed\n"
PRIVATE_TOKENS = ("SYNTH_PRIVATE", SERIAL, "TOK1", "TESTONLY", "ezvizlife")


def _config():
    return SimpleNamespace(
        app_key="SYNTH",
        app_secret="TESTONLY",
        region="overseas",
        region_config=RegionConfig.overseas(auth_base_url="https://auth.ezvizlife.com"),
    )


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(
        mode=1,
        biz_json=None,
        config=_config(),
        config_calls=[],
        auth_calls=[],
        biz_calls=[],
        built=[],
    )

    def auth(request):
        state.auth_calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "TOK1",
                    "expireTime": 9999999999999,
                    "areaDomain": "https://biz.ezvizlife.com",
                },
            },
        )

    def biz(request):
        state.biz_calls.append(request)
        payload = state.biz_json or {
            "status": 200,
            "body": {
                "meta": {"code": 200, "message": "ok"},
                "data": {
                    "alarmSoundMode": state.mode,
                    "deviceSerial": "SYNTH_PRIVATE",
                    "extra": "SYNTH_PRIVATE",
                },
            },
        }
        return httpx.Response(200, json=payload)

    def factory(real):
        def make(app_key, app_secret, *, region):
            state.built.append((app_key, app_secret, region))
            return real(
                app_key,
                app_secret,
                region=region,
                auth_transport=httpx.MockTransport(auth),
                transport_factory=lambda origin: httpx.MockTransport(biz),
            )

        return make

    def load():
        state.config_calls.append(1)
        return state.config

    monkeypatch.setattr(example, "load_config", load)
    monkeypatch.setattr(example, "Client", factory(Client))
    monkeypatch.setattr(example, "AsyncClient", factory(AsyncClient))
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", SERIAL)
    return state


def _assert_biz_request(request):
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "biz.ezvizlife.com"
    assert request.url.path == "/api/v3/device/alarmSound/enabled/get"
    assert not request.url.query
    assert request.headers.get("accesstoken") == "TOK1"
    serial = [v for k, v in request.headers.items() if "serial" in k.lower()]
    assert serial == [SERIAL]
    assert "content-type" not in request.headers
    assert request.headers.get("content-length", "0") == "0"


def _assert_failure(capsys):
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    for token in PRIVATE_TOKENS:
        assert token not in out.err


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("value,expected", [(0, 0), (1, 1), (2, 2), (-1, None), (99, None)])
def test_alarm_sound_mode(harness, capsys, mode, value, expected):
    harness.mode = value
    assert example.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert json.loads(out.out) == {
        "region": "overseas",
        "mode": mode,
        "status": "ok",
        "alarm_sound_mode": expected,
    }
    for token in PRIVATE_TOKENS:
        assert token not in out.out
    assert harness.built == [("SYNTH", "TESTONLY", harness.config.region_config)]
    assert len(harness.auth_calls) == 1
    assert len(harness.biz_calls) == 1
    _assert_biz_request(harness.biz_calls[0])


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("value", ["1", True])
def test_malformed_mode_value(harness, capsys, mode, value):
    harness.mode = value
    assert example.main(["--mode", mode]) == 1
    _assert_failure(capsys)
    assert len(harness.biz_calls) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_permission_denied(harness, capsys, mode):
    harness.biz_json = {
        "status": 200,
        "body": {"meta": {"code": 10031, "message": "permission denied"}, "data": {}},
    }
    assert example.main(["--mode", mode]) == 1
    _assert_failure(capsys)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_config_value_error(harness, monkeypatch, capsys, mode):
    def bad():
        raise ValueError("bad config")

    monkeypatch.setattr(example, "load_config", bad)
    assert example.main(["--mode", mode]) == 1
    _assert_failure(capsys)
    assert harness.built == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
def test_interrupted(harness, monkeypatch, capsys, mode, exc):
    def cancel():
        raise exc()

    monkeypatch.setattr(example, "load_config", cancel)
    assert example.main(["--mode", mode]) == 130
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help(harness, capsys, flag):
    assert example.main([flag]) == 0
    out = capsys.readouterr()
    assert out.out == USAGE and out.err == ""
    assert harness.config_calls == [] and harness.auth_calls == []


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--mode"],
        ["--mode", "bogus"],
        ["--mode", "SYNC"],
        ["--mode", "sync", "extra"],
        ["-m", "sync"],
        ["sync"],
    ],
)
def test_invalid_args(harness, capsys, args):
    assert example.main(args) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == USAGE
    assert harness.config_calls == [] and harness.built == []


def test_domestic_guard(harness, capsys):
    harness.config.region = "domestic"
    assert example.main(["--mode", "sync"]) == 1
    _assert_failure(capsys)
    assert harness.built == [] and harness.biz_calls == []


def test_missing_serial(harness, monkeypatch, capsys):
    monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    assert example.main(["--mode", "async"]) == 1
    _assert_failure(capsys)
    assert harness.built == [] and harness.biz_calls == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_client_constructor_missing_cert(harness, monkeypatch, capsys, mode):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("SYNTH_PRIVATE_CERT_PATH")

    monkeypatch.setattr(example, "Client" if mode == "sync" else "AsyncClient", unavailable)
    assert example.main(["--mode", mode]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "error: alarm sound mode query failed\n"
    assert harness.auth_calls == harness.biz_calls == []
