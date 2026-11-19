import asyncio
import json
from collections import deque

import httpx
import pytest

from ezviz_unified import (
    AsyncClient,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
)

APP_KEY = "SYNTH_APP"
APP_SECRET = "SYNTH_SECRET"
AREA_DOMAIN = "https://open.ys7.com"
BASE = "/api/service/device/add/"


class Probe:
    """Queue-driven transport harness shared by third-party protocol tests."""

    def __init__(self, *steps):
        self._steps = deque(steps)
        self.auth = []
        self.biz = []
        self.recorder = DiagnosticRecorder()

    def _auth(self, request):
        self.auth.append(request)
        token = "TOK%d" % len(self.auth)
        data = {"accessToken": token, "expireTime": 9999999999999, "areaDomain": AREA_DOMAIN}
        return httpx.Response(200, json={"code": "200", "data": data})

    def _business(self, request):
        self.biz.append(request)
        if not self._steps:
            raise AssertionError("unexpected synthetic business request")
        step = self._steps.popleft()
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return httpx.Response(200, json=step)

    def call(self, mode, name, *args, region=None, **kwargs):
        options = dict(
            region=region or RegionConfig.china(),
            auth_transport=httpx.MockTransport(self._auth),
            transport_factory=lambda origin: httpx.MockTransport(self._business),
            diagnostic_recorder=self.recorder,
        )
        if mode == "sync":
            with Client(APP_KEY, APP_SECRET, **options) as client:
                return getattr(client.device_add_links, name)(*args, **kwargs)
        if mode == "async":

            async def runner():
                async with AsyncClient(APP_KEY, APP_SECRET, **options) as client:
                    return await getattr(client.device_add_links, name)(*args, **kwargs)

            return asyncio.run(runner())
        raise ValueError("unknown mode: %r" % (mode,))


def reply(data, code=200, message="ok"):
    return {"meta": {"code": code, "message": message, "moreInfo": None}, "data": data}


def outcomes(recorder, mode, kind, name):
    key = "entrypoint" if kind == "call" else "operation"
    return [
        event["outcome"]
        for event in recorder.report()["events"]
        if event["kind"] == kind
        and event[key] == "device_add_links." + name
        and event["mode"] == mode
    ]


LONG_NOTE = " " + "🚀" * 254 + " "  # exactly 256 characters
OPAQUE_CURSOR = "a b&c=d%/e?+f"

RECORD_ROWS = [
    {"id": 91, "bindTime": 17, "note": "synthetic", "extra": {"k": [1, 2]}},
    {"id": 92},
]
LINK_ROWS = [
    {"id": "0091", "expireTime": 17, "expired": False, "url": "https://u.invalid/x", "extra": "e"},
    {"id": "0092", "expireTime": 0, "expired": True, "url": None},
    {"id": "0093", "expireTime": 9, "expired": False, "url": None},
    {"id": "0094"},
]


def _ok(result, payload):
    assert type(result.code) is int and result.code == 200
    assert result.http_status == 200
    assert result.message and result.message == "***"
    assert result.raw == payload
    assert result.raw["meta"]["moreInfo"] == payload["meta"]["moreInfo"]
    assert result.data == payload["data"]
    assert result.data is result.raw["data"]


def _wire(request, path):
    assert request.url.scheme == "https"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == path
    assert request.headers.get_list("accessToken") == ["TOK1"]


@pytest.mark.parametrize(
    "mode,expire_days,note,has_note,url",
    [
        (m, d, n, h, u)
        for m in ("sync", "async")
        for d, n, h, u in [
            (1, None, False, "https://ezviz.invalid/t?x=1"),
            (7, "", True, "scheme:add/opaque?a=1&b=2"),
            (1, LONG_NOTE, True, "https://b.invalid/p#frag"),
        ]
    ],
)
def test_create_posts_json_token_url(mode, expire_days, note, has_note, url):
    assert len(LONG_NOTE) == 256
    payload = reply({"url": url})
    p = Probe(payload)
    result = p.call(mode, "create", expire_days, note=note)
    _ok(result, payload)
    assert result.data == {"url": url}
    assert len(p.auth) == len(p.biz) == 1
    req = p.biz[0]
    assert req.method == "POST"
    _wire(req, BASE + "tokenUrl")
    assert req.url.params.multi_items() == []
    assert req.headers["content-type"] == "application/json"
    body = json.loads(req.content)
    assert set(body) == ({"expireTime", "note"} if has_note else {"expireTime"})
    assert type(body["expireTime"]) is int and body["expireTime"] == expire_days
    if has_note:
        assert body["note"] == note
    assert outcomes(p.recorder, mode, "call", "create") == ["started", "succeeded"]
    assert outcomes(p.recorder, mode, "attempt", "create") == ["started", "succeeded"]


