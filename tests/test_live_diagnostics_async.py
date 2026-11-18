import asyncio
import getpass
import io
import json
import stat
import sys
import warnings
from urllib.parse import parse_qs

import httpx
import pytest

from tools.live import verify_diagnostics, verify_diagnostics_async

_SENTINELS = ("C12345678", "PRIVATE_SECRET", "TEST_APP_KEY", "SYNTHETIC_TOKEN")
_EXPECTED_ORDER = [
    ("POST", "/api/lapp/token/get"),
    ("POST", "/api/lapp/device/list"),
    ("POST", "/api/lapp/device/info"),
    ("POST", "/api/lapp/device/camera/list"),
    ("POST", "/api/lapp/device/status/get"),
    ("POST", "/api/lapp/device/capacity"),
    ("POST", "/api/lapp/device/version/info"),
]


def _sdk_response(data):
    return httpx.Response(200, json={"code": "200", "msg": "Operation succeeded", "data": data})


def _recording_handler(rows, *, fail_path=None, fail_code="10017"):
    state = {"requests": [], "forms": []}

    async def handle(request):
        state["requests"].append(request)
        state["forms"].append(parse_qs((await request.aread()).decode("utf-8")))
        if (request.method, request.url.path) == ("POST", "/api/lapp/token/get"):
            return _sdk_response({"accessToken": "SYNTHETIC_TOKEN", "expireTime": 4102444800000})
        if request.url.path == fail_path:
            return httpx.Response(200, json={"code": fail_code, "msg": "PRIVATE_REASON"})
        if request.url.path == "/api/lapp/device/list":
            return _sdk_response(rows)
        return _sdk_response({})

    return state, handle


def _run_verify(monkeypatch, rows, **kwargs):
    state, handle = _recording_handler(rows, **kwargs)
    mock = httpx.MockTransport(handle)
    monkeypatch.setattr(verify_diagnostics_async, "_new_delegate_transport", lambda: mock)
    result = asyncio.run(
        verify_diagnostics_async.verify({"app_key": "TEST_APP_KEY", "app_secret": "PRIVATE_SECRET"})
    )
    return state, result


def _capture_reports(monkeypatch):
    """Wrap ``DiagnosticRecorder.report`` to keep each real snapshot."""
    reports = []
    original = verify_diagnostics.DiagnosticRecorder.report

    def patched(self):
        report = original(self)
        reports.append(report)
        return report

    monkeypatch.setattr(verify_diagnostics.DiagnosticRecorder, "report", patched)
    return reports


def _counting_delegate(seen):
    async def handle(request):
        seen.append(request)
        return httpx.Response(200)

    return httpx.MockTransport(handle)


def _new_trace():
    return []


def _legit_fields(path):
    """The exact legal field contract for one allowlisted route."""
    serial = "C12345678"
    return {
        "/api/lapp/token/get": {"appKey": "K", "appSecret": "S"},
        "/api/lapp/device/list": {"accessToken": "T", "pageStart": "0", "pageSize": "1"},
        "/api/lapp/device/info": {"accessToken": "T", "deviceSerial": serial},
        "/api/lapp/device/camera/list": {"accessToken": "T", "deviceSerial": serial},
        "/api/lapp/device/status/get": {
            "accessToken": "T",
            "deviceSerial": serial,
            "channel": "1",
        },
        "/api/lapp/device/capacity": {
            "accessToken": "T",
            "deviceSerial": serial,
            "channelNo": "1",
        },
        "/api/lapp/device/version/info": {"accessToken": "T", "deviceSerial": serial},
    }[path]


async def _admit_prefix(client, paths):
    """Run the legal ordered prefix so the next route reaches field checks."""
    for path in paths:
        await client.post("https://open.ys7.com" + path, data=_legit_fields(path))


def _prefix_paths(path):
    ordered = [route_path for _method, route_path in verify_diagnostics_async._ROUTE_ORDER]
    return ordered[: ordered.index(path)]


def _bound_budget(serial="C12345678"):
    budget = verify_diagnostics._RequestBudget()
    budget.bind_device(serial)
    return budget


