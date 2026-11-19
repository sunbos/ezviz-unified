import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_device_utc import API_HOST, AUTH_HOST, TOKEN, _client

from ezviz_unified import (
    ProtocolError,
)

ROUTES = {
    "list": "/api/lapp/detector/list",
    "bindable_cameras": "/api/lapp/detector/ipc/list/bindable",
    "linked_cameras": "/api/lapp/detector/ipc/list/bind",
}
HOST_SERIAL = "MiXeD 主+/9"
DETECTOR_SERIAL = "Sub 探+/7"


def read(mode, client, name, *serials):
    if mode == "async":

        async def go():
            async with client:
                return await getattr(client.detectors, name)(*serials)

        return asyncio.run(go())
    with client:
        return getattr(client.detectors, name)(*serials)


def payload(name, code="200", data=None, message="ok"):
    envelope = {"code": code, "msg": message, "data": data}
    return {"result": envelope} if name == "list" else envelope


def invoke(mode, name, *replies, serial=HOST_SERIAL, detector=DETECTOR_SERIAL, region=None):
    steps = iter(replies)

    def business(request):
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step if isinstance(step, httpx.Response) else httpx.Response(200, json=step)

    client, recorder, seen = _client(mode, biz=business, region=region)
    args = (serial, detector) if name == "linked_cameras" else (serial,)
    return lambda: read(mode, client, name, *args), recorder, seen


def outcomes(recorder, mode, kind, name):
    events = recorder.report()["events"]
    key = "entrypoint" if kind == "call" else "operation"
    return [
        event["outcome"]
        for event in events
        if event["kind"] == kind and event["mode"] == mode and event[key] == "detectors." + name
    ]


pytestmark = [
    pytest.mark.parametrize("mode", ["sync", "async"]),
    pytest.mark.parametrize("name", list(ROUTES)),
]

ROWS = {
    "list": [
        {
            "detectorSerial": DETECTOR_SERIAL,
            "detectorType": "PIR 主",
            "status": 1,
            "extra": {"keep": "保留"},
        },
        {"deviceSerial": "Sub 探+/7", "channelNo": 0},
    ],
    "bindable_cameras": [
        {"cameraName": 7, "deviceSerial": "Ipc 主+", "channelNo": 1},
        {"cameraName": "Cam 主+/9", "ipcSerial": "I-2", "online": True},
    ],
    "linked_cameras": [
        {"detectorSerial": DETECTOR_SERIAL, "ipcSerial": "IPC 主", "channelNo": 2, "other": "k"},
        {"deviceSerial": "Ds 探+/", "other": None},
    ],
}


def test_wire_form(mode, name):
    call, rec, seen = invoke(mode, name, payload(name, data=[]))
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    auth_req, req = seen["auth"][0], seen["biz"][0]
    assert auth_req.url.host == AUTH_HOST
    assert req.url.host == API_HOST and API_HOST != AUTH_HOST
    assert req.method == "POST"
    assert req.url.scheme == "https" and req.url.path == ROUTES[name]
    assert not req.url.query
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert not [h for h in req.headers if "token" in h.lower()]
    sent = sorted(parse_qsl(req.content.decode(), keep_blank_values=True))
    want = [("accessToken", TOKEN), ("deviceSerial", HOST_SERIAL)]
    if name == "linked_cameras":
        want.append(("detectorSerial", DETECTOR_SERIAL))
    assert sent == sorted(want)


def test_data_roundtrip(mode, name):
    rows = ROWS[name]
    reply = payload(name, data=rows)
    resp = invoke(mode, name, reply)[0]()
    assert resp.code == "200"
    assert resp.raw == reply
    assert resp.data == rows


def test_empty_list(mode, name):
    call, rec, seen = invoke(mode, name, payload(name, data=[]))
    resp = call()
    assert resp.code == "200" and resp.data == []
    assert len(seen["biz"]) == 1
    assert outcomes(rec, mode, "call", name) == ["started", "succeeded"]


def test_layout_decoy(mode, name):
    rows = [{"kept": "主+/"}]
    if name == "list":
        reply = {
            "code": "E94",
            "data": "decoy",
            "result": {"code": "200", "msg": "ok", "data": rows},
        }
    else:
        reply = {
            "code": "200",
            "msg": "ok",
            "data": rows,
            "result": {"code": "10002", "data": "expired"},
        }
    call, rec, seen = invoke(mode, name, reply)
    resp = call()
    assert resp.code == "200" and resp.raw == reply and resp.data == rows
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize(
    "env",
    [
        {"code": "200", "msg": "ok"},
        {"code": "200", "msg": "ok", "data": None},
        {"code": "200", "msg": "ok", "data": "oops"},
        {"code": "200", "msg": "ok", "data": {"a": 1}},
        {"code": "200", "msg": "ok", "data": [{"ok": 1}, 3]},
    ],
)
def test_bad_data(mode, name, env):
    reply = {"result": env} if name == "list" else env
    call, rec, seen = invoke(mode, name, reply)
    with pytest.raises(ProtocolError) as exc:
        call()
    err = exc.value
    assert err.code is None
    assert err.operation == "detectors." + name
    assert err.http_status == 200
    assert len(seen["biz"]) == 1
    assert outcomes(rec, mode, "call", name) == ["started", "failed"]
    assert outcomes(rec, mode, "attempt", name) == ["started", "succeeded"]


def test_form_serial_masking_opt_in(name, mode):
    from ezviz_unified import AsyncTransport, ResponsePolicy, SyncTransport

    class Form(dict):
        def __bool__(self):
            raise AssertionError("request mapping must not be truth tested")

    fields = Form(deviceSerial="A B")
    if name == "linked_cameras":
        fields["detectorSerial"] = "C D"
    expected_form = sorted(fields.items())
    payload = {"code": "200", "msg": "A+B", "data": {"value": "A+B"}}

    def handler(req):
        assert not req.url.query
        assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == expected_form
        return httpx.Response(200, json=payload)

    for flag in (False, True):
        mock = httpx.MockTransport(handler)
        policy = ResponsePolicy(redact_form_serials=flag)
        if mode.startswith("async"):

            async def call(mock=mock, policy=policy):
                async with AsyncTransport("https://test.invalid", transport=mock) as client:
                    return await client.request("POST", "/synthetic", data=fields, policy=policy)

            resp = asyncio.run(call())
        else:
            with SyncTransport("https://test.invalid", transport=mock) as client:
                resp = client.request("POST", "/synthetic", data=fields, policy=policy)
        assert resp.message == ("***" if flag else "A+B")
        assert resp.raw["msg"] == "A+B"
        assert resp.data["value"] == "A+B"
