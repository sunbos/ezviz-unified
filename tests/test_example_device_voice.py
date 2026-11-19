"""CLI-level tests for ``examples.device_voice`` (domestic read-only listing).

Every test drives the real ``cli.main(argv)`` entry point against the real SDK
objects.  Only the ``Client``/``AsyncClient`` constructor names on the example
module are wrapped so the real clients still run, just with
``httpx.MockTransport`` for ``auth_transport``/``transport_factory`` plus one
real ``DiagnosticRecorder`` -- nothing else is stubbed, coerced, or
suppressed.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

import examples.device_voice as cli
from ezviz_unified import AsyncClient, Client, DiagnosticRecorder

_REAL_CLIENT = Client
_REAL_ASYNC_CLIENT = AsyncClient

USAGE = "usage: python -m examples.device_voice --mode sync|async\n"
ERROR = "error: device voice list failed\n"
APP_KEY = "SYNTHKEY"
APP_SECRET = "SYNTHSECRET"
TOKEN = "SYNTH_TOKEN"
SERIAL = "CSEPMR511ABCDE0"
PADDED_SERIAL = " 　CSEPMR511-語音素材-测试　 "
DOMESTIC_ORIGIN = "https://open.ys7.com"
AUTH_PATH = "/api/lapp/token/get"
LIST_PATH = "/api/route/voice/v3/devices/voices"
LIST_OPERATION = "device_voice.list"
RENAME_OPERATION = "device_voice.rename"

_AUTH_PAYLOAD = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": DOMESTIC_ORIGIN,
    },
}

_SECRET_VOICE = {
    "voiceId": 987654321,
    "voiceName": "SECRET_VOICE_NAME_私人素材",
    "voiceUrl": "https://media.invalid/private/SECRET_VOICE_LOCATOR.mp3",
    "status": 1,
    "updateTime": "2026-01-01 00:00:00",
}

_LEAK_MARKERS = (
    "SECRET_VOICE_NAME",
    "SECRET_VOICE_LOCATOR",
    "media.invalid",
    "987654321",
    TOKEN,
    APP_KEY,
    APP_SECRET,
    "accessToken",
    SERIAL,
)


def _list_payload(voices):
    return {"meta": {"code": 200, "message": "ok"}, "voiceInfos": voices}


def _env(monkeypatch, *, region="domestic", serial=SERIAL):
    monkeypatch.setenv("EZVIZ_REGION", region)
    monkeypatch.setenv("EZVIZ_APP_KEY", APP_KEY)
    monkeypatch.setenv("EZVIZ_APP_SECRET", APP_SECRET)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    if serial is None:
        monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    else:
        monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)


def _wire(monkeypatch, biz_responder=None, auth_responder=None):
    """Patch only the CLI's constructor names so real clients get mocks."""
    state = SimpleNamespace(auth_requests=[], biz_requests=[], recorder=DiagnosticRecorder())

    def auth_handler(request):
        state.auth_requests.append(request)
        responder = auth_responder or (lambda _req: httpx.Response(200, json=_AUTH_PAYLOAD))
        return responder(request)

    def biz_handler(request):
        state.biz_requests.append(request)
        responder = biz_responder or (lambda _req: httpx.Response(200, json=_list_payload([])))
        return responder(request)

    def wrap(cls):
        def wrapper(*args, **kwargs):
            kwargs["auth_transport"] = httpx.MockTransport(auth_handler)
            kwargs["transport_factory"] = lambda origin: httpx.MockTransport(biz_handler)
            kwargs["diagnostic_recorder"] = state.recorder
            return cls(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(cli, "Client", wrap(_REAL_CLIENT))
    monkeypatch.setattr(cli, "AsyncClient", wrap(_REAL_ASYNC_CLIENT))
    return state


def _origin(url):
    return f"{url.scheme}://{url.host}"


def _boom_load_config(*args, **kwargs):
    raise AssertionError("load_config must not run for usage/help handling")


def _assert_error_output(capsys):
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


def _assert_auth_request(request):
    assert request.method == "POST"
    assert _origin(request.url) == DOMESTIC_ORIGIN
    assert request.url.path == AUTH_PATH
    assert request.url.params.multi_items() == []
    assert parse_qs(request.content.decode()) == {
        "appKey": [APP_KEY],
        "appSecret": [APP_SECRET],
    }


def _assert_list_request(request, *, serial):
    assert request.method == "GET"
    assert _origin(request.url) == DOMESTIC_ORIGIN
    assert request.url.path == LIST_PATH
    assert request.content == b""
    header_names = {name.lower() for name in request.headers.keys()}
    assert "content-type" not in header_names
    assert TOKEN not in str(request.headers)
    pairs = request.url.params.multi_items()
    assert len(pairs) == 2
    assert dict(pairs) == {"accessToken": TOKEN, "deviceSerial": serial}


def _assert_success_diagnostics(recorder, mode):
    events = recorder.report()["events"]
    assert events
    for event in events:
        assert event.get("mode") == mode
        entrypoint = event.get("entrypoint")
        assert isinstance(entrypoint, str) and entrypoint.strip()
    calls = [event for event in events if event.get("kind") == "call"]
    assert calls
    call_outcomes = [event.get("outcome") for event in calls]
    assert "started" in call_outcomes
    assert any(outcome not in (None, "started") for outcome in call_outcomes)
    attempts = [event for event in events if event.get("kind") == "attempt"]
    list_attempts = [event for event in attempts if event.get("operation") == LIST_OPERATION]
    assert list_attempts
    attempt_outcomes = [event.get("outcome") for event in list_attempts]
    assert "started" in attempt_outcomes
    assert any(outcome not in (None, "started") for outcome in attempt_outcomes)
    assert all(event.get("operation") != RENAME_OPERATION for event in attempts)


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_before_load_config(monkeypatch, capsys, flag):
    monkeypatch.setattr(cli, "load_config", _boom_load_config, raising=False)
    state = _wire(monkeypatch)

    rc = cli.main([flag])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.out == USAGE
    assert captured.err == ""
    assert state.auth_requests == []
    assert state.biz_requests == []


@pytest.mark.parametrize(
    "bad_argv",
    [
        [],
        ["--rename"],
        ["--mode", "bogus"],
        ["--mode"],
        ["--mode", "sync", "--rename"],
        ["--unexpected-flag"],
    ],
    ids=[
        "empty-args",
        "rename",
        "bad-mode",
        "missing-mode-value",
        "rename-after-mode",
        "unknown",
    ],
)
def test_invalid_args_print_usage_before_load_config(monkeypatch, capsys, bad_argv):
    monkeypatch.setattr(cli, "load_config", _boom_load_config, raising=False)
    state = _wire(monkeypatch)

    rc = cli.main(bad_argv)
    captured = capsys.readouterr()

    assert rc == 1
    assert captured.out == ""
    assert captured.err == USAGE
    assert state.auth_requests == []
    assert state.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "voices,expected_has_voices",
    [([_SECRET_VOICE], True), ([], False)],
    ids=["single-voice", "empty"],
)
def test_device_voice_list_success(monkeypatch, capsys, mode, voices, expected_has_voices):
    _env(monkeypatch)
    state = _wire(
        monkeypatch,
        biz_responder=lambda _req: httpx.Response(200, json=_list_payload(voices)),
    )

    rc = cli.main(["--mode", mode])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "has_voices": expected_has_voices,
    }
    for marker in _LEAK_MARKERS:
        assert marker not in captured.out

    assert len(state.auth_requests) == 1
    _assert_auth_request(state.auth_requests[0])
    assert len(state.biz_requests) == 1
    _assert_list_request(state.biz_requests[0], serial=SERIAL)
    _assert_success_diagnostics(state.recorder, mode)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_device_voice_list_preserves_original_serial(monkeypatch, capsys, mode):
    _env(monkeypatch, serial=PADDED_SERIAL)
    state = _wire(
        monkeypatch,
        biz_responder=lambda _req: httpx.Response(200, json=_list_payload([_SECRET_VOICE])),
    )

    rc = cli.main(["--mode", mode])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "has_voices": True,
    }
    for marker in _LEAK_MARKERS:
        assert marker not in captured.out
    assert PADDED_SERIAL not in captured.out
    assert len(state.auth_requests) == 1
    assert len(state.biz_requests) == 1
    _assert_list_request(state.biz_requests[0], serial=PADDED_SERIAL)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "serial_case",
    ["missing", "", "   ", "　　 \t"],
    ids=["missing", "empty", "spaces", "unicode-blank"],
)
def test_missing_or_blank_serial_fails_without_http(monkeypatch, capsys, mode, serial_case):
    _env(
        monkeypatch,
        serial=None if serial_case == "missing" else serial_case,
    )
    state = _wire(monkeypatch)

    rc = cli.main(["--mode", mode])

    assert rc == 1
    _assert_error_output(capsys)
    assert state.auth_requests == []
    assert state.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_overseas_region_fails_without_http(monkeypatch, capsys, mode):
    _env(monkeypatch, region="overseas")
    state = _wire(monkeypatch)

    rc = cli.main(["--mode", mode])

    assert rc == 1
    _assert_error_output(capsys)
    assert state.auth_requests == []
    assert state.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_business_api_error_returns_1(monkeypatch, capsys, mode):
    _env(monkeypatch)
    state = _wire(
        monkeypatch,
        biz_responder=lambda _req: httpx.Response(
            200, json={"meta": {"code": 500, "message": "boom"}}
        ),
    )

    rc = cli.main(["--mode", mode])

    assert rc == 1
    _assert_error_output(capsys)
    assert len(state.auth_requests) == 1
    _assert_auth_request(state.auth_requests[0])
    assert len(state.biz_requests) == 1

    events = state.recorder.report()["events"]
    calls = [event for event in events if event.get("kind") == "call"]
    outcomes = [event.get("outcome") for event in calls]
    assert "started" in outcomes
    assert any(outcome not in (None, "started") for outcome in outcomes)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("shape", ["missing-meta", "non-json"], ids=["missing-meta", "non-json"])
