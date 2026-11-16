import io
import json
import sys
from urllib.parse import parse_qs, urlencode

import httpx
import pytest

from tools.live import verify_diagnostics


def _sdk_response(data):
    return httpx.Response(200, json={"code": "200", "msg": "Operation succeeded", "data": data})


def _recording_handler(rows, *, fail_path=None, fail_code="10017"):
    state = {"requests": [], "forms": []}

    def handle(request):
        state["requests"].append(request)
        state["forms"].append(parse_qs(request.read().decode("utf-8")))
        if (request.method, request.url.path) == ("POST", "/api/lapp/token/get"):
            return _sdk_response({"accessToken": "TEST_TOKEN", "expireTime": 1893456000000})
        if request.url.path == fail_path:
            return httpx.Response(200, json={"code": fail_code, "msg": "PRIVATE_REASON"})
        if request.url.path == "/api/lapp/device/list":
            return _sdk_response(rows)
        return _sdk_response({})

    return state, handle


def _run_verify(monkeypatch, rows, **kwargs):
    state, handle = _recording_handler(rows, **kwargs)
    mock = httpx.MockTransport(handle)
    monkeypatch.setattr(verify_diagnostics, "_new_delegate_transport", lambda: mock)
    result = verify_diagnostics.verify({"app_key": "TEST_APP_KEY", "app_secret": "PRIVATE_SECRET"})
    return state, result


def _counting_delegate(seen):
    return httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200))


def _bound_budget(serial="C12345678"):
    budget = verify_diagnostics._RequestBudget()
    budget.bind_device(serial)
    return budget


def test_full_run_sends_seven_bounded_requests_and_binds_one_serial(monkeypatch):
    state, result = _run_verify(monkeypatch, [{"deviceSerial": "C12345678"}])
    assert [(request.method, request.url.path) for request in state["requests"]] == [
        ("POST", "/api/lapp/token/get"),
        ("POST", "/api/lapp/device/list"),
        ("POST", "/api/lapp/device/info"),
        ("POST", "/api/lapp/device/camera/list"),
        ("POST", "/api/lapp/device/status/get"),
        ("POST", "/api/lapp/device/capacity"),
        ("POST", "/api/lapp/device/version/info"),
    ]
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
    assert result["receipt"] == {
        "schema_version": 1,
        "scope": "bounded_domestic_diagnostic_v1",
        "outcome": "passed",
        "logical_calls": 6,
        "http_requests": 7,
        "read_only": True,
    }
    report = result["diagnostics"]
    assert report["schema_version"] == 1 and report["scope"] == "local_sdk_diagnostics"
    events = report.get("events")
    assert isinstance(events, list) and events
    kinds = {event.get("kind") for event in events if isinstance(event, dict)}
    assert "call" in kinds and "authentication" in kinds
    payload = json.dumps(result)
    for sentinel in ("C12345678", "PRIVATE_SECRET", "TEST_APP_KEY", "TEST_TOKEN"):
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
    assert set(result["receipt"]) == {
        "schema_version",
        "scope",
        "outcome",
        "logical_calls",
        "http_requests",
        "read_only",
    }
    assert result["receipt"]["outcome"] == "not_verified"
    assert result["receipt"]["logical_calls"] == 1
    assert result["receipt"]["http_requests"] == 2
    assert "PRIVATE" not in json.dumps(result)


def test_step_failure_reports_failed_with_whitelisted_output(monkeypatch):
    _state, result = _run_verify(
        monkeypatch, [{"deviceSerial": "C12345678"}], fail_path="/api/lapp/device/capacity"
    )
    receipt = result["receipt"]
    assert receipt["outcome"] == "failed"
    assert receipt["logical_calls"] == 6 and receipt["http_requests"] == 7
    text = json.dumps(result)
    assert "PRIVATE" not in text and "C12345678" not in text


