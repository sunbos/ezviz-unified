"""Independent contract tests for the planned ``ezviz_unified.inspection`` module.

Only the fixed public API from the implementation plan is exercised:
``inspect_device``/``inspect_device_async`` plus the ``InspectionResult`` and
``DeviceInspection`` value objects. Real ``Client``/``AsyncClient`` instances
backed by ``httpx.MockTransport`` cover the regional token routes and all five
read-only operations; narrow monkeypatches of the existing facades cover exact
response/error identity, fatal authentication propagation, and cancellation.
No internals of the proposed helper are mocked.
"""

import asyncio
import dataclasses
import importlib
import importlib.util
from urllib.parse import parse_qs

import httpx
import pytest

import ezviz_unified
from ezviz_unified import (
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    RegionConfig,
)
from ezviz_unified.errors import ApiError, EzvizError

OPERATIONS = (
    "devices.get",
    "devices.channels",
    "devices.status",
    "diagnostics.capabilities",
    "diagnostics.version",
)

PATHS = {
    "devices.get": "/api/lapp/device/info",
    "devices.channels": "/api/lapp/device/camera/list",
    "devices.status": "/api/lapp/device/status/get",
    "diagnostics.capabilities": "/api/lapp/device/capacity",
    "diagnostics.version": "/api/lapp/device/version/info",
}

ORDERED_PATHS = [PATHS[name] for name in OPERATIONS]
SUMMARY_ROW_KEYS = {"operation", "status", "error_type", "code", "http_status"}


@pytest.fixture
def inspection():
    assert importlib.util.find_spec("ezviz_unified.inspection") is not None, (
        "ezviz_unified.inspection is missing"
    )
    return importlib.import_module("ezviz_unified.inspection")


def _refuse_request(request):
    raise AssertionError(f"unexpected HTTP request: {request.method} {request.url}")


def _counting(calls):
    def handle(request):
        calls.append(request)
        return httpx.Response(500)

    return handle


def _token_response(token, region):
    data = {"accessToken": token, "expireTime": 2_000_000}
    if region.scope == "overseas":
        data["areaDomain"] = "https://isgpopen.ezviz.com"
    return httpx.Response(200, json={"code": "200", "data": data})


def _carries(request, needle):
    return (
        needle in request.content.decode()
        or needle in str(request.url)
        or any(needle in str(value) for value in request.headers.values())
    )


def build_client(kind, region, auth_handler=_refuse_request, business_handler=_refuse_request):
    cls = Client if kind == "sync" else AsyncClient
    return cls(
        "APPKEY",
        "APPSECRET",
        region=region,
        clock=lambda: 1000,
        auth_transport=httpx.MockTransport(auth_handler),
        transport_factory=lambda origin: httpx.MockTransport(business_handler),
    )


def close_client(kind, client):
    if kind == "sync":
        client.close()
    else:
        asyncio.run(client.aclose())


def run_helper(inspection, kind, client, serial, **kwargs):
    """Call the fixed entry point; await coroutine results in a fresh loop."""
    entry = inspection.inspect_device if kind == "sync" else inspection.inspect_device_async
    outcome = entry(client, serial, **kwargs)
    if asyncio.iscoroutine(outcome):
        return asyncio.run(outcome)
    return outcome


def install_facade_recorders(monkeypatch, kind, behavior):
    """Record calls to the five existing read-only facade methods.

    ``behavior`` maps each operation name to either an ``ApiResponse`` to
    return or an exception instance to raise. Returns the shared call log as
    ``(operation, serial, kwargs)`` tuples. Only the pre-existing public
    facades are patched, never the proposed helper internals.
    """
    calls = []
    devices = importlib.import_module("ezviz_unified.devices")
    diagnostics = importlib.import_module("ezviz_unified.diagnostics")
    if kind == "sync":
        targets = (
            (devices.Devices, "get", "devices.get"),
            (devices.Devices, "channels", "devices.channels"),
            (devices.Devices, "status", "devices.status"),
            (diagnostics.Diagnostics, "capabilities", "diagnostics.capabilities"),
            (diagnostics.Diagnostics, "version", "diagnostics.version"),
        )
    else:
        targets = (
            (devices.AsyncDevices, "get", "devices.get"),
            (devices.AsyncDevices, "channels", "devices.channels"),
            (devices.AsyncDevices, "status", "devices.status"),
            (diagnostics.AsyncDiagnostics, "capabilities", "diagnostics.capabilities"),
            (diagnostics.AsyncDiagnostics, "version", "diagnostics.version"),
        )

    def factory(name):
        def resolve(self, serial, **kwargs):
            calls.append((name, serial, kwargs))
            outcome = behavior[name]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        if kind == "sync":
            return resolve

        async def async_resolve(self, serial, **kwargs):
            return resolve(self, serial, **kwargs)

        return async_resolve

    for cls, method, name in targets:
        monkeypatch.setattr(cls, method, factory(name))
    return calls