def test_full_run_sends_seven_bounded_requests_and_binds_one_serial(monkeypatch):
    reports = _capture_reports(monkeypatch)
    state, result = _run_verify(monkeypatch, [{"deviceSerial": "C12345678"}])
    assert [(request.method, request.url.path) for request in state["requests"]] == _EXPECTED_ORDER
    assert all(
        (request.url.scheme, request.url.host, request.url.port or 443)
        == ("https", "open.ys7.com", 443)
        for request in state["requests"]
    )
    assert set(state["forms"][0]) == {"appKey", "appSecret"}
    list_form = state["forms"][1]
    assert set(list_form) == {"accessToken", "pageStart", "pageSize"}
    assert list_form["pageStart"] == ["0"] and list_form["pageSize"] == ["1"]
    for index in (2, 3, 6):
        assert set(state["forms"][index]) == {"accessToken", "deviceSerial"}
    assert set(state["forms"][4]) == {"accessToken", "deviceSerial", "channel"}
    assert set(state["forms"][5]) == {"accessToken", "deviceSerial", "channelNo"}
    assert state["forms"][4]["channel"] == ["1"]
    assert state["forms"][5]["channelNo"] == ["1"]
    assert [form["deviceSerial"] for form in state["forms"][2:]] == [["C12345678"]] * 5
    assert set(result) == {
        "schema_version",
        "scope",
        "outcome",
        "mode",
        "read_only",
        "http_requests",
        "logical_calls",
        "attempted_routes",
        "successful_steps",
        "diagnostics_intact",
        "closed",
        "boundary_rejected",
    }
    assert result["schema_version"] == 1
    assert result["scope"] == "domestic_async_device_inspection"
    assert result["outcome"] == "passed"
    assert result["mode"] == "async"
    assert result["read_only"] is True
    assert result["http_requests"] == 7
    assert result["logical_calls"] == 6
    assert result["attempted_routes"] == [
        "auth",
        "list",
        "info",
        "channels",
        "status",
        "capabilities",
        "version",
    ]
    assert result["successful_steps"] == [
        "list",
        "info",
        "channels",
        "status",
        "capabilities",
        "version",
    ]
    assert result["diagnostics_intact"] is True
    assert result["closed"] is True
    assert result["boundary_rejected"] is False
    assert reports, "verify must inspect the real recorder report"
    report = reports[-1]
    assert report["schema_version"] == 1 and report["scope"] == "local_sdk_diagnostics"
    assert report["recording_errors"] == 0 and report["dropped_events"] == 0
    events = report["events"]
    terminal_calls = [
        event
        for event in events
        if event.get("kind") == "call" and event.get("outcome") == "succeeded"
    ]
    # The six business facade entrypoints each record exactly one succeeded
    # terminal; inner ``client.request`` scopes are separate call events.
    business_entrypoints = {
        "devices.list",
        "devices.get",
        "devices.channels",
        "devices.status",
        "diagnostics.capabilities",
        "diagnostics.version",
    }
    assert {
        event["entrypoint"]
        for event in terminal_calls
        if event["entrypoint"] in business_entrypoints
    } == business_entrypoints
    assert any(event["entrypoint"] == "client.request" for event in terminal_calls)
    assert all(event["mode"] == "async" for event in events if event.get("mode"))
    assert {event["region"] for event in events} == {"domestic"}
    assert any(
        event.get("kind") == "inspection"
        and event.get("entrypoint") == "inspect_device_async"
        and event.get("outcome") == "succeeded"
        for event in events
    )
    assert any(
        event.get("kind") == "close" and event.get("entrypoint") == "client.aclose"
        for event in events
    )
    payload = json.dumps(result) + json.dumps(report)
    for sentinel in _SENTINELS:
        assert sentinel not in payload


@pytest.mark.parametrize(
    "rows", [[], [{}], [{"deviceSerial": "   "}], [{"deviceSerial": 7}], ["not-a-dict"]]
)
def test_missing_or_empty_device_list_is_not_verified(monkeypatch, rows):
    state, result = _run_verify(monkeypatch, rows)
    assert [(request.method, request.url.path) for request in state["requests"]] == [
        ("POST", "/api/lapp/token/get"),
        ("POST", "/api/lapp/device/list"),
    ]
    assert result["outcome"] == "not_verified"
    assert result["mode"] == "async" and result["read_only"] is True
    assert result["logical_calls"] == 1
    assert result["http_requests"] == 2
    # Only devices.list actually succeeded; no later step is claimed.
    assert result["successful_steps"] == ["list"]
    assert result["closed"] is True
    assert "PRIVATE" not in json.dumps(result)


def test_step_failure_reports_failed_with_whitelisted_output(monkeypatch):
    reports = _capture_reports(monkeypatch)
    _state, result = _run_verify(
        monkeypatch, [{"deviceSerial": "C12345678"}], fail_path="/api/lapp/device/capacity"
    )
    assert result["outcome"] == "failed"
    assert result["logical_calls"] == 6 and result["http_requests"] == 7
    # The failed capabilities step is honestly absent from successes while
    # the version step still ran and succeeded afterwards — the list is not
    # truncated at the first failure and never claims capabilities.
    assert result["successful_steps"] == ["list", "info", "channels", "status", "version"]
    assert "capabilities" not in result["successful_steps"]
    assert result["closed"] is True
    # The successor ``version`` step still ran and is honestly recorded: all
    # six business entrypoints produced exactly one terminal event, so the
    # truncated prefix is a projection choice, not a missing call.
    report = reports[-1]
    terminals = {}
    for event in report["events"]:
        if (
            event.get("kind") == "call"
            and event.get("outcome") in verify_diagnostics._TERMINAL_OUTCOMES
        ):
            terminals.setdefault(event.get("entrypoint"), []).append(event["outcome"])
    for entrypoint in (
        "devices.list",
        "devices.get",
        "devices.channels",
        "devices.status",
        "diagnostics.capabilities",
        "diagnostics.version",
    ):
        assert len(terminals.get(entrypoint, [])) == 1
    assert terminals["diagnostics.capabilities"] == ["failed"]
    assert terminals["diagnostics.version"] == ["succeeded"]
    text = json.dumps(result)
    assert "PRIVATE" not in text and "C12345678" not in text


def test_auth_failure_stops_before_any_business_request(monkeypatch):
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATE_AUTH"})

    monkeypatch.setattr(
        verify_diagnostics_async,
        "_new_delegate_transport",
        lambda: httpx.MockTransport(handle),
    )
    result = asyncio.run(verify_diagnostics_async.verify({"app_key": "K", "app_secret": "S"}))
    assert [request.url.path for request in seen] == ["/api/lapp/token/get"]
    assert result["outcome"] == "failed"
    assert result["http_requests"] == 1
    # The failed list call is recorded honestly — never as a full pass.
    assert result["logical_calls"] == 1
    assert result["successful_steps"] == []
    assert "PRIVATE" not in json.dumps(result)


