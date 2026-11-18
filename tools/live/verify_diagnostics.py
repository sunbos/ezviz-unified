"""Explicit one-shot read-only diagnostic acceptance for one domestic account.

Standalone narrow live tool, not an SDK service. It authenticates once,
reads the first authorized ordinary device from a single ``devices.list``
call (strict ``pageStart=0`` / ``pageSize=1``), binds that device serial
in memory, then runs the fixed five-call ``inspect_device`` sequence on
channel 1. Authentication and business requests share one origin
(``https://open.ys7.com``, default port only), an allowlist of seven
method/path routes each with an exact urlencoded field contract, and a
shared HTTP budget (at most seven requests, at most one per route);
anything outside that boundary is rejected before it is sent. The tool
returns only a bounded receipt plus the diagnostic recorder's whitelisted
report — never credentials, device identifiers, raw objects, or
free-form error text.
"""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from ezviz_unified import Client, RegionConfig
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.inspection import inspect_device

_EXPECTED_ORIGIN = ("https", "open.ys7.com", 443)
_AUTH_ROUTE = ("POST", "/api/lapp/token/get")
_LIST_ROUTE = ("POST", "/api/lapp/device/list")
_INFO_ROUTE = ("POST", "/api/lapp/device/info")
_CHANNELS_ROUTE = ("POST", "/api/lapp/device/camera/list")
_STATUS_ROUTE = ("POST", "/api/lapp/device/status/get")
_CAPACITY_ROUTE = ("POST", "/api/lapp/device/capacity")
_VERSION_ROUTE = ("POST", "/api/lapp/device/version/info")
_DEVICE_ROUTES = frozenset(
    {_INFO_ROUTE, _CHANNELS_ROUTE, _STATUS_ROUTE, _CAPACITY_ROUTE, _VERSION_ROUTE}
)
_BUSINESS_ROUTES = _DEVICE_ROUTES | {_LIST_ROUTE}
_ALLOWED_ROUTES = _BUSINESS_ROUTES | {_AUTH_ROUTE}
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_ROUTE_FIELDS = {
    _AUTH_ROUTE: frozenset({"appKey", "appSecret"}),
    _LIST_ROUTE: frozenset({"accessToken", "pageStart", "pageSize"}),
    _INFO_ROUTE: frozenset({"accessToken", "deviceSerial"}),
    _CHANNELS_ROUTE: frozenset({"accessToken", "deviceSerial"}),
    _STATUS_ROUTE: frozenset({"accessToken", "deviceSerial", "channel"}),
    _CAPACITY_ROUTE: frozenset({"accessToken", "deviceSerial", "channelNo"}),
    _VERSION_ROUTE: frozenset({"accessToken", "deviceSerial"}),
}
_FIXED_FIELD_VALUES = {
    _LIST_ROUTE: {"pageStart": "0", "pageSize": "1"},
    _STATUS_ROUTE: {"channel": "1"},
    _CAPACITY_ROUTE: {"channelNo": "1"},
}
_MAX_HTTP_REQUESTS = 7
_MAX_PER_ROUTE = 1
_MAX_BODY_BYTES = 8192
_RECEIPT_SCOPE = "bounded_domestic_diagnostic_v1"
_EXPECTED_REPORT_SCOPE = "local_sdk_diagnostics"
_RESULT_FILENAME = "domestic-diagnostic.json"
_CONFIG_KEYS = frozenset({"app_key", "app_secret", "region"})
_LOGICAL_STEPS = frozenset(
    {
        "devices.list",
        "devices.get",
        "devices.channels",
        "devices.status",
        "diagnostics.capabilities",
        "diagnostics.version",
    }
)
_TERMINAL_OUTCOMES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
_VALID_OUTCOMES = frozenset({"passed", "failed", "not_verified"})

_FORM = """<!doctype html><meta charset=utf-8><title>EZVIZ 只读诊断验收</title>
<h1>EZVIZ 只读诊断验收</h1><p>固定国内区域：仅认证一次、读取一页设备中的第一台
（pageStart=0、pageSize=1），再对其执行固定的五个只读逻辑调用
（信息、通道、状态、能力、版本，通道1）。最多7次HTTP请求、每路由至多1次；
不查询存储或媒体，不做任何写操作。密钥仅用于本次进程；结果只包含安全回执与
SDK白名单诊断报告，不含设备标识、密钥或原始响应。</p>
<form method=post autocomplete=off>
<label>AppKey<input name=app_key type=password required autocomplete=off></label><br>
<label>AppSecret<input name=app_secret type=password required autocomplete=off></label><br>
<button>运行只读诊断验收</button></form>"""


