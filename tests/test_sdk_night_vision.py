"""Wire and input tests for the domestic night-vision illumination API."""

import json
from urllib.parse import parse_qsl, quote

import pytest
from test_sdk_charging_status import _auth, _biz, _kwargs, _timeout
from test_sdk_stream_archive import _invoke

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AuthenticationError,
    DiagnosticRecorder,
    FillLightMode,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

TOKEN = "SynT+Q%27"
SERIAL = "Syn %私"
GET = "get_night_vision"
SET = "set_night_vision"
OPTS = {"luminance": -7, "duration": 999999, "mode": 3}
KINDS = ["sync", "async"]
ESCAPED = quote(SERIAL, safe="")


def reply(data, code=200, message="ok"):
    return {"meta": {"code": code, "message": message}, "data": data, "extra": 7}


class Probe:
    """One scenario: real Client/AsyncClient over two MockTransports."""

    def __init__(self, *steps, region=None, tokens=(TOKEN,)):
        self.auth = []
        self.biz = []
        self.rec = DiagnosticRecorder()
        self.options = _kwargs(
            region or RegionConfig.china(),
            _auth(tokens, self.auth),
            _biz(steps, self.biz),
            self.rec,
        )

    def call(self, kind, name, *args, **kwargs):
        return _invoke(kind, "illumination", name, args, kwargs, self.options)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "data",
    [
        {"luminance": 4, "duration": 5, "graphicType": 17, "unknown": {"x": [1]}},
        '{"graphicType":9,"raw":"text"}',
        "",
        {},
    ],
)
def test_get_wire(kind, data):
    payload = reply({"valueInfo": data, "other": "kept"})
    probe = Probe(payload)
    res = probe.call(kind, GET, SERIAL, -9)
    assert len(probe.auth) == len(probe.biz) == 1
    req = probe.biz[0]
    assert req.method == "GET" and req.url.host == "open.ys7.com"
    assert req.url.raw_path.split(b"?", 1)[0] == f"/api/v3/keyValue/{ESCAPED}/-9/op".encode()
    assert sorted(req.url.params.multi_items()) == [
        ("accessToken", TOKEN),
        ("key", "NightVision_Model"),
    ]
    assert not req.content and "content-type" not in req.headers
    assert "accessToken" not in req.headers and "deviceSerial" not in req.headers
    assert isinstance(res, ApiResponse)
    assert res.raw == payload
    assert res.data == payload["data"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", [3, 0, FillLightMode.HUMAN_DETECTION_COLOR])
@pytest.mark.parametrize("result", ["night vision updated", ""])
def test_set_wire(kind, mode, result):
    payload = reply({"result": result, "other": "kept"})
    probe = Probe(payload)
    kw = dict(OPTS, mode=mode)
    res = probe.call(kind, SET, SERIAL, 1, **kw)
    assert len(probe.auth) == len(probe.biz) == 1  # no implicit readback
    req = probe.biz[0]
    assert req.method == "PUT" and req.url.host == "open.ys7.com"
    assert req.url.raw_path.split(b"?", 1)[0] == f"/api/v3/keyValue/{ESCAPED}/1/op".encode()
    assert "accessToken" not in req.headers and "deviceSerial" not in req.headers
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert sorted(req.url.params.multi_items()) == [("accessToken", TOKEN)]
    expected = json.dumps(
        {
            "luminance": kw["luminance"],
            "duration": kw["duration"],
            "graphicType": getattr(mode, "value", mode),
        },
        separators=(",", ":"),
    )
    assert sorted(parse_qsl(req.content.decode())) == [
        ("key", "NightVision_Model"),
        ("value", expected),
    ]
    assert isinstance(res, ApiResponse)
    assert res.raw == payload
    assert res.data == payload["data"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name,args,kwargs", [(GET, (SERIAL, 1), {}), (SET, (SERIAL, 1), OPTS)])
def test_overseas_region_rejected(kind, name, args, kwargs):
    probe = Probe(reply({}), region=RegionConfig.overseas())
    with pytest.raises(ValueError):
        probe.call(kind, name, *args, **kwargs)
    assert probe.auth == probe.biz == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "serial,channel", [("a/b", 1), ("", 1), ("..", 1), (SERIAL, True), (SERIAL, 1.5), (SERIAL, "1")]
)
def test_get_rejects_bad_input(kind, serial, channel):
    probe = Probe(reply({}))
    with pytest.raises((TypeError, ValueError)):
        probe.call(kind, GET, serial, channel)
    assert probe.auth == probe.biz == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "bad",
    [
        {"luminance": True},
        {"luminance": 1.5},
        {"luminance": "-7"},
        {"duration": True},
        {"duration": 0.5},
        {"duration": "999999"},
        {"mode": True},
        {"mode": 3.0},
        {"mode": "3"},
        {"mode": 99},
    ],
)
def test_set_rejects_bad_input(kind, bad):
    probe = Probe(reply({}))
    with pytest.raises((TypeError, ValueError)):
        probe.call(kind, SET, SERIAL, 1, **dict(OPTS, **bad))
    assert probe.auth == probe.biz == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        (GET, (SERIAL,), {}),
        (SET, (SERIAL,), {}),
        (SET, (SERIAL, 1), {}),
        (SET, (SERIAL, 1), {"luminance": -7, "duration": 999999}),
        (SET, (SERIAL, 1, -7, 999999, 3), {}),
    ],
)
def test_signature_errors(kind, name, args, kwargs):
    probe = Probe(reply({}))
    with pytest.raises(TypeError):
        probe.call(kind, name, *args, **kwargs)
    assert probe.auth == probe.biz == []


