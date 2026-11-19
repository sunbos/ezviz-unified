"""Synthetic privacy regression tests for ``live_streams.address`` media URLs."""

import asyncio
import itertools
import json
import logging

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    RegionConfig,
    ResponsePolicy,
)
from ezviz_unified.response import decode_response
from ezviz_unified.transport import SyncTransport

BASE = "https://open.ys7.com"
AUTH_PATH = "/api/lapp/token/get"
BIZ_PATH = "/api/service/media/streammanage/stream/address"
CREDS = {
    "sync": ("APPKEY_PRIV_SYNC", "APPSECRET_PRIV_SYNC"),
    "async": ("APPKEY_PRIV_ASYNC", "APPSECRET_PRIV_ASYNC"),
}
SCHEMES = ["HtTp", "hTTpS", "rTmP", "RTMPS"]
IDS = ["http", "https", "rtmp", "rtmps"]
ERRORS = {
    "http400_api": (400, "url", ApiError),
    "biz10031_denied": (200, 10031, PermissionDeniedError),
    "http403_denied": (403, "url", PermissionDeniedError),
}
META = {"code_path": "meta.code", "message_path": "meta.message"}
_seq = itertools.count(1)


def _canary(scheme):
    """Unique .invalid media URL; returns (url, path_mark, tail_mark)."""
    n = next(_seq)
    url = f"{scheme}://m{n}.invalid/priv'ate/PTH{n}?tail=TAI{n}"
    return url, f"PTH{n}", f"TAI{n}"


def _transport(mode, handler, base=BASE):
    return httpx.MockTransport(handler)


def _auth(mode, hits):
    def handler(request):
        assert request.url.path == AUTH_PATH
        hits.append(request)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "SYNTH_TOKEN",
                    "expireTime": 9999999999999,
                    "areaDomain": BASE,
                },
            },
        )

    return _transport(mode, handler)


def _invoke(mode, auth, biz, recorder):
    kwargs = {
        "region": RegionConfig.china(),
        "auth_transport": auth,
        "transport_factory": lambda origin: biz,
        "diagnostic_recorder": recorder,
    }
    key, secret = CREDS[mode]
    if mode == "sync":
        with Client(key, secret, **kwargs) as client:
            return client.live_streams.address(42, 2, address_type=2)

    async def call():
        async with AsyncClient(key, secret, **kwargs) as client:
            return await client.live_streams.address(42, 2, address_type=2)

    return asyncio.run(call())


def _clean(text, p, t):
    assert p not in text and t not in text


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "httpx"]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("scheme", SCHEMES, ids=IDS)
def test_success_retains_data_masks_rendering(mode, scheme, caplog):
    url, p, t = _canary(scheme)
    auth_hits = []

    def biz(request):
        assert request.url.path == BIZ_PATH
        return httpx.Response(
            200, json={"meta": {"code": 200, "message": url}, "data": {"address": url}}
        )

    rec = DiagnosticRecorder()
    with caplog.at_level(logging.INFO, logger="httpx"):
        resp = _invoke(mode, _auth(mode, auth_hits), _transport(mode, biz), rec)
    assert len(auth_hits) == 1
    assert resp.data["address"] == url
    assert url in str(resp.raw)
    assert resp.message == "***"
    _clean(repr(resp), p, t)
    _clean(json.dumps(rec.report(), default=str), p, t)
    req_lines = [m for m in _lines(caplog) if m.startswith("HTTP Request:")]
    assert len(req_lines) == 2
    assert AUTH_PATH in req_lines[0]
    assert BIZ_PATH not in req_lines[-1]
    assert "***" in req_lines[-1]
    for m in req_lines:
        _clean(m, p, t)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("scheme", SCHEMES, ids=IDS)
@pytest.mark.parametrize("case", list(ERRORS))
def test_error_masks_code_message_and_reason(mode, scheme, case, caplog):
    status, code_kind, exc_type = ERRORS[case]
    url, p, t = _canary(scheme)

    def biz(request):
        code = url if code_kind == "url" else code_kind
        return httpx.Response(
            status,
            json={"meta": {"code": code, "message": url}},
            extensions={"reason_phrase": url.encode("ascii")},
        )

    with caplog.at_level(logging.INFO, logger="httpx"):
        with pytest.raises(exc_type) as excinfo:
            _invoke(mode, _auth(mode, []), _transport(mode, biz), DiagnosticRecorder())
    err = excinfo.value
    _clean(str(err.code), p, t)
    _clean(err.message, p, t)
    assert err.message == "***"
    if code_kind == "url":
        assert err.code == "***"
    else:
        assert err.code == code_kind
    _clean(str(err), p, t)
    _clean(repr(err), p, t)
    assert "***" in str(err)
    req_lines = [m for m in _lines(caplog) if m.startswith("HTTP Request:")]
    assert len(req_lines) == 2
    assert AUTH_PATH in req_lines[0]
    assert BIZ_PATH not in req_lines[-1]
    assert "***" in req_lines[-1]
    for m in req_lines:
        _clean(m, p, t)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_code_10002_does_not_replay_issuance(mode):
    url, p, t = _canary("rtmps")
    hits = {"auth": [], "biz": []}

    def biz(request):
        hits["biz"].append(request)
        return httpx.Response(200, json={"meta": {"code": 10002, "message": url}})

    with pytest.raises(AuthenticationError) as excinfo:
        _invoke(mode, _auth(mode, hits["auth"]), _transport(mode, biz), DiagnosticRecorder())
    _clean(str(excinfo.value), p, t)
    assert len(hits["auth"]) == 1
    assert len(hits["biz"]) == 1