# ---------------------------------------------------------------------------
# Public surface and value objects (no client required).
# ---------------------------------------------------------------------------


def test_exports_do_not_extend_client_facade(inspection):
    for name in (
        "InspectionResult",
        "DeviceInspection",
        "inspect_device",
        "inspect_device_async",
    ):
        assert getattr(ezviz_unified, name) is getattr(inspection, name)
    assert not hasattr(Client, "inspect_device")
    assert not hasattr(AsyncClient, "inspect_device")
    assert not hasattr(AsyncClient, "inspect_device_async")


def test_inspection_result_requires_exactly_one_outcome(inspection):
    response = ApiResponse(200, "200", data={"secret": "PRIVATEPAYLOAD"})
    error = ApiError("PRIVATE detail", code="10005", http_status=200)

    ok = inspection.InspectionResult("devices.get", response=response)
    assert ok.succeeded is True
    assert ok.response is response and ok.error is None

    failed = inspection.InspectionResult("devices.get", error=error)
    assert failed.succeeded is False
    assert failed.error is error and failed.response is None

    with pytest.raises(ValueError):
        inspection.InspectionResult("devices.get")
    with pytest.raises(ValueError):
        inspection.InspectionResult("devices.get", response=response, error=error)

    with pytest.raises(dataclasses.FrozenInstanceError):
        ok.operation = "devices.list"

    assert "PRIVATEPAYLOAD" not in repr(ok)
    assert "PRIVATE detail" not in repr(failed)


