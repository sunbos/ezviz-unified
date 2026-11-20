"""Wire, response, and privacy contracts for the domestic single-page cursor lookup."""

import asyncio
import json
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_client import close, make_client, resolve
from test_sdk_motion_area import REGIONS
from test_sdk_motion_area import Probe as _RegionProbe
from test_sdk_stream_archive import _invoke

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

OP = "list_by_id"
PATH = "/api/lapp/device/list"
CURSOR = " REQ_PRIVATE+/&=% 私 "
NEXT = " NEXT_PRIVATE+/&=% 私 "
ROWS = [
    {
        "id": "ROW_PRIVATE",
        "deviceSerial": "SER_PRIVATE",
        "deviceName": "NAME_PRIVATE",
        "netAddress": None,
    },
    {"id": "LAST_PRIVATE", "netAddress": "IP_PRIVATE", "extra": ["EXT_PRIVATE"]},
]
OK = {
    "code": "200",
    "msg": "ok",
    "data": ROWS,
    "page": {"nextId": NEXT, "total": 417, "size": 50, "extra": "PAGE_PRIVATE"},
    "rootExtra": "ROOT_PRIVATE",
}
STALE = {"code": "10002", "msg": "PRIVATE_STALE"}
KW = {"cursor": CURSOR, "page_start": 3, "page_size": 7}


class Probe(_RegionProbe):
    def run(self, mode, scope, name, *args, **kwargs):
        options = dict(
            region=REGIONS[scope],
            auth_transport=httpx.MockTransport(self._auth),
            transport_factory=lambda origin: httpx.MockTransport(self._business),
            diagnostic_recorder=self.recorder,
        )
        return _invoke(mode, "devices", name, args, kwargs, options)


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def _ev(probe, kind, name):
    public = "devices." + name
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


@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, ok, caplog):
    scope = "domestic"
    payload = {**OK, "code": "200" if ok else "PRIVATE_BACKEND_CODE", "msg": "PRIVATE_UPSTREAM_MSG"}
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(wire)
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.run(mode, scope, OP, **KW)
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.run(mode, scope, OP, **KW)
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [r.getMessage() for r in caplog.records if r.name == "httpx" and PATH in r.getMessage()]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        "REQ_PRIVATE",
        "NEXT_PRIVATE",
        "ROW_PRIVATE",
        "SER_PRIVATE",
        "NAME_PRIVATE",
        "IP_PRIVATE",
        "PAGE_PRIVATE",
        "ROOT_PRIVATE",
        "TOK1",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob


def test_closed_client_never_authenticates(mode):
    async def scenario():
        client, auth_calls, origins = make_client(mode, lambda r: pytest.fail("unexpected request"))
        await close(client)
        await close(client)
        assert client.is_closed
        with pytest.raises(TransportError, match="closed"):
            await resolve(client.devices.list_by_id(**KW))
        assert auth_calls == origins == []

    asyncio.run(scenario())


def test_permission_denied_10031(mode):
    p = Probe({"code": "10031", "msg": "PRIVATE_DENIAL"})
    with pytest.raises(PermissionDeniedError) as err:
        p.run(mode, "domestic", OP, **KW)
    assert str(err.value.code) == "10031" and err.value.http_status == 200
    assert len(p.auth) == len(p.biz) == 1


def test_read_timeout_maps_transport_error(mode):
    p = Probe(httpx.ReadTimeout("private timeout"))
    with pytest.raises(TransportError) as excinfo:
        p.run(mode, "domestic", OP, **KW)
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(p.auth) == len(p.biz) == 1