def test_token_refresh_retry_is_refused_before_a_second_token_request(monkeypatch):
    seen = []

    async def handle(request):
        seen.append(request)
        if request.url.path == "/api/lapp/token/get":
            return _sdk_response({"accessToken": "SYNTHETIC_TOKEN", "expireTime": 4102444800000})
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATE_EXPIRED"})

    monkeypatch.setattr(
        verify_diagnostics_async,
        "_new_delegate_transport",
        lambda: httpx.MockTransport(handle),
    )
    result = asyncio.run(verify_diagnostics_async.verify({"app_key": "K", "app_secret": "S"}))
    assert len(seen) == 2
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert result["outcome"] == "failed"
    assert result["http_requests"] == 2
    assert result["boundary_rejected"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://open.ys7.com:8443/api/lapp/device/list",
        "http://open.ys7.com/api/lapp/device/list",
        "https://user:pw@open.ys7.com/api/lapp/device/list",
        "https://open.ys7.com/api/lapp/device/list?x=1",
        "https://open.ys7.com/api/lapp/device/list#frag",
        "https://euopen.ys7.com/api/lapp/device/list",
        "https://open.ys7.com.evil.example/api/lapp/device/list",
    ],
)
def test_guard_transport_rejects_non_default_origin_shapes(url):
    seen = []
    budget = verify_diagnostics._RequestBudget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(url, data={"accessToken": "T", "pageStart": "0", "pageSize": "1"})

    asyncio.run(probe())
    # Only the legal auth request reached the delegate; the bad origin did not.
    assert [request.url.path for request in seen] == ["/api/lapp/token/get"]
    assert trace == ["auth"]
    assert budget.total == 1 and budget.rejected == 1


@pytest.mark.parametrize(
    "path,fields",
    [
        ("/api/lapp/device/list", {"accessToken": "T", "pageStart": "1", "pageSize": "1"}),
        ("/api/lapp/device/list", {"accessToken": "T", "pageStart": "0", "pageSize": "2"}),
        ("/api/lapp/device/list", {"accessToken": "T", "pageStart": "0"}),
        ("/api/lapp/device/list", {"pageStart": "0", "pageSize": "1"}),
        (
            "/api/lapp/device/list",
            {"accessToken": "T", "pageStart": "0", "pageSize": "1", "page": "0"},
        ),
        ("/api/lapp/device/info", {"accessToken": "T", "deviceSerial": "WRONGSERIAL"}),
        ("/api/lapp/device/info", {"deviceSerial": "C12345678"}),
        ("/api/lapp/device/info", {"accessToken": " ", "deviceSerial": "C12345678"}),
        (
            "/api/lapp/device/info",
            {"accessToken": "T", "deviceSerial": "C12345678", "x": "1"},
        ),
        ("/api/lapp/device/camera/list", {"accessToken": "T"}),
        (
            "/api/lapp/device/status/get",
            {"accessToken": "T", "deviceSerial": "C12345678", "channel": "2"},
        ),
        (
            "/api/lapp/device/status/get",
            {"accessToken": "T", "deviceSerial": "C12345678"},
        ),
        (
            "/api/lapp/device/capacity",
            {"accessToken": "T", "deviceSerial": "C12345678", "channelNo": "9"},
        ),
        (
            "/api/lapp/device/capacity",
            {"accessToken": "T", "deviceSerial": "C12345678"},
        ),
        (
            "/api/lapp/device/version/info",
            {"accessToken": "T", "deviceSerial": "C12345678", "channel": "1"},
        ),
        ("/api/lapp/token/get", {"appKey": "K"}),
        ("/api/lapp/token/get", {"appKey": "K", "appSecret": "S", "accessToken": "T"}),
        ("/api/lapp/token/get", {"appKey": "K", "appSecret": " "}),
    ],
)
def test_guard_transport_rejects_out_of_contract_form_fields(path, fields):
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, _prefix_paths(path))
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post("https://open.ys7.com" + path, data=fields)

    asyncio.run(probe())
    # Only the legal prefix requests were delegated; the bad form was not.
    assert [request.url.path for request in seen] == [
        route_path for route_path in _prefix_paths(path)
    ]
    assert budget.total == len(_prefix_paths(path))
    assert budget.rejected == 1


@pytest.mark.parametrize(
    "body",
    [
        b"accessToken=T&accessToken=T&deviceSerial=C12345678",
        b"accessToken=T&deviceSerial",
        "accessToken=T&deviceSerial=C12345678".encode("utf-16"),
        b"accessToken=" + b"T" * 9000 + b"&deviceSerial=C12345678",
    ],
)
def test_guard_transport_rejects_malformed_duplicate_or_oversized_bodies(body):
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info",
                    content=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.rejected == 1


def test_guard_transport_rejects_non_form_or_missing_bodies():
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post("https://open.ys7.com/api/lapp/device/info")
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info",
                    json={"accessToken": "T", "deviceSerial": "C12345678"},
                )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.rejected == 2