def test_auth_failure_stops_before_any_business_request(monkeypatch):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATE_AUTH"})

    monkeypatch.setattr(
        verify_diagnostics, "_new_delegate_transport", lambda: httpx.MockTransport(handle)
    )
    result = verify_diagnostics.verify({"app_key": "K", "app_secret": "S"})
    assert [request.url.path for request in seen] == ["/api/lapp/token/get"]
    assert result["receipt"]["outcome"] == "failed"
    assert result["receipt"]["logical_calls"] == 1
    assert result["receipt"]["http_requests"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_token_refresh_retry_is_refused_before_a_second_token_request(monkeypatch):
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path == "/api/lapp/token/get":
            return _sdk_response({"accessToken": "TEST_TOKEN", "expireTime": 1893456000000})
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATE_EXPIRED"})

    monkeypatch.setattr(
        verify_diagnostics, "_new_delegate_transport", lambda: httpx.MockTransport(handle)
    )
    result = verify_diagnostics.verify({"app_key": "K", "app_secret": "S"})
    assert len(seen) == 2
    assert [request.url.path for request in seen] == [
        "/api/lapp/token/get",
        "/api/lapp/device/list",
    ]
    assert result["receipt"]["outcome"] == "failed"
    assert result["receipt"]["http_requests"] == 2


def test_guard_transport_accepts_implicit_default_and_explicit_443_port():
    seen = []
    transport = verify_diagnostics._GuardedTransport(
        _counting_delegate(seen), verify_diagnostics._RequestBudget()
    )
    with httpx.Client(transport=transport) as client:
        client.post(
            "https://open.ys7.com/api/lapp/device/list",
            data={"accessToken": "T", "pageStart": "0", "pageSize": "1"},
        )
        client.post(
            "https://open.ys7.com:443/api/lapp/token/get",
            data={"appKey": "K", "appSecret": "S"},
        )
    assert [request.url.port or 443 for request in seen] == [443, 443]


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
    transport = verify_diagnostics._GuardedTransport(_counting_delegate(seen), budget)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post(url, data={"accessToken": "T", "pageStart": "0", "pageSize": "1"})
    assert seen == []
    assert budget.total == 0


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
    transport = verify_diagnostics._GuardedTransport(_counting_delegate(seen), budget)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post("https://open.ys7.com" + path, data=fields)
    assert seen == []
    assert budget.total == 0


@pytest.mark.parametrize(
    "body",
    [
        b"accessToken=T&accessToken=T2&deviceSerial=C12345678",
        b"accessToken=T&deviceSerial=C12345678&deviceSerial=C12345678",
        b"accessToken=T&deviceSerial=C12345678&",
        b"accessToken=T&deviceSerial",
        "accessToken=T&deviceSerial=C12345678".encode("utf-16"),
        b"accessToken=" + b"T" * 9000 + b"&deviceSerial=C12345678",
    ],
)
def test_guard_transport_rejects_malformed_duplicate_or_oversized_bodies(body):
    seen = []
    transport = verify_diagnostics._GuardedTransport(_counting_delegate(seen), _bound_budget())
    with httpx.Client(transport=transport) as client:
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post(
                "https://open.ys7.com/api/lapp/device/info",
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    assert seen == []


def test_guard_transport_rejects_non_form_or_missing_bodies():
    seen = []
    transport = verify_diagnostics._GuardedTransport(_counting_delegate(seen), _bound_budget())
    with httpx.Client(transport=transport) as client:
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post("https://open.ys7.com/api/lapp/device/info")
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post(
                "https://open.ys7.com/api/lapp/device/info",
                json={"accessToken": "T", "deviceSerial": "C12345678"},
            )
    assert seen == []


def test_guard_rejects_device_routes_until_a_serial_is_bound():
    seen = []
    budget = verify_diagnostics._RequestBudget()
    transport = verify_diagnostics._GuardedTransport(_counting_delegate(seen), budget)
    fields = {"accessToken": "T", "deviceSerial": "C12345678"}
    with httpx.Client(transport=transport) as client:
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post("https://open.ys7.com/api/lapp/device/info", data=fields)
    assert seen == [] and budget.total == 0
    budget.bind_device("C12345678")
    with httpx.Client(transport=transport) as client:
        client.post("https://open.ys7.com/api/lapp/device/info", data=fields)
    assert len(seen) == 1 and budget.total == 1


def test_budget_binds_only_the_first_listed_serial():
    budget = verify_diagnostics._RequestBudget()
    budget.bind_device("C12345678")
    budget.bind_device("F99999999")
    budget.bind_device("")
    assert budget.bound_device == "C12345678"


def test_guard_transport_rejects_out_of_scope_requests_before_delegating():
    seen = []
    budget = _bound_budget()
    transport = verify_diagnostics._GuardedTransport(_counting_delegate(seen), budget)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post(
                "https://open.ys7.com/api/lapp/device/encrypt/off",
                data={"accessToken": "T", "deviceSerial": "C12345678"},
            )
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.put(
                "https://open.ys7.com/api/v3/device/format/disk",
                data={"accessToken": "T"},
            )
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.get("https://open.ys7.com/api/lapp/device/list")
        client.post(
            "https://open.ys7.com/api/lapp/device/list",
            data={"accessToken": "T", "pageStart": "0", "pageSize": "1"},
        )
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            client.post(
                "https://open.ys7.com/api/lapp/device/list",
                data={"accessToken": "T", "pageStart": "0", "pageSize": "1"},
            )
    assert len(seen) == 1
    assert budget.total == 1 and budget.rejected == 1


def test_transport_factory_accepts_only_the_domestic_origin(monkeypatch):
    monkeypatch.setattr(
        verify_diagnostics,
        "_new_delegate_transport",
        lambda: httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    factory = verify_diagnostics._guarded_factory(verify_diagnostics._RequestBudget())
    for origin in (
        "https://open.ys7.com",
        "https://open.ys7.com:443",
        "https://open.ys7.com/",
    ):
        transport = factory(origin)
        assert isinstance(transport, verify_diagnostics._GuardedTransport)
        transport.close()
    for origin in (
        "http://open.ys7.com",
        "https://open.ys7.com:8443",
        "https://euopen.ys7.com",
        "https://user:pw@open.ys7.com",
        "https://open.ys7.com.evil.example",
        "https://open.ys7.com/api",
        "https://open.ys7.com/?x=1",
        "https://open.ys7.com/#frag",
    ):
        with pytest.raises(verify_diagnostics._BoundaryRejection):
            factory(origin)


def test_shared_budget_allows_each_allowed_route_once_and_caps_at_seven():
    budget = verify_diagnostics._RequestBudget()
    routes = sorted(verify_diagnostics._ALLOWED_ROUTES)
    assert len(routes) == 7
    assert all(budget.admit(route) for route in routes)
    assert budget.total == 7
    assert not budget.admit(routes[0])
    assert budget.total == 7 and budget.rejected == 1


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"app_key": "K"},
        {"app_secret": "S"},
        {"app_key": "  ", "app_secret": "S"},
        {"app_key": "K", "app_secret": "S", "region": "overseas"},
        {"app_key": "K", "app_secret": "S", "inspection": "true"},
        {"app_key": "K", "app_secret": "S", "include_diagnostics": "true"},
        {"app_key": "K", "app_secret": 7},
    ],
)
def test_config_is_rejected_before_client_construction(monkeypatch, config):
    def forbidden(*args, **kwargs):
        raise AssertionError("Client must not be constructed for rejected config")

    monkeypatch.setattr(verify_diagnostics, "Client", forbidden)
    with pytest.raises(ValueError):
        verify_diagnostics.verify(config)


