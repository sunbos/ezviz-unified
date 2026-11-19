"""Offline wire contracts for the domestic talk-speaker volume API."""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

PATH = "/api/v3/device/talkSpeakerVolume"
GET_ENTRY = "settings.get_talk_speaker_volume"
SET_ENTRY = "settings.set_talk_speaker_volume"
SERIAL = "  MiXéd-设备-SN42  "
ECHO_SERIAL = "EchoSerialUniqueZ9"
MARKER = "PayloadMarkerSecret7"
CLIENTS = [pytest.param(Client, id="sync"), pytest.param(AsyncClient, id="async")]


def _auth(calls):
    def handler(request):
        calls.append(request)
        token = {
            "accessToken": "SYNTH_TOKEN",
            "expireTime": 9999999999999,
            "areaDomain": "https://open.ys7.com",
        }
        return httpx.Response(200, json={"code": "200", "data": token})

    return handler


def _invoke(cls, rec, auth, biz, method, *args, region=None):
    kwargs = dict(
        region=region or RegionConfig.china(),
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(biz),
        diagnostic_recorder=rec,
    )
    if cls is Client:
        with cls("SYNTHKEY", "SYNTHSECRET", **kwargs) as client:
            return getattr(client.settings, method)(*args)

    async def _run():
        async with cls("SYNTHKEY", "SYNTHSECRET", **kwargs) as client:
            return await getattr(client.settings, method)(*args)

    return asyncio.run(_run())


def _events(rec, kind, **match):
    return [
        e
        for e in rec.report()["events"]
        if e.get("kind") == kind and all(e.get(k) == v for k, v in match.items())
    ]


def _expect(rec, cls, entry, calls, attempts):
    events = _events(rec, "call", entrypoint=entry)
    assert [e["outcome"] for e in events] == calls
    assert all(e["mode"] == ("sync" if cls is Client else "async") for e in events)
    assert [e["outcome"] for e in _events(rec, "attempt", operation=entry)] == attempts


def _entry(method):
    return GET_ENTRY if method == "get_talk_speaker_volume" else SET_ENTRY


@pytest.mark.parametrize("cls", CLIENTS)
@pytest.mark.parametrize("volume", [0, 100, 101])
def test_get_talk_speaker_volume(cls, volume):
    auth_calls, biz_calls = [], []

    def biz(request):
        biz_calls.append(request)
        assert request.method == "GET"
        assert request.url.path == PATH
        assert request.url.host == "open.ys7.com"
        assert request.headers["accessToken"] == "SYNTH_TOKEN"
        assert request.headers.get_list("accessToken") == ["SYNTH_TOKEN"]
        assert request.url.params.multi_items() == [("deviceSerial", SERIAL)]
        assert request.content in (b"", None)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "msg": "ok",
                "data": {"talkSpeakerVolume": volume, "vendor": MARKER},
            },
        )

    rec = DiagnosticRecorder()
    response = _invoke(cls, rec, _auth(auth_calls), biz, "get_talk_speaker_volume", SERIAL)
    assert response.code == "200"
    assert response.message == "ok"
    assert response.data["talkSpeakerVolume"] == volume
    assert response.data["vendor"] == MARKER
    assert response.raw["data"] == {"talkSpeakerVolume": volume, "vendor": MARKER}
    assert MARKER not in repr(response)
    assert MARKER not in repr(rec.report())
    assert (len(auth_calls), len(biz_calls)) == (1, 1)
    _expect(rec, cls, GET_ENTRY, ["started", "succeeded"], ["started", "succeeded"])


@pytest.mark.parametrize("cls", CLIENTS)
@pytest.mark.parametrize("volume", [0, 100])
def test_set_talk_speaker_volume(cls, volume):
    auth_calls, biz_calls = [], []

    def biz(request):
        biz_calls.append(request)
        assert request.method == "POST"
        assert request.url.path == PATH
        assert request.url.host == "open.ys7.com"
        assert request.headers["accessToken"] == "SYNTH_TOKEN"
        assert request.headers.get_list("accessToken") == ["SYNTH_TOKEN"]
        assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
        assert request.url.params.multi_items() == []
        assert parse_qs(request.content.decode()) == {
            "deviceSerial": [SERIAL],
            "volume": [str(volume)],
        }
        return httpx.Response(200, json={"code": "200", "msg": "ok", "trace": "kept"})

    rec = DiagnosticRecorder()
    response = _invoke(cls, rec, _auth(auth_calls), biz, "set_talk_speaker_volume", SERIAL, volume)
    assert response.code == "200"
    assert response.message == "ok"
    assert response.data is None
    assert "data" not in response.raw
    assert response.raw["trace"] == "kept"
    assert (len(auth_calls), len(biz_calls)) == (1, 1)
    _expect(rec, cls, SET_ENTRY, ["started", "succeeded"], ["started", "succeeded"])


