"""Offline contracts for both domestic stored recording query shapes."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_motion_area import REGIONS
from test_sdk_motion_area import Probe as _RegionProbe
from test_sdk_stream_archive import _invoke

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

MS, TEXT = "by_time", "by_text_time"
NAMES = (MS, TEXT)
PATH = "/api/lapp/video/by/time"
SERIAL = "SERIAL_PRIVATE"
START = "2040-02-29 23:58:03"
STOP = "2040-03-01 00:00:05"
MIN_I64 = -(1 << 63)
MAX_I64 = (1 << 63) - 1
ROWS = [
    {
        "id": None,
        "deviceSerial": "SERIAL_PRIVATE",
        "cameraNo": "003",
        "createTime": "CREATED_PRIVATE",
        "ownerId": "OWNER_PRIVATE",
        "fileId": "FILE_PRIVATE",
        "fileIndex": "INDEX_PRIVATE",
        "fileName": "NAME_PRIVATE",
        "keyChecksum": "CHECKSUM_PRIVATE",
        "coverPic": "https://cover.invalid/IMG_PRIVATE",
        "downloadPath": "https://media.invalid/VID_PRIVATE?t=URL_PRIVATE",
        "extra": ["EXT_PRIVATE"],
    },
    {
        "channelNo": 3,
        "fileType": 991,
        "createTime": 903046901,
        "fileId": None,
        "downloadPath": None,
    },
]
PAGED = {"files": ROWS, "isAll": False, "nextFileTime": 850168912345, "extra": "PAGE_PRIVATE"}
ARGS = {MS: (SERIAL,), TEXT: ()}
KW = {
    MS: {
        "channel": 0,
        "rec_type": 1,
        "start_time_ms": -9102285123,
        "end_time_ms": 9102285123,
        "version": "2.0",
        "page_size": 1,
    },
    TEXT: {
        "device_serial": SERIAL,
        "channel": "003",
        "rec_type": 2,
        "start_time": START,
        "stop_time": STOP,
    },
}
FIELDS = {
    MS: {
        "deviceSerial": SERIAL,
        "channelNo": "0",
        "recType": "1",
        "startTime": "-9102285123",
        "endTime": "9102285123",
        "version": "2.0",
        "pageSize": "1",
    },
    TEXT: {
        "deviceSerial": SERIAL,
        "channelNo": "003",
        "recType": "2",
        "startTime": START,
        "stopTime": STOP,
    },
}
OK = {"code": "200", "msg": "ok", "data": ROWS, "extra": "ROOT_PRIVATE"}
STALE = {"code": "10002", "msg": "STALE_PRIVATE"}


class Probe(_RegionProbe):
    def run(self, mode, scope, name, *args, **kwargs):
        options = dict(
            region=REGIONS[scope],
            auth_transport=httpx.MockTransport(self._auth),
            transport_factory=lambda origin: httpx.MockTransport(self._business),
            diagnostic_recorder=self.recorder,
        )
        return _invoke(mode, "recordings", name, args, kwargs, options)


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def _ev(probe, kind, name):
    public = "recordings." + name
    events = [event for event in probe.recorder.report()["events"] if event.get("mode") == kind]
    calls = [
        event["outcome"]
        for event in events
        if event.get("kind") == "call" and event.get("entrypoint") == public
    ]
    attempts = [
        event["outcome"]
        for event in events
        if event.get("kind") == "attempt"
        and event.get("entrypoint") == "client.request"
        and event.get("operation") == public
    ]
    return calls, attempts


@pytest.mark.parametrize("name", NAMES)
def test_closed_client_never_authenticates(mode, name):
    async def scenario():
        client, auth_calls, origins = make_client(mode, lambda r: pytest.fail("unexpected request"))
        await close(client)
        await close(client)
        assert client.is_closed
        with pytest.raises(TransportError, match="closed"):
            await resolve(getattr(client.recordings, name)(*ARGS[name], **KW[name]))
        assert auth_calls == origins == []

    asyncio.run(scenario())


@pytest.mark.parametrize("name", NAMES)
def test_permission_denied_10031(mode, name):
    p = Probe({"code": "10031", "msg": "PRIVATE_DENIAL"})
    with pytest.raises(PermissionDeniedError) as err:
        p.run(mode, "domestic", name, *ARGS[name], **KW[name])
    assert str(err.value.code) == "10031" and err.value.http_status == 200
    assert len(p.auth) == len(p.biz) == 1


@pytest.mark.parametrize("name", NAMES)
def test_read_timeout_maps_transport_error(mode, name):
    p = Probe(httpx.ReadTimeout("private timeout"))
    with pytest.raises(TransportError) as excinfo:
        p.run(mode, "domestic", name, *ARGS[name], **KW[name])
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(p.auth) == len(p.biz) == 1


@pytest.mark.parametrize("name", NAMES)
def test_async_cancel(name):
    import asyncio

    probe = Probe(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        probe.run("async", "domestic", name, *ARGS[name], **KW[name])
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, "async", name) == (["started", "cancelled"], ["started", "cancelled"])


@pytest.mark.parametrize("name", NAMES)
def test_get_refresh_exhausted(mode, name):
    probe = Probe(STALE, STALE)
    with pytest.raises(AuthenticationError):
        probe.run(mode, "domestic", name, *ARGS[name], **KW[name])
    check_replayed_form(probe, name)
    assert _ev(probe, mode, name) == (
        ["started", "failed"],
        ["started", "failed", "started", "failed"],
    )


@pytest.mark.parametrize("name", NAMES)
def test_get_refresh(mode, name):
    probe = Probe(STALE, OK)
    assert probe.run(mode, "domestic", name, *ARGS[name], **KW[name]).raw == OK
    check_replayed_form(probe, name)
    assert _ev(probe, mode, name) == (
        ["started", "succeeded"],
        ["started", "failed", "started", "succeeded"],
    )


@pytest.mark.parametrize("name", NAMES)
def test_auth_failure(mode, name):
    p = Probe()

    def deny(request):
        p.auth.append(request)
        return httpx.Response(403, json={"code": "10031", "msg": "PRIVATE_DENIAL"})

    p._auth = deny
    with pytest.raises(ApiError) as exc:
        p.run(mode, "domestic", name, *ARGS[name], **KW[name])
    assert len(p.auth) == 1
    assert p.biz == []
    blob = repr(exc.value) + " " + str(exc.value)
    assert SERIAL not in blob
    assert "TOK1" not in blob


@pytest.mark.parametrize("ok", [True, False])
@pytest.mark.parametrize("name", NAMES)
def test_sensitive_output(mode, name, ok, caplog):
    scope = "domestic"
    payload = {**OK, "code": "200" if ok else "PRIVATE_BACKEND_CODE", "msg": "PRIVATE_UPSTREAM_MSG"}
    payload["data"] = PAGED if name == MS else ROWS
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(wire)
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.run(mode, scope, name, *ARGS[name], **KW[name])
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.run(mode, scope, name, *ARGS[name], **KW[name])
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [r.getMessage() for r in caplog.records if r.name == "httpx" and PATH in r.getMessage()]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        SERIAL,
        "TOK1",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
        "ROOT_PRIVATE",
        "PAGE_PRIVATE",
        "CHECKSUM_PRIVATE",
        "OWNER_PRIVATE",
        "FILE_PRIVATE",
        "INDEX_PRIVATE",
        "NAME_PRIVATE",
        "IMG_PRIVATE",
        "VID_PRIVATE",
        "URL_PRIVATE",
        "CREATED_PRIVATE",
        START,
        STOP,
        "9102285123",
        "850168912345",
    ):
        assert marker not in blob


def check_replayed_form(probe, name):
    assert len(probe.auth) == 2 and len(probe.biz) == 2
    fields = FIELDS[name]
    _form(probe.biz[0], "TOK1", fields)
    _form(probe.biz[1], "TOK2", fields)


def _form(req, token, fields):
    assert req.method == "POST"
    assert req.url.scheme == "https" and req.url.host == "open.ys7.com"
    assert req.url.path == PATH
    assert req.url.query == b""
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert sorted(parse_qsl(req.content.decode(), keep_blank_values=True)) == sorted(
        [("accessToken", token), *fields.items()]
    )
    for key in (
        "authorization",
        "accessToken",
        "deviceSerial",
        "channelNo",
        "startTime",
        "endTime",
        "stopTime",
        "version",
        "pageSize",
        "recType",
    ):
        assert key not in req.headers


@pytest.mark.parametrize(
    "name,kwargs,fields,data",
    [
        (MS, {}, {"deviceSerial": SERIAL}, ROWS),
        (TEXT, {}, {}, ROWS),
        (MS, KW[MS], FIELDS[MS], PAGED),
        (TEXT, KW[TEXT], FIELDS[TEXT], ROWS),
        (
            MS,
            {"rec_type": 1, "version": "2.0"},
            {"deviceSerial": SERIAL, "recType": "1", "version": "2.0"},
            PAGED,
        ),
        (
            MS,
            {"version": "2.0", "rec_type": 2},
            {"deviceSerial": SERIAL, "version": "2.0", "recType": "2"},
            ROWS,
        ),
        (
            MS,
            {"version": "2.0", "page_size": 500, "rec_type": 2},
            {"deviceSerial": SERIAL, "version": "2.0", "pageSize": "500", "recType": "2"},
            PAGED,
        ),
        (
            MS,
            {"page_size": 1000, "rec_type": 1},
            {"deviceSerial": SERIAL, "pageSize": "1000", "recType": "1"},
            ROWS,
        ),
        (
            MS,
            {"start_time_ms": MIN_I64, "end_time_ms": MAX_I64},
            {
                "deviceSerial": SERIAL,
                "startTime": str(MIN_I64),
                "endTime": str(MAX_I64),
            },
            ROWS,
        ),
        (
            MS,
            {"rec_type": 0, "version": "2.0"},
            {"deviceSerial": SERIAL, "recType": "0", "version": "2.0"},
            ROWS,
        ),
        (TEXT, {"stop_time": STOP}, {"stopTime": str(STOP)}, ROWS),
        (TEXT, {"start_time": START}, {"startTime": str(START)}, ROWS),
        (TEXT, {"channel": "001"}, {"channelNo": "001"}, ROWS),
    ],
)
def test_valid_wire(mode, name, kwargs, fields, data):
    body = {"code": "200", "msg": "ok", "data": data, "extra": "private"}
    p = Probe(body)
    result = p.run(mode, "domestic", name, *ARGS[name], **kwargs)
    assert len(p.auth) == 1
    assert len(p.biz) == 1
    auth = p.auth[0]
    assert auth.method == "POST"
    assert auth.url.host == "open.ys7.com"
    assert auth.url.path == "/api/lapp/token/get"
    _form(p.biz[0], "TOK1", fields)
    assert result.raw == body
    assert result.data == data
    assert result.raw["code"] == "200"
    assert _ev(p, mode, name) == (["started", "succeeded"], ["started", "succeeded"])


@pytest.mark.parametrize("name", NAMES)
def test_stored_recordings_success_shapes(mode, name):
    rows = list(ROWS) + [
        {
            "downloadPath": "https://media.invalid/dl",
            "coverPic": "https://img.invalid/c.png",
            "nul": None,
        },
        {"unknown": {"deep": [None, {"k": None}]}},
    ]
    if name == TEXT:
        datas = [[], rows]
    else:
        datas = [
            dict(PAGED, files=rows, isAll=True, nextFileTime=0, topExtension={"x": None}),
            dict(PAGED, files=[], isAll=False, nextFileTime=1700000000000),
            dict(PAGED, files=[], isAll=False, nextFileTime=0),
            dict(PAGED, files=rows, isAll=False, nextFileTime=-1234),
            dict(PAGED, files=[], isAll=False, nextFileTime=-(2**63)),
        ]
    media_hosts = {
        httpx.URL(v).host
        for row in rows
        if isinstance(row, dict)
        for v in (row.get("downloadPath"), row.get("coverPic"))
        if isinstance(v, str)
    }
    bodies = [{"code": "200", "msg": "ok", "data": d} for d in datas]
    if name == MS:
        bodies += [{"code": "200", "msg": "degraded", "data": d} for d in ([], rows)]
    for body in bodies:
        p = Probe(body)
        res = p.run(mode, "domestic", name, *ARGS[name], **KW[name])
        assert isinstance(res, ApiResponse)
        assert res.data == body["data"]
        assert res.raw == body
        assert len(p.auth) == 1
        assert len(p.biz) == 1
        assert not any(r.url.host in media_hosts for r in p.biz)


@pytest.mark.parametrize("name", NAMES)
def test_stored_recordings_shape_rejections(mode, name):
    missing = object()

    def paged(**over):
        data = dict(PAGED)
        data.update(files=list(ROWS), isAll=False, nextFileTime=1234)
        for key, value in over.items():
            if value is missing:
                data.pop(key, None)
            else:
                data[key] = value
        return data

    cases = [missing, None, {}, [None], "x", 7, True]
    if name == TEXT:
        cases += [1.5, dict(PAGED)]
    else:
        cases += [
            paged(files=missing),
            paged(files=None),
            paged(files={}),
            paged(files=[None]),
            paged(files=[{"a": 1}, "x"]),
            paged(isAll=missing),
            paged(isAll=0),
            paged(isAll=1),
            paged(isAll=2),
            paged(isAll="true"),
            paged(isAll=None),
            paged(nextFileTime=missing),
            paged(nextFileTime=True),
            paged(nextFileTime=1.5),
            paged(nextFileTime="1234"),
            paged(nextFileTime=2**63),
            paged(nextFileTime=-(2**63) - 1),
            paged(isAll=True, nextFileTime=1),
            paged(isAll=True, nextFileTime=-1),
        ]
    for data in cases:
        body = {"code": "200", "msg": "ok"}
        if data is not missing:
            body["data"] = data
        p = Probe(body)
        with pytest.raises(ProtocolError):
            p.run(mode, "domestic", name, *ARGS[name], **KW[name])
        assert len(p.auth) == 1
        assert len(p.biz) == 1

    good = list(ROWS) if name == TEXT else paged()
    for body in ([{"code": "200", "data": good}], 42, {"body": {"code": "200", "data": good}}):
        p = Probe(body)
        with pytest.raises(ProtocolError):
            p.run(mode, "domestic", name, *ARGS[name], **KW[name])
        assert len(p.auth) == 1
        assert len(p.biz) == 1
    p = Probe({"code": 200, "msg": "ok", "data": good})
    with pytest.raises((ApiError, ProtocolError)):
        p.run(mode, "domestic", name, *ARGS[name], **KW[name])


def test_stored_recordings_manual_pagination(mode):
    pointer = 1700000000001
    media_row = {
        "downloadPath": "https://media.invalid/dl",
        "coverPic": "https://img.invalid/c.png",
    }
    first = dict(PAGED, files=list(ROWS) + [media_row], isAll=False, nextFileTime=pointer)
    terminal = dict(PAGED, files=[{"r": 2}], isAll=True, nextFileTime=0)
    p = Probe(
        {"code": "200", "msg": "ok", "data": first}, {"code": "200", "msg": "ok", "data": terminal}
    )
    res1 = p.run(mode, "domestic", MS, *ARGS[MS], **KW[MS])
    assert res1.data == first
    assert res1.data["nextFileTime"] == pointer
    assert len(p.biz) == 1
    kwargs = dict(KW[MS])
    kwargs["start_time_ms"] = res1.data["nextFileTime"]
    if kwargs.get("end_time_ms") is not None and kwargs["end_time_ms"] < kwargs["start_time_ms"]:
        kwargs.pop("end_time_ms")
    res2 = p.run(mode, "domestic", MS, *ARGS[MS], **kwargs)
    assert res2.data == terminal
    assert len(p.biz) == 2
    sent = []
    for r in p.biz:
        assert r.url.query == b""
        fields = dict(parse_qsl(r.content.decode(), keep_blank_values=True))
        sent.append(fields["startTime"])
    assert sent == [str(KW[MS]["start_time_ms"]), str(pointer)]
    media_hosts = {httpx.URL(media_row[k]).host for k in ("downloadPath", "coverPic")}
    assert not any(r.url.host in media_hosts for r in p.auth + p.biz)


INVALID_CALLS = [
    # MS device serial (supplied positionally, otherwise-valid time window)
    (MS, ("",), {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000}, ValueError),
    (MS, ("abc123",), {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000}, ValueError),
    (
        MS,
        ("AB\ud8009",),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000},
        ValueError,
    ),
    # MS rec_type: bool and out-of-range
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "rec_type": True},
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "rec_type": 99},
        ValueError,
    ),
    # MS channel: noninteger bool / string / float
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "channel": True},
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "channel": "1"},
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "channel": 1.5},
        ValueError,
    ),
    # MS millisecond fields: bool / float / str / outside signed64 / reversed
    (MS, (SERIAL,), {"start_time_ms": True, "end_time_ms": 1704153600000}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": 1704067200000, "end_time_ms": False}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": 1704067200000, "end_time_ms": 1.5}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": "1704067200000", "end_time_ms": 1704153600000}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": 1704067200000, "end_time_ms": "1704153600000"}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": MIN_I64 - 1, "end_time_ms": 1704153600000}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": 1704067200000, "end_time_ms": MAX_I64 + 1}, ValueError),
    (MS, (SERIAL,), {"start_time_ms": 1704153600000, "end_time_ms": 1704067200000}, ValueError),
    # MS version must be exactly "2.0"
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "version": 2.0},
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "version": ""},
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "version": "2"},
        ValueError,
    ),
    # MS page_size: needs nonzero rec_type; cloud bound 1000; local bound 500
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000, "page_size": 50},
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {
            "start_time_ms": 1704067200000,
            "end_time_ms": 1704153600000,
            "rec_type": 0,
            "page_size": 50,
        },
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {
            "start_time_ms": 1704067200000,
            "end_time_ms": 1704153600000,
            "rec_type": 1,
            "version": "2.0",
            "page_size": 0,
        },
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {
            "start_time_ms": 1704067200000,
            "end_time_ms": 1704153600000,
            "rec_type": 1,
            "version": "2.0",
            "page_size": True,
        },
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {
            "start_time_ms": 1704067200000,
            "end_time_ms": 1704153600000,
            "rec_type": 1,
            "version": "2.0",
            "page_size": 1001,
        },
        ValueError,
    ),
    (
        MS,
        (SERIAL,),
        {
            "start_time_ms": 1704067200000,
            "end_time_ms": 1704153600000,
            "rec_type": 2,
            "page_size": 501,
        },
        ValueError,
    ),
    # MS negative page_size is rejected
    (MS, (SERIAL,), {"rec_type": 2, "page_size": -1}, ValueError),
    # MS binding errors (TypeError only)
    (MS, (), {"start_time_ms": 1704067200000, "end_time_ms": 1704153600000}, TypeError),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000, "stop_time": "2024-01-02 00:00:00"},
        TypeError,
    ),
    (MS, (SERIAL,), {"start_time_ms": 1704067200000, "stop_time_ms": 1704153600000}, TypeError),
    (MS, (SERIAL,), {"start_time": "2024-01-01 00:00:00"}, TypeError),
    # TEXT serial (kw-only, otherwise-valid time window)
    (
        TEXT,
        (),
        {
            "device_serial": "",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": "abc123",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": "AB\ud8009",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    # TEXT channel: blank / whitespace-only / int / surrogate
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "channel": "",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "channel": "   ",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "channel": 3,
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "channel": "\ud800",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    # TEXT reversed range
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2024-06-01 00:00:00",
            "stop_time": "2024-01-01 00:00:00",
        },
        ValueError,
    ),
    # TEXT calendar / width violations
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00+08:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "２０２４-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {"device_serial": SERIAL, "start_time": "2024-01-01", "stop_time": "2024-01-02 00:00:00"},
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2023-02-29 00:00:00",
            "stop_time": "2024-03-01 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00.5",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2024-1-15 8:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        ValueError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2024-13-01 00:00:00",
            "stop_time": "2025-01-01 00:00:00",
        },
        ValueError,
    ),
    # TEXT rec_type=True is invalid
    (TEXT, (), {"rec_type": True}, ValueError),
    # TEXT binding errors: positional serial and MS-only keyword names
    (
        TEXT,
        (SERIAL,),
        {"start_time": "2024-01-01 00:00:00", "stop_time": "2024-01-02 00:00:00"},
        TypeError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "page_size": 10,
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        TypeError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "end_time_ms": 1704153600000,
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        TypeError,
    ),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time_ms": 1704067200000,
            "stop_time": "2024-01-02 00:00:00",
        },
        TypeError,
    ),
    (TEXT, (), {"rec_type": 99}, ValueError),
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "version": "2.0",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        TypeError,
    ),
]

EDGE_FORMS = [
    # MS equal endpoints
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704153600000, "end_time_ms": 1704153600000},
        {"deviceSerial": SERIAL, "startTime": "1704153600000", "endTime": "1704153600000"},
    ),
    # MS zero endpoints are valid and equal
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 0, "end_time_ms": 0},
        {"deviceSerial": SERIAL, "startTime": "0", "endTime": "0"},
    ),
    # MS negative start is a valid signed64 instant
    (
        MS,
        (SERIAL,),
        {"start_time_ms": -86400000, "end_time_ms": 1704153600000},
        {"deviceSerial": SERIAL, "startTime": "-86400000", "endTime": "1704153600000"},
    ),
    # MS range spanning a date/year rollover boundary
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067199999, "end_time_ms": 1704067200000},
        {"deviceSerial": SERIAL, "startTime": "1704067199999", "endTime": "1704067200000"},
    ),
    # MS one-sided bounds
    (
        MS,
        (SERIAL,),
        {"end_time_ms": 1704153600000},
        {"deviceSerial": SERIAL, "endTime": "1704153600000"},
    ),
    (
        MS,
        (SERIAL,),
        {"start_time_ms": 1704067200000},
        {"deviceSerial": SERIAL, "startTime": "1704067200000"},
    ),
    # Explicit version without a requested source
    (MS, (SERIAL,), {"version": "2.0"}, {"deviceSerial": SERIAL, "version": "2.0"}),
    # Explicit automatic source
    (MS, (SERIAL,), {"rec_type": 0}, {"deviceSerial": SERIAL, "recType": "0"}),
    # MS local page size without version
    (
        MS,
        (SERIAL,),
        {"rec_type": 2, "page_size": 50},
        {"deviceSerial": SERIAL, "recType": "2", "pageSize": "50"},
    ),
    # MS local version without page size
    (
        MS,
        (SERIAL,),
        {"rec_type": 2, "version": "2.0"},
        {"deviceSerial": SERIAL, "recType": "2", "version": "2.0"},
    ),
    # TEXT call with all optional fields omitted
    (TEXT, (), {}, {}),
    # TEXT equal endpoints on a valid leap day, no timezone/fractional part
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2024-02-29 12:00:00",
            "stop_time": "2024-02-29 12:00:00",
        },
        {
            "deviceSerial": SERIAL,
            "startTime": "2024-02-29 12:00:00",
            "stopTime": "2024-02-29 12:00:00",
        },
    ),
    # TEXT range spanning a valid date/year rollover
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "start_time": "2023-12-31 23:59:59",
            "stop_time": "2024-01-01 00:00:00",
        },
        {
            "deviceSerial": SERIAL,
            "startTime": "2023-12-31 23:59:59",
            "stopTime": "2024-01-01 00:00:00",
        },
    ),
    # TEXT mixed-case uppercase UTF-8 serial preserved
    (
        TEXT,
        (),
        {
            "device_serial": "EZÉΩ9",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        {
            "deviceSerial": "EZÉΩ9",
            "startTime": "2024-01-01 00:00:00",
            "stopTime": "2024-01-02 00:00:00",
        },
    ),
    # TEXT nonblank channel preserved verbatim
    (
        TEXT,
        (),
        {
            "device_serial": SERIAL,
            "channel": " 003 ",
            "start_time": "2024-01-01 00:00:00",
            "stop_time": "2024-01-02 00:00:00",
        },
        {
            "deviceSerial": SERIAL,
            "channelNo": " 003 ",
            "startTime": "2024-01-01 00:00:00",
            "stopTime": "2024-01-02 00:00:00",
        },
    ),
    # TEXT start-only / stop-only are optional one-sided bounds
    (
        TEXT,
        (),
        {"device_serial": SERIAL, "start_time": "2024-01-01 00:00:00"},
        {"deviceSerial": SERIAL, "startTime": "2024-01-01 00:00:00"},
    ),
    (
        TEXT,
        (),
        {"device_serial": SERIAL, "stop_time": "2024-01-02 00:00:00"},
        {"deviceSerial": SERIAL, "stopTime": "2024-01-02 00:00:00"},
    ),
    # TEXT device omitted entirely -> broad device filter with a time component
    (
        TEXT,
        (),
        {"start_time": "2024-01-01 00:00:00", "stop_time": "2024-01-02 00:00:00"},
        {"startTime": "2024-01-01 00:00:00", "stopTime": "2024-01-02 00:00:00"},
    ),
]


@pytest.mark.parametrize("name,args,kwargs,exc", INVALID_CALLS)
def test_invalid_inputs(mode, name, args, kwargs, exc):
    p = Probe()
    with pytest.raises(exc):
        p.run(mode, "domestic", name, *args, **kwargs)
    assert p.auth == p.biz == []
    assert _ev(p, mode, name)[1] == []


@pytest.mark.parametrize("name", NAMES)
def test_overseas_rejected_before_validation(mode, name):
    p = Probe()
    with pytest.raises(ValueError) as exc_valid:
        p.run(mode, "overseas", name, *ARGS[name], **KW[name])
    with pytest.raises(ValueError) as exc_invalid:
        p.run(mode, "overseas", name, *ARGS[name], **{**KW[name], "rec_type": object()})
    assert str(exc_valid.value) == str(exc_invalid.value)
    assert p.auth == [] and p.biz == []
    assert _ev(p, mode, name) == (["started", "failed", "started", "failed"], [])


@pytest.mark.parametrize("name,args,kwargs,fields", EDGE_FORMS)
def test_positive_boundaries(mode, name, args, kwargs, fields):
    p = Probe(OK)
    assert p.run(mode, "domestic", name, *args, **kwargs).raw == OK
    assert len(p.auth) == len(p.biz) == 1
    _form(p.biz[0], "TOK1", fields)
