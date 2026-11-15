"""Explicit read-only live verification; credentials stay in process memory."""

from __future__ import annotations

import argparse
import html
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from ezviz_unified import Client, EzvizError, RegionConfig
from ezviz_unified.inspection import inspect_device

_READ_ROUTES = frozenset(
    {
        ("POST", "/api/lapp/device/list"),
        ("POST", "/api/lapp/trust/device/list"),
        ("POST", "/api/lapp/device/info"),
        ("POST", "/api/lapp/device/camera/list"),
        ("POST", "/api/lapp/device/status/get"),
        ("POST", "/api/lapp/device/support/ezviz"),
        ("POST", "/api/lapp/device/capacity"),
        ("POST", "/api/lapp/device/version/info"),
        ("GET", "/api/v3/device/format/status"),
        ("GET", "/api/v3/device/diskCapacity"),
    }
)
_CAPABILITY_FLAGS = (
    "support_cloud",
    "support_talk",
    "support_capture",
    "support_disk",
    "support_defence",
    "support_defenceplan",
    "ptz_preset",
    "ptz_top_bottom",
    "ptz_left_right",
    "ptz_center_mirror",
)

_DEFAULT_SCOPE = "bounded_readonly_request_and_response_shape_check"
_INSPECTION_SCOPE = "bounded_readonly_inspection_policy_check"


class _ReadOnlyTransport(httpx.BaseTransport):
    def __init__(self, delegate: httpx.BaseTransport, origin: str = "https://open.ys7.com"):
        self._delegate = delegate
        self._origin = httpx.URL(origin)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        actual = (request.url.scheme, request.url.host, request.url.port)
        expected = (self._origin.scheme, self._origin.host, self._origin.port)
        if (
            actual != expected
            or request.url.username
            or request.url.password
            or (request.method, request.url.path) not in _READ_ROUTES
        ):
            raise ValueError("request is outside the read-only probe allowlist")
        return self._delegate.handle_request(request)

    def close(self) -> None:
        self._delegate.close()


def _flag(value: object) -> bool | None:
    if (type(value) is int and value in (0, 1)) or (isinstance(value, str) and value in ("0", "1")):
        return value in (1, "1")
    return None


def _diagnostic_summary(name: str, data: object) -> dict[str, object] | None:
    if not isinstance(data, dict):
        return None
    if name == "capabilities":
        return {
            "advertised_flags": {
                key: flag for key in _CAPABILITY_FLAGS if (flag := _flag(data.get(key))) is not None
            }
        }
    if name == "version":
        if not all(
            isinstance(data.get(key), str) and data[key].strip()
            for key in ("currentVersion", "latestVersion")
        ):
            return None
        return {
            "version_fields_present": True,
            "upgrade_needed_reported": _flag(data.get("isNeedUpgrade")),
            "upgrading_reported": _flag(data.get("isUpgrading")),
        }
    if name == "storage_status":
        media = data.get("storageStatus")
        if not isinstance(media, list) or not all(
            isinstance(row, dict)
            and isinstance(row.get("status"), str)
            and row["status"] in tuple(str(i) for i in range(8))
            for row in media
        ):
            return None
        return {"media_count": len(media), "status_codes": [row["status"] for row in media]}
    capacity = data.get("diskCapacity")
    if not isinstance(capacity, str) or not re.fullmatch(r"-?\d+(?:,-?\d+)*", capacity):
        return None
    return {"capacity_entries_reported": len(capacity.split(","))}


