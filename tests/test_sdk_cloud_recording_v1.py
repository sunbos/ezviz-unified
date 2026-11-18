"""Wire/parameter/response/region precision tests for ``cloud_recording_v1``.

Both ``Client`` and ``AsyncClient`` are exercised for ``unfreeze_file``,
``file_info`` and ``download_urls`` against fully synthetic transports.
Retry/cancellation behaviour is covered by a separate module.
"""

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import AsyncClient, Client, DiagnosticRecorder, RegionConfig

APP_KEY = "EVAL_KEY"
APP_SECRET = "EVAL_SECRET"
TOKEN = "SYNTHETIC_TOKEN"
PROJECT = "PROJ_0123456789ABCDEF"
FILE = "FILE_0123456789ABCDEF"
AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}
ENTRYPOINT = "cloud_recording_v1."
MODES = ("sync", "async")
ALL_METHODS = ("unfreeze_file", "file_info", "download_urls")
STRICT_METHODS = ("unfreeze_file", "download_urls")
WIRE = {
    "unfreeze_file": ("POST", "/api/open/cloud/v1/file/unfreeze"),
    "file_info": ("GET", "/api/open/cloud/v1/file"),
    "download_urls": ("GET", "/api/service/cloudrecord/file/official/download"),
}
EVENT_KEYS = {
    "sequence",
    "span_id",
    "parent_span_id",
    "kind",
    "entrypoint",
    "operation",
    "mode",
    "region",
    "outcome",
    "error_family",
    "reason",
    "code_category",
    "http_status_category",
    "attempt",
}
TERMINAL_OUTCOMES = ("succeeded", "failed", "cancelled")

BAD_CASES = []
for _method in STRICT_METHODS:
    for _project in ("P" * 33, "项目PROJ", "", "   ", 123, None, "BAD\ud800X"):
        BAD_CASES.append((_method, (_project, FILE), {}))
for _project in ("", "   ", 123, None, "BAD\ud800X"):
    BAD_CASES.append(("file_info", (_project, FILE), {}))
for _method in ALL_METHODS:
    for _file in ("F" * 33, "E1" + "q" * 127, "", 9.5, "F\ud800"):
        BAD_CASES.append((_method, (PROJECT, _file), {}))
for _expire in (59, 604801, True, False, "60", 60.5):
    BAD_CASES.append(("download_urls", (PROJECT, FILE), {"expire_seconds": _expire}))
for _content in ("", " ", "CT\ud800", 7):
    BAD_CASES.append(("download_urls", (PROJECT, FILE), {"content_type": _content}))


def rsp_body(data):
    return {"meta": {"code": 200, "message": "OK"}, "data": data, "trace": "UNKNOWN_FIELD"}


def ok_transport(body):
    def handler(request):
        return httpx.Response(200, json=body)

    return handler