@pytest.mark.parametrize(
    "mode,kwargs,expect,serial",
    [
        (m, kw, e, s)
        for m in ("sync", "async")
        for kw, e, s in [
            ({}, [("pageSize", "10")], None),
            ({"device_serial": "SN  A1  02"}, [("pageSize", "10")], "SN  A1  02"),
            (
                {"device_serial": "MiX  03", "cursor": "c9=&%", "page_size": 25},
                [("id", "c9=&%"), ("pageSize", "25")],
                "MiX  03",
            ),
        ]
    ],
)
def test_records_get_header_and_params(mode, kwargs, expect, serial):
    payload = reply(json.loads(json.dumps(RECORD_ROWS)))
    p = Probe(payload)
    result = p.call(mode, "records", **kwargs)
    _ok(result, payload)
    assert result.data == RECORD_ROWS
    assert len(result.data) == len(RECORD_ROWS)
    for row, want in zip(result.data, RECORD_ROWS):
        assert type(row) is dict
        assert set(row) == set(want)
        for key, value in want.items():
            assert type(row[key]) is type(value) and row[key] == value
        if "id" in row:
            assert type(row["id"]) is int
        if "bindTime" in row:
            assert type(row["bindTime"]) is int
    assert len(p.auth) == len(p.biz) == 1
    req = p.biz[0]
    assert req.method == "GET"
    _wire(req, BASE + "tokenNote")
    assert "content-type" not in req.headers
    assert not req.content
    if serial is None:
        assert "deviceSerial" not in req.headers
    else:
        assert (b"deviceSerial", serial.encode()) in req.headers.raw
    assert sorted(req.url.params.multi_items()) == sorted(expect)
    assert outcomes(p.recorder, mode, "call", "records") == ["started", "succeeded"]
    assert outcomes(p.recorder, mode, "attempt", "records") == ["started", "succeeded"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_records_cursor_passed_as_str_of_int_id(mode):
    first_payload = reply(json.loads(json.dumps(RECORD_ROWS)))
    p = Probe(first_payload, reply([]))
    first = p.call(mode, "records")
    _ok(first, first_payload)
    assert first.data == RECORD_ROWS
    assert type(first.data[-1]["id"]) is int and first.data[-1]["id"] == 92
    assert len(p.auth) == len(p.biz) == 1
    assert p.biz[0].method == "GET"
    _wire(p.biz[0], BASE + "tokenNote")
    assert p.biz[0].url.params.multi_items() == [("pageSize", "10")]
    second = p.call(mode, "records", cursor=str(first.data[-1]["id"]))
    assert second.data == []
    assert len(p.auth) == len(p.biz) == 2
    assert [r.method for r in p.biz] == ["GET", "GET"]
    assert [r.url.path for r in p.biz] == [BASE + "tokenNote"] * 2
    assert sorted(p.biz[1].url.params.multi_items()) == [("id", "92"), ("pageSize", "10")]
    assert "deviceSerial" not in p.biz[0].headers
    assert "deviceSerial" not in p.biz[1].headers
    assert p.biz[1].headers.get_list("accessToken") == ["TOK2"]
    assert outcomes(p.recorder, mode, "call", "records") == ["started", "succeeded"] * 2
    assert outcomes(p.recorder, mode, "attempt", "records") == ["started", "succeeded"] * 2


@pytest.mark.parametrize(
    "mode,kwargs,expect",
    [
        (m, kw, e)
        for m in ("sync", "async")
        for kw, e in [
            ({}, [("pageSize", "10")]),
            ({"page_size": 3}, [("pageSize", "3")]),
            ({"cursor": OPAQUE_CURSOR}, [("id", OPAQUE_CURSOR), ("pageSize", "10")]),
        ]
    ],
)
def test_list_get_token_urls(mode, kwargs, expect):
    payload = reply(json.loads(json.dumps(LINK_ROWS)))
    payload["meta"]["moreInfo"] = {"trace": "t-1"}
    p = Probe(payload)
    result = p.call(mode, "list", **kwargs)
    _ok(result, payload)
    assert result.data == LINK_ROWS
    assert len(result.data) == len(LINK_ROWS)
    for row, want in zip(result.data, LINK_ROWS):
        assert type(row) is dict
        assert set(row) == set(want)
        for key, value in want.items():
            assert type(row[key]) is type(value) and row[key] == value
        if "id" in row:
            assert type(row["id"]) is str
        if "expireTime" in row:
            assert type(row["expireTime"]) is int
        if "expired" in row:
            assert type(row["expired"]) is bool
        if "url" in row:
            assert row["url"] is None or type(row["url"]) is str
    assert len(p.auth) == len(p.biz) == 1
    req = p.biz[0]
    assert req.method == "GET"
    _wire(req, BASE + "tokenUrls")
    assert "deviceSerial" not in req.headers
    assert "content-type" not in req.headers
    assert not req.content
    assert sorted(req.url.params.multi_items()) == sorted(expect)
    assert outcomes(p.recorder, mode, "call", "list") == ["started", "succeeded"]
    assert outcomes(p.recorder, mode, "attempt", "list") == ["started", "succeeded"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_list_second_call_keeps_cursor_literal(mode):
    first_payload = reply(json.loads(json.dumps(LINK_ROWS)))
    p = Probe(first_payload, reply([]))
    first = p.call(mode, "list")
    _ok(first, first_payload)
    assert first.data == LINK_ROWS
    assert type(first.data[-1]["id"]) is str and first.data[-1]["id"] == "0094"
    assert len(p.auth) == len(p.biz) == 1
    assert p.biz[0].method == "GET"
    _wire(p.biz[0], BASE + "tokenUrls")
    assert p.biz[0].url.params.multi_items() == [("pageSize", "10")]
    second = p.call(mode, "list", cursor=first.data[-1]["id"])
    assert second.data == []
    assert len(p.auth) == len(p.biz) == 2
    assert [r.method for r in p.biz] == ["GET", "GET"]
    assert [r.url.path for r in p.biz] == [BASE + "tokenUrls"] * 2
    assert sorted(p.biz[1].url.params.multi_items()) == [("id", "0094"), ("pageSize", "10")]
    assert "deviceSerial" not in p.biz[0].headers
    assert "deviceSerial" not in p.biz[1].headers
    assert p.biz[1].headers.get_list("accessToken") == ["TOK2"]
    assert outcomes(p.recorder, mode, "call", "list") == ["started", "succeeded"] * 2
    assert outcomes(p.recorder, mode, "attempt", "list") == ["started", "succeeded"] * 2


@pytest.mark.parametrize(
    "mode,name,args,data",
    [
        (m, n, a, d)
        for m in ("sync", "async")
        for n, a, d in [
            ("create", (1,), "raw"),
            ("create", (1,), {}),
            ("create", (1,), {"url": 7}),
            ("create", (1,), {"url": None}),
            ("records", (), {"rows": []}),
            ("records", (), ["x"]),
            ("records", (), [{"id": "91"}]),
            ("records", (), [{"id": 1, "bindTime": "17"}]),
            ("list", (), {"list": []}),
            ("list", (), [7]),
            ("list", (), [{"id": 91}]),
            ("list", (), [{"id": "x", "expireTime": "17"}]),
            ("list", (), [{"id": "x", "expired": "yes"}]),
            ("list", (), [{"id": "x", "url": 7}]),
            ("create", (1,), {"url": ""}),
            ("create", (1,), {"url": "   "}),
            ("records", (), [{"id": None}]),
            ("records", (), [{"id": True}]),
            ("records", (), [{"id": 1, "bindTime": False}]),
            ("list", (), [{"id": None}]),
            ("list", (), [{"id": "x", "expireTime": True}]),
            ("list", (), [{"id": "x", "expired": 1}]),
        ]
    ],
)
def test_wrong_shapes_raise_protocol_error(mode, name, args, data):
    p = Probe(reply(data))
    with pytest.raises(ProtocolError) as exc_info:
        p.call(mode, name, *args)
    err = exc_info.value
    assert err.operation == "device_add_links." + name
    assert err.http_status == 200
    assert err.code is None
    assert len(p.auth) == len(p.biz) == 1
    assert outcomes(p.recorder, mode, "call", name) == ["started", "failed"]
    assert outcomes(p.recorder, mode, "attempt", name) == ["started", "succeeded"]
