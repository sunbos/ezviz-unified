"""Wire/response/error tests for ``cloud_storage.ai_models`` (domestic 5149).

Both ``Client`` and ``AsyncClient`` call the real ``cloud_storage.ai_models``
facade over fully synthetic ``httpx.MockTransport`` auth/business transports.
No environment, network or credential access. Covers the header-token
empty-body wire shape, ``data`` list-of-objects validation, one-shot token
refresh/replay, masked business/HTTP errors and diagnostic privacy.
"""

from __future__ import annotations

import asyncio
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
    EzvizError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

APP_KEY = "EVAL_KEY"
APP_SECRET = "PUBLIC MOCK ONLY: not a credential for AI model catalog 5149"
TOKEN_1 = "CRV1TOKEN_" + "a" * 56
TOKEN_2 = "CRV1TOKEN_" + "b" * 56
ORIGIN = "https://open.ys7.com"
PATH = "/api/service/cloud/storage/service/intelligent/list"
ENTRYPOINT = "cloud_storage.ai_models"
MODES = ("sync", "async")
OVERSEAS = RegionConfig.overseas()

ROWS = [
    {
        "intelligentId": "AI_0001",
        "intelligentName": "demo detector",
        "description": "synthetic row",
        "futureField": {"nested": [1, 2]},
    },
    {},
    {"intelligentId": None, "intelligentName": None, "description": None},
]

VALID_DATA = [
    [],
    [{}],
    [{"intelligentId": "AI-9"}],
    [{"intelligentId": None}],
    [{"intelligentId": "a", "intelligentName": "n", "description": "d", "x": [0]}],
]

INVALID_BODIES = [
    {"code": "200", "msg": "ok"},
    {"code": "200", "msg": "ok", "data": None},
    {"code": "200", "msg": "ok", "data": {}},
    {"code": "200", "msg": "ok", "data": "rows"},
    {"code": "200", "msg": "ok", "data": [123]},
    {"code": "200", "msg": "ok", "data": [["x"]]},
    {"code": "200", "msg": "ok", "data": [None]},
    {"code": "200", "msg": "ok", "data": [{"intelligentId": 9}]},
    {"code": "200", "msg": "ok", "data": [{"intelligentName": 1.5}]},
    {"code": "200", "msg": "ok", "data": [{"description": ["x"]}]},
    {"code": "200", "msg": "ok", "data": [{"intelligentId": True}]},
    {"code": "200", "msg": "ok", "data": [{"intelligentId": "ok"}, {"intelligentName": 4}]},
]


def envelope(data, code="200", msg="ok"):
    return {"code": code, "msg": msg, "data": data, "unrelated": "kept"}


def rsp(body=None, status=200):
    return httpx.Response(status, json=envelope(ROWS) if body is None else body)


def _timeout(request):
    raise httpx.ReadTimeout("synthetic timeout", request=request)


class Harness:
    """Sync/async real client over counting auth/business MockTransports."""

    def __init__(self, mode, script, region=None, recorder=None):
        self.mode = mode
        self.recorder = recorder
        self.counts = {"auth": 0, "factory": 0, "http": 0}
        self.requests = []
        self._script = list(script)

        def auth(request):
            token = (TOKEN_1, TOKEN_2)[min(self.counts["auth"], 1)]
            self.counts["auth"] += 1
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "data": {
                        "accessToken": token,
                        "expireTime": 9_999_999_999_999,
                        "areaDomain": ORIGIN,
                    },
                },
            )

        def business(request):
            self.counts["http"] += 1
            self.requests.append(request)
            if not self._script:
                return httpx.Response(500, json={"code": "5999", "msg": "unscripted"})
            item = self._script.pop(0)
            return item(request) if callable(item) else item

        def factory(origin):
            self.counts["factory"] += 1
            return httpx.MockTransport(business)

        self.kwargs = {
            "region": region if region is not None else RegionConfig.china(),
            "clock": lambda: 1000,
            "auth_transport": httpx.MockTransport(auth),
            "transport_factory": factory,
        }
        if recorder is not None:
            self.kwargs["diagnostic_recorder"] = recorder

    def call(self):
        if self.mode == "sync":
            with Client(APP_KEY, APP_SECRET, **self.kwargs) as client:
                return client.cloud_storage.ai_models()

        async def main():
            async with AsyncClient(APP_KEY, APP_SECRET, **self.kwargs) as client:
                return await client.cloud_storage.ai_models()

        return asyncio.run(main())


@pytest.mark.parametrize("mode", MODES)
def test_wire_header_token_empty_body_and_response(mode):
    h = Harness(mode, [rsp()])
    resp = h.call()
    assert resp.http_status == 200
    assert resp.operation == ENTRYPOINT
    assert resp.data is resp.raw["data"]
    assert resp.data == ROWS
    assert resp.raw["unrelated"] == "kept"
    assert h.counts == {"auth": 1, "factory": 1, "http": 1}
    req = h.requests[-1]
    assert req.method == "POST"
    assert req.url.scheme == "https"
    assert req.url.host == "open.ys7.com"
    assert req.url.path == PATH
    assert req.headers["accessToken"] == TOKEN_1
    assert req.url.params.multi_items() == []
    assert req.read() == b""
    assert "content-type" not in req.headers


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("data", VALID_DATA)
def test_valid_data_shapes(mode, data):
    h = Harness(mode, [rsp(envelope(data))])
    assert h.call().data == data
    assert h.counts["http"] == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("body", INVALID_BODIES)