def test_guard_rejects_device_routes_until_a_serial_is_bound():
    seen = []
    budget = verify_diagnostics._RequestBudget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )
    fields = {"accessToken": "T", "deviceSerial": "C12345678"}

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post("https://open.ys7.com/api/lapp/device/info", data=fields)

    asyncio.run(probe())
    # auth+list were delegated; the unbound device/info call was rejected.
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.total == 2 and budget.rejected == 1
    budget.bind_device("C12345678")

    async def allowed():
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post("https://open.ys7.com/api/lapp/device/info", data=fields)

    asyncio.run(allowed())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
        "/api/lapp/device/info",
    ]
    assert trace == ["auth", "list", "info"]
    assert budget.total == 3


def test_guard_transport_rejects_out_of_scope_requests_before_delegating():
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )
    list_fields = {"accessToken": "T", "pageStart": "0", "pageSize": "1"}

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            # Out-of-scope paths and wrong methods are rejected before ordering.
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/encrypt/off",
                    data={"accessToken": "T", "deviceSerial": "C12345678"},
                )
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.put(
                    "https://open.ys7.com/api/v3/device/format/disk",
                    data={"accessToken": "T"},
                )
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.get("https://open.ys7.com/api/lapp/device/list")
            # Admit the legal auth prefix so the list route is the next step.
            await client.post(
                "https://open.ys7.com/api/lapp/token/get",
                data={"appKey": "K", "appSecret": "S"},
            )
            await client.post("https://open.ys7.com/api/lapp/device/list", data=list_fields)
            # A duplicate of the same route is rejected by the shared budget.
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post("https://open.ys7.com/api/lapp/device/list", data=list_fields)

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert trace == ["auth", "list"]
    assert budget.total == 2
    # Three boundary rejections plus the duplicate list, which admit()
    # counts exactly once.
    assert budget.rejected == 4


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"app_key": "K"},
        {"app_secret": "S"},
        {"app_key": "  ", "app_secret": "S"},
        {"app_key": "K", "app_secret": "S", "region": "overseas"},
        {"app_key": "K", "app_secret": "S", "inspection": "true"},
        {"app_key": "K", "app_secret": 7},
    ],
)
def test_config_is_rejected_before_client_construction(monkeypatch, config):
    def forbidden(*args, **kwargs):
        raise AssertionError("AsyncClient must not be constructed for rejected config")

    monkeypatch.setattr(verify_diagnostics_async, "AsyncClient", forbidden)
    with pytest.raises(ValueError):
        asyncio.run(verify_diagnostics_async.verify(config))


@pytest.mark.parametrize("mutate", [{"recording_errors": 1}, {"dropped_events": 2}])
def test_unhealthy_report_prevents_a_passed_projection(monkeypatch, mutate):
    _state, handle = _recording_handler([{"deviceSerial": "C12345678"}])
    monkeypatch.setattr(
        verify_diagnostics_async,
        "_new_delegate_transport",
        lambda: httpx.MockTransport(handle),
    )
    original = verify_diagnostics.DiagnosticRecorder.report

    def patched(self):
        report = original(self)
        if isinstance(report, dict):
            report = dict(report)
            report.update(mutate)
        return report

    monkeypatch.setattr(verify_diagnostics.DiagnosticRecorder, "report", patched)
    result = asyncio.run(verify_diagnostics_async.verify({"app_key": "K", "app_secret": "S"}))
    assert result["outcome"] == "failed"
    assert result["diagnostics_intact"] is False
    assert result["logical_calls"] == 6
    assert result["http_requests"] == 7


def test_cancellation_closes_client_and_never_passes(monkeypatch):
    seen = []
    request_received = asyncio.Event()
    release_response = asyncio.Event()

    async def handle(request):
        seen.append(request)
        if request.url.path == "/api/lapp/token/get":
            return _sdk_response({"accessToken": "SYNTHETIC_TOKEN", "expireTime": 4102444800000})
        # Signal that the business request really arrived, then hang until
        # the caller releases (or the task is cancelled) — no fixed sleep.
        request_received.set()
        await release_response.wait()
        return _sdk_response([])

    monkeypatch.setattr(
        verify_diagnostics_async,
        "_new_delegate_transport",
        lambda: httpx.MockTransport(handle),
    )

    real_client_cls = verify_diagnostics_async.AsyncClient
    clients = []

    class _TrackedClient(real_client_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clients.append(self)

    monkeypatch.setattr(verify_diagnostics_async, "AsyncClient", _TrackedClient)

    async def run():
        task = asyncio.create_task(
            verify_diagnostics_async.verify({"app_key": "K", "app_secret": "S"})
        )
        # Cancel only once the business request has truly been received.
        await asyncio.wait_for(request_received.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release_response.set()

    asyncio.run(run())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    # Cancellation still ran the real cleanup path: the client was closed.
    assert clients and clients[0].is_closed


def test_guard_transport_rejects_forwarded_auth_headers_and_query_credentials():
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info",
                    data={"accessToken": "T", "deviceSerial": "C12345678"},
                    headers={"Authorization": "Bearer PRIVATE_TOKEN"},
                )
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info?accessToken=T",
                    data={"accessToken": "T", "deviceSerial": "C12345678"},
                )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.rejected == 2


def test_guard_rejects_out_of_order_route_before_delegating():
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            # device/info is a legal route but out of the fixed order: the
            # guard expects token/get next, so it rejects before delegating.
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info",
                    data=_legit_fields("/api/lapp/device/info"),
                )
            # The next-in-order auth request is still admitted afterwards.
            await client.post(
                "https://open.ys7.com/api/lapp/token/get",
                data=_legit_fields("/api/lapp/token/get"),
            )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == ["/api/lapp/token/get"]
    assert trace == ["auth"]
    assert budget.total == 1 and budget.rejected == 1


