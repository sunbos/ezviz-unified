"""Offline wire/behaviour tests for ``cloud_recording_v1.project`` (doc 1363).

Fully synthetic: ``httpx.MockTransport`` auth/business doubles only; no
environment, network or real credentials. Both ``Client`` and
``AsyncClient`` are exercised through the real facade.
"""

import asyncio
import json
import logging

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    RegionConfig,
    TransportError,
)
from ezviz_unified.errors import ProtocolError

APP_KEY = "EVAL_KEY"
APP_SECRET = "PUBLIC MOCK ONLY: not a credential for project details 1363"
TOKEN_1 = "SYNTHETIC_TOKEN_ALPHA"
TOKEN_2 = "SYNTHETIC_TOKEN_BRAVO"
PROJECT = "PROJ_0123456789ABCDEF"
ORIGIN = "https://open.ys7.com"
PATH = "/api/open/cloud/v1/project/" + PROJECT
ENTRY = "cloud_recording_v1.project"
MODES = ("sync", "async")
MISS = object()

DATA = {
    "projectId": PROJECT,
    "projectName": "Demo project",
    "expireDays": 30,
    "providerType": "demo",
    "projectTag": None,
    "extensionField": {"nested": [1, 2]},
}
STALE = {"meta": {"code": 10002, "message": "stale token"}, "data": None}
BAD_DATA = (
    None,
    "x",
    1,
    True,
    [],
    [{}],
    {"projectId": 5},
    {"projectId": True},
    {"expireDays": "30"},
    {"expireDays": True},
    {"providerType": 1},
    {"storageType": "0"},
)
BAD_IDS = (
    "P" * 33,
    "项目PROJ",
    "",
    "   ",
    "has-dash",
    "has.dot",
    "a/b",
    123,
    1.5,
    None,
    True,
    "BAD\ud800X",
)


def body(data=DATA, code=200):
    return {"meta": {"code": code, "message": "OK"}, "data": data, "extra": "kept"}


def rsp(payload=None, status=200):
    return httpx.Response(status, json=body() if payload is None else payload)


def timeout(request):
    raise httpx.ReadTimeout("synthetic timeout", request=request)


def overseas_region():
    return RegionConfig.overseas()


class Harness:
    """Counting auth/business MockTransports around a real sync/async client."""

    def __init__(self, mode, script, *, region=None, recorder=None, tokens=(TOKEN_1,)):
        self.mode = mode
        self.events = []
        pool = list(tokens) or [TOKEN_1]

        def auth(request):
            self.events.append(("auth", request))
            token = pool.pop(0) if len(pool) > 1 else pool[0]
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

        queue = list(script)

        def business(request):
            self.events.append(("http", request))
            item = queue.pop(0)
            return item(request) if callable(item) else item

        self.kwargs = {
            "region": region if region is not None else RegionConfig.china(),
            "clock": lambda: 1000,
            "auth_transport": httpx.MockTransport(auth),
            "transport_factory": lambda origin: httpx.MockTransport(business),
        }
        if recorder is not None:
            self.kwargs["diagnostic_recorder"] = recorder

    def call(self, *args):
        if self.mode == "sync":
            with Client(APP_KEY, APP_SECRET, **self.kwargs) as client:
                return client.cloud_recording_v1.project(*args)

        async def main():
            async with AsyncClient(APP_KEY, APP_SECRET, **self.kwargs) as client:
                return await client.cloud_recording_v1.project(*args)

        return asyncio.run(main())

    def count(self, kind):
        return sum(1 for key, _ in self.events if key == kind)

    def http_requests(self):
        return [request for key, request in self.events if key == "http"]


@pytest.mark.parametrize("mode", MODES)
def test_wire_token_and_response(mode):
    h = Harness(mode, [rsp()])
    resp = h.call(PROJECT)
    assert type(resp) is ApiResponse
    assert resp.data is resp.raw["data"]
    assert resp.data == DATA
    assert resp.raw["extra"] == "kept"
    assert resp.raw["meta"] == {"code": 200, "message": "OK"}
    assert h.count("auth") == 1 and h.count("http") == 1
    req = h.http_requests()[-1]
    assert req.method == "GET"
    assert req.url.scheme == "https" and req.url.host == "open.ys7.com"
    assert req.url.path == PATH
    assert sorted(req.url.params.multi_items()) == [("accessToken", TOKEN_1)]
    assert req.read() == b"" and "content-type" not in req.headers
    assert req.headers.get("accessToken") is None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("data", [{}, {"projectTag": None}, {"unknown": 1}, DATA])