@pytest.mark.parametrize("scheme", ["rTmP", "RTMPS"], ids=["rtmp", "rtmps"])
def test_default_and_http_only_policies_keep_rtmp(scheme):
    url, _, _ = _canary(scheme)
    payload = {"code": 418, "msg": url}
    with pytest.raises(ApiError) as d:
        decode_response(dict(payload), operation="probe_default")
    assert d.value.message == url
    with pytest.raises(ApiError) as h:
        decode_response(
            dict(payload),
            policy=ResponsePolicy(redact_message_urls=True),
            operation="probe_http_only",
        )
    assert h.value.message == url
    with pytest.raises(ApiError) as m:
        decode_response(
            dict(payload), policy=ResponsePolicy(redact_media_urls=True), operation="probe_media"
        )
    assert m.value.message == "***"


def test_default_transport_reason_stays_visible(caplog):
    url, _, _ = _canary("rtmps")

    def handler(request):
        return httpx.Response(
            400,
            json={"meta": {"code": url, "message": url}},
            extensions={"reason_phrase": url.encode("ascii")},
        )

    t = SyncTransport("https://probe.invalid", transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO, logger="httpx"):
        with pytest.raises(ApiError) as err:
            t.request("POST", "/probe", policy=ResponsePolicy(**META), operation="visible_probe")
    assert err.value.message == url
    assert any(url in m for m in _lines(caplog))


def test_nested_default_read_visible_inside_masked(caplog):
    outer_url, op, ot = _canary("rTmP")
    inner_url, _, _ = _canary("RTMPS")

    def inner_handler(request):
        return httpx.Response(
            400,
            json={"meta": {"code": inner_url, "message": inner_url}},
            extensions={"reason_phrase": inner_url.encode("ascii")},
        )

    inner = SyncTransport("https://inner.invalid", transport=httpx.MockTransport(inner_handler))

    def outer_handler(request):
        with pytest.raises(ApiError) as inner_err:
            inner.request(
                "POST",
                "/nested-read",
                policy=ResponsePolicy(**META),
                operation="nested_default_read",
            )
        assert inner_err.value.message == inner_url
        return httpx.Response(
            400,
            json={"meta": {"code": outer_url, "message": outer_url}},
            extensions={"reason_phrase": outer_url.encode("ascii")},
        )

    outer = SyncTransport("https://outer.invalid", transport=httpx.MockTransport(outer_handler))
    masked = ResponsePolicy(redact_media_urls=True, **META)
    with caplog.at_level(logging.INFO, logger="httpx"):
        with pytest.raises(ApiError) as outer_err:
            outer.request("POST", "/masked-read", policy=masked, operation="outer_masked_read")
    assert outer_err.value.message == "***"
    req_lines = [m for m in _lines(caplog) if m.startswith("HTTP Request:")]
    assert len(req_lines) == 2
    assert inner_url in req_lines[0]
    assert "***" in req_lines[1]
    for m in req_lines:
        _clean(m, op, ot)


def test_unrelated_task_marker_survives_cancelled_request(caplog):
    outer_url, op, ot = _canary("rtmps")
    side = f"RTMPSIDE{next(_seq)}"
    started = asyncio.Event()
    release = asyncio.Event()
    gate = asyncio.Event()
    spawned = []

    def side_handler(request):
        return httpx.Response(
            200, extensions={"reason_phrase": f"rtmp://side.invalid/{side}".encode("ascii")}
        )

    async def biz(request):
        async def ordinary():
            await gate.wait()
            async with httpx.AsyncClient(transport=httpx.MockTransport(side_handler)) as c:
                await c.get(f"https://unrelated.invalid/{side}")

        spawned.append(asyncio.create_task(ordinary()))
        started.set()
        await release.wait()
        return httpx.Response(
            200, json={"meta": {"code": 200, "message": outer_url}, "data": {"address": outer_url}}
        )

    async def main():
        key, secret = CREDS["async"]
        kwargs = {
            "region": RegionConfig.china(),
            "auth_transport": _auth("async", []),
            "transport_factory": lambda origin: _transport("async", biz),
            "diagnostic_recorder": DiagnosticRecorder(),
        }
        async with AsyncClient(key, secret, **kwargs) as client:
            pending = asyncio.create_task(client.live_streams.address(42, 2, address_type=2))
            await asyncio.wait_for(started.wait(), 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            release.set()
        gate.set()
        await asyncio.wait_for(asyncio.gather(*spawned), 3)

    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(main())
    lines = _lines(caplog)
    for m in lines:
        _clean(m, op, ot)
    side_l = [m for m in lines if "unrelated.invalid" in m]
    assert side_l
    assert any(f"rtmp://side.invalid/{side}" in m for m in side_l)