def test_guard_transport_refuses_a_dict_shaped_trace_at_construction():
    # The order cursor must be a real shared list; a dict-shaped fixture
    # that once slipped through is refused before any request can run.
    budget = _bound_budget()
    with pytest.raises(ValueError):
        verify_diagnostics_async._GuardedAsyncTransport(
            _counting_delegate([]), budget, {"routes": []}
        )


def test_guard_rejects_out_of_order_legal_list_trace_before_delegating():
    # A genuine list trace stays the order cursor: skipping ``channels``
    # for ``status`` is refused even though both fields and order-list
    # membership are legal, and nothing reaches the delegate.
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(
                client,
                [
                    "/api/lapp/token/get",
                    "/api/lapp/device/list",
                    "/api/lapp/device/info",
                ],
            )
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/status/get",
                    data=_legit_fields("/api/lapp/device/status/get"),
                )
            # The real next-in-order route is still admitted afterwards.
            await client.post(
                "https://open.ys7.com/api/lapp/device/camera/list",
                data=_legit_fields("/api/lapp/device/camera/list"),
            )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
        "/api/lapp/device/info",
        "/api/lapp/device/camera/list",
    ]
    assert trace == ["auth", "list", "info", "channels"]
    assert budget.total == 4 and budget.rejected == 1


def test_guard_rejects_unbuffered_stream_body_without_iterating():
    seen = []
    iterated = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    class _UnconsumedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            iterated.append(True)
            yield b"accessToken=T&deviceSerial=x"

    async def admit(path, fields):
        request = httpx.Request("POST", "https://open.ys7.com" + path, data=fields)
        return await transport.handle_async_request(request)

    async def probe():
        # Admit the legal auth+list prefix so ``info`` is the next-in-order
        # route for the shared trace cursor.
        await admit("/api/lapp/token/get", _legit_fields("/api/lapp/token/get"))
        await admit("/api/lapp/device/list", _legit_fields("/api/lapp/device/list"))
        # A genuinely unbuffered async body: an explicit ``stream=`` object is
        # never read by ``httpx.Request``, so ``request.content`` raises
        # ``RequestNotRead``. A legal Content-Length, the exact form MIME and
        # no Transfer-Encoding isolate the unbuffered body as the sole defect.
        request = httpx.Request(
            "POST",
            "https://open.ys7.com/api/lapp/device/info",
            stream=_UnconsumedBody(),
            headers={
                "Host": "open.ys7.com",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "28",
            },
        )
        assert "Transfer-Encoding" not in request.headers
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            await transport.handle_async_request(request)

    asyncio.run(probe())
    # The guard must refuse an unbuffered body without ever consuming it.
    assert iterated == []
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert trace == ["auth", "list"]
    assert budget.total == 2
    assert budget.rejected == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        {"Proxy-Authorization": "Basic PRIVATE"},
        {"Host": "evil.example"},
    ],
)
def test_guard_rejects_bad_content_type_suffix_proxy_and_host(headers):
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info",
                    data=_legit_fields("/api/lapp/device/info"),
                    headers=headers,
                )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.rejected == 1


def test_guard_rejects_duplicate_content_type_header():
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info",
                    headers=[
                        ("Content-Type", "application/x-www-form-urlencoded"),
                        ("Content-Type", "application/x-www-form-urlencoded"),
                    ],
                    content=b"accessToken=T&deviceSerial=C12345678",
                )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.rejected == 1


def test_guard_rejects_request_query():
    seen = []
    budget = _bound_budget()
    trace = _new_trace()
    transport = verify_diagnostics_async._GuardedAsyncTransport(
        _counting_delegate(seen), budget, trace
    )

    async def probe():
        async with httpx.AsyncClient(transport=transport) as client:
            await _admit_prefix(client, ["/api/lapp/token/get", "/api/lapp/device/list"])
            with pytest.raises(verify_diagnostics._BoundaryRejection):
                await client.post(
                    "https://open.ys7.com/api/lapp/device/info?foo=bar",
                    data=_legit_fields("/api/lapp/device/info"),
                )

    asyncio.run(probe())
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert budget.rejected == 1


def _passed_verify_result(**overrides):
    """The full 12-field allowlisted projection a real ``verify`` returns."""
    result = {
        "schema_version": 1,
        "scope": "domestic_async_device_inspection",
        "outcome": "passed",
        "mode": "async",
        "read_only": True,
        "http_requests": 7,
        "logical_calls": 6,
        "attempted_routes": [
            "auth",
            "list",
            "info",
            "channels",
            "status",
            "capabilities",
            "version",
        ],
        "successful_steps": [
            "list",
            "info",
            "channels",
            "status",
            "capabilities",
            "version",
        ],
        "diagnostics_intact": True,
        "closed": True,
        "boundary_rejected": False,
    }
    result.update(overrides)
    return result


