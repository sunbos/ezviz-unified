"""Wire success matrix for the motion detection area settings endpoints."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_device_add_links import Probe as _BaseProbe
from test_sdk_light_linkage import outcomes
from test_sdk_stream_archive import _invoke

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

GET, SET = "get_motion_area", "set_motion_area"
SERIAL, CHANNEL = "MiXeD A-1", "001"
AREA = " area✓+%&=, PRIVATE_GRID "
CASES = [("domestic", GET), ("domestic", SET), ("overseas", SET)]
REGIONS = {
    "domestic": RegionConfig.china(),
    "overseas": RegionConfig.overseas(
        auth_base_url="https://synthetic-auth.ezvizlife.com",
    ),
}
TRANSPORT_HEADERS = {
    "host",
    "user-agent",
    "accept",
    "accept-encoding",
    "connection",
    "content-length",
    "content-type",
}


def options(name):
    opts = {"channel_no": CHANNEL}
    if name == SET:
        opts["area"] = AREA
    return opts


def reply(scope, code=200, **fields):
    if scope == "domestic":
        return {"code": str(code), "msg": "ok", **fields}
    return {"body": {"meta": {"code": code, "message": "ok"}, **fields}}


class Probe(_BaseProbe):
    def _auth(self, request):
        self.auth.append(request)
        token = ("TOK1", "TOK2")[(len(self.auth) - 1) % 2]
        domain = "https://" + request.url.host.replace("-auth.", "-api.")
        data = {"accessToken": token, "expireTime": 9999999999999, "areaDomain": domain}
        return httpx.Response(200, json={"code": "200", "data": data})

    def run(self, mode, scope, name, *args, **kwargs):
        options = dict(
            region=REGIONS[scope],
            auth_transport=httpx.MockTransport(self._auth),
            transport_factory=lambda origin: httpx.MockTransport(self._business),
            diagnostic_recorder=self.recorder,
        )
        return _invoke(mode, "settings", name, args, kwargs, options)


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


@pytest.mark.parametrize("scope,name", CASES)
def test_wire_success(mode, scope, name):
    data = {"opaque": [1, {"echo": "x"}]}
    if name == GET:
        data = {
            "area": [0, -7, 1 << 40],
            "rows": 0,
            "columns": 0,
            "channelNo": 3,
            "deviceSerial": "response-private",
            "ext": [{"x": 3}],
        }
    envelope = reply(scope, data=data)
    probe = Probe(envelope)
    result = probe.run(mode, scope, name, SERIAL, **options(name))
    assert isinstance(result, ApiResponse) and result.data == data and result.raw == envelope
    assert len(probe.auth) == len(probe.biz) == 1
    auth, biz = probe.auth[0], probe.biz[0]
    domestic = scope == "domestic"
    assert auth.url.host == ("open.ys7.com" if domestic else "synthetic-auth.ezvizlife.com")
    assert biz.url.host == ("open.ys7.com" if domestic else "synthetic-api.ezvizlife.com")
    assert auth.url.scheme == biz.url.scheme == "https"
    assert biz.method == ("GET" if name == GET else "POST")
    assert biz.url.path == "/api/v3/device/motion/detect/" + name.split("_")[0]
    custom = {k.lower(): v for k, v in biz.headers.items() if k.lower() not in TRANSPORT_HEADERS}
    assert custom == {"accesstoken": "TOK1", "deviceserial": SERIAL, "channelno": CHANNEL}
    if name == GET:
        assert biz.url.query == biz.content == b"" and "content-type" not in biz.headers
    elif domestic:
        assert biz.url.query == b""
        assert biz.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert parse_qsl(biz.content.decode()) == [("area", AREA)]
    else:
        assert parse_qsl(biz.url.query.decode()) == [("area", AREA)]
        assert biz.content == b"" and "content-type" not in biz.headers


BAD_ID = (True, 1, 1.5, None, b"x", "", " x", "x ", "\t", "\x7f", "é1")
BAD_AREA = (None, b"x", ["x"], "", " \t", "a\x00b", "a\x85b", "a\x7fb", "a\ud800b")
_MISS = object()


def _idle(p):
    assert p.auth == [] and p.biz == []


def test_overseas_get_blocked(mode):
    cases = [
        (SERIAL, options(GET)),
        (object(), options(GET)),
        (SERIAL, {"channel_no": object()}),
    ]
    for serial, kw in cases:
        p = Probe()
        with pytest.raises(
            ValueError, match="^overseas motion area read origin is unresolved$"
        ) as exc:
            p.run(mode, "overseas", GET, serial, **kw)
        assert str(exc.value) == "overseas motion area read origin is unresolved"
        _idle(p)
        events = p.recorder.report()["events"]
        calls = [
            e
            for e in events
            if e["kind"] == "call"
            and e["entrypoint"] == "settings.get_motion_area"
            and e["mode"] == mode
        ]
        assert [e["outcome"] for e in calls] == ["started", "failed"]
        assert not [e for e in events if e["kind"] == "attempt"]
        assert not [e for e in events if e["kind"] in ("auth", "business")]


@pytest.mark.parametrize("scope,name", CASES)
@pytest.mark.parametrize("field", ("serial", "channel_no"))
@pytest.mark.parametrize("bad", BAD_ID)
def test_invalid_identity(mode, scope, name, field, bad):
    kw = options(name) if field == "serial" else {**options(name), "channel_no": bad}
    p = Probe()
    with pytest.raises(ValueError):
        p.run(mode, scope, name, bad if field == "serial" else SERIAL, **kw)
    _idle(p)


@pytest.mark.parametrize("scope", ("domestic", "overseas"))
@pytest.mark.parametrize("bad", BAD_AREA)
def test_invalid_area(mode, scope, bad):
    p = Probe()
    with pytest.raises(ValueError):
        p.run(mode, scope, SET, SERIAL, **{**options(SET), "area": bad})
    _idle(p)


@pytest.mark.parametrize("scope,name", CASES)
@pytest.mark.parametrize(
    "variant", ("extra", "positional", "alias", "missing", "missing_serial", "missing_last")
)
def test_invalid_signature(mode, scope, name, variant):
    opts = options(name)
    args, kw = {
        "extra": ((SERIAL,), {**opts, "bogus": 1}),
        "positional": ((SERIAL, CHANNEL) + ((AREA,) if name == SET else ()), {}),
        "alias": (
            (SERIAL,),
            {**{k: v for k, v in opts.items() if k != "channel_no"}, "channel": CHANNEL},
        ),
        "missing": ((SERIAL,), {}),
        "missing_serial": ((), options(name)),
        "missing_last": ((SERIAL,), options(GET) if name == SET else {}),
    }[variant]
    p = Probe()
    with pytest.raises(TypeError):
        p.run(mode, scope, name, *args, **kw)
    _idle(p)


@pytest.mark.parametrize(
    "data",
    (
        _MISS,
        None,
        "x",
        [],
        {},
        {"area": None},
        {"area": "s"},
        {"area": [True]},
        {"area": [0.5]},
        {"area": ["s"]},
        {"area": [], "rows": "1"},
        {"area": [], "columns": False},
        {"area": [], "channelNo": "1"},
        {"area": [], "channelNo": True},
        {"area": [], "deviceSerial": 1},
    ),
)
def test_domestic_get_bad_data(mode, data):
    p = Probe(reply("domestic", **({} if data is _MISS else {"data": data})))
    with pytest.raises(ProtocolError):
        p.run(mode, "domestic", GET, SERIAL, **options(GET))
    assert len(p.biz) == 1


@pytest.mark.parametrize(
    "scope,name,body",
    (
        ("domestic", GET, reply("domestic", data={"area": []})),
        ("domestic", SET, reply("domestic")),
        ("overseas", SET, reply("overseas")),
    ),
)
def test_valid_envelope(mode, scope, name, body):
    p = Probe(body)
    assert isinstance(p.run(mode, scope, name, SERIAL, **options(name)), ApiResponse)
    assert len(p.biz) == 1


@pytest.mark.parametrize(
    "scope,name,body",
    (
        ("domestic", GET, reply("overseas", data={"area": []})),
        ("domestic", SET, reply("overseas")),
        ("overseas", SET, reply("domestic")),
        ("domestic", GET, {"code": 200, "msg": "ok", "data": {"area": []}}),
        ("domestic", SET, {"code": 200, "msg": "ok"}),
        ("overseas", SET, {"body": {"meta": {"code": "200", "message": "ok"}}}),
    ),
)
def test_wrong_envelope_or_code_type(mode, scope, name, body):
    p = Probe(body)
    with pytest.raises((ApiError, ProtocolError)):
        p.run(mode, scope, name, SERIAL, **options(name))
    assert len(p.biz) == 1


def invoke(mode, scope, name, *steps):
    p = Probe(*steps)
    return (
        (lambda: p.run(mode, scope, name, SERIAL, **options(name))),
        p.recorder,
        {"auth": p.auth, "biz": p.biz},
    )


def refreshed(rec, mode, seen, terminal):
    assert [r.headers["accessToken"] for r in seen["biz"]] == ["TOK1", "TOK2"]
    assert outcomes(rec, mode, "retry", GET) == ["scheduled"]
    assert outcomes(rec, mode, "call", GET) == ["started", terminal]


def test_get_invalid_token_retry_then_success(mode):
    call, rec, seen = invoke(
        mode, "domestic", GET, reply("domestic", 10002), reply("domestic", data={"area": [7]})
    )
    call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", GET) == ["started", "failed", "started", "succeeded"]
    refreshed(rec, mode, seen, "succeeded")


def test_get_invalid_token_exhausted_raises(mode):
    call, rec, seen = invoke(
        mode, "domestic", GET, reply("domestic", 10002), reply("domestic", 10002)
    )
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 2
    assert outcomes(rec, mode, "attempt", GET) == ["started", "failed", "started", "failed"]
    refreshed(rec, mode, seen, "failed")


@pytest.mark.parametrize("scope", ("domestic", "overseas"))
def test_set_invalid_token_not_retried(mode, scope):
    call, rec, seen = invoke(mode, scope, SET, reply(scope, 10002))
    with pytest.raises(AuthenticationError):
        call()
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert outcomes(rec, mode, "attempt", SET) == ["started", "failed"]
    assert outcomes(rec, mode, "retry", SET) == []


@pytest.mark.parametrize("scope,name", CASES)
def test_permission_denied_10031(mode, scope, name):
    call, rec, seen = invoke(mode, scope, name, reply(scope, 10031))
    with pytest.raises(PermissionDeniedError) as err:
        call()
    assert str(err.value.code) == "10031" and err.value.http_status == 200
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("scope,name", CASES)
def test_generic_business_code_api_error(mode, scope, name):
    call, rec, seen = invoke(mode, scope, name, reply(scope, 60020))
    with pytest.raises(ApiError) as excinfo:
        call()
    assert type(excinfo.value) is ApiError
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert excinfo.value.http_status == 200


@pytest.mark.parametrize("scope,name", CASES)
def test_read_timeout_maps_transport_error(mode, scope, name):
    call, rec, seen = invoke(mode, scope, name, httpx.ReadTimeout("synthetic"))
    with pytest.raises(TransportError) as excinfo:
        call()
    assert excinfo.value.reason == "timeout"
    assert not isinstance(excinfo.value, ApiError)
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("scope,name", CASES)
def test_async_cancellation_propagates(scope, name):
    call, rec, seen = invoke("async", scope, name, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call()
    assert outcomes(rec, "async", "call", name) == ["started", "cancelled"]
    assert outcomes(rec, "async", "attempt", name) == ["started", "cancelled"]
    assert len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("scope,name", CASES)
@pytest.mark.parametrize("ok", [True, False])
def test_sensitive_output(mode, scope, name, ok, caplog):
    payload = reply(
        scope, 200 if ok else "PRIVATE_BACKEND_CODE", data={"area": [7], "extra": "EXT_PRIVATE"}
    )
    node = payload if scope == "domestic" else payload["body"]["meta"]
    node["msg" if scope == "domestic" else "message"] = "PRIVATE_UPSTREAM_MSG"
    wire = httpx.Response(200, json=payload, extensions={"reason_phrase": b"PRIVATE_REASON"})
    probe = Probe(wire)
    with caplog.at_level("INFO", logger="httpx"):
        if ok:
            out = probe.run(mode, scope, name, SERIAL, **options(name))
            assert out.message == "***" and out.raw == payload
            surfaces = [out.message, repr(out)]
        else:
            with pytest.raises(ApiError) as ei:
                probe.run(mode, scope, name, SERIAL, **options(name))
            err = ei.value
            assert err.code == "***" and err.message == "***"
            surfaces = [str(err), repr(err), err.message]
    hits = [
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx" and "/api/v3/device/motion/detect/" in r.getMessage()
    ]
    assert len(hits) == 1 and "?" not in hits[0] and "PRIVATE_REASON" not in hits[0]
    blob = "\n".join(surfaces + [repr(probe.recorder.report()), caplog.text])
    for marker in (
        SERIAL,
        "TOK1",
        "PRIVATE_GRID",
        "PRIVATE_UPSTREAM_MSG",
        "PRIVATE_BACKEND_CODE",
        "PRIVATE_REASON",
        "EXT_PRIVATE",
    ):
        assert marker not in blob