def test_device_inspection_summary_sanitizes_and_returns_fresh_dicts(inspection):
    ok = inspection.InspectionResult(
        "devices.get", response=ApiResponse(200, "200", data="PAYLOAD")
    )
    failures = (
        ApiError("PRIVATE detail", code=10002, http_status=401),
        ApiError("PRIVATE detail", code="10017", http_status=503),
        ApiError("PRIVATE detail", code="not-digits", http_status="500"),
        ApiError("PRIVATE detail", code=True, http_status=200.0),
        ApiError("PRIVATE detail", code="５００", http_status=True),
    )
    results = (ok,) + tuple(
        inspection.InspectionResult(f"op{i}", error=err) for i, err in enumerate(failures)
    )
    report = inspection.DeviceInspection("domestic", "DEV12345", 7, results)

    assert report.all_succeeded is False
    assert "DEV12345" not in repr(report)
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.region = "overseas"
    assert inspection.DeviceInspection("domestic", "S", 1, ()).all_succeeded is False
    assert inspection.DeviceInspection("overseas", "s", 1, (ok,)).all_succeeded is True

    summary = report.public_summary()
    assert set(summary) == {"region", "all_succeeded", "results"}
    assert summary["region"] == "domestic"
    assert summary["all_succeeded"] is False

    rows = summary["results"]
    assert rows[0] == {"operation": "devices.get", "status": "ok"}
    failed = rows[1:]
    assert all(set(row) <= SUMMARY_ROW_KEYS for row in rows)
    assert all("channel" not in row and "message" not in row for row in rows)
    assert [row["error_type"] for row in failed] == ["ApiError"] * len(failed)
    assert all(row["status"] == "failed" for row in failed)
    assert failed[0]["code"] == 10002 and failed[0]["http_status"] == 401
    assert failed[1]["code"] == "10017" and failed[1]["http_status"] == 503
    for row in failed[2:]:
        assert "code" not in row and "http_status" not in row

    blob = repr(summary)
    for leaked in ("DEV12345", "PRIVATE", "PAYLOAD"):
        assert leaked not in blob
    assert "message" not in summary and "device_serial" not in summary

    again = report.public_summary()
    assert again == summary and again is not summary
    assert again["results"] is not rows
    rows[0]["status"] = "tampered"
    assert again["results"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Real clients over MockTransport: regional token route plus five reads.
# ---------------------------------------------------------------------------


def test_sync_domestic_success_full_wire_and_borrowed_client(inspection):
    region = RegionConfig.china()
    auth_calls, business_calls = [], []

    def auth(request):
        auth_calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://open.ys7.com/api/lapp/token/get"
        return _token_response("TOKEN1", region)

    def business(request):
        business_calls.append(request)
        assert request.method == "POST"
        form = parse_qs(request.content.decode())
        assert form["deviceSerial"] == ["DEV12345"]
        path = request.url.path
        if path == "/api/lapp/device/status/get":
            assert form["channel"] == ["7"]
        if path == "/api/lapp/device/capacity":
            assert form["channelNo"] == ["7"]
        return httpx.Response(
            200,
            json={
                "code": "200",
                "msg": "ok",
                "data": {"path": path, "oddField": {"nested": [1, 2]}},
                "vendorExtra": "kept",
            },
        )

    client = build_client("sync", region, auth, business)
    try:
        report = inspection.inspect_device(client, "DEV12345", channel=7)

        assert report.region == "domestic"
        assert report.device_serial == "DEV12345"
        assert report.channel == 7
        assert isinstance(report.results, tuple) and len(report.results) == 5
        assert [res.operation for res in report.results] == list(OPERATIONS)
        assert report.all_succeeded is True
        for res in report.results:
            assert res.succeeded and res.error is None
            assert isinstance(res.response, ApiResponse)
            assert res.response.data["oddField"] == {"nested": [1, 2]}
        assert report.results[0].response.data["path"] == "/api/lapp/device/info"

        # Exactly the five documented read endpoints, in order, nothing else.
        assert [req.url.path for req in business_calls] == ORDERED_PATHS
        assert all(req.url.host == "open.ys7.com" for req in business_calls)
        assert len(auth_calls) == 1
        assert all(_carries(req, "TOKEN1") for req in business_calls)

        summary = report.public_summary()
        assert summary["region"] == "domestic" and summary["all_succeeded"] is True
        assert [row["operation"] for row in summary["results"]] == list(OPERATIONS)
        assert all(row["status"] == "ok" for row in summary["results"])
        assert "DEV12345" not in repr(summary)

        # The helper borrows the client: it remains usable afterwards.
        extra = client.devices.get("DEV12345")
        assert extra.data["path"] == "/api/lapp/device/info"
        assert len(business_calls) == 6 and len(auth_calls) == 1
    finally:
        client.close()


def test_async_overseas_success_full_wire_and_borrowed_client(inspection):
    region = RegionConfig.overseas()
    auth_calls, business_calls = [], []

    def auth(request):
        auth_calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://open.ezvizlife.com/api/lapp/token/get"
        return _token_response("TOKEN9", region)

    def business(request):
        business_calls.append(request)
        assert request.method == "POST"
        assert request.url.host == "isgpopen.ezviz.com"
        form = parse_qs(request.content.decode())
        assert form["deviceSerial"] == ["dev12345"]
        if request.url.path == "/api/lapp/device/status/get":
            assert form["channel"] == ["1"]
        if request.url.path == "/api/lapp/device/capacity":
            assert form["channelNo"] == ["1"]
        return httpx.Response(200, json={"code": "200", "data": {"path": request.url.path}})

    async def scenario():
        client = build_client("async", region, auth, business)
        try:
            # Lowercase serial and default channel are valid overseas.
            report = await inspection.inspect_device_async(client, "dev12345")
            assert report.region == "overseas"
            assert report.device_serial == "dev12345"
            assert report.channel == 1
            assert [res.operation for res in report.results] == list(OPERATIONS)
            assert report.all_succeeded is True
            # Borrowed async client remains usable.
            extra = await client.diagnostics.version("dev12345")
            assert extra.data["path"] == "/api/lapp/device/version/info"
        finally:
            await client.aclose()

    asyncio.run(scenario())
    expected = ORDERED_PATHS + ["/api/lapp/device/version/info"]
    assert [req.url.path for req in business_calls] == expected
    assert len(auth_calls) == 1
    assert all(_carries(req, "TOKEN9") for req in business_calls)


@pytest.mark.parametrize("failure", ["business_code", "transport"])
def test_non_auth_failures_captured_and_sequence_continues(inspection, failure):
    region = RegionConfig.china()
    auth_calls, business_calls = [], []

    def auth(request):
        auth_calls.append(request)
        return _token_response("T", region)

    def business(request):
        business_calls.append(request)
        if request.url.path == "/api/lapp/device/status/get":
            if failure == "business_code":
                return httpx.Response(200, json={"code": "10005", "msg": "PRIVATE weird failure"})
            raise httpx.ReadTimeout("boom", request=request)
        return httpx.Response(200, json={"code": "200", "data": {"ok": True}})

    client = build_client("sync", region, auth, business)
    try:
        report = inspection.inspect_device(client, "DEV12345")
        assert [res.operation for res in report.results] == list(OPERATIONS)
        assert report.all_succeeded is False
        failed = report.results[2]
        assert failed.succeeded is False and failed.response is None
        assert isinstance(failed.error, EzvizError)
        assert not isinstance(failed.error, AuthenticationError)
        assert all(res.succeeded for res in report.results[:2] + report.results[3:])

        row = report.public_summary()["results"][2]
        assert row["status"] == "failed"
        assert row["error_type"] == type(failed.error).__name__
        assert "PRIVATE weird failure" not in repr(report.public_summary())

        # All five reads still ran, exactly once each, with a single login.
        assert [req.url.path for req in business_calls] == ORDERED_PATHS
        assert len(auth_calls) == 1
    finally:
        client.close()


def test_authentication_error_propagates_and_stops_remaining_calls(inspection):
    region = RegionConfig.china()
    auth_calls, business_calls = [], []

    def auth(request):
        auth_calls.append(request)
        return _token_response("T", region)

    def business(request):
        business_calls.append(request)
        return httpx.Response(401, json={"code": "10002", "msg": "PRIVATE expired"})

    client = build_client("sync", region, auth, business)
    try:
        with pytest.raises(AuthenticationError):
            inspection.inspect_device(client, "DEV12345")
        assert business_calls, "the first read should have been attempted"
        # Authentication failure is fatal: nothing past devices.get may run,
        # even if the SDK itself retried the same call after a token refresh.
        assert {req.url.path for req in business_calls} == {"/api/lapp/device/info"}
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Narrow facade monkeypatches: ordering, arguments, and exact identity.
# ---------------------------------------------------------------------------


def test_sync_call_order_arguments_and_response_identity(inspection, monkeypatch):
    sentinels = {
        name: ApiResponse(200, "200", operation=name, data={"i": n}, raw={"extra": n})
        for n, name in enumerate(OPERATIONS)
    }
    calls = install_facade_recorders(monkeypatch, "sync", dict(sentinels))
    client = build_client("sync", RegionConfig.china())
    serial = "DEV12345"
    try:
        report = inspection.inspect_device(client, serial, channel=9)
    finally:
        client.close()

    assert [name for name, _, _ in calls] == list(OPERATIONS)
    assert all(entry[1] is serial for entry in calls)
    assert calls[0][2] == {} and calls[1][2] == {} and calls[4][2] == {}
    assert calls[2][2] == {"channel": 9}
    assert calls[3][2] == {"channel": 9}

    assert [res.operation for res in report.results] == list(OPERATIONS)
    assert all(res.response is sentinels[name] for res, name in zip(report.results, OPERATIONS))
    assert report.results[0].response.raw == {"extra": 0}
    assert report.all_succeeded is True


def test_captured_error_preserves_identity_and_continues(inspection, monkeypatch):
    sentinel = ApiError("PRIVATE kaput", code="10005", http_status=200)
    behavior = {name: ApiResponse(200, "200") for name in OPERATIONS}
    behavior["devices.status"] = sentinel
    calls = install_facade_recorders(monkeypatch, "sync", behavior)
    client = build_client("sync", RegionConfig.china())
    try:
        report = inspection.inspect_device(client, "DEV12345")
    finally:
        client.close()

    assert [name for name, _, _ in calls] == list(OPERATIONS)
    assert report.results[2].error is sentinel
    assert report.results[2].succeeded is False
    assert report.all_succeeded is False
    assert "PRIVATE kaput" not in repr(report.results[2])


def test_async_authentication_error_preserves_identity_and_stops(inspection, monkeypatch):
    sentinel = AuthenticationError("PRIVATE token rejected", code="10002", http_status=200)
    behavior = {name: ApiResponse(200, "200") for name in OPERATIONS}
    behavior["devices.get"] = sentinel
    calls = install_facade_recorders(monkeypatch, "async", behavior)
    client = build_client("async", RegionConfig.china())

    async def scenario():
        try:
            with pytest.raises(AuthenticationError) as caught:
                await inspection.inspect_device_async(client, "DEV12345")
            assert caught.value is sentinel
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert [name for name, _, _ in calls] == ["devices.get"]


@pytest.mark.parametrize(
    "kind,fail_at,thrown",
    [
        ("sync", "devices.status", RuntimeError("bug in facade")),
        ("sync", "devices.channels", ValueError("facade bug")),
        ("sync", "diagnostics.version", KeyboardInterrupt()),
        ("async", "devices.channels", asyncio.CancelledError()),
        ("async", "diagnostics.capabilities", TypeError("bad facade")),
    ],
)
def test_non_ezviz_exceptions_propagate(inspection, monkeypatch, kind, fail_at, thrown):
    behavior = {name: ApiResponse(200, "200") for name in OPERATIONS}
    behavior[fail_at] = thrown
    calls = install_facade_recorders(monkeypatch, kind, behavior)
    client = build_client(kind, RegionConfig.china())

    async def scenario():
        try:
            with pytest.raises(type(thrown)) as caught:
                await inspection.inspect_device_async(client, "DEV12345")
            assert caught.value is thrown
        finally:
            await client.aclose()

    if kind == "sync":
        try:
            with pytest.raises(type(thrown)) as caught:
                inspection.inspect_device(client, "DEV12345")
            assert caught.value is thrown
        finally:
            client.close()
    else:
        asyncio.run(scenario())

    expected = list(OPERATIONS)[: OPERATIONS.index(fail_at) + 1]
    assert [name for name, _, _ in calls] == expected


# ---------------------------------------------------------------------------
# Input validation happens before any token or business request.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("region", [RegionConfig.china(), RegionConfig.overseas()])
@pytest.mark.parametrize("serial", ["", "   ", 0, None])
def test_invalid_serial_rejected_before_any_request(inspection, kind, region, serial):
    auth_calls, business_calls = [], []
    client = build_client(kind, region, _counting(auth_calls), _counting(business_calls))
    try:
        with pytest.raises(ValueError):
            run_helper(inspection, kind, client, serial)
    finally:
        close_client(kind, client)
    assert auth_calls == [] and business_calls == []


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("region", [RegionConfig.china(), RegionConfig.overseas()])
@pytest.mark.parametrize("channel", [True, "2", 2.0, None])
def test_invalid_channel_rejected_before_any_request(inspection, kind, region, channel):
    auth_calls, business_calls = [], []
    client = build_client(kind, region, _counting(auth_calls), _counting(business_calls))
    serial = "dev12345" if region.scope == "overseas" else "DEV12345"
    try:
        with pytest.raises(ValueError):
            run_helper(inspection, kind, client, serial, channel=channel)
    finally:
        close_client(kind, client)
    assert auth_calls == [] and business_calls == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_domestic_lowercase_serial_rejected_before_any_request(inspection, kind):
    auth_calls, business_calls = [], []
    client = build_client(
        kind, RegionConfig.china(), _counting(auth_calls), _counting(business_calls)
    )
    try:
        with pytest.raises(ValueError):
            run_helper(inspection, kind, client, "dev12345")
    finally:
        close_client(kind, client)
    assert auth_calls == [] and business_calls == []
