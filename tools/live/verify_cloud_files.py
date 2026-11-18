"""Bounded one-shot read-only domestic VOD resource discovery acceptance.

Standalone narrow live tool, not an SDK service. It authenticates once for a
single domestic client, reads one page of VOD-type storage spaces created
inside a fixed 30-day ``createTime`` window (strict ``pageSize=50`` /
``bizTypeList=vod``, one page only, no pagination), binds the first ordinary
space — a dict row with ``bizType`` ``"vod"``, int ``namespaceType`` ``1``
and an int ``spaceId`` — in memory, then issues exactly one ``vod.files``
call on that bound id. Authentication and business requests share one
origin (``https://open.ys7.com``, default port only), an allowlist of three
method/path routes each with an exact header/query/urlencoded field
contract, and a shared HTTP budget (at most seven requests total, at most
3/2/2 per route); anything outside that boundary is rejected before it is
sent. Every response is forced to ``Accept-Encoding: identity`` and always
closed; the tool retains and reassembles at most 1 MiB of response payload
(a received-body cap, not a guarantee about network bytes or process
memory — the first chunk that would push the retained total past 1 MiB is
refused without buffering and the stream is closed at once rather than
consumed to the end). The intake form waits at most 600 seconds overall;
each accepted connection gets an absolute 10-second deadline covering its
request line, headers and body — slow drips cannot extend it. Once a
valid submission starts the probe, a 120-second soft deadline is checked
only before each request is sent and between response chunks. The
underlying HTTPX calls can block past the cutoff more than once (for
example while waiting on a slow response header before chunk iteration
starts), so it is not a hard cutoff and no worst-case total such as
120+10 seconds is guaranteed. The tool returns only a
whitelisted report — counts, fixed enums and type buckets — never
credentials, identifiers, raw objects, URLs, or free-form error text.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import secrets
import socket
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from http.client import parse_headers as _http_parse_headers
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from ezviz_unified import Client, RegionConfig
from ezviz_unified.errors import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)
from tools.live.verify_diagnostics import _credentials

_EXPECTED_ORIGIN = ("https", "open.ys7.com", 443)
_AUTH_ROUTE = ("POST", "/api/lapp/token/get")
_SPACES_ROUTE = ("GET", "/api/service/open/storage/engine/space/listById")
_VOD_ROUTE = ("GET", "/api/service/open/vod/files")
_ALLOWED_ROUTES = frozenset({_AUTH_ROUTE, _SPACES_ROUTE, _VOD_ROUTE})
_ROUTE_CAPS = {_AUTH_ROUTE: 3, _SPACES_ROUTE: 2, _VOD_ROUTE: 2}
_ROUTE_STAGE = {
    _AUTH_ROUTE: "authentication",
    _SPACES_ROUTE: "spaces",
    _VOD_ROUTE: "vod",
}
_MAX_HTTP_REQUESTS = 7
_MAX_BODY_BYTES = 8192
_MAX_RESPONSE_BYTES = 1024 * 1024
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_SCOPE = "bounded_domestic_vod_resource_discovery"
_V1_DISCOVERY = "not_available_in_current_sdk"
_SHANGHAI_WALL = timezone(timedelta(hours=8))
_WINDOW = timedelta(days=30)
_PAGE_SIZE_VALUE = "50"
_BIZ_TYPE = "vod"
_MAX_FIELD_SAMPLE_ITEMS = 50
_SERVE_TIMEOUT_SECONDS = 600
_CONNECTION_DEADLINE_SECONDS = 10
_PROBE_DEADLINE_SECONDS = 120
_TIME_PARAM_FORMAT = "%Y-%m-%d %H:%M:%S"
_KNOWN_VOD_FIELDS = (
    "folderNode",
    "fileName",
    "fileType",
    "fileChildType",
    "fileSize",
    "totalSize",
    "storageType",
    "uploadStatus",
    "createTime",
    "expireTime",
    "coverPic",
    "coverPicUrl",
    "urls",
)
_TYPE_BUCKETS = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "other", "string"}
)
_VOD_DATA_TYPES = frozenset({"array", "object", "null", "other"})
_OUTCOMES = frozenset({"success", "not_verified", "failed"})
_STAGES = frozenset({"credential", "authentication", "spaces", "vod", "complete"})
_ERROR_FAMILIES = frozenset(
    {"none", "authentication", "permission", "api", "protocol", "transport", "unknown"}
)
_REASON_OK = "ok"
_REASON_INVALID_CONFIG = "invalid_config"
_REASON_EMPTY_PAGE = "empty_space_page"
_REASON_NO_CANDIDATE = "no_usable_vod_space_in_selected_page"
_REASON_SPACE_SHAPE = "unexpected_space_page_shape"
_REASON_VOD_SHAPE = "unexpected_vod_data_shape"
_REASON_BOUNDARY = "boundary_rejection"
_REASON_DEADLINE = "deadline_exceeded"
_REASON_CLOSE_FAILURE = "close_failure"
_REASON_UNKNOWN = "unknown_error"
_FAMILY_REASON = {
    "authentication": "authentication_error",
    "permission": "permission_denied",
    "api": "api_error",
    "protocol": _REASON_BOUNDARY,
    "transport": "transport_error",
    "unknown": _REASON_UNKNOWN,
}
_REASONS = frozenset(
    {
        _REASON_OK,
        _REASON_INVALID_CONFIG,
        _REASON_EMPTY_PAGE,
        _REASON_NO_CANDIDATE,
        _REASON_SPACE_SHAPE,
        _REASON_VOD_SHAPE,
        _REASON_BOUNDARY,
        _REASON_DEADLINE,
        _REASON_CLOSE_FAILURE,
        *_FAMILY_REASON.values(),
    }
)
_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "region",
        "recorded_at_utc",
        "window_start",
        "window_end",
        "window_basis",
        "window_timezone_assumption",
        "max_http_requests",
        "http_requests",
        "logical_calls",
        "route_counts",
        "stage",
        "outcome",
        "reason",
        "space_page_count",
        "space_has_next",
        "ordinary_vod_candidate_found",
        "vod_data_type",
        "vod_item_count",
        "known_field_types",
        "error_family",
        "cloud_recording_v1_discovery",
    }
)

_FORM = """<!doctype html><meta charset=utf-8><title>EZVIZ VOD 只读探测验收</title>
<h1>EZVIZ VOD 只读探测验收</h1><p>固定国内区域：认证一次后读取30天createTime
窗口内一页VOD类型空间（pageSize=50、bizTypeList=vod，不翻页），再对第一条合格
普通VOD空间（bizType=vod、namespaceType=1、整型spaceId）执行一次vod.files只读
查询。最多7次HTTP请求（认证≤3、spaces≤2、vod≤2）；不访问/下载媒体URL，不查询
1.0，不做任何写操作。密钥仅用于本次进程内存；报告只含计数、固定枚举与类型桶，
不含标识符、密钥、URL或原始响应。</p>
<form method=post autocomplete=off>
<label>AppKey<input name=app_key type=password required autocomplete=off></label><br>
<label>AppSecret<input name=app_secret type=password required autocomplete=off></label><br>
<button>运行只读探测验收</button></form>"""


class _BoundaryRejection(Exception):
    """Fixed stop raised before any out-of-boundary traffic is delegated."""


def _new_inner_transport() -> httpx.BaseTransport:
    """Delegate seam; tests may monkeypatch this with an offline transport."""
    return httpx.HTTPTransport(retries=0, trust_env=False)


class _RequestBudget:
    """Shared one-shot HTTP budget plus in-memory-only resource binding.

    ``bound_space`` pins the integer ``spaceId`` selected from this run's
    space page, and the window is frozen at construction by ``verify`` to
    the exact ``startTime``/``endTime`` pair it will send — the spaces
    request, including a token-refresh replay, must repeat the identical
    query; it never binds to whatever an outbound request first claims.
    Bound values are never logged, hashed, or written to the report.
    """

    __slots__ = ("total", "rejected", "last_stage", "_routes", "bound_space", "_bound_window")

    def __init__(self, window: tuple[str, str]) -> None:
        self.total = 0
        self.rejected = 0
        self.last_stage: str | None = None
        self._routes: dict[tuple[str, str], int] = {}
        self.bound_space: int | None = None
        # Frozen before the run: ``(startTime, endTime)`` exactly as the
        # tool serialized them. Every spaces request — including the single
        # replay after an invalid-token refresh — must repeat this pair.
        self._bound_window = window

    def bind_space(self, space_id: object) -> None:
        if self.bound_space is None and type(space_id) is int:
            self.bound_space = space_id

    def check_window(self, start: str, end: str) -> bool:
        return self._bound_window == (start, end)

    def admit(self, route: tuple[str, str]) -> bool:
        if self.total >= _MAX_HTTP_REQUESTS or self._routes.get(route, 0) >= _ROUTE_CAPS[route]:
            self.rejected += 1
            return False
        self._routes[route] = self._routes.get(route, 0) + 1
        self.total += 1
        self.last_stage = _ROUTE_STAGE[route]
        return True

    def route_counts(self) -> dict[str, int]:
        return {
            "authentication": self._routes.get(_AUTH_ROUTE, 0),
            "spaces": self._routes.get(_SPACES_ROUTE, 0),
            "vod": self._routes.get(_VOD_ROUTE, 0),
        }


def _url_origin_ok(url: httpx.URL, *, root_only: bool) -> bool:
    """Fixed domestic origin: HTTPS, exact host, default port, no extras."""
    port = url.port if url.port is not None else 443
    if (
        (url.scheme, url.host, port) != _EXPECTED_ORIGIN
        or url.username
        or url.password
        or url.fragment
    ):
        return False
    if root_only:
        return url.path in ("", "/") and not url.query
    return True


def _urlencoded_fields(request: httpx.Request) -> dict[str, list[str]] | None:
    """Parse a bounded urlencoded body in memory; ``None`` means reject."""
    if len(request.headers.get_list("Content-Type")) != 1:
        return None
    content_type = request.headers.get("Content-Type", "")
    if content_type.split(";", 1)[0].strip().lower() != _FORM_CONTENT_TYPE:
        return None
    declared_values = request.headers.get_list("Content-Length")
    if len(declared_values) != 1:
        return None
    declared = declared_values[0]
    if not _ascii_digits(declared) or not 0 < int(declared) <= _MAX_BODY_BYTES:
        return None
    if request.headers.get_list("Transfer-Encoding"):
        return None
    try:
        body = request.read()
    except Exception:
        return None
    if len(body) != int(declared) or not 0 < len(body) <= _MAX_BODY_BYTES:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        return parse_qs(
            text,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
        )
    except ValueError:
        return None


def _ascii_digits(value: object) -> bool:
    """True only for a non-empty string of ASCII ``0-9`` characters."""
    return isinstance(value, str) and bool(value) and all("0" <= c <= "9" for c in value)


def _single_header(request: httpx.Request, name: str) -> bool:
    values = request.headers.get_list(name)
    return len(values) == 1 and bool(values[0].strip())


def _auth_allowed(request: httpx.Request) -> bool:
    """Auth: no query params, urlencoded form of exactly appKey/appSecret."""
    if request.url.params.multi_items():
        return False
    fields = _urlencoded_fields(request)
    return (
        fields is not None
        and set(fields) == {"appKey", "appSecret"}
        and all(len(v) == 1 and bool(v[0].strip()) for v in fields.values())
    )


def _get_fields(request: httpx.Request) -> dict[str, str] | None:
    """GET contract: empty body, single-valued query keys, token in header."""
    try:
        if request.read():
            return None
    except Exception:
        return None
    if not _single_header(request, "accessToken"):
        return None
    items = request.url.params.multi_items()
    keys = [key for key, _ in items]
    if len(keys) != len(set(keys)):
        return None
    return dict(items)


def _spaces_allowed(request: httpx.Request, budget: _RequestBudget) -> bool:
    values = _get_fields(request)
    if values is None or set(values) != {"startTime", "endTime", "pageSize", "bizTypeList"}:
        return False
    if values["pageSize"] != _PAGE_SIZE_VALUE or values["bizTypeList"] != _BIZ_TYPE:
        return False
    return budget.check_window(values["startTime"], values["endTime"])


def _vod_allowed(request: httpx.Request, budget: _RequestBudget) -> bool:
    values = _get_fields(request)
    bound = budget.bound_space
    return (
        values is not None
        and set(values) == {"spaceId"}
        and bound is not None
        and values["spaceId"] == str(bound)
    )


def _bounded_response(
    request: httpx.Request,
    response: httpx.Response,
    deadline: float | None,
) -> httpx.Response:
    """Retain at most 1 MiB of an identity-encoded payload; always close.

    The cap bounds the received payload this tool keeps and reassembles —
    it is not a limit on bytes already read by the delegate or on overall
    process memory. The first chunk that would push the retained total
    past the cap is refused without being buffered: iteration stops and
    the stream is closed instead of being consumed to the end. ``deadline``
    is the soft probe cutoff, checked only between chunks; each socket
    read already blocked may finish past it, so the cutoff can be overrun
    more than once and bounds no overall duration.
    """
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower() or "identity"
    try:
        if encoding != "identity":
            raise _BoundaryRejection("response encoding is outside the acceptance boundary")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.stream:
            if not chunk:
                continue
            if total + len(chunk) > _MAX_RESPONSE_BYTES:
                raise _BoundaryRejection("response exceeds the retained payload limit")
            chunks.append(chunk)
            total += len(chunk)
            if deadline is not None and time.monotonic() > deadline:
                raise _BoundaryRejection("probe exceeded its soft deadline")
    finally:
        response.close()
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=b"".join(chunks),
        request=request,
    )


class _GuardedTransport(httpx.BaseTransport):
    """Delegates only allowlisted domestic read requests inside the budget.

    Beyond method/path/origin, a candidate request must satisfy the exact
    contract for its route — auth form fields, or an empty-body GET with
    ``accessToken`` only in the header, a precise query key set, the fixed
    page/bizType/window values, and the bound space id — before any budget
    is spent or the delegate is invoked.
    """

    def __init__(
        self,
        delegate: httpx.BaseTransport,
        budget: _RequestBudget,
        deadline: float | None = None,
    ) -> None:
        self._delegate = delegate
        self._budget = budget
        self._deadline = deadline

    def _contract_ok(self, route: tuple[str, str], request: httpx.Request) -> bool:
        if route == _AUTH_ROUTE:
            return _auth_allowed(request)
        if route == _SPACES_ROUTE:
            return _spaces_allowed(request, self._budget)
        if route == _VOD_ROUTE:
            return _vod_allowed(request, self._budget)
        return False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        route = (request.method, request.url.path)
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise _BoundaryRejection("probe exceeded its soft deadline")
        if (
            not _url_origin_ok(request.url, root_only=False)
            or route not in _ALLOWED_ROUTES
            or not self._contract_ok(route, request)
            or not self._budget.admit(route)
        ):
            raise _BoundaryRejection("request is outside the VOD discovery acceptance boundary")
        request.headers["Accept-Encoding"] = "identity"
        return _bounded_response(request, self._delegate.handle_request(request), self._deadline)

    def close(self) -> None:
        self._delegate.close()


def _guarded_factory(
    budget: _RequestBudget, deadline: float | None = None
) -> Callable[[str], httpx.BaseTransport]:
    def factory(origin: str) -> httpx.BaseTransport:
        if not _url_origin_ok(httpx.URL(origin), root_only=True):
            raise _BoundaryRejection("origin is outside the VOD discovery acceptance boundary")
        return _GuardedTransport(_new_inner_transport(), budget, deadline)

    return factory


def _first_candidate(rows: list[object]) -> int | None:
    """First row, in server order, shaped like an ordinary VOD space."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("bizType") != _BIZ_TYPE:
            continue
        if type(row.get("namespaceType")) is not int or row["namespaceType"] != 1:
            continue
        if type(row.get("spaceId")) is not int:
            continue
        return row["spaceId"]
    return None