class _BoundaryRejection(Exception):
    """Fixed stop raised before any out-of-boundary request is sent."""


class _RequestBudget:
    """Shared one-shot HTTP budget across auth and business transports.

    ``bound_device`` pins — in memory only, never logged, hashed, or
    output — the single device serial selected from the first list page;
    the five bound device calls must repeat it verbatim.
    """

    __slots__ = ("_routes", "bound_device", "rejected", "total")

    def __init__(self) -> None:
        self.total = 0
        self.rejected = 0
        self._routes: dict[tuple[str, str], int] = {}
        self.bound_device: str | None = None

    def bind_device(self, serial: str) -> None:
        """Pin the first listed device serial for the rest of the run."""
        if self.bound_device is None and isinstance(serial, str) and serial.strip():
            self.bound_device = serial

    def admit(self, route: tuple[str, str]) -> bool:
        if self.total >= _MAX_HTTP_REQUESTS or self._routes.get(route, 0) >= _MAX_PER_ROUTE:
            self.rejected += 1
            return False
        self._routes[route] = self._routes.get(route, 0) + 1
        self.total += 1
        return True


def _new_delegate_transport() -> httpx.BaseTransport:
    return httpx.HTTPTransport(retries=0, trust_env=False)


def _url_origin_ok(url: httpx.URL, *, root_only: bool) -> bool:
    """Fixed domestic origin: HTTPS, exact host, default port, no extras."""
    port = url.port if url.port is not None else 443
    if (
        (url.scheme, url.host, port) != _EXPECTED_ORIGIN
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        return False
    return not root_only or url.path in ("", "/")


def _parsed_form_fields(request: httpx.Request) -> dict[str, list[str]] | None:
    """Parse a bounded urlencoded body in memory; ``None`` means reject.

    Parsed values stay inside the process — they only authorize the fixed
    field contract and are never logged, echoed, or transformed.
    """
    content_type = request.headers.get("Content-Type", "")
    if not content_type.startswith(_FORM_CONTENT_TYPE):
        return None
    declared = request.headers.get("Content-Length")
    if declared is not None and (
        not declared.isdigit() or not 0 < int(declared) <= _MAX_BODY_BYTES
    ):
        return None
    try:
        body = request.read()
    except Exception:
        return None
    if not 0 < len(body) <= _MAX_BODY_BYTES:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        return parse_qs(text, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None


def _fields_allowed(route: tuple[str, str], request: httpx.Request, budget: _RequestBudget) -> bool:
    """Exact urlencoded field contract for one allowlisted route.

    Shared by the sync and async guards; body bytes must already be
    buffered on ``request`` (this SDK always sends form bytes). Parsed
    values stay inside the process — they only authorize the fixed field
    contract and are never logged, echoed, or transformed.
    """
    fields = _parsed_form_fields(request)
    if fields is None or set(fields) != _ROUTE_FIELDS[route]:
        return False
    if any(len(values) != 1 or not values[0].strip() for values in fields.values()):
        return False
    for key, expected in _FIXED_FIELD_VALUES.get(route, {}).items():
        if fields.get(key) != [expected]:
            return False
    if route in _DEVICE_ROUTES:
        bound = budget.bound_device
        if bound is None or fields["deviceSerial"] != [bound]:
            return False
    return True


class _GuardedTransport(httpx.BaseTransport):
    """Delegates only allowlisted domestic read requests inside the budget.

    Beyond method/path/origin, a candidate request must satisfy the exact
    urlencoded field contract for its route — precise key sets, single
    non-blank values, the fixed first page, channel 1 where applicable,
    and the bound device serial — before any budget is spent or the
    delegate is invoked.
    """

    def __init__(self, delegate: httpx.BaseTransport, budget: _RequestBudget) -> None:
        self._delegate = delegate
        self._budget = budget

    def _fields_allowed(self, route: tuple[str, str], request: httpx.Request) -> bool:
        return _fields_allowed(route, request, self._budget)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        route = (request.method, request.url.path)
        if (
            not _url_origin_ok(request.url, root_only=False)
            or route not in _ALLOWED_ROUTES
            or not self._fields_allowed(route, request)
            or not self._budget.admit(route)
        ):
            raise _BoundaryRejection("request is outside the diagnostic acceptance boundary")
        return self._delegate.handle_request(request)

    def close(self) -> None:
        self._delegate.close()


def _guarded_factory(budget: _RequestBudget) -> Callable[[str], httpx.BaseTransport]:
    def factory(origin: str) -> httpx.BaseTransport:
        if not _url_origin_ok(httpx.URL(origin), root_only=True):
            raise _BoundaryRejection("origin is outside the diagnostic acceptance boundary")
        return _GuardedTransport(_new_delegate_transport(), budget)

    return factory


def _credentials(config: object) -> tuple[str, str]:
    if not isinstance(config, dict) or not config.keys() <= _CONFIG_KEYS:
        raise ValueError("config keys are limited to app_key, app_secret and optional region")
    if config.get("region", "domestic") != "domestic":
        raise ValueError("this acceptance run is fixed to the domestic region")
    app_key = config.get("app_key")
    app_secret = config.get("app_secret")
    if (
        not isinstance(app_key, str)
        or not app_key.strip()
        or not isinstance(app_secret, str)
        or not app_secret.strip()
    ):
        raise ValueError("app_key and app_secret must be non-blank strings")
    return app_key, app_secret


def _form_config(values: Mapping[str, list[str]]) -> dict[str, str]:
    """Keep only credentials; posted mode or region fields are ignored."""
    return {
        "app_key": values.get("app_key", [""])[0],
        "app_secret": values.get("app_secret", [""])[0],
    }


def _count_logical_calls(report: object) -> int:
    """Count terminal facade ``call`` events for the six bounded calls.

    Only ``call`` events whose ``entrypoint`` is one of the six logical
    steps and whose outcome is terminal are counted — nothing is
    double-counted and nothing is clamped.
    """
    if not isinstance(report, dict):
        return 0
    events = report.get("events")
    count = 0
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            if (
                event.get("kind") == "call"
                and event.get("outcome") in _TERMINAL_OUTCOMES
                and event.get("entrypoint") in _LOGICAL_STEPS
            ):
                count += 1
    return count


def _diagnostic_report_intact(report: object, http_requests: int) -> bool:
    """Whether the report proves the instrumented run completed intact.

    ``passed`` requires the real recorder shape — exact-int schema ``1``,
    the fixed ``local_sdk_diagnostics`` scope, exact-int zero counters and
    a list of dict events — plus exactly one ``succeeded`` terminal
    ``call`` event per logical entrypoint (never ``client.request``) and a
    fully spent seven-request budget. Anything less is ``failed``.
    """
    if not isinstance(report, dict) or http_requests != _MAX_HTTP_REQUESTS:
        return False
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        return False
    if type(report.get("scope")) is not str or report["scope"] != _EXPECTED_REPORT_SCOPE:
        return False
    for counter in ("recording_errors", "dropped_events"):
        value = report.get(counter)
        if type(value) is not int or value != 0:
            return False
    events = report.get("events")
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        return False
    for step in _LOGICAL_STEPS:
        terminals = [
            event
            for event in events
            if event.get("kind") == "call"
            and event.get("entrypoint") == step
            and event.get("outcome") in _TERMINAL_OUTCOMES
        ]
        if len(terminals) != 1 or terminals[0].get("outcome") != "succeeded":
            return False
    return True


def verify(config: dict[str, str]) -> dict[str, object]:
    """Run the bounded one-shot check; the result stays whitelisted either way.

    Returns ``{"receipt": ..., "diagnostics": ...}`` where ``diagnostics``
    is ``recorder.report()``. Any failure — including a rejected boundary
    request — ends the run with the fixed ``failed`` outcome; an empty
    account or a missing device serial yields ``not_verified``. The first
    listed serial is bound into the shared budget before the five device
    calls. ``passed`` additionally requires an intact report — the exact
    recorder schema, zero counters, one ``succeeded`` terminal ``call``
    event per logical entrypoint and a fully spent seven-request budget —
    anything less demotes to ``failed``. The client is always closed
    before the recorder snapshot is taken.
    """
    app_key, app_secret = _credentials(config)
    recorder = DiagnosticRecorder()
    budget = _RequestBudget()
    client: Client | None = None
    outcome = "failed"
    report: dict[str, object] | None = None
    try:
        client = Client(
            app_key,
            app_secret,
            region=RegionConfig.china(),
            timeout=10,
            auth_transport=_GuardedTransport(_new_delegate_transport(), budget),
            transport_factory=_guarded_factory(budget),
            diagnostic_recorder=recorder,
        )
        response = client.devices.list(page_start=0, page_size=1)
        rows = response.data
        if not isinstance(rows, list):
            outcome = "failed"
        elif not rows:
            outcome = "not_verified"
        else:
            first = rows[0]
            serial = first.get("deviceSerial") if isinstance(first, dict) else None
            if not isinstance(serial, str) or not serial.strip():
                outcome = "not_verified"
            else:
                budget.bind_device(serial)
                inspection = inspect_device(client, serial, channel=1)
                results = inspection.results
                outcome = (
                    "passed"
                    if len(results) == 5 and all(item.succeeded for item in results)
                    else "failed"
                )
    except Exception:
        outcome = "failed"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                outcome = "failed"
        try:
            report = recorder.report()
        except Exception:
            report = None
            outcome = "failed"
    if outcome == "passed" and not _diagnostic_report_intact(report, budget.total):
        outcome = "failed"
    receipt = {
        "schema_version": 1,
        "scope": _RECEIPT_SCOPE,
        "outcome": outcome if outcome in _VALID_OUTCOMES else "failed",
        "logical_calls": _count_logical_calls(report),
        "http_requests": budget.total,
        "read_only": True,
    }
    return {"receipt": receipt, "diagnostics": report}


def _page(result: object) -> str:
    return (
        "<h1>只读诊断验收结果</h1><pre>"
        + html.escape(json.dumps(result, ensure_ascii=False, indent=2))
        + "</pre>"
    )


def _make_server(output: Path) -> tuple[HTTPServer, str, str]:
    """Loopback form whose single claimed POST reaches the local SDK once."""
    route = "/" + secrets.token_urlsafe(24)
    claimed = {"consumed": False}
    origin = ""
    expected_host = ""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def _reply(self, status: int, body: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body.encode())

        def _host_ok(self) -> bool:
            return self.headers.get("Host") == expected_host

        def do_GET(self) -> None:
            if self.path != route or not self._host_ok():
                self._reply(404, "Not found")
                return
            self._reply(200, _FORM)

        def do_POST(self) -> None:
            if self.path != route or self.headers.get("Origin") != origin or not self._host_ok():
                self._reply(403, "Forbidden")
                return
            if claimed["consumed"]:
                self._reply(409, "Already consumed")
                return
            claimed["consumed"] = True
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= _MAX_BODY_BYTES:
                    raise ValueError("invalid input size")
                values = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
                config = _form_config(values)
                values.clear()
                try:
                    result = verify(config)
                finally:
                    config.clear()
            except Exception:
                self._reply(400, "Verification stopped.")
                return
            output.mkdir(parents=True, exist_ok=True)
            destination = output / _RESULT_FILENAME
            destination.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            self._reply(200, _page(result))

    server = HTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    expected_host = f"127.0.0.1:{server.server_port}"
    return server, route, origin


def serve(output: Path) -> None:
    """Print a temporary loopback URL; the form submits credentials locally."""
    server, route, origin = _make_server(output)
    print(origin + route, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.serve:
        if args.output is None:
            parser.error("--serve requires an explicit --output directory")
        try:
            serve(args.output)
        except Exception:
            sys.stderr.write("verification stopped\n")
            return 1
        return 0
    try:
        result = verify(json.load(sys.stdin))
    except Exception:
        sys.stderr.write("verification stopped\n")
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    receipt = result.get("receipt") if isinstance(result, dict) else None
    if not isinstance(receipt, dict) or receipt.get("outcome") != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