def test_form_config_keeps_only_credentials_and_drops_overrides():
    values = {
        "app_key": ["K"],
        "app_secret": ["S"],
        "region": ["overseas"],
        "inspection": ["false"],
        "include_managed": ["true"],
    }
    assert verify_diagnostics._form_config(values) == {"app_key": "K", "app_secret": "S"}


@pytest.mark.parametrize("mutate", [{"recording_errors": 1}, {"dropped_events": 2}])
def test_unhealthy_report_prevents_a_passed_receipt(monkeypatch, mutate):
    _state, handle = _recording_handler([{"deviceSerial": "C12345678"}])
    monkeypatch.setattr(
        verify_diagnostics, "_new_delegate_transport", lambda: httpx.MockTransport(handle)
    )
    original = verify_diagnostics.DiagnosticRecorder.report

    def patched(self):
        report = original(self)
        if isinstance(report, dict):
            report = dict(report)
            report.update(mutate)
        return report

    monkeypatch.setattr(verify_diagnostics.DiagnosticRecorder, "report", patched)
    result = verify_diagnostics.verify({"app_key": "K", "app_secret": "S"})
    assert result["receipt"]["outcome"] == "failed"
    assert result["receipt"]["logical_calls"] == 6
    assert result["receipt"]["http_requests"] == 7


def test_missing_report_prevents_a_passed_receipt(monkeypatch):
    _state, handle = _recording_handler([{"deviceSerial": "C12345678"}])
    monkeypatch.setattr(
        verify_diagnostics, "_new_delegate_transport", lambda: httpx.MockTransport(handle)
    )
    monkeypatch.setattr(verify_diagnostics.DiagnosticRecorder, "report", lambda self: None)
    result = verify_diagnostics.verify({"app_key": "K", "app_secret": "S"})
    assert result["receipt"]["outcome"] == "failed"
    assert result["receipt"]["logical_calls"] == 0
    assert result["diagnostics"] is None