def test_valid_data_shapes(mode, data):
    resp = Harness(mode, [rsp(body(data))]).call(PROJECT)
    assert resp.data == data


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "payload",
    [body(d) for d in BAD_DATA] + [{"meta": {"code": 200, "message": "OK"}}],
)
def test_invalid_data_shapes(mode, payload):
    h = Harness(mode, [rsp(payload)])
    with pytest.raises(ProtocolError):
        h.call(PROJECT)
    assert h.count("http") == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("project_id", BAD_IDS)
def test_invalid_project_id_rejected_locally(mode, project_id):
    h = Harness(mode, [])
    with pytest.raises(ValueError):
        h.call(project_id)
    assert h.events == []


@pytest.mark.parametrize("mode", MODES)
def test_non_domestic_region_rejected(mode):
    h = Harness(mode, [], region=overseas_region())
    with pytest.raises(ValueError):
        h.call(PROJECT)
    assert h.events == []


@pytest.mark.parametrize("mode", MODES)
def test_refresh_once_same_request(mode):
    h = Harness(mode, [rsp(STALE), rsp()], tokens=[TOKEN_1, TOKEN_2])
    resp = h.call(PROJECT)
    assert resp.data == DATA
    reqs = h.http_requests()
    assert len(reqs) == 2
    assert [r.url.params["accessToken"] for r in reqs] == [TOKEN_1, TOKEN_2]
    assert all(r.url.path == PATH for r in reqs)
    assert h.count("auth") == 2


@pytest.mark.parametrize("mode", MODES)
def test_refresh_exhausted_raises_auth(mode):
    h = Harness(mode, [rsp(STALE), rsp(STALE)], tokens=[TOKEN_1, TOKEN_2])
    with pytest.raises(AuthenticationError) as err:
        h.call(PROJECT)
    assert err.value.code == 10002
    assert h.count("auth") == 2 and h.count("http") == 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "status,code,expected",
    [
        (200, 10031, PermissionDeniedError),
        (200, "10031", PermissionDeniedError),
        (403, MISS, PermissionDeniedError),
        (200, 400, ApiError),
        (200, "400", ApiError),
        (200, 403, ApiError),
        (200, 404, ApiError),
        (200, "404", ApiError),
        (200, 500, ApiError),
        (200, "500", ApiError),
        (503, 404, ApiError),
        (503, "404", ApiError),
        (200, "200", ApiError),
        (200, MISS, ProtocolError),
        (200, None, ProtocolError),
        (200, "", ApiError),
        (200, [200], ProtocolError),
        (200, {"code": 200}, ProtocolError),
    ],
)
def test_error_classification_and_masking(mode, status, code, expected):
    meta = {"message": "synthetic upstream text"}
    if code is not MISS:
        meta["code"] = code
    h = Harness(mode, [rsp({"meta": meta}, status)])
    with pytest.raises(expected) as err:
        h.call(PROJECT)
    assert type(err.value) is expected
    assert err.value.http_status == status
    text = str(err.value)
    assert "synthetic upstream text" not in text and PROJECT not in text
    if expected is ProtocolError:
        assert err.value.code is None
        if code is MISS:
            assert err.value.message.startswith("missing business code at")
        else:
            assert err.value.message.startswith("non-scalar business code at")
    else:
        assert err.value.message == "***"
        if status == 200 and code == "":
            assert err.value.code == "***"
        elif status == 200:
            assert err.value.code == code and type(err.value.code) is type(code)


@pytest.mark.parametrize("mode", MODES)
def test_transport_failure(mode):
    h = Harness(mode, [timeout])
    with pytest.raises(TransportError):
        h.call(PROJECT)
    assert h.count("http") == 1


@pytest.mark.parametrize("mode", MODES)
def test_diagnostics_no_leak(mode, caplog):
    caplog.set_level(logging.DEBUG)
    recorder = DiagnosticRecorder()
    h = Harness(mode, [rsp(STALE), rsp()], tokens=[TOKEN_1, TOKEN_2], recorder=recorder)
    h.call(PROJECT)
    blob = json.dumps(recorder.report())
    assert ENTRY in blob
    events = recorder.report()["events"]
    calls = [
        e
        for e in events
        if e["kind"] == "call" and e["outcome"] == "succeeded" and e["parent_span_id"] is None
    ]
    assert len(calls) == 1 and calls[0]["entrypoint"] == ENTRY
    started = [e for e in events if e["kind"] == "attempt" and e["outcome"] == "started"]
    assert len(started) == 2
    for attempt in started:
        assert attempt["entrypoint"] == "client.request"
        assert attempt["operation"] == ENTRY
    for secret in (TOKEN_1, TOKEN_2, PROJECT, APP_KEY, APP_SECRET, "stale token"):
        assert secret not in blob
        assert secret not in caplog.text