def _run_prompt(monkeypatch, argv, prompts=None, verify_result=None):
    """Drive ``main`` with a TTY stdin/stdout and an optional stubbed verify.

    ``sys.stdin`` is replaced by an ``isatty()``-True text stream (only its
    TTY flag is consulted). ``sys.stdout`` keeps the object pytest's capsys
    installed — only its ``isatty`` is flipped to True — so anything ``main``
    writes still lands in capsys. Both real ``isatty`` gates stay exercised.
    """
    entered = []
    calls = []
    prompts = list(prompts if prompts is not None else ["TEST_APP_KEY", "PRIVATE_SECRET"])
    answers = iter(prompts)

    def fake_getpass(prompt=""):
        entered.append(prompt)
        return next(answers)

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    class _TtyStdout(io.TextIOBase):
        """Forward writes to capsys's stdout while reporting a real TTY."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def isatty(self):
            return True

        def write(self, s):
            return self._wrapped.write(s)

        def flush(self):
            return self._wrapped.flush()

    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _TtyStdout(sys.stdout))

    if verify_result is not None:

        async def fake_verify(config):
            calls.append(dict(config))
            return verify_result

        monkeypatch.setattr(verify_diagnostics_async, "verify", fake_verify)
    code = verify_diagnostics_async.main(argv)
    return code, entered, calls


def test_prompt_mode_runs_verify_and_prints_only_safe_json(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    code, entered, calls = _run_prompt(
        monkeypatch,
        ["--prompt"],
        verify_result=_passed_verify_result(),
    )
    assert code == 0
    assert len(entered) == 2
    assert calls == [{"app_key": "TEST_APP_KEY", "app_secret": "PRIVATE_SECRET"}]
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out) == _passed_verify_result()
    assert set(json.loads(out)) == {
        "schema_version",
        "scope",
        "outcome",
        "mode",
        "read_only",
        "http_requests",
        "logical_calls",
        "attempted_routes",
        "successful_steps",
        "diagnostics_intact",
        "closed",
        "boundary_rejected",
    }
    assert not list(tmp_path.iterdir())
    for sentinel in _SENTINELS:
        assert sentinel not in out and sentinel not in err


def test_prompt_mode_returns_nonzero_when_failed(monkeypatch, capsys):
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt"],
        verify_result=_passed_verify_result(outcome="failed"),
    )
    assert code == 1
    out, _err = capsys.readouterr()
    assert json.loads(out)["outcome"] == "failed"


def test_prompt_mode_returns_nonzero_when_not_verified(monkeypatch, capsys):
    # An honest not_verified receipt: only auth+list ran, the list step
    # succeeded, the client closed cleanly and nothing hit the boundary.
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt"],
        verify_result=_passed_verify_result(
            outcome="not_verified",
            http_requests=2,
            logical_calls=1,
            attempted_routes=["auth", "list"],
            successful_steps=["list"],
            diagnostics_intact=False,
            closed=True,
        ),
    )
    assert code == 1
    out, _err = capsys.readouterr()
    assert json.loads(out)["outcome"] == "not_verified"


def test_prompt_requires_tty_and_never_getpasses(monkeypatch, capsys):
    class _NoTty(io.StringIO):
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdin", _NoTty())

    def forbidden(*args, **kwargs):
        raise AssertionError("getpass must not run without a TTY")

    monkeypatch.setattr(getpass, "getpass", forbidden)
    code = verify_diagnostics_async.main(["--prompt"])
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err


def test_prompt_getpass_fallback_aborts_before_verify(monkeypatch, capsys):
    class _Tty(io.StringIO):
        def isatty(self):
            return True

    class _TtyStdout(io.TextIOBase):
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def isatty(self):
            return True

        def write(self, s):
            return self._wrapped.write(s)

        def flush(self):
            return self._wrapped.flush()

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _TtyStdout(sys.stdout))

    def echo_getpass(prompt=""):
        # A real getpass fallback warns; the verifier turns warnings into errors.
        warnings.warn("PRIVATE_FALLBACK", getpass.GetPassWarning, stacklevel=2)
        return "TEST_APP_KEY"

    monkeypatch.setattr(getpass, "getpass", echo_getpass)

    def forbidden(*args, **kwargs):
        raise AssertionError("verify must not run after a getpass fallback")

    monkeypatch.setattr(verify_diagnostics_async, "verify", forbidden)
    code = verify_diagnostics_async.main(["--prompt"])
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err


@pytest.mark.parametrize("prompts", [["", "S"], ["K", ""]])
def test_prompt_empty_inputs_abort_before_verify(monkeypatch, capsys, prompts):
    def forbidden(*args, **kwargs):
        raise AssertionError("verify must not run on empty credentials")

    monkeypatch.setattr(verify_diagnostics_async, "verify", forbidden)
    code, _entered, _calls = _run_prompt(monkeypatch, ["--prompt"], prompts=prompts)
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err


def test_output_refuses_existing_file_before_networking(monkeypatch, tmp_path, capsys):
    destination = tmp_path / "report.json"
    destination.write_text("{}", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("verify must not run when --output already exists")

    monkeypatch.setattr(verify_diagnostics_async, "verify", forbidden)
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=None,
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err
    assert destination.read_text(encoding="utf-8") == "{}"


def test_output_refuses_symlink_and_default_run_writes_nothing(monkeypatch, tmp_path, capsys):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    def forbidden(*args, **kwargs):
        raise AssertionError("verify must not run when --output is a symlink")

    monkeypatch.setattr(verify_diagnostics_async, "verify", forbidden)
    code, _entered, _calls = _run_prompt(
        monkeypatch, ["--prompt", "--output", str(link)], verify_result=None
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err
    assert target.read_text(encoding="utf-8") == "{}"
    assert link.is_symlink()
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt"],
        verify_result=_passed_verify_result(outcome="failed"),
    )
    assert code == 1
    assert [p.name for p in tmp_path.iterdir()] == ["target.json", "link.json"]


def test_output_writes_allowlisted_report_on_success(monkeypatch, tmp_path):
    destination = tmp_path / "report.json"
    code, _entered, calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=_passed_verify_result(),
    )
    assert code == 0 and calls
    assert json.loads(destination.read_text(encoding="utf-8")) == _passed_verify_result()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    payload = destination.read_text(encoding="utf-8")
    for sentinel in _SENTINELS:
        assert sentinel not in payload


def test_prompt_failure_prints_fixed_stderr_without_secrets(monkeypatch, capsys):
    async def explode(config):
        raise RuntimeError("PRIVATE_DETAIL")

    monkeypatch.setattr(verify_diagnostics_async, "verify", explode)
    code, _entered, _calls = _run_prompt(monkeypatch, ["--prompt"])
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() and "Traceback" not in err and "PRIVATE" not in err


@pytest.mark.parametrize(
    "forged",
    [
        # A partial payload that only *claims* a pass.
        {"outcome": "passed", "mode": "async"},
        # Full 12 keys but passed with no real close / intact diagnostics.
        _passed_verify_result(closed=False),
        _passed_verify_result(diagnostics_intact=False),
        # An unknown extra key must also be refused, never serialized.
        _passed_verify_result(extra="PRIVATE_LEAK"),
    ],
)
def test_partial_or_forged_passed_is_never_serialized(monkeypatch, capsys, tmp_path, forged):
    """A verify payload that is not the strict whitelist never reaches stdout/file."""
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "report.json"
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=forged,
    )
    assert code == 1
    out, err = capsys.readouterr()
    # Nothing was printed and no artifact was left behind on refusal.
    assert out == ""
    assert not destination.exists()
    assert not list(tmp_path.iterdir())
    for sentinel in _SENTINELS:
        assert sentinel not in out + err


def test_invalid_argv_with_marker_is_not_echoed(monkeypatch, capsys):
    """A mistyped flag (possibly a pasted secret) must never echo to stderr."""
    code = verify_diagnostics_async.main(["--prompt", "--appSecret=PRIVATE_LEAKED"])
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE_LEAKED" not in out + err
    assert "--appSecret" not in out + err


@pytest.mark.parametrize("argv", [[], ["--output", "x.json"]])
def test_missing_prompt_flag_is_nonzero_usage_error(monkeypatch, capsys, argv):
    """Missing --prompt is a fixed usage error, never an exit-0 parse pass."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def forbidden(*args, **kwargs):
        raise AssertionError("getpass must not run without --prompt")

    monkeypatch.setattr(getpass, "getpass", forbidden)
    code = verify_diagnostics_async.main(argv)
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() and "PRIVATE" not in err
    assert "--prompt" not in out + err