def test_business_protocol_error_returns_1(monkeypatch, capsys, mode, shape):
    def responder(_req):
        if shape == "non-json":
            return httpx.Response(200, content=b"<html>bad gateway</html>")
        return httpx.Response(200, json={"result": []})

    _env(monkeypatch)
    state = _wire(monkeypatch, biz_responder=responder)

    rc = cli.main(["--mode", mode])

    assert rc == 1
    _assert_error_output(capsys)
    assert len(state.auth_requests) == 1
    assert len(state.biz_requests) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_auth_error_returns_1_without_business_call(monkeypatch, capsys, mode):
    _env(monkeypatch)
    state = _wire(
        monkeypatch,
        auth_responder=lambda _req: httpx.Response(
            200,
            json={"code": "10017", "data": None, "msg": "invalid key"},
        ),
    )

    rc = cli.main(["--mode", mode])

    assert rc == 1
    _assert_error_output(capsys)
    assert len(state.auth_requests) >= 1
    assert state.biz_requests == []


@pytest.mark.parametrize(
    "mode,raised",
    [("sync", KeyboardInterrupt), ("async", asyncio.CancelledError)],
    ids=["sync-keyboard-interrupt", "async-cancelled"],
)
def test_interrupt_in_business_transport_returns_130(monkeypatch, capsys, mode, raised):
    def boom(_req):
        raise raised()

    _env(monkeypatch)
    state = _wire(monkeypatch, biz_responder=boom)

    rc = cli.main(["--mode", mode])

    assert rc == 130
    _assert_error_output(capsys)
    assert len(state.auth_requests) == 1
    assert len(state.biz_requests) == 1