def _terminal_call_event(report, entrypoint):
    for event in report.get("events", []):
        if (
            isinstance(event, dict)
            and event.get("kind") == "call"
            and event.get("entrypoint") == entrypoint
            and event.get("outcome") in verify_diagnostics._TERMINAL_OUTCOMES
        ):
            return event
    return None


def _drop_step(report):
    report["events"] = [
        event
        for event in report["events"]
        if not (isinstance(event, dict) and event.get("entrypoint") == "diagnostics.version")
    ]


def _duplicate_terminal(report):
    event = _terminal_call_event(report, "devices.list")
    if event is not None:
        report["events"].append(dict(event))


def _fail_terminal(report):
    event = _terminal_call_event(report, "devices.get")
    if event is not None:
        event["outcome"] = "failed"


def _verify_with_mutated_report(monkeypatch, mutate):
    """Run the bounded flow but mutate the real ``report()`` snapshot once."""
    original = verify_diagnostics.DiagnosticRecorder.report

    def patched(self):
        report = original(self)
        if isinstance(report, dict):
            mutate(report)
        return report

    monkeypatch.setattr(verify_diagnostics.DiagnosticRecorder, "report", patched)
    _state, result = _run_verify(monkeypatch, [{"deviceSerial": "C12345678"}])
    return result


@pytest.mark.parametrize(
    "mutate,expected",
    [
        pytest.param(lambda report: None, "passed", id="complete-report"),
        pytest.param(lambda report: report.clear(), "failed", id="empty-dict"),
        pytest.param(lambda report: report.update(events=[]), "failed", id="empty-events"),
        pytest.param(
            lambda report: report.pop("recording_errors"),
            "failed",
            id="missing-counter",
        ),
        pytest.param(
            lambda report: report.update(recording_errors=False),
            "failed",
            id="bool-counter",
        ),
        pytest.param(
            lambda report: report.update(schema_version=True),
            "failed",
            id="wrong-schema",
        ),
        pytest.param(
            lambda report: report.update(scope="overseas_sdk_diagnostics"),
            "failed",
            id="wrong-scope",
        ),
        pytest.param(lambda report: report.update(events={}), "failed", id="non-list-events"),
        pytest.param(
            lambda report: report["events"].append("not-an-event"),
            "failed",
            id="non-dict-event",
        ),
        pytest.param(_drop_step, "failed", id="missing-step"),
        pytest.param(_duplicate_terminal, "failed", id="duplicate-terminal"),
        pytest.param(_fail_terminal, "failed", id="failed-terminal"),
    ],
)
def test_incomplete_diagnostic_report_prevents_pass(monkeypatch, mutate, expected):
    result = _verify_with_mutated_report(monkeypatch, mutate)
    assert result["receipt"]["outcome"] == expected
    assert result["receipt"]["http_requests"] == 7
    payload = json.dumps(result)
    for sentinel in ("C12345678", "PRIVATE_SECRET", "TEST_APP_KEY", "TEST_TOKEN"):
        assert sentinel not in payload


class _FakeHTTPServer:
    """Captures the handler class and a fixed port; never binds a socket."""

    def __init__(self, _address, handler_class):
        self.handler_class = handler_class
        self.server_port = 8765


def _serve_offline(monkeypatch, tmp_path, captured=None):
    """Drive the real ``_make_server`` closures in-process, without sockets."""

    def fake_verify(config):
        if captured is not None:
            captured.append(dict(config))
        return {"receipt": {"outcome": "passed"}, "diagnostics": {"events": []}}

    monkeypatch.setattr(verify_diagnostics, "verify", fake_verify)
    monkeypatch.setattr(verify_diagnostics, "HTTPServer", _FakeHTTPServer)
    server, route, origin = verify_diagnostics._make_server(tmp_path)
    default_headers = {"Host": f"127.0.0.1:{server.server_port}"}

    def request(method, path, *, headers=None, body=b""):
        merged = dict(default_headers)
        merged.update(headers or {})
        return _issue(server.handler_class, method, path, headers=merged, body=body)

    return request, route, origin