def test_invalid_data_shapes(mode, body):
    h = Harness(mode, [rsp(body)])
    with pytest.raises(ProtocolError):
        h.call()
    assert h.counts["http"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_integer_success_code_is_not_success(mode):
    h = Harness(mode, [rsp({"code": 200, "msg": "ok", "data": ROWS})])
    with pytest.raises(EzvizError):
        h.call()
    assert h.counts["http"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_overseas_rejected_before_auth_or_transport(mode):
    h = Harness(mode, [rsp()], region=OVERSEAS)
    with pytest.raises(ValueError):
        h.call()
    assert h.counts == {"auth": 0, "factory": 0, "http": 0}
    assert h.requests == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("code", ["10002", 10002])
def test_single_refresh_replays_same_wire(mode, code):
    h = Harness(mode, [rsp(envelope(None, code=code, msg="stale")), rsp()])
    resp = h.call()
    assert resp.data == ROWS
    assert h.counts["auth"] == 2 and h.counts["http"] == 2
    assert [r.headers["accessToken"] for r in h.requests] == [TOKEN_1, TOKEN_2]
    for req in h.requests:
        assert req.method == "POST" and req.url.path == PATH
        assert req.read() == b"" and req.url.params.multi_items() == []


@pytest.mark.parametrize("mode", MODES)
def test_refresh_exhausted_raises(mode):
    stale = envelope(None, code="10002", msg="stale again")
    h = Harness(mode, [rsp(stale), rsp(stale)])
    with pytest.raises(AuthenticationError) as err:
        h.call()
    assert err.value.code == "10002"
    assert h.counts["http"] == 2 and h.counts["auth"] == 2


@pytest.mark.parametrize("mode", MODES)
def test_business_error_masked_no_retry(mode):
    h = Harness(mode, [rsp(envelope(None, code="5000", msg="LEAKY-UPSTREAM"))])
    with pytest.raises(ApiError) as err:
        h.call()
    assert err.value.message == "***"
    assert err.value.code == "***"
    assert "LEAKY-UPSTREAM" not in str(err.value)
    assert h.counts["http"] == 1 and h.counts["auth"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_http_error_masked_no_retry(mode):
    h = Harness(mode, [rsp(envelope(None, code="404", msg="LEAKY"), status=503)])
    with pytest.raises(ApiError) as err:
        h.call()
    assert err.value.http_status == 503 and err.value.message == "***"
    assert h.counts["http"] == 1 and h.counts["auth"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_transport_error_no_retry(mode):
    h = Harness(mode, [_timeout])
    with pytest.raises(TransportError):
        h.call()
    assert h.counts["http"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_diagnostics_names_and_privacy(mode, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    h = Harness(mode, [rsp()], recorder=recorder)
    h.call()
    events = recorder.report()["events"]
    calls = [e for e in events if e["kind"] == "call" and e["parent_span_id"] is None]
    succeeded = [e for e in calls if e["outcome"] == "succeeded"]
    assert len(succeeded) == 1
    span = succeeded[0]
    assert span["parent_span_id"] is None
    assert span["entrypoint"] == ENTRYPOINT
    attempts = [e for e in events if e["kind"] == "attempt" and e["outcome"] == "started"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["entrypoint"] == "client.request"
    assert attempt["operation"] == ENTRYPOINT
    assert isinstance(attempt["attempt"], int)
    blob = json.dumps(recorder.report())
    for secret in (APP_KEY, APP_SECRET, TOKEN_1, TOKEN_2, "AI_0001", "demo detector"):
        assert secret not in blob
        assert secret not in caplog.text


@pytest.mark.parametrize("mode", MODES)
def test_error_diagnostics_mask_upstream(mode, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    h = Harness(
        mode,
        [rsp(envelope(None, code="5000", msg="LEAKY-UPSTREAM"))],
        recorder=recorder,
    )
    with pytest.raises(ApiError):
        h.call()
    events = recorder.report()["events"]
    calls = [e for e in events if e["kind"] == "call" and e["parent_span_id"] is None]
    failed = [e for e in calls if e["outcome"] == "failed"]
    assert len(failed) == 1 and failed[0]["entrypoint"] == ENTRYPOINT
    attempts = [e for e in events if e["kind"] == "attempt" and e["outcome"] == "started"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["entrypoint"] == "client.request" and attempt["operation"] == ENTRYPOINT
    blob = json.dumps(recorder.report())
    assert "LEAKY-UPSTREAM" not in blob and "5000" not in blob
    assert "LEAKY-UPSTREAM" not in caplog.text