@pytest.mark.parametrize("cls", CLIENTS)
@pytest.mark.parametrize(
    ("method", "args", "payload", "error", "terminal"),
    [
        (
            "get_talk_speaker_volume",
            (SERIAL,),
            {"code": "200", "msg": "ok"},
            ProtocolError,
            "succeeded",
        ),
        (
            "get_talk_speaker_volume",
            (SERIAL,),
            {"code": "200", "msg": "ok", "data": {"other": 1}},
            ProtocolError,
            "succeeded",
        ),
        (
            "get_talk_speaker_volume",
            (SERIAL,),
            {"code": "200", "msg": "ok", "data": [1]},
            ProtocolError,
            "succeeded",
        ),
        (
            "get_talk_speaker_volume",
            (SERIAL,),
            {"code": "200", "msg": "ok", "data": {"talkSpeakerVolume": True}},
            ProtocolError,
            "succeeded",
        ),
        (
            "get_talk_speaker_volume",
            (SERIAL,),
            {"code": "200", "msg": "ok", "data": {"talkSpeakerVolume": "50"}},
            ProtocolError,
            "succeeded",
        ),
        ("set_talk_speaker_volume", (SERIAL, 10), {"code": 200, "msg": "ok"}, ApiError, "failed"),
        ("set_talk_speaker_volume", (SERIAL, 10), {"code": "0", "msg": "no"}, ApiError, "failed"),
        (
            "set_talk_speaker_volume",
            (SERIAL, 10),
            {"code": True, "msg": "ok"},
            ProtocolError,
            "failed",
        ),
        ("set_talk_speaker_volume", (SERIAL, 10), {"msg": "ok"}, ProtocolError, "failed"),
    ],
    ids=[
        "get-no-data",
        "get-no-key",
        "get-list",
        "get-bool",
        "get-str",
        "set-int200",
        "set-zero",
        "set-bool",
        "set-no-root",
    ],
)
def test_bad_wire_payloads(cls, method, args, payload, error, terminal):
    biz_calls = []

    def biz(request):
        biz_calls.append(request)
        return httpx.Response(200, json=payload)

    rec = DiagnosticRecorder()
    with pytest.raises(error) as excinfo:
        _invoke(cls, rec, _auth([]), biz, method, *args)
    if method == "get_talk_speaker_volume":
        assert excinfo.value.operation == "settings.get_talk_speaker_volume"
        assert excinfo.value.http_status == 200
    assert len(biz_calls) == 1
    _expect(rec, cls, _entry(method), ["started", "failed"], ["started", terminal])


@pytest.mark.parametrize("cls", CLIENTS)
def test_set_talk_speaker_volume_no_replay(cls):
    auth_calls, biz_calls = [], []

    def biz(request):
        biz_calls.append(request)
        return httpx.Response(200, json={"code": "10002", "msg": "accessToken expired"})

    rec = DiagnosticRecorder()
    with pytest.raises(AuthenticationError) as excinfo:
        _invoke(cls, rec, _auth(auth_calls), biz, "set_talk_speaker_volume", SERIAL, 10)
    assert excinfo.value.code == "10002"
    assert excinfo.value.operation == "settings.set_talk_speaker_volume"
    assert (len(auth_calls), len(biz_calls)) == (1, 1)
    assert not any(e.get("kind") == "retry" for e in rec.report()["events"])
    _expect(rec, cls, SET_ENTRY, ["started", "failed"], ["started", "failed"])


@pytest.mark.parametrize("cls", CLIENTS)
@pytest.mark.parametrize(
    ("method", "args", "region"),
    [
        ("get_talk_speaker_volume", ("",), None),
        ("get_talk_speaker_volume", ("   ",), None),
        ("get_talk_speaker_volume", ("\ud800",), None),
        ("get_talk_speaker_volume", (SERIAL,), RegionConfig.overseas()),
        ("set_talk_speaker_volume", ("", 10), None),
        ("set_talk_speaker_volume", ("\ud800", 10), None),
        ("set_talk_speaker_volume", (SERIAL, True), None),
        ("set_talk_speaker_volume", (SERIAL, 1.5), None),
        ("set_talk_speaker_volume", (SERIAL, 1e2), None),
        ("set_talk_speaker_volume", (SERIAL, -1), None),
        ("set_talk_speaker_volume", (SERIAL, 101), None),
        ("set_talk_speaker_volume", (SERIAL, "50"), None),
        ("set_talk_speaker_volume", (SERIAL, 10), RegionConfig.overseas()),
    ],
    ids=[
        "get-empty",
        "get-blank",
        "get-surrogate",
        "get-region",
        "set-empty",
        "set-surrogate",
        "set-bool",
        "set-float",
        "set-1e2",
        "set-neg",
        "set-high",
        "set-str",
        "set-region",
    ],
)
def test_validation_rejected_before_wire(cls, method, args, region):
    auth_calls, biz_calls = [], []

    def biz(request):
        biz_calls.append(request)
        return httpx.Response(200, json={})

    rec = DiagnosticRecorder()
    with pytest.raises(ValueError):
        _invoke(cls, rec, _auth(auth_calls), biz, method, *args, region=region)
    assert (auth_calls, biz_calls) == ([], [])
    _expect(rec, cls, _entry(method), ["started", "failed"], [])


@pytest.mark.parametrize("cls", CLIENTS)
def test_error_redacts_echoed_serial(cls):
    def biz(_request):
        return httpx.Response(200, json={"code": "10999", "msg": "check failed for " + ECHO_SERIAL})

    rec = DiagnosticRecorder()
    with pytest.raises(ApiError) as excinfo:
        _invoke(cls, rec, _auth([]), biz, "set_talk_speaker_volume", ECHO_SERIAL, 10)
    err = excinfo.value
    assert "10999" in str(err)
    assert "check failed for" in str(err)
    assert ECHO_SERIAL not in str(err)
    assert ECHO_SERIAL not in repr(err)
    assert ECHO_SERIAL not in repr(rec.report())
