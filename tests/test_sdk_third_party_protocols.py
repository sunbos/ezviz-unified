"""Synthetic contract tests for the SDK third-party protocol facade.

Every request is answered by ``httpx.MockTransport`` handlers owned by
:class:`Probe`; all payloads are synthetic and no real device, account or
host is ever contacted.  Sibling suites reuse the harness via
``from test_sdk_third_party_protocols import Probe``.
"""

import asyncio
from collections import deque

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

APP_KEY = "DEMOAPP"
APP_SECRET = "DEMOSECRET"
AREA_DOMAIN = "https://open.ys7.com"
PROTOCOLS = ("dhreg", "tiandy", "huawei", "jt808")
MODES = ("sync", "async")


PROJECT = {
    "dasDomain": "das.synthetic.invalid",
    "dasPort": 8000,
    "productKey": "SYNTH-PRODUCT",
    "createTime": 7,
    "vendorExtension": "keep-me",
}
ROWS = [
    {"deviceSerial": "SYNTH-A", "registrationSecret": "synthetic"},
    {"deviceSerial": "SYNTH-B"},
]


def _ok(data):
    return {"meta": {"code": 200, "message": "ok"}, "data": data}


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
                return getattr(client.third_party_protocols, name)(*args, **kwargs)
        if mode == "async":

            async def runner():
                async with AsyncClient(APP_KEY, APP_SECRET, **options) as client:
                    return await getattr(client.third_party_protocols, name)(*args, **kwargs)

            return asyncio.run(runner())
        raise ValueError("unknown mode: %r" % (mode,))


def _query(request):
    return sorted(request.url.params.multi_items())


def _has_token(request, token):
    return request.headers.get_list("accessToken") == [token]


def _check(request, method, path, query):
    assert request.method == method
    assert request.url.scheme == "https" and request.url.host == "open.ys7.com"
    assert request.url.path == path
    assert _query(request) == sorted(query)
    assert _has_token(request, "TOK1")
    assert b"TOK1" not in request.url.query and b"TOK1" not in request.content