def _public_receipt(report: dict[str, object]) -> dict[str, object]:
    """Persist request outcomes without publishing a device's state or capabilities."""
    allowed = {
        "operation",
        "status",
        "error_type",
        "code",
        "http_status",
        "returned_items",
        "sample_source_doc",
        "reason",
    }
    result = {
        key: report[key]
        for key in ("recorded_at_utc", "region", "auth_origin", "business_origins", "read_only")
        if key in report
    }
    rows = []
    entries = report.get("results")
    if not isinstance(entries, list) or not all(isinstance(row, dict) for row in entries):
        raise ValueError("invalid probe results")
    for row in entries:
        public = {key: value for key, value in row.items() if key in allowed}
        if public.get("reason") in {
            "storage_capability_not_advertised",
            "device_online_not_verified",
        }:
            public["reason"] = "not_selected_for_this_probe"
        rows.append(public)
    result["results"] = rows
    result["validation_scope"] = (
        _INSPECTION_SCOPE if report.get("validation_scope") == _INSPECTION_SCOPE else _DEFAULT_SCOPE
    )
    return result


def verify(config: dict[str, str]) -> dict[str, object]:
    scope = config["region"]
    if scope not in {"domestic", "overseas"}:
        raise ValueError("invalid region")
    include_managed = config.get("include_managed") == "true"
    include_diagnostics = config.get("include_diagnostics") == "true"
    protocol_only = config.get("protocol_only") == "true"
    inspection = config.get("inspection") == "true"
    if inspection and (include_managed or include_diagnostics or protocol_only):
        raise ValueError("inspection profile cannot combine with other probe options")
    if protocol_only and (include_managed or include_diagnostics or scope != "domestic"):
        raise ValueError("protocol probe requires domestic scope without managed inventory")
    if include_managed and scope != "domestic":
        raise ValueError("managed inventory is only documented for domestic accounts")
    region = (
        RegionConfig.china()
        if scope == "domestic"
        else RegionConfig.overseas(
            auth_base_url=config.get("auth_base_url") or "https://open.ezvizlife.com"
        )
    )
    results: list[dict[str, object]] = []
    origins: list[str] = []
    report = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "region": scope,
        "auth_origin": region.auth_base_url,
        "business_origins": origins,
        "read_only": True,
        "results": results,
    }
    if inspection:
        report["validation_scope"] = _INSPECTION_SCOPE

    def transport(origin: str) -> httpx.BaseTransport:
        origins.append(origin)
        return _ReadOnlyTransport(httpx.HTTPTransport(retries=0, trust_env=False), origin)

    def record_error(operation: str, error: EzvizError) -> None:
        code = str(error.code) if error.code is not None else ""
        results.append(
            {
                "operation": operation,
                "status": "failed",
                "error_type": type(error).__name__,
                "code": code if re.fullmatch(r"\d{3,8}", code) else None,
                "http_status": error.http_status,
            }
        )

    with Client(
        config["app_key"],
        config["app_secret"],
        region=region,
        timeout=10,
        transport_factory=transport,
    ) as client:
        if protocol_only:
            try:
                sample = client.diagnostics.protocol_support(
                    "CS-C1-10F", "V4.1.0 build 130101", app_key=config["app_key"]
                )
                if isinstance(sample.data, list):
                    flags = [row.get("isSupport") for row in sample.data if isinstance(row, dict)]
                    results.append(
                        {
                            "operation": "diagnostics.protocol_support",
                            "status": "passed",
                            "sample_source_doc": "652",
                            "returned_items": len(sample.data),
                            "reported_support": [
                                flag for flag in flags if type(flag) is int and flag in (0, 1)
                            ],
                        }
                    )
                else:
                    results.append(
                        {
                            "operation": "diagnostics.protocol_support",
                            "status": "unexpected_data_shape",
                        }
                    )
            except EzvizError as exc:
                record_error("diagnostics.protocol_support", exc)
            return report
        try:
            response = client.devices.list(page_size=1)
        except EzvizError as exc:
            record_error("devices.list", exc)
            return report
        rows = response.data
        if not isinstance(rows, list):
            results.append({"operation": "devices.list", "status": "unexpected_data_shape"})
            return report
        results.append(
            {"operation": "devices.list", "status": "passed", "returned_items": len(rows)}
        )
        if include_managed:
            try:
                managed = client.devices.list_managed(page_size=1)
                if isinstance(managed.data, list):
                    results.append(
                        {
                            "operation": "devices.list_managed",
                            "status": "passed",
                            "returned_items": len(managed.data),
                        }
                    )
                else:
                    results.append(
                        {"operation": "devices.list_managed", "status": "unexpected_data_shape"}
                    )
            except EzvizError as exc:
                record_error("devices.list_managed", exc)
        if inspection:
            serial = rows[0].get("deviceSerial") if rows and isinstance(rows[0], dict) else None
            if not isinstance(serial, str) or not serial.strip():
                reason = "no_device" if not rows else "missing_device_serial"
                results.append(
                    {
                        "operation": "inspect_device",
                        "status": "not_verified",
                        "reason": reason,
                    }
                )
                return report
            try:
                outcome = inspect_device(client, serial, channel=1)
            except EzvizError as exc:
                record_error("inspect_device", exc)
                return report
            for row in outcome.public_summary()["results"]:
                item: dict[str, object] = {
                    "operation": row["operation"],
                    "status": "passed" if row["status"] == "ok" else "failed",
                }
                for key in ("error_type", "code", "http_status"):
                    if key in row:
                        item[key] = row[key]
                results.append(item)
            return report
        serial = rows[0].get("deviceSerial") if rows and isinstance(rows[0], dict) else None
        if not isinstance(serial, str) or not serial.strip():
            reason = "no_device" if not rows else "missing_device_serial"
            results.append(
                {"operation": "device_details", "status": "not_verified", "reason": reason}
            )
            return report
        online = None
        for name in ("get", "channels", "status"):
            try:
                response = getattr(client.devices, name)(serial)
                expected_type = list if name == "channels" else dict
                status = (
                    "passed"
                    if isinstance(response.data, expected_type)
                    else "unexpected_data_shape"
                )
                item: dict[str, object] = {"operation": f"devices.{name}", "status": status}
                if name == "get" and isinstance(response.data, dict):
                    value = response.data.get("status")
                    if type(value) is int and value in (0, 1):
                        online = value == 1
                        item["online"] = online
                results.append(item)
            except EzvizError as exc:
                record_error(f"devices.{name}", exc)
        if include_diagnostics:
            disk_advertised = False
            probes = ["capabilities", "version", "storage_status"]
            if scope == "domestic":
                probes.append("disk_capacity")
            for name in probes:
                operation = f"diagnostics.{name}"
                if name in {"storage_status", "disk_capacity"} and (
                    online is not True or not disk_advertised
                ):
                    reason = (
                        "device_online_not_verified"
                        if online is not True
                        else "storage_capability_not_advertised"
                    )
                    results.append(
                        {"operation": operation, "status": "not_verified", "reason": reason}
                    )
                    continue
                try:
                    response = getattr(client.diagnostics, name)(serial)
                    summary = _diagnostic_summary(name, response.data)
                    if summary is None:
                        results.append({"operation": operation, "status": "unexpected_data_shape"})
                        continue
                    results.append({"operation": operation, "status": "passed", **summary})
                    if name == "capabilities":
                        flags = summary.get("advertised_flags")
                        disk_advertised = (
                            isinstance(flags, dict) and flags.get("support_disk") is True
                        )
                except EzvizError as exc:
                    record_error(operation, exc)
    return report