def _issue(handler_class, method, path, *, headers, body):
    """Instantiate the handler without its socket constructor and capture the reply."""
    handler = object.__new__(handler_class)
    handler.path = path
    handler.headers = headers
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    sent = {"status": None, "headers": {}}
    handler.send_response = lambda code: sent.__setitem__("status", code)
    handler.send_header = lambda key, value: sent["headers"].__setitem__(key, value)
    handler.end_headers = lambda: None
    getattr(handler, method)()
    assert sent["headers"].get("Cache-Control") == "no-store"
    assert "default-src 'none'" in sent["headers"].get("Content-Security-Policy", "")
    return sent["status"], sent["headers"], handler.wfile.getvalue().decode("utf-8")


def test_serve_consumes_single_post_and_ignores_posted_overrides(monkeypatch, tmp_path):
    captured = []
    request, route, origin = _serve_offline(monkeypatch, tmp_path, captured)
    status, headers, body = request("do_GET", route)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "type=password" in body and "name=app_key" in body
    assert "name=app_secret" in body
    assert "name=region" not in body and "hidden" not in body
    form = urlencode({"app_key": "K", "app_secret": "S", "region": "overseas"}).encode("utf-8")
    status, _, body = request(
        "do_POST",
        route,
        headers={"Origin": origin, "Content-Length": str(len(form))},
        body=form,
    )
    assert status == 200
    assert "diagnostics" in body
    assert captured == [{"app_key": "K", "app_secret": "S"}]
    repeat = urlencode({"app_key": "K", "app_secret": "S"}).encode("utf-8")
    status, _, _ = request(
        "do_POST",
        route,
        headers={"Origin": origin, "Content-Length": str(len(repeat))},
        body=repeat,
    )
    assert status == 409
    status, _, _ = request("do_POST", route, headers={"Origin": "https://x"})
    assert status == 403
    status, _, _ = request("do_GET", route, headers={"Host": "evil.example"})
    assert status == 404
    written = json.loads((tmp_path / "domestic-diagnostic.json").read_text())
    assert written == {"receipt": {"outcome": "passed"}, "diagnostics": {"events": []}}


def test_serve_rejects_oversized_post_before_verify(monkeypatch, tmp_path):
    captured = []
    request, route, origin = _serve_offline(monkeypatch, tmp_path, captured)
    oversized = b"x" * 9000
    status, headers, _ = request(
        "do_POST",
        route,
        headers={
            "Origin": origin,
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(oversized)),
        },
        body=oversized,
    )
    assert status == 400
    assert headers["Cache-Control"] == "no-store"
    assert captured == []


def test_stdin_mode_outputs_only_safe_json(monkeypatch, capsys):
    monkeypatch.setattr(
        verify_diagnostics,
        "verify",
        lambda config: {"receipt": {"outcome": "passed"}, "diagnostics": {"events": []}},
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"app_key": "K", "app_secret": "S"}'))
    assert verify_diagnostics.main([]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out) == {
        "receipt": {"outcome": "passed"},
        "diagnostics": {"events": []},
    }


@pytest.mark.parametrize("outcome", ["failed", "not_verified"])
def test_stdin_mode_returns_nonzero_when_not_passed(monkeypatch, capsys, outcome):
    monkeypatch.setattr(
        verify_diagnostics,
        "verify",
        lambda config: {"receipt": {"outcome": outcome}, "diagnostics": None},
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"app_key": "K", "app_secret": "S"}'))
    assert verify_diagnostics.main([]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["receipt"]["outcome"] == outcome


@pytest.mark.parametrize(
    "payload",
    ["not-json", "[]", '{"region": "overseas"}', '{"app_key": "K", "app_secret": "S"}'],
)
def test_stdin_mode_errors_are_fixed_stderr_without_traceback(monkeypatch, capsys, payload):
    def explode(config):
        raise RuntimeError("PRIVATE_DETAIL")

    monkeypatch.setattr(verify_diagnostics, "verify", explode)
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert verify_diagnostics.main([]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() and "Traceback" not in err and "PRIVATE" not in err


def test_serve_requires_explicit_output_and_creates_nothing(monkeypatch, tmp_path, capsys):
    def forbidden(_output):
        raise AssertionError("serve must not run without an explicit --output")

    monkeypatch.setattr(verify_diagnostics, "serve", forbidden)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        verify_diagnostics.main(["--serve"])
    assert excinfo.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and "--output" in err
    assert list(tmp_path.iterdir()) == []