def _outcomes(probe, kind, name, event_kind):
    public = "illumination." + name
    return [
        e["outcome"]
        for e in probe.rec.report()["events"]
        if e.get("mode") == kind
        and e.get("kind") == event_kind
        and (
            (event_kind == "call" and e.get("entrypoint") == public)
            or (
                event_kind == "attempt"
                and e.get("entrypoint") == "client.request"
                and e.get("operation") == public
            )
        )
    ]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "name,kw,body,exc",
    [
        (GET, {}, {"body": reply({"valueInfo": {}})}, ProtocolError),
        (GET, {}, {"code": 200}, ProtocolError),
        (GET, {}, reply({"valueInfo": {}}, code="200"), ApiError),
        (GET, {}, reply({"valueInfo": {}}, code=True), ProtocolError),
        (GET, {}, reply(None), ProtocolError),
        (GET, {}, reply(["v"]), ProtocolError),
        (GET, {}, reply({}), ProtocolError),
        (GET, {}, reply({"valueInfo": None}), ProtocolError),
        (GET, {}, reply({"valueInfo": []}), ProtocolError),
        (GET, {}, reply({"valueInfo": {"luminance": True}}), ProtocolError),
        (GET, {}, reply({"valueInfo": {"duration": 1.5}}), ProtocolError),
        (GET, {}, reply({"valueInfo": {"graphicType": "3"}}), ProtocolError),
        (SET, OPTS, reply({}), ProtocolError),
        (SET, OPTS, reply({"result": 7}), ProtocolError),
    ],
)
def test_malformed_payloads(kind, name, kw, body, exc):
    p = Probe(body)
    with pytest.raises(exc):
        p.call(kind, name, SERIAL, -9, **kw)
    assert len(p.auth) == 1 and len(p.biz) == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "name,steps,exc,auths,bizs",
    [
        (GET, (reply({"valueInfo": {}}, code=10002), reply({"valueInfo": {}})), None, 2, 2),
        (
            GET,
            (reply({"valueInfo": {}}, code=10002), reply({"valueInfo": {}}, code=10002)),
            AuthenticationError,
            2,
            2,
        ),
        (SET, (reply({"result": "ok"}, code=10002),), AuthenticationError, 1, 1),
        (SET, (reply({"result": "x"}, code=10031),), PermissionDeniedError, 1, 1),
        (SET, (_timeout,), TransportError, 1, 1),
    ],
)
def test_refresh_cap_and_put_failures(kind, name, steps, exc, auths, bizs):
    import contextlib

    p = Probe(*steps, tokens=(TOKEN, TOKEN + "2"))
    with pytest.raises(exc) if exc else contextlib.nullcontext():
        p.call(kind, name, SERIAL, -9, **(OPTS if name == SET else {}))
    assert len(p.auth) == auths and len(p.biz) == bizs
    for req, expected in zip(p.biz, [TOKEN, TOKEN + "2"][:bizs], strict=True):
        assert req.method == ("GET" if name == "get_night_vision" else "PUT")
        assert req.url.params.get_list("accessToken") == [expected]
    if exc is None:
        want = (["started", "failed"] * bizs)[:-1] + ["succeeded"]
    else:
        want = ["started", "failed"] * bizs
    assert _outcomes(p, kind, name, "attempt") == want
    assert _outcomes(p, kind, name, "call")[-1] == ("succeeded" if exc is None else "failed")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "name,kw,payload", [(GET, {}, {"valueInfo": {}}), (SET, OPTS, {"result": "ok"})]
)
def test_success_diagnostics(kind, name, kw, payload):
    p = Probe(reply(payload))
    p.call(kind, name, SERIAL, -9, **kw)
    assert _outcomes(p, kind, name, "call") == ["started", "succeeded"]
    assert _outcomes(p, kind, name, "attempt") == ["started", "succeeded"]


def test_async_business_cancel():
    import asyncio

    def boom(_req):
        raise asyncio.CancelledError

    p = Probe(boom)
    with pytest.raises(asyncio.CancelledError):
        p.call("async", GET, SERIAL, -9)
    assert len(p.auth) == 1 and len(p.biz) == 1
    assert _outcomes(p, "async", GET, "call") == ["started", "cancelled"]
    assert _outcomes(p, "async", GET, "attempt") == ["started", "cancelled"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name,kw", [(GET, {}), (SET, OPTS)])
def test_denied_serial_token_redaction(kind, name, kw, caplog):
    import logging

    msg = f"no {SERIAL} {ESCAPED} {TOKEN} {quote(TOKEN, safe='')}"
    p = Probe(reply({"valueInfo": {}, "result": "x"}, code=10031, message=msg))
    with caplog.at_level(logging.INFO, logger="httpx"), pytest.raises(PermissionDeniedError) as ei:
        p.call(kind, name, SERIAL, -9, **kw)
    info = "\n".join(
        r.getMessage() for r in caplog.records if r.name == "httpx" and r.levelno == logging.INFO
    )
    assert p.biz[0].method in info
    blob = info + str(ei.value) + repr(ei.value) + p.rec.to_json()
    for s in (SERIAL, ESCAPED, TOKEN, quote(TOKEN, safe="")):
        assert s not in blob