def _events(probe, name, kind, mode=None):
    return [
        event["outcome"]
        for event in probe.recorder.report()["events"]
        if event.get("kind") == kind
        and event.get("entrypoint")
        == ("client.request" if kind == "attempt" else "third_party_protocols." + name)
        and (kind != "attempt" or event.get("operation") == "third_party_protocols." + name)
        and (mode is None or event.get("mode") == mode)
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_open_project_posts_empty_form(mode, protocol):
    probe = Probe(_ok({"createTime": 7, "productKey": "SYNTH-PRODUCT"}))
    response = probe.call(mode, "open_project", protocol)
    assert response.code == 200
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    request = probe.biz[0]
    _check(request, "POST", "/api/service/thirdproto/%s/project" % protocol, [])
    assert request.content == b""
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"


@pytest.mark.parametrize(
    "bad",
    [
        "overseas",
        "EHOME",
        "unknown",
        "",
        "ehome",
        "gb28181",
        "onvif",
        ["tiandy"],
        {"p": 1},
        None,
        3,
    ],
    ids=[
        "overseas",
        "upper-ehome",
        "unknown",
        "empty",
        "invalid-ehome",
        "invalid-gb28181",
        "invalid-onvif",
        "list",
        "dict",
        "none",
        "int",
    ],
)
def test_protocol_rejected_before_auth(bad):
    probe = Probe(_ok({}))
    with pytest.raises(ValueError):
        probe.call("sync", "open_project", bad)
    assert probe.auth == [] and probe.biz == []


def test_invalid_protocol_marks_call_failed():
    probe = Probe(_ok({}))
    with pytest.raises(ValueError):
        probe.call("sync", "open_project", "unknown")
    assert _events(probe, "open_project", "call", "sync") == ["started", "failed"]
    assert _events(probe, "open_project", "attempt") == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("port", [8000, "8000"], ids=["int-port", "str-port"])
def test_get_project_preserves_payload(mode, port):
    project = dict(PROJECT, dasPort=port)
    probe = Probe(_ok(project))
    response = probe.call(mode, "get_project", "tiandy")
    assert len(probe.biz) == 1  # only the explicitly invoked request, never the returned address
    request = probe.biz[0]
    _check(request, "GET", "/api/service/thirdproto/tiandy/project", [])
    assert not request.content and "content-type" not in request.headers
    assert response.data == project
    assert type(response.data["dasPort"]) is type(port)


def test_get_project_explicit_region():
    probe = Probe(_ok(dict(PROJECT)))
    probe.call("sync", "get_project", "tiandy", region=RegionConfig.china())
    assert probe.biz[0].url.host == "open.ys7.com"


@pytest.mark.parametrize(
    "data",
    [
        {"dasPort": 8000, "productKey": "PK", "createTime": 7},
        {"dasDomain": "das.synthetic.invalid", "productKey": "PK", "createTime": 7},
    ],
    ids=["missing-dasDomain", "missing-port"],
)
def test_get_project_requires_das_domain_and_port(data):
    probe = Probe(_ok(data))
    with pytest.raises(ProtocolError):
        probe.call("sync", "get_project", "tiandy")
    assert len(probe.biz) == 1


@pytest.mark.parametrize("mode", MODES)
def test_get_task_sends_decimal_task_id(mode):
    probe = Probe(_ok({"taskId": 42, "status": 9, "deviceCount": 3, "createTime": 1700000000}))
    response = probe.call(mode, "get_task", "tiandy", 42)
    _check(probe.biz[0], "GET", "/api/service/thirdproto/tiandy/licenses/task", [("taskId", "42")])
    assert response.data["taskId"] == 42


@pytest.mark.parametrize(
    "data",
    [
        {"taskId": 1, "status": 9, "deviceCount": 5, "createTime": 1700000000, "ext": "a"},
        {"taskId": 2, "status": 10, "deviceCount": 0, "createTime": 1700000001},
        {"taskId": 3, "status": 10, "deviceCount": 4, "createTime": 1700000002},
        {"taskId": 4, "status": 77, "deviceCount": 0, "createTime": 1700000003, "mystery": True},
    ],
    ids=["all-success", "all-failed", "partial", "unknown-status"],
)
def test_get_task_preserves_status_and_extensions(data):
    probe = Probe(_ok(data))
    response = probe.call("sync", "get_task", "tiandy", 7)
    assert response.data == data
    assert _query(probe.biz[0]) == [("taskId", "7")]


@pytest.mark.parametrize("bad", [True, False])
def test_get_task_rejects_bool_task_id(bad):
    probe = Probe(_ok({}))
    with pytest.raises(ValueError):
        probe.call("sync", "get_task", "tiandy", bad)
    assert probe.auth == [] and probe.biz == []


@pytest.mark.parametrize("mode", MODES)
def test_list_devices_single_page(mode):
    probe = Probe(_ok({"data": ROWS, "nextIndex": 37, "extra": "x"}))
    response = probe.call(mode, "list_devices", "jt808")
    _check(
        probe.biz[0],
        "GET",
        "/api/service/thirdproto/jt808/devices/page",
        [("pageSize", "10"), ("startIndex", "0")],
    )
    assert response.data["data"] == ROWS and response.data["nextIndex"] == 37
    assert len(probe.biz) == 1  # a single call never triggers additional HTTP


def test_list_devices_serial_and_task_coexist():
    probe = Probe(_ok({"data": ROWS, "nextIndex": None}))
    response = probe.call(
        "sync",
        "list_devices",
        "huawei",
        device_serial="SYNTH-A",
        task_id=9,
        start_index=4,
        page_size=50,
    )
    _check(
        probe.biz[0],
        "GET",
        "/api/service/thirdproto/huawei/devices/page",
        [("deviceSerial", "SYNTH-A"), ("pageSize", "50"), ("startIndex", "4"), ("taskId", "9")],
    )
    assert response.data["nextIndex"] is None


def test_list_devices_manual_paging_uses_platform_next_index():
    probe = Probe(
        _ok({"data": ROWS[:1], "nextIndex": 37}), _ok({"data": ROWS[1:], "nextIndex": None})
    )
    first = probe.call("sync", "list_devices", "tiandy")
    second = probe.call("sync", "list_devices", "tiandy", start_index=first.data["nextIndex"])
    assert _query(probe.biz[1]) == [("pageSize", "10"), ("startIndex", "37")]
    assert second.data["data"] == ROWS[1:] and len(probe.biz) == 2


@pytest.mark.parametrize(
    "data",
    [ROWS, {"data": {"oops": 1}}, {"data": True}],
    ids=["rows-at-root", "rows-object", "rows-bool"],
)
def test_list_devices_rejects_misplaced_rows(data):
    probe = Probe(_ok(data))
    with pytest.raises(ProtocolError):
        probe.call("sync", "list_devices", "tiandy")
    assert len(probe.biz) == 1


def test_10002_triggers_single_refresh():
    probe = Probe({"meta": {"code": 10002, "message": "expired"}, "data": None}, _ok(dict(PROJECT)))
    response = probe.call("sync", "get_project", "tiandy")
    assert response.code == 200
    assert len(probe.auth) == 2 and len(probe.biz) == 2
    assert _has_token(probe.biz[1], "TOK2")
    assert _events(probe, "get_project", "call", "sync") == ["started", "succeeded"]
    assert _events(probe, "get_project", "attempt") == ["started", "failed", "started", "succeeded"]


def test_10002_exhausted_raises_authentication_error():
    stale = {"meta": {"code": 10002, "message": "expired"}, "data": None}
    probe = Probe(stale, stale)
    with pytest.raises(AuthenticationError):
        probe.call("sync", "get_project", "tiandy")
    assert len(probe.auth) == 2 and len(probe.biz) == 2


def test_10031_is_permission_denied_single_attempt():
    probe = Probe({"meta": {"code": 10031, "message": "denied"}, "data": None})
    with pytest.raises(PermissionDeniedError):
        probe.call("sync", "get_project", "tiandy")
    assert len(probe.biz) == 1
    assert _events(probe, "get_project", "call", "sync") == ["started", "failed"]


def test_http_403_is_permission_denied():
    probe = Probe(
        httpx.Response(403, json={"meta": {"code": 10031, "message": "denied"}, "data": None})
    )
    with pytest.raises(PermissionDeniedError):
        probe.call("sync", "get_project", "tiandy")
    assert len(probe.biz) == 1


def test_integer_403_business_code_is_not_permission_denied():
    probe = Probe({"meta": {"code": 403, "message": "odd"}, "data": None})
    with pytest.raises(ApiError) as info:
        probe.call("sync", "get_project", "tiandy")
    assert not isinstance(info.value, PermissionDeniedError)


def test_string_meta_code_is_api_error():
    probe = Probe({"meta": {"code": "200", "message": "ok"}, "data": {}})
    with pytest.raises(ApiError) as info:
        probe.call("sync", "get_project", "tiandy")
    assert not isinstance(info.value, (AuthenticationError, PermissionDeniedError, ProtocolError))


def test_missing_meta_code_is_protocol_error():
    probe = Probe({"meta": {"message": "no code"}, "data": {}})
    with pytest.raises(ProtocolError):
        probe.call("sync", "get_project", "tiandy")


def test_timeout_is_transport_error_single_attempt():
    probe = Probe(httpx.ConnectTimeout("synthetic connect timeout"))
    with pytest.raises(TransportError) as info:
        probe.call("sync", "get_project", "tiandy")
    assert info.value.reason == "timeout"
    assert len(probe.biz) == 1


def test_async_cancellation_propagates_and_marks_cancelled():
    probe = Probe(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        probe.call("async", "get_project", "tiandy")
    assert len(probe.biz) == 1
    assert _events(probe, "get_project", "call", "async") == ["started", "cancelled"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"meta": {"code": 200, "message": "ok"}, "data": True}, id="bool-data"),
        pytest.param({"meta": {"code": 200, "message": "ok"}, "data": [1, 2]}, id="list-data"),
        pytest.param({"meta": {"code": 200, "message": "ok"}, "data": "oops"}, id="string-data"),
        pytest.param({"wrong": "envelope"}, id="wrong-envelope"),
        pytest.param([1, 2, 3], id="list-envelope"),
        pytest.param(True, id="bool-envelope"),
    ],
)
def test_malformed_payload_is_protocol_error(payload):
    probe = Probe(payload)
    with pytest.raises(ProtocolError) as info:
        probe.call("sync", "get_project", "tiandy")
    assert info.value.code is None and len(probe.biz) == 1
    assert _events(probe, "get_project", "call", "sync") == ["started", "failed"]
    meta = payload.get("meta") if isinstance(payload, dict) else None
    expected_attempt = (
        ["started", "succeeded"]
        if isinstance(meta, dict) and meta.get("code") == 200
        else ["started", "failed"]
    )
    assert _events(probe, "get_project", "attempt") == expected_attempt


@pytest.mark.parametrize("mode", MODES)
def test_diagnostics_success(mode):
    probe = Probe(_ok(dict(PROJECT)))
    probe.call(mode, "get_project", "tiandy")
    assert _events(probe, "get_project", "call", mode) == ["started", "succeeded"]
    assert _events(probe, "get_project", "attempt") == ["started", "succeeded"]


def test_exhausted_steps_raise_assertion():
    probe = Probe()
    with pytest.raises(AssertionError):
        probe.call("sync", "get_project", "tiandy")
    assert len(probe.auth) == 1 and len(probe.biz) == 1