class Harness:
    """Builds a sync or async client plus counting transports."""

    def __init__(self, mode, handler, region=None):
        self.mode = mode
        self.recorder = DiagnosticRecorder()
        self.counts = {"auth": 0, "factory": 0, "http": 0}
        self.requests = []

        def auth(request):
            self.counts["auth"] += 1
            return httpx.Response(200, json=dict(AUTH_BODY))

        def recording(request):
            self.counts["http"] += 1
            self.requests.append(request)
            return handler(request)

        def factory(origin):
            self.counts["factory"] += 1
            return httpx.MockTransport(recording)

        self.kwargs = {
            "region": region if region is not None else RegionConfig.china(),
            "clock": lambda: 1000,
            "auth_transport": httpx.MockTransport(auth),
            "transport_factory": factory,
            "diagnostic_recorder": self.recorder,
        }

    def call(self, method, *args, **kwargs):
        if self.mode == "sync":
            with Client(APP_KEY, APP_SECRET, **self.kwargs) as client:
                return getattr(client.cloud_recording_v1, method)(*args, **kwargs)

        async def main():
            async with AsyncClient(APP_KEY, APP_SECRET, **self.kwargs) as client:
                return await getattr(client.cloud_recording_v1, method)(*args, **kwargs)

        return asyncio.run(main())

    def query(self):
        return self.requests[-1].url.params.multi_items()

    def spans(self, method):
        return [
            e
            for e in self.recorder.report()["events"]
            if e["kind"] == "call" and e["entrypoint"] == ENTRYPOINT + method
        ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", ALL_METHODS)
def test_wire_token_and_response(mode, method):
    data = {"marker": "DATA", "url": "https://files.example.invalid/" + method}
    h = Harness(mode, ok_transport(rsp_body(data)))
    resp = h.call(method, PROJECT, FILE)
    assert resp.data is resp.raw["data"]
    assert resp.data == data
    assert resp.raw["trace"] == "UNKNOWN_FIELD"
    assert resp.raw["meta"] == {"code": 200, "message": "OK"}
    assert h.counts["auth"] == 1
    assert h.counts["http"] == 1
    assert h.counts["factory"] == 1
    req = h.requests[-1]
    assert req.method == WIRE[method][0]
    assert req.url.scheme == "https"
    assert req.url.host == "open.ys7.com"
    assert req.url.path == WIRE[method][1]
    if method == "unfreeze_file":
        assert h.query() == []
        assert req.headers.get("accessToken") is None
        assert req.headers["content-type"] == "application/x-www-form-urlencoded"
        assert parse_qs(req.read().decode("utf-8")) == {
            "accessToken": [TOKEN],
            "projectId": [PROJECT],
            "fileId": [FILE],
        }
    else:
        assert req.read() == b""
        assert "content-type" not in req.headers
        if method == "file_info":
            assert sorted(h.query()) == sorted(
                [("accessToken", TOKEN), ("projectId", PROJECT), ("fileId", FILE)]
            )
            assert req.headers.get("accessToken") is None
        else:
            assert sorted(h.query()) == sorted([("projectId", PROJECT), ("fileId", FILE)])
            assert req.headers["accessToken"] == TOKEN


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("payload", [None, {}, [], {"k": None}])
@pytest.mark.parametrize("method", ALL_METHODS)
def test_data_preserved(mode, method, payload):
    h = Harness(mode, ok_transport(rsp_body(payload)))
    resp = h.call(method, PROJECT, FILE)
    assert resp.data is resp.raw["data"]
    assert resp.data == payload


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "content_type,expire",
    [
        (None, None),
        ("CUSTOM/SYNTH", 60),
        ("  padded ct  ", 604800),
        ("x", None),
    ],
)
def test_download_options(mode, content_type, expire):
    h = Harness(mode, ok_transport(rsp_body({"u": "https://files.example.invalid/d"})))
    h.call("download_urls", PROJECT, FILE, content_type=content_type, expire_seconds=expire)
    expected = [("projectId", PROJECT), ("fileId", FILE)]
    if content_type is not None:
        expected.append(("contentType", content_type))
    if expire is not None:
        expected.append(("expireSeconds", str(expire)))
    assert sorted(h.query()) == sorted(expected)
    assert h.requests[-1].headers["accessToken"] == TOKEN


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "method,project",
    [(m, "P" * 32) for m in ALL_METHODS] + [("file_info", "  项目信息" + "长" * 40 + "  ")],
)
def test_project_boundary(mode, method, project):
    h = Harness(mode, ok_transport(rsp_body({})))
    h.call(method, project, FILE)
    if method == "unfreeze_file":
        form = parse_qs(h.requests[-1].read().decode("utf-8"))
        assert form["projectId"] == [project]
    else:
        assert ("projectId", project) in h.query()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", ALL_METHODS)
@pytest.mark.parametrize("file_id", ["F" * 32, "E1", "E1x", "E1$", "E1" + "a" * 126])
def test_file_id_boundary(mode, method, file_id):
    h = Harness(mode, ok_transport(rsp_body({})))
    h.call(method, PROJECT, file_id)
    if method == "unfreeze_file":
        form = parse_qs(h.requests[-1].read().decode("utf-8"))
        assert form["fileId"] == [file_id]
    else:
        assert ("fileId", file_id) in h.query()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method,args,kwargs", BAD_CASES)
def test_invalid_rejected(mode, method, args, kwargs):
    h = Harness(mode, ok_transport(rsp_body({})))
    with pytest.raises(ValueError):
        h.call(method, *args, **kwargs)
    assert h.counts == {"auth": 0, "factory": 0, "http": 0}
    assert h.requests == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", ALL_METHODS)
@pytest.mark.parametrize("project", [PROJECT, "", "项目"])
def test_region_gate(mode, method, project):
    h = Harness(mode, ok_transport(rsp_body({})), region=RegionConfig.overseas())
    with pytest.raises(ValueError):
        h.call(method, project, FILE)
    assert h.counts == {"auth": 0, "factory": 0, "http": 0}
    assert h.requests == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", ALL_METHODS)
def test_diagnostics_success(mode, method):
    h = Harness(mode, ok_transport(rsp_body({"k": "v"})))
    h.call(method, PROJECT, FILE)
    events = h.recorder.report()["events"]
    spans = h.spans(method)
    assert len(spans) == 2
    assert spans[0]["span_id"] == spans[1]["span_id"]
    terminals = [e for e in spans if e["outcome"] in TERMINAL_OUTCOMES]
    assert len(terminals) == 1
    assert terminals[0]["outcome"] == "succeeded"
    assert set(terminals[0]) == EVENT_KEYS
    assert any(e["kind"] == "close" for e in events)
    assert json.loads(h.recorder.to_json())


@pytest.mark.parametrize("mode", MODES)
def test_diagnostics_validation_failure(mode):
    h = Harness(mode, ok_transport(rsp_body({})))
    with pytest.raises(ValueError):
        h.call("unfreeze_file", "P" * 33, FILE)
    assert h.counts == {"auth": 0, "factory": 0, "http": 0}
    terminals = [e for e in h.spans("unfreeze_file") if e["outcome"] in TERMINAL_OUTCOMES]
    assert len(terminals) == 1
    assert (terminals[0]["outcome"], terminals[0]["error_family"], terminals[0]["reason"]) == (
        "failed",
        "validation",
        "none",
    )
