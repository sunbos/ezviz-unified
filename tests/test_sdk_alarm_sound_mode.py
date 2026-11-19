import asyncio
import logging

import httpx
import pytest
from test_sdk_device_utc import API_HOST, AUTH_HOST, TOKEN, _client

from ezviz_unified import ProtocolError, RegionConfig

OP = "settings.get_alarm_sound_mode"
SERIAL = "sYnTh A1-b2"  # original mixed case, legal interior space


def read(mode, client, serial):
    if mode == "async":

        async def go():
            async with client:
                return await client.settings.get_alarm_sound_mode(serial)

        return asyncio.run(go())
    with client:
        return client.settings.get_alarm_sound_mode(serial)


def _payload(data):
    return {
        "status": 200,
        "meta": {"code": 10002, "message": "root-decoy"},
        "extra": {"root": True},
        "body": {"meta": {"code": 200, "message": "ok"}, "data": data},
    }


def _reply(payload):
    return lambda request: httpx.Response(200, json=payload)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("mode_value", [0, 1, 2, -7, 93])
def test_get_alarm_sound_mode_ok(mode, mode_value):
    payload = _payload({"alarmSoundMode": mode_value, "unused": ["x", 1]})
    client, rec, seen = _client(mode, biz=_reply(payload))
    res = read(mode, client, SERIAL)
    assert res.raw == payload
    assert res.data == payload["body"]["data"]
    value = res.data["alarmSoundMode"]
    assert type(value) is int and value == mode_value
    assert len(seen["auth"]) == 1 and len(seen["biz"]) == 1
    assert seen["auth"][0].url.host == AUTH_HOST
    req = seen["biz"][0]
    assert req.method == "GET" and req.url.scheme == "https" and req.url.host == API_HOST
    assert req.url.path == "/api/v3/device/alarmSound/enabled/get"
    assert not req.url.query and not req.read()
    assert "content-type" not in req.headers
    assert "channel" not in req.headers
    assert req.headers.get_list("accessToken") == [TOKEN]
    assert req.headers.get_list("deviceSerial") == [SERIAL]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "data",
    [
        {},
        {"alarmSoundMode": "1"},
        {"alarmSoundMode": 1.0},
        {"alarmSoundMode": True},
        None,
        [{"alarmSoundMode": 1}],
        {"alarmSoundMode": None},
    ],
)
def test_get_alarm_sound_mode_bad_data(mode, data):
    client, rec, seen = _client(mode, biz=_reply(_payload(data)))
    with pytest.raises(ProtocolError) as exc:
        read(mode, client, SERIAL)
    assert exc.value.code is None and exc.value.http_status == 200
    assert len(seen["biz"]) == 1
    events = rec.report()["events"]
    calls = [
        e for e in events if e["kind"] == "call" and e["entrypoint"] == OP and e["mode"] == mode
    ]
    attempts = [
        e
        for e in events
        if e["kind"] == "attempt"
        and e["entrypoint"] == "client.request"
        and e["operation"] == OP
        and e["mode"] == mode
    ]
    assert [e["outcome"] for e in calls] == ["started", "failed"]
    assert [e["outcome"] for e in attempts] == ["started", "succeeded"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("serial", ["", "   ", 9, "A序列B", "A\x01B", "A\x7fB", " AB", "AB "])
def test_get_alarm_sound_mode_bad_serial(mode, serial):
    client, rec, seen = _client(mode)
    with pytest.raises(ValueError):
        read(mode, client, serial)
    assert seen["auth"] == [] and seen["biz"] == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_get_alarm_sound_mode_domestic(mode):
    client, rec, seen = _client(mode, region=RegionConfig.china())
    with pytest.raises(ValueError):
        read(mode, client, SERIAL)
    assert seen["auth"] == [] and seen["biz"] == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_get_alarm_sound_mode_diagnostics_clean(mode, caplog):
    client, rec, seen = _client(mode, biz=_reply(_payload({"alarmSoundMode": 1})))
    with caplog.at_level(logging.INFO):
        read(mode, client, SERIAL)
    events = rec.report()["events"]
    calls = [
        e for e in events if e["kind"] == "call" and e["entrypoint"] == OP and e["mode"] == mode
    ]
    assert [e["outcome"] for e in calls] == ["started", "succeeded"]
    httpx_info = [
        r for r in caplog.records if r.name == "httpx" and r.levelname == "INFO" and r.getMessage()
    ]
    assert httpx_info
    blob = repr(rec.report()) + "\n" + caplog.text
    assert TOKEN not in blob
    assert SERIAL not in blob