def test_help_is_still_a_clean_systemexit(capsys):
    """``--help`` remains the only argparse path reaching stdout normally."""
    with pytest.raises(SystemExit) as excinfo:
        verify_diagnostics_async.main(["--help"])
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert "--prompt" in out


def test_output_write_error_fails_and_cleans_artifact(monkeypatch, tmp_path, capsys):
    """A stream write failure still fails and removes only this run's file."""
    destination = tmp_path / "report.json"

    class _FailingStream:
        def write(self, _s):
            raise OSError("PRIVATE_DISK")

        def flush(self):
            pass

        def close(self):
            pass

    real_open = verify_diagnostics_async._open_output_exclusive

    def wrapped_open(path):
        stream, identity = real_open(path)
        stream.close()
        return _FailingStream(), identity

    monkeypatch.setattr(verify_diagnostics_async, "_open_output_exclusive", wrapped_open)
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=_passed_verify_result(),
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err
    # The fresh artifact this run created is removed; nothing is left behind.
    assert not destination.exists()


def test_output_close_error_fails_and_leaves_no_false_passed(monkeypatch, tmp_path, capsys):
    """A close/flush fault on the output stream still yields a failure."""
    destination = tmp_path / "report.json"

    class _CloseFails:
        def write(self, s):
            return len(s)

        def flush(self):
            pass

        def close(self):
            raise OSError("PRIVATE_CLOSE")

    real_open = verify_diagnostics_async._open_output_exclusive

    def wrapped_open(path):
        stream, identity = real_open(path)
        stream.close()
        return _CloseFails(), identity

    monkeypatch.setattr(verify_diagnostics_async, "_open_output_exclusive", wrapped_open)
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=_passed_verify_result(),
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err
    assert not destination.exists()


def _verify_with_mutated_report(monkeypatch, mutate):
    """Run the bounded async flow but mutate the real report snapshot once."""
    _state, handle = _recording_handler([{"deviceSerial": "C12345678"}])
    monkeypatch.setattr(
        verify_diagnostics_async,
        "_new_delegate_transport",
        lambda: httpx.MockTransport(handle),
    )
    original = verify_diagnostics.DiagnosticRecorder.report

    def patched(self):
        report = original(self)
        if isinstance(report, dict):
            mutate(report)
        return report

    monkeypatch.setattr(verify_diagnostics.DiagnosticRecorder, "report", patched)
    return asyncio.run(verify_diagnostics_async.verify({"app_key": "K", "app_secret": "S"}))


def _terminal_call_events(report, entrypoint):
    return [
        event
        for event in report["events"]
        if isinstance(event, dict)
        and event.get("kind") == "call"
        and event.get("entrypoint") == entrypoint
        and event.get("outcome") in verify_diagnostics._TERMINAL_OUTCOMES
    ]