def test_async_cancel():
    import asyncio

    probe = Probe(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        probe.run("async", "domestic", OP, **KW)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert _ev(probe, "async", OP) == (["started", "cancelled"], ["started", "cancelled"])


def test_get_refresh_exhausted(mode):
    probe = Probe(STALE, STALE)
    with pytest.raises(AuthenticationError):
        probe.run(mode, "domestic", OP, **KW)
    check_replayed_page(probe)
    assert _ev(probe, mode, OP) == (
        ["started", "failed"],
        ["started", "failed", "started", "failed"],
    )


def test_get_refresh(mode):
    probe = Probe(STALE, OK)
    assert probe.run(mode, "domestic", OP, **KW).raw == OK
    check_replayed_page(probe)
    assert _ev(probe, mode, OP) == (
        ["started", "succeeded"],
        ["started", "failed", "started", "succeeded"],
    )


def _query(req, token, fields):
    assert req.method == "POST"
    assert req.url.scheme == "https" and req.url.host == "open.ys7.com"
    assert req.url.path == PATH
    assert sorted(req.url.params.multi_items()) == sorted([("accessToken", token), *fields.items()])
    assert req.content == b""
    for header in ("content-type", "authorization", "accessToken", "id", "pageStart", "pageSize"):
        assert header not in req.headers


def check_replayed_page(probe):
    assert len(probe.auth) == 2 and len(probe.biz) == 2
    fields = {"id": CURSOR, "pageStart": "3", "pageSize": "7"}
    _query(probe.biz[0], "TOK1", fields)
    _query(probe.biz[1], "TOK2", fields)


@pytest.mark.parametrize(
    "kwargs,fields",
    [
        ({}, {}),
        ({"cursor": None, "page_start": None, "page_size": None}, {}),
        ({"cursor": CURSOR}, {"id": CURSOR}),
        ({"page_start": 0, "page_size": 1}, {"pageStart": "0", "pageSize": "1"}),
        ({"page_start": 100, "page_size": 50}, {"pageStart": "100", "pageSize": "50"}),
        (KW, {"id": CURSOR, "pageStart": "3", "pageSize": "7"}),
    ],
)
def test_query_wire(mode, kwargs, fields):
    p = Probe(OK)
    resp = p.run(mode, "domestic", OP, **kwargs)
    assert len(p.auth) == 1 and len(p.biz) == 1
    auth = p.auth[0]
    assert (
        auth.method == "POST"
        and auth.url.host == "open.ys7.com"
        and auth.url.path == "/api/lapp/token/get"
    )
    _query(p.biz[0], "TOK1", fields)
    assert resp.raw == OK and resp.data == ROWS
    assert resp.raw["page"]["nextId"] == NEXT != ROWS[-1]["id"]
    assert _ev(p, mode, OP) == (["started", "succeeded"], ["started", "succeeded"])


@pytest.mark.parametrize(
    "data,page",
    [
        ([], {"nextId": ""}),
        ([{}], {"nextId": None}),
        (ROWS, dict(OK["page"])),
        (ROWS, {"nextId": " ü/%& "}),
        ([dict(ROWS[-1], extra=None, mix={"k": [None, 1]})], {"nextId": ""}),
        ([], {"nextId": None, "total": 9, "size": 9}),
        (ROWS, {"nextId": NEXT, "total": 0}),
    ],
)
def test_domestic_list_by_id_page_semantics(mode, data, page):
    body = dict(OK, data=data, page=page)
    p = Probe(body)
    r = p.run(mode, "domestic", OP)
    assert r.raw == body
    assert r.raw["data"] == data
    assert r.raw["page"] == page
    assert len(p.auth) == 1
    assert len(p.biz) == 1
    _query(p.biz[0], "TOK1", {})


@pytest.mark.parametrize(
    "step,exc",
    [
        ({k: v for k, v in OK.items() if k != "data"}, ProtocolError),
        (dict(OK, data=None), ProtocolError),
        (dict(OK, data={}), ProtocolError),
        (dict(OK, data=[None]), ProtocolError),
        ({k: v for k, v in OK.items() if k != "page"}, ProtocolError),
        (dict(OK, page=None), ProtocolError),
        (dict(OK, page=[]), ProtocolError),
        (dict(OK, page={}), ProtocolError),
        (
            dict(OK, data=[dict(ROWS[-1], nextId="decoy")], page={"total": 1}, nextId="top"),
            ProtocolError,
        ),
        (dict(OK, page=dict(OK["page"], nextId=7)), ProtocolError),
        (dict(OK, page=dict(OK["page"], nextId=False)), ProtocolError),
        (dict(OK, page=dict(OK["page"], nextId="   ")), ProtocolError),
        (dict(OK, page=dict(OK["page"], nextId="\x01")), ProtocolError),
        (dict(OK, page=dict(OK["page"], nextId="a\x85b")), ProtocolError),
        (dict(OK, page=dict(OK["page"], nextId="\x7f")), ProtocolError),
        (
            dict(OK, page=dict(OK["page"], nextId=chr(0xD800))),
            ProtocolError,
        ),
        (dict(OK, page=dict(OK["page"], total=True)), ProtocolError),
        (dict(OK, page=dict(OK["page"], total="9")), ProtocolError),
        (dict(OK, page=dict(OK["page"], total=1.5)), ProtocolError),
        (dict(OK, page=dict(OK["page"], size=True)), ProtocolError),
        (dict(OK, page=dict(OK["page"], size="2")), ProtocolError),
        (dict(OK, page=dict(OK["page"], size=0.5)), ProtocolError),
        (dict(OK, code=200), (ApiError, ProtocolError)),
        ({"body": dict(OK)}, ProtocolError),
    ],
)
def test_domestic_list_by_id_page_rejects(mode, step, exc):
    p = Probe(
        httpx.Response(
            200,
            content=json.dumps(step, ensure_ascii=True).encode("ascii"),
            headers={"Content-Type": "application/json"},
        )
    )
    with pytest.raises(exc):
        p.run(mode, "domestic", OP)
    assert len(p.auth) == 1
    assert len(p.biz) == 1


def test_domestic_list_by_id_cursor_handoff(mode):
    p = Probe(OK, dict(OK, data=[], page={"nextId": None}))
    r1 = p.run(mode, "domestic", OP)
    assert len(p.biz) == 1
    r2 = p.run(mode, "domestic", OP, cursor=r1.raw["page"]["nextId"])
    assert r2.raw["page"]["nextId"] is None
    assert len(p.biz) == 2
    assert len(p.auth) == 2
    _query(p.biz[0], "TOK1", {})
    _query(p.biz[1], "TOK2", {"id": NEXT})


def test_legacy_list_passthrough(mode):
    body = {"code": "200", "msg": "legacy visible", "data": [{"arbitrary": 42}]}
    cases = [
        ("domestic", (), 0, 10, "open.ys7.com"),
        ("domestic", (400, 50), 400, 50, "open.ys7.com"),
        ("overseas", (401, 1), 401, 1, "synthetic-api.ezvizlife.com"),
    ]
    for scope, args, page_start, page_size, host in cases:
        p = Probe(body)
        resp = p.run(mode, scope, "list", *args)
        assert resp.message == "legacy visible"
        assert resp.data == [{"arbitrary": 42}]
        assert resp.raw == body
        assert len(p.biz) == 1
        req = p.biz[0]
        assert req.method == "POST"
        assert req.url.host == host
        assert req.url.path == PATH
        assert not req.url.query
        form = parse_qsl(req.content.decode())
        assert len(form) == 3
        assert dict(form) == {
            "accessToken": "TOK1",
            "pageStart": str(page_start),
            "pageSize": str(page_size),
        }
        assert (
            len(p.auth) == 1
            and p.auth[0].method == "POST"
            and p.auth[0].url.path == "/api/lapp/token/get"
        )
        assert p.auth[0].url.host == (
            "open.ys7.com" if scope == "domestic" else "synthetic-auth.ezvizlife.com"
        ) and req.headers["content-type"].startswith("application/x-www-form-urlencoded")

    p = Probe(body)
    with pytest.raises(ValueError):
        p.run(mode, "domestic", "list", 401, 1)
    assert p.auth == [] and p.biz == []


def test_list_by_id_preflight(mode):
    p = Probe(OK)
    bad = (
        [("cursor", v) for v in ("", "   ", 7, True, b"c", "\x00", "\x85", "\x7f", "\ud800")]
        + [("page_start", v) for v in (-1, 101, True, 1.0, "0")]
        + [("page_size", v) for v in (0, 51, True, 3.0, "1")]
    )
    for field, value in bad:
        with pytest.raises(ValueError):
            p.run(mode, "domestic", OP, **{**KW, field: value})
        assert p.auth == [] and p.biz == []
        assert _ev(p, mode, OP)[1] == []

    with pytest.raises(TypeError):
        p.run(mode, "domestic", OP, "c")
    with pytest.raises(TypeError):
        p.run(mode, "domestic", OP, id="c")

    with pytest.raises(ValueError) as exc_valid:
        p.run(mode, "overseas", OP, **KW)
    with pytest.raises(ValueError) as exc_invalid:
        p.run(mode, "overseas", OP, **{**KW, "cursor": ""})
    assert str(exc_valid.value) == str(exc_invalid.value)
    assert p.auth == [] and p.biz == []


def test_list_by_id_auth_failure(mode):
    p = Probe()

    def deny(request):
        p.auth.append(request)
        return httpx.Response(403, json={"code": "10031", "msg": "PRIVATE_DENIAL"})

    p._auth = deny
    with pytest.raises(ApiError) as exc:
        p.run(mode, "domestic", OP, **KW)
    assert len(p.auth) == 1
    assert p.biz == []
    blob = repr(exc.value) + " " + str(exc.value)
    assert CURSOR not in blob
    assert "TOK1" not in blob