def serve(
    output: Path,
    *,
    include_managed: bool = False,
    protocol_only: bool = False,
    include_diagnostics: bool = False,
    inspection: bool = False,
) -> None:
    """Temporary loopback form lets authorized browser inputs reach the local SDK."""
    route = "/" + secrets.token_urlsafe(24)
    form = """<!doctype html><meta charset=utf-8><title>EZVIZ 只读验收</title>
    <h1>EZVIZ 只读验收</h1><p>仅认证、查询一页设备及第一台设备的资料、通道、状态。
    密钥仅用于本次进程；结果不包含设备标识、密钥或原始响应。</p>
    <form method=post autocomplete=off>
    <label>区域<select name=region><option value=domestic>国内</option>
    <option value=overseas>海外</option></select></label><br>
    <label>AppKey<input name=app_key type=password required autocomplete=off></label><br>
    <label>AppSecret<input name=app_secret type=password required autocomplete=off></label><br>
    <label>海外认证入口（留空使用官方文档默认值）<input name=auth_base_url></label><br>
    <button>运行只读验收</button></form>"""
    if include_managed:
        form = form.replace(
            "<form", "<p>另查询一页国内托管设备，仅记录条数，不查询托管设备详情。</p><form", 1
        )
        form = form.replace(
            "</form>", '<input type="hidden" name="include_managed" value="true"></form>'
        )
    if protocol_only:
        form = form.replace(
            "仅认证、查询一页设备及第一台设备的资料、通道、状态。",
            "仅认证并查询文档652公开示例型号的协议支持，不查询或操作具体设备。",
        )
        form = form.replace(
            "</form>", '<input type="hidden" name="protocol_only" value="true"></form>'
        )
    if include_diagnostics:
        form = form.replace(
            "<form",
            "<p>追加能力与版本查询；在线且报告存储能力时查询存储状态和容量。不会修改设备。</p><form",
            1,
        )
        form = form.replace(
            "</form>", '<input type="hidden" name="include_diagnostics" value="true"></form>'
        )
    if inspection:
        form = form.replace(
            "<form",
            "<p>检查模式：读取第一页设备后，仅对第一台设备依次执行信息、通道、状态、能力、版本"
            "共五个只读逻辑调用（固定通道1），不做存储、媒体查询或任何写操作。</p><form",
            1,
        )
        form = form.replace(
            "</form>", '<input type="hidden" name="inspection" value="true"></form>'
        )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def reply(self, status: int, body: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:
            self.reply(200, form) if self.path == route else self.reply(404, "Not found")

        def do_POST(self) -> None:
            if self.path != route or self.headers.get("Origin") != origin:
                self.reply(403, "Forbidden")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("invalid input size")
                values = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
                config = {k: v[0] for k, v in values.items()}
                result = verify(config)
                config.clear()
                values.clear()
            except Exception as exc:
                self.reply(400, f"Verification stopped: {html.escape(type(exc).__name__)}")
                return
            output.mkdir(parents=True, exist_ok=True)
            destination = output / f"{result['region']}.json"
            destination.write_text(
                json.dumps(_public_receipt(result), ensure_ascii=False, indent=2) + "\n"
            )
            self.reply(
                200,
                "<h1>只读验收结果</h1><pre>"
                + html.escape(json.dumps(result, ensure_ascii=False, indent=2))
                + "</pre>",
            )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    origin = f"http://127.0.0.1:{server.server_port}"
    print(origin + route, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--include-managed", action="store_true")
    mode.add_argument("--protocol-only", action="store_true")
    mode.add_argument("--inspection", action="store_true")
    parser.add_argument("--include-diagnostics", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("docs/validation/live"))
    args = parser.parse_args()
    if args.protocol_only and args.include_diagnostics:
        parser.error("--protocol-only cannot be combined with --include-diagnostics")
    if args.inspection and args.include_diagnostics:
        parser.error("--inspection cannot be combined with --include-diagnostics")
    if args.serve:
        serve(
            args.output,
            include_managed=args.include_managed,
            protocol_only=args.protocol_only,
            include_diagnostics=args.include_diagnostics,
            inspection=args.inspection,
        )
    else:
        try:
            config = json.load(sys.stdin)
            if args.include_managed:
                config["include_managed"] = "true"
            if args.protocol_only:
                config["protocol_only"] = "true"
            if args.include_diagnostics:
                config["include_diagnostics"] = "true"
            if args.inspection:
                config["inspection"] = "true"
            print(json.dumps(_public_receipt(verify(config)), ensure_ascii=False, indent=2))
        except Exception as error:
            print(json.dumps({"status": "stopped", "error_type": type(error).__name__}))
            sys.exit(1)