def _data_kind(value: object) -> str:
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "other"


def _type_bucket(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def _field_type_summary(items: list[object]) -> dict[str, list[str]]:
    """Aggregate JSON type buckets for a fixed field-name whitelist only."""
    summary: dict[str, set[str]] = {name: set() for name in _KNOWN_VOD_FIELDS}
    for item in items[:_MAX_FIELD_SAMPLE_ITEMS]:
        if not isinstance(item, dict):
            continue
        for name in _KNOWN_VOD_FIELDS:
            if name in item:
                summary[name].add(_type_bucket(item[name]))
    return {name: sorted(buckets) for name, buckets in summary.items() if buckets}


def _error_family(exc: BaseException) -> str:
    """Map a failure onto a fixed family by declared error type only."""
    if isinstance(exc, _BoundaryRejection):
        return "protocol"
    if isinstance(exc, AuthenticationError):
        return "authentication"
    if isinstance(exc, PermissionDeniedError):
        return "permission"
    if isinstance(exc, ApiError):
        return "api"
    if isinstance(exc, ProtocolError):
        return "protocol"
    if isinstance(exc, (TransportError, httpx.HTTPError)):
        return "transport"
    return "unknown"


def verify(config: dict[str, str]) -> dict[str, object]:
    """Run the bounded one-shot probe; the result stays whitelisted either way.

    ``success`` means the single ``vod.files`` call returned an ``array``
    (empty is allowed) for the bound space — nothing more is proven about
    media, the account, hardware, or 1.0. An empty page or a page without a
    qualifying candidate is ``not_verified``: a 30-day ``createTime`` window
    misses older spaces and permission errors cannot prove absence of the
    feature. Any boundary rejection, transport or API failure is ``failed``
    with only a fixed error family. Process-control ``BaseException``s keep
    propagating; ordinary unknown failures end as ``failed``/``unknown``.
    """
    recorded_at = datetime.now(timezone.utc).replace(microsecond=0)
    window_end = datetime.now(_SHANGHAI_WALL).replace(tzinfo=None, microsecond=0)
    window_start = window_end - _WINDOW

    outcome = "failed"
    reason = _REASON_INVALID_CONFIG
    family = "none"
    stage = "credential"
    logical_calls = 0
    space_page_count: int | None = None
    space_has_next: bool | None = None
    candidate_found = False
    vod_data_type: str | None = None
    vod_item_count: int | None = None
    known_types: dict[str, list[str]] | None = None

    window = (window_start.strftime(_TIME_PARAM_FORMAT), window_end.strftime(_TIME_PARAM_FORMAT))
    budget = _RequestBudget(window)
    deadline = time.monotonic() + _PROBE_DEADLINE_SECONDS
    client: Client | None = None
    try:
        app_key, app_secret = _credentials(config)
    except (ValueError, TypeError):
        pass
    except Exception:
        outcome, reason, family = "failed", _REASON_UNKNOWN, "unknown"
    else:
        stage = "authentication"
        try:
            client = Client(
                app_key,
                app_secret,
                region=RegionConfig.china(),
                timeout=10,
                auth_transport=_GuardedTransport(_new_inner_transport(), budget, deadline),
                transport_factory=_guarded_factory(budget, deadline),
            )
            logical_calls = 1
            response = client.storage.spaces(
                start_time=window_start,
                end_time=window_end,
                page_size=50,
                biz_type=_BIZ_TYPE,
            )
            data = response.data if isinstance(response.data, dict) else None
            result = data.get("result") if data is not None else None
            has_next = data.get("hasNext") if data is not None else None
            space_has_next = has_next if type(has_next) is bool else None
            if not isinstance(result, list) or len(result) > 50:
                outcome, reason, family = "not_verified", _REASON_SPACE_SHAPE, "protocol"
            else:
                space_page_count = len(result)
                if not result:
                    outcome, reason, family = "not_verified", _REASON_EMPTY_PAGE, "none"
                else:
                    space_id = _first_candidate(result)
                    if space_id is None:
                        outcome, reason, family = "not_verified", _REASON_NO_CANDIDATE, "none"
                    else:
                        candidate_found = True
                        budget.bind_space(space_id)
                        logical_calls = 2
                        vod_data = client.vod.files(space_id=space_id).data
                        vod_data_type = _data_kind(vod_data)
                        if isinstance(vod_data, list):
                            vod_item_count = len(vod_data)
                            known_types = _field_type_summary(vod_data)
                            outcome, reason, family = "success", _REASON_OK, "none"
                        else:
                            outcome, reason, family = "not_verified", _REASON_VOD_SHAPE, "protocol"
        except _BoundaryRejection as exc:
            if "deadline" in str(exc):
                outcome, reason, family = "failed", _REASON_DEADLINE, "protocol"
            else:
                outcome, reason, family = "failed", _REASON_BOUNDARY, "protocol"
        except Exception as exc:
            outcome = "failed"
            family = _error_family(exc)
            reason = _FAMILY_REASON.get(family, _REASON_UNKNOWN)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    if outcome != "failed":
                        outcome, reason, family = "failed", _REASON_CLOSE_FAILURE, "unknown"

    report: dict[str, object] = {
        "schema_version": 1,
        "scope": _SCOPE,
        "region": "domestic",
        "recorded_at_utc": recorded_at.isoformat(),
        "window_start": window[0],
        "window_end": window[1],
        "window_basis": "createTime",
        "window_timezone_assumption": "Asia/Shanghai_wall_time",
        "max_http_requests": _MAX_HTTP_REQUESTS,
        "http_requests": budget.total,
        "logical_calls": logical_calls,
        "route_counts": budget.route_counts(),
        "stage": "complete" if outcome == "success" else (budget.last_stage or stage),
        "outcome": outcome,
        "reason": reason,
        "space_page_count": space_page_count,
        "space_has_next": space_has_next,
        "ordinary_vod_candidate_found": candidate_found,
        "vod_item_count": vod_item_count,
        "error_family": family,
        "cloud_recording_v1_discovery": _V1_DISCOVERY,
    }
    if vod_data_type is not None:
        report["vod_data_type"] = vod_data_type
    if known_types is not None:
        report["known_field_types"] = known_types
    return report


def _report_allowed(report: object) -> bool:
    """Whitelist validation run before the report may be persisted."""
    if not isinstance(report, dict) or not report.keys() <= _REPORT_KEYS:
        return False
    try:
        if type(report["schema_version"]) is not int or report["schema_version"] != 1:
            return False
        if report["scope"] != _SCOPE or report["region"] != "domestic":
            return False
        recorded = report["recorded_at_utc"]
        if not isinstance(recorded, str):
            return False
        parsed_recorded = datetime.fromisoformat(recorded)
        if parsed_recorded.utcoffset() != timedelta(0):
            return False
        if parsed_recorded.isoformat() != recorded:
            return False
        window_start = report["window_start"]
        window_end = report["window_end"]
        if not isinstance(window_start, str) or not isinstance(window_end, str):
            return False
        start = datetime.strptime(window_start, _TIME_PARAM_FORMAT)
        end = datetime.strptime(window_end, _TIME_PARAM_FORMAT)
        if start.strftime(_TIME_PARAM_FORMAT) != window_start:
            return False
        if end.strftime(_TIME_PARAM_FORMAT) != window_end:
            return False
        if end - start != _WINDOW:
            return False
        if report["window_basis"] != "createTime":
            return False
        if report["window_timezone_assumption"] != "Asia/Shanghai_wall_time":
            return False
        if (
            type(report["max_http_requests"]) is not int
            or report["max_http_requests"] != _MAX_HTTP_REQUESTS
        ):
            return False
        if (
            type(report["http_requests"]) is not int
            or not 0 <= report["http_requests"] <= _MAX_HTTP_REQUESTS
        ):
            return False
        if type(report["logical_calls"]) is not int or not 0 <= report["logical_calls"] <= 2:
            return False
        counts = report["route_counts"]
        if not isinstance(counts, dict) or set(counts) != {"authentication", "spaces", "vod"}:
            return False
        per_route_caps = {"authentication": 3, "spaces": 2, "vod": 2}
        for name, cap in per_route_caps.items():
            value = counts[name]
            if type(value) is not int or not 0 <= value <= cap:
                return False
        if sum(counts.values()) != report["http_requests"]:
            return False
        if report["stage"] not in _STAGES or report["outcome"] not in _OUTCOMES:
            return False
        if report["reason"] not in _REASONS or report["error_family"] not in _ERROR_FAMILIES:
            return False
        if type(report["ordinary_vod_candidate_found"]) is not bool:
            return False
        if report["cloud_recording_v1_discovery"] != _V1_DISCOVERY:
            return False
        page_count = report.get("space_page_count")
        if page_count is not None and (type(page_count) is not int or not 0 <= page_count <= 50):
            return False
        has_next = report.get("space_has_next")
        if has_next is not None and type(has_next) is not bool:
            return False
        item_count = report.get("vod_item_count")
        if item_count is not None and (type(item_count) is not int or item_count < 0):
            return False
        if "vod_data_type" in report and report["vod_data_type"] not in _VOD_DATA_TYPES:
            return False
        if "known_field_types" in report:
            fields = report["known_field_types"]
            if not isinstance(fields, dict) or not set(fields) <= set(_KNOWN_VOD_FIELDS):
                return False
            for buckets in fields.values():
                if not isinstance(buckets, list) or not set(buckets) <= _TYPE_BUCKETS:
                    return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _percent_escapes_ok(body: bytes) -> bool:
    """Every ``%`` in the raw body must start a two-hex-digit escape."""
    index = 0
    length = len(body)
    while index < length:
        if body[index] == 0x25:  # '%'
            if index + 2 >= length:
                return False
            high, low = body[index + 1], body[index + 2]
            if not (0x30 <= high <= 0x39 or 0x41 <= high <= 0x46 or 0x61 <= high <= 0x66):
                return False
            if not (0x30 <= low <= 0x39 or 0x41 <= low <= 0x46 or 0x61 <= low <= 0x66):
                return False
            index += 3
        else:
            index += 1
    return True


def _summary_page(report: dict[str, object]) -> str:
    """Fixed result summary — outcome/stage/reason only, no echo of inputs."""
    summary = {key: report.get(key) for key in ("outcome", "stage", "reason")}
    return (
        "<h1>VOD 只读探测验收结果</h1><pre>"
        + html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
        + "</pre>"
    )


def _make_server(output: Path) -> tuple[HTTPServer, str, str, dict[str, bool]]:
    """Loopback form whose single valid POST reaches the local SDK once."""
    route = "/" + secrets.token_urlsafe(24)
    state = {"consumed": False, "done": False}
    origin = ""
    expected_host = ""

    class _DeadlineReader:
        """Read a request line, headers and body against one absolute deadline.

        Each ``socket.recv`` gets only the time still remaining before the
        fixed per-connection deadline — a slow drip cannot reset it. The
        deadline is capped by the caller's overall intake deadline.
        """

        def __init__(self, conn: socket.socket, deadline: float) -> None:
            self._conn = conn
            self._deadline = deadline
            self._buffer = bytearray()

        def _remaining(self) -> float:
            left = self._deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("connection read deadline exceeded")
            return left

        def _recv(self) -> None:
            self._conn.settimeout(self._remaining())
            chunk = self._conn.recv(65536)
            if not chunk:
                raise TimeoutError("connection closed before the deadline read finished")
            self._buffer.extend(chunk)

        def readline(self, limit: int) -> bytes:
            while True:
                newline = self._buffer.find(b"\n")
                if newline >= 0:
                    line = bytes(self._buffer[: newline + 1])
                    del self._buffer[: newline + 1]
                    return line
                if len(self._buffer) >= limit:
                    raise TimeoutError("request line or header exceeded its bound")
                self._recv()

        def read(self, count: int) -> bytes:
            while len(self._buffer) < count:
                self._recv()
            out = bytes(self._buffer[:count])
            del self._buffer[:count]
            return out

    class Handler(BaseHTTPRequestHandler):
        # ``handle`` below drives a single request through the deadline
        # reader so the request line, headers and body share one absolute
        # deadline; the default socket read is replaced.
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(_CONNECTION_DEADLINE_SECONDS)

        def log_message(self, *_args: object) -> None:
            pass

        def handle(self) -> None:
            # One-shot intake: read one request under an absolute deadline,
            # dispatch it, then always close. ``_headers_buffer`` is what
            # ``send_response``/``end_headers`` stage into before flushing.
            try:
                self._headers_buffer = []
                self._reader = _DeadlineReader(
                    self.connection,
                    min(
                        time.monotonic() + _CONNECTION_DEADLINE_SECONDS,
                        getattr(self.server, "_intake_deadline", float("inf")),
                    ),
                )
                if self._read_request():
                    method = self.command
                    if method == "POST":
                        self.do_POST()
                    elif method == "GET":
                        self.do_GET()
                    else:
                        self.send_error(405)
                    self.wfile.flush()
            except (TimeoutError, socket.timeout, OSError):
                pass
            finally:
                self.close_connection = True

        def _read_request(self) -> bool:
            # Request line + headers under the deadline; fixed replies only.
            # ``requestline``/``request_version`` start as the stdlib
            # oversize-handling sentinels so an early ``send_error`` before
            # any version was parsed still logs a safe line and answers with
            # this handler's fixed HTTP/1.1 status line.
            self.command = ""
            self.requestline = ""
            self.request_version = ""
            self.headers = None
            self.raw_requestline = self._reader.readline(65537)
            if not self.raw_requestline or len(self.raw_requestline) > 65536:
                if len(self.raw_requestline) > 65536:
                    self.send_error(414)
                return False
            self.requestline = str(self.raw_requestline, "iso-8859-1").rstrip("\r\n")
            words = self.requestline.split()
            if len(words) == 3:
                command, path, version = words
            elif len(words) == 2:
                command, path = words
                version = "HTTP/0.9"
            else:
                self.send_error(400)
                return False
            if not version.startswith("HTTP/"):
                self.send_error(400)
                return False
            self.command, self.path, self.request_version = command, path, version
            if self.command == "POST" and urlsplit(self.path).path != route:
                # A POST to anything but the form route carries no body we
                # may read; answering now would leave its bytes on the wire.
                self.send_error(403)
                return False
            header_lines: list[bytes] = []
            total = 0
            while True:
                line = self._reader.readline(65537)
                if len(line) > 65536:
                    self.send_error(431)
                    return False
                if line in (b"\r\n", b"\n", b""):
                    break
                total += len(line)
                if total > 262144 or len(header_lines) >= 200:
                    self.send_error(431)
                    return False
                header_lines.append(line)
            try:
                self.headers = _http_parse_headers(io.BytesIO(b"".join(header_lines)))
            except Exception:
                self.send_error(400)
                return False
            if self.command == "POST":
                declared = self.headers.get_all("Content-Length")
                if self.headers.get_all("Transfer-Encoding") or (
                    declared is not None
                    and len(declared) == 1
                    and _ascii_digits(declared[0])
                    and int(declared[0]) > _MAX_BODY_BYTES
                ):
                    # An over-limit or framed body is refused before it is
                    # read; the connection closes rather than consuming it.
                    self.send_error(400)
                    return False
            return True

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:
            # Fixed text only; no echoed request detail and no traceback.
            fixed = {
                400: "Bad request",
                404: "Not found",
                405: "Method not allowed",
                414: "Request line too long",
                431: "Headers too large",
            }
            try:
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(fixed.get(code, "Verification stopped.").encode())
            except OSError:
                self.close_connection = True

        def _reply(self, status: int, body: str) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
                )
                self.send_header("Content-Length", str(len(body.encode())))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body.encode())
            except OSError:
                self.close_connection = True

        def _single_header(self, name: str) -> str | None:
            values = self.headers.get_all(name) if self.headers is not None else None
            if values is None or len(values) != 1:
                return None
            return values[0]

        def _host_ok(self) -> bool:
            return self._single_header("Host") == expected_host

        def do_GET(self) -> None:
            if self.path != route or not self._host_ok():
                self._reply(404, "Not found")
                return
            self._reply(200, _FORM)

        def do_POST(self) -> None:
            values: dict[str, list[str]] | None = None
            config: dict[str, str] = {}
            try:
                if self.path != route or not self._host_ok():
                    self._reply(403, "Forbidden")
                    return
                if self._single_header("Origin") != origin:
                    self._reply(403, "Forbidden")
                    return
                if state["consumed"]:
                    self._reply(409, "Already consumed")
                    return
                if self.headers.get_all("Transfer-Encoding"):
                    self._reply(400, "Verification stopped.")
                    return
                content_type = self._single_header("Content-Type") or ""
                declared = self._single_header("Content-Length")
                if (
                    content_type.split(";", 1)[0].strip().lower() != _FORM_CONTENT_TYPE
                    or declared is None
                    or not _ascii_digits(declared)
                    or not 0 < int(declared) <= _MAX_BODY_BYTES
                ):
                    self._reply(400, "Verification stopped.")
                    return
                body = self._reader.read(int(declared))
                if len(body) != int(declared):
                    self._reply(400, "Verification stopped.")
                    return
                if not _percent_escapes_ok(body):
                    self._reply(400, "Verification stopped.")
                    return
                try:
                    values = parse_qs(
                        body.decode("utf-8"),
                        keep_blank_values=True,
                        strict_parsing=True,
                        encoding="utf-8",
                        errors="strict",
                    )
                except ValueError:
                    self._reply(400, "Verification stopped.")
                    return
                if set(values) != {"app_key", "app_secret"} or any(
                    len(v) != 1 or not v[0].strip() for v in values.values()
                ):
                    self._reply(400, "Verification stopped.")
                    return
                state["consumed"] = True
                config = {
                    "app_key": values["app_key"][0],
                    "app_secret": values["app_secret"][0],
                }
                # One valid submission ends the one-shot run; whether verify,
                # the report check, the file write, or the reply fails, the
                # inputs are cleared and ``done`` is latched in ``finally``.
                try:
                    try:
                        report = verify(config)
                    except Exception:
                        report = None
                    if report is None or not _report_allowed(report):
                        self._reply(500, "Verification stopped.")
                        return
                    try:
                        with output.open("x", encoding="utf-8") as stream:
                            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                    except OSError:
                        self._reply(500, "Verification stopped.")
                        return
                    self._reply(200, _summary_page(report))
                finally:
                    state["done"] = True
                    config.clear()
                    if values is not None:
                        values.clear()
            except (TimeoutError, socket.timeout, OSError):
                self.close_connection = True
            except Exception:
                self._reply(500, "Verification stopped.")
            finally:
                if values is not None:
                    values.clear()
                config.clear()
                self.close_connection = True

    class _QuietHTTPServer(HTTPServer):
        def handle_error(self, request: object, client_address: object) -> None:
            # Never print a traceback or request detail; just close quietly.
            pass

    server = _QuietHTTPServer(("127.0.0.1", 0), Handler)
    # Stamped by ``serve`` to the same absolute instant as the intake loop.
    server._intake_deadline = float("inf")
    origin = f"http://127.0.0.1:{server.server_port}"
    expected_host = f"127.0.0.1:{server.server_port}"
    return server, route, origin, state


def serve(output: Path) -> None:
    """Print a temporary loopback URL; the form submits credentials locally.

    Waits at most 600 seconds for one valid submission, then closes
    explicitly whether or not it arrived. Only the startup URL and a fixed
    completion status are printed — never exceptions or data.
    """
    server, route, origin, state = _make_server(output)
    print(origin + route, flush=True)
    deadline = time.monotonic() + _SERVE_TIMEOUT_SECONDS
    server._intake_deadline = deadline
    server.timeout = 0.5
    try:
        while not state["done"] and time.monotonic() < deadline:
            try:
                server.handle_request()
            except OSError:
                continue
    finally:
        server.server_close()
    print("verification finished" if state["done"] else "verification timed out", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.serve or args.output is None:
        parser.error("this tool only runs as --serve with an explicit --output file path")
    if args.output.exists():
        parser.error("--output already exists; refusing to overwrite")
    try:
        serve(args.output)
    except Exception:
        sys.stderr.write("verification stopped\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