def _rename_list_terminal(report):
    """Re-label the real ``devices.list`` terminal as an unknown entrypoint."""
    for event in _terminal_call_events(report, "devices.list"):
        event["entrypoint"] = "devices.mystery_write"
        return


def _foreign_mode_list_terminal(report):
    """Clone the ``devices.list`` terminal with a foreign (sync) mode."""
    for event in _terminal_call_events(report, "devices.list"):
        forged = dict(event)
        forged["mode"] = "sync"
        report["events"].append(forged)
        return


@pytest.mark.parametrize(
    "mutate,expected_steps,expected_calls,expected_intact",
    [
        # An unfamiliar async/domestic business terminal is not one of the
        # six steps: an unknown business ``call`` terminal corrupts the
        # snapshot outright (failed, diagnostics_intact=False), while the
        # five real business steps are still honestly projected.
        pytest.param(
            _rename_list_terminal,
            ["info", "channels", "status", "capabilities", "version"],
            5,
            False,
            id="unfamiliar-entrypoint",
        ),
        # A foreign-mode terminal inside the same run keeps every step
        # observed but taints the snapshot, so the pass is refused.
        pytest.param(
            _foreign_mode_list_terminal,
            ["list", "info", "channels", "status", "capabilities", "version"],
            6,
            False,
            id="foreign-mode-terminal",
        ),
    ],
)
def test_strange_terminal_events_never_claim_passed(
    monkeypatch, mutate, expected_steps, expected_calls, expected_intact
):
    result = _verify_with_mutated_report(monkeypatch, mutate)
    assert result["outcome"] == "failed"
    assert result["diagnostics_intact"] is expected_intact
    assert result["successful_steps"] == expected_steps
    assert result["logical_calls"] == expected_calls
    assert result["http_requests"] == 7
    text = json.dumps(result)
    for sentinel in _SENTINELS:
        assert sentinel not in text
    # The forged entrypoint name itself is never surfaced in the receipt.
    assert "mystery" not in text


def test_unknown_business_terminal_inside_real_report_fails_cleanly(monkeypatch):
    """An injected unknown business ``call`` terminal corrupts the real run.

    The six legitimate events cannot mask a foreign terminal: the receipt
    fails outright with intact=False and never echoes the unknown name.
    """

    def inject(report):
        report["events"].append(
            {
                "kind": "call",
                "entrypoint": "devices.mystery_write",
                "mode": "async",
                "region": "domestic",
                "outcome": "succeeded",
            }
        )

    result = _verify_with_mutated_report(monkeypatch, inject)
    assert result["outcome"] == "failed"
    assert result["diagnostics_intact"] is False
    assert result["logical_calls"] == 6
    assert result["http_requests"] == 7
    text = json.dumps(result)
    assert "mystery_write" not in text
    for sentinel in _SENTINELS:
        assert sentinel not in text


@pytest.mark.parametrize(
    "forged",
    [
        # Reversed/out-of-order routes cannot claim the fixed sequence pass.
        _passed_verify_result(
            attempted_routes=[
                "auth",
                "list",
                "channels",
                "info",
                "status",
                "capabilities",
                "version",
            ]
        ),
        # A failed partial payload naming a route outside the fixed labels.
        _passed_verify_result(
            outcome="failed",
            attempted_routes=["auth", "list", "mystery_route"],
            successful_steps=["list"],
            logical_calls=1,
        ),
        # A failed partial payload naming a step outside the fixed labels.
        _passed_verify_result(
            outcome="failed",
            successful_steps=["list", "mystery_step"],
            logical_calls=2,
        ),
    ],
)
def test_out_of_order_or_unknown_label_is_never_serialized(monkeypatch, capsys, tmp_path, forged):
    """Route/step labels outside the fixed vocabulary are refused, not written."""
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "report.json"
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=forged,
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert not destination.exists()
    assert not list(tmp_path.iterdir())
    for sentinel in _SENTINELS:
        assert sentinel not in out + err


def test_output_file_replaced_before_write_failure_is_never_unlinked(monkeypatch, tmp_path, capsys):
    """A different file swapped onto the path before failure is left alone.

    The cleanup may only ever remove the exact inode this run opened; a
    user-replaced file at the same path is not this run's artifact.
    """
    destination = tmp_path / "report.json"

    class _FailingStream:
        def write(self, _s):
            raise OSError("PRIVATE_DISK")

        def flush(self):
            pass

        def close(self):
            pass

    real_open = verify_diagnostics_async._open_output_exclusive

    def wrapped_open(path):
        stream, identity = real_open(path)
        stream.close()
        # The fresh file this run created is replaced by an unrelated file
        # before the stream write fails — cleanup must refuse to delete it.
        destination.unlink()
        destination.write_text("ORIGINAL", encoding="utf-8")
        return _FailingStream(), identity

    monkeypatch.setattr(verify_diagnostics_async, "_open_output_exclusive", wrapped_open)
    code, _entered, _calls = _run_prompt(
        monkeypatch,
        ["--prompt", "--output", str(destination)],
        verify_result=_passed_verify_result(),
    )
    assert code == 1
    out, err = capsys.readouterr()
    assert "PRIVATE" not in out + err
    # The replacement file is not this run's artifact: it must survive.
    assert destination.read_text(encoding="utf-8") == "ORIGINAL"
