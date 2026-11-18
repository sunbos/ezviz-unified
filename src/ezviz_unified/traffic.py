"""Domestic traffic usage queries (open.ys7.com docs 1424/1425/1426/1427).

All four endpoints are domestic-only LAPP form POSTs whose ``accessToken``
is injected by the shared client. ``account_summary`` (1424) takes no
filters and returns a single object, while ``account_details`` (1425),
``day_details`` (1426) and ``device_details`` (1427) return arrays of
usage rows. Optional filters are sent only when supplied: millisecond
timestamps are forwarded verbatim as signed 64-bit integers (each side
may be omitted, negative, or out of order — the SDK never guesses the
platform timezone or injects the current date), ``pageStart`` only has
to be non-negative, and ``pageSize`` keeps the documented upper bound
of 50 plus a local positive lower-bound check. The SDK never auto-paginates, never aggregates rows, and returns
the :class:`ApiResponse` untouched so every field, known or unknown, is
preserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .devices import _check_page_size
from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import _integer, _text
from .vod import _utf8

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncTraffic", "Traffic"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_LAPP_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1

_ACCOUNT_SUMMARY = Operation(
    "traffic.account_summary",
    "POST",
    "/api/lapp/traffic/user/total",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_ACCOUNT_DETAILS = Operation(
    "traffic.account_details",
    "POST",
    "/api/lapp/traffic/user/detail",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_DAY_DETAILS = Operation(
    "traffic.day_details",
    "POST",
    "/api/lapp/traffic/day/detail",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_DEVICE_DETAILS = Operation(
    "traffic.device_details",
    "POST",
    "/api/lapp/traffic/device/detail",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)


def _require_region(scope: str) -> None:
    if scope != "domestic":
        raise ValueError("traffic is only supported in the domestic region")


def _millis(name: str, value: object) -> int:
    moment = _integer(name, value)
    if not _INT64_MIN <= moment <= _INT64_MAX:
        raise ValueError(f"{name} must fit within a signed 64-bit integer")
    return moment


def _page_start(value: object) -> str:
    start = _integer("pageStart", value)
    if start < 0:
        raise ValueError("pageStart must be >= 0")
    try:
        return str(start)
    except ValueError:
        raise ValueError("pageStart cannot be encoded as decimal") from None


def _device_serial(value: object) -> str:
    serial = _text("deviceSerial", value)
    _utf8("deviceSerial", serial)
    return serial


def _time_range(
    data: dict[str, object],
    *,
    start_time: int | None,
    end_time: int | None,
) -> None:
    if start_time is not None:
        data["startTime"] = _millis("startTime", start_time)
    if end_time is not None:
        data["endTime"] = _millis("endTime", end_time)


def _paging(
    data: dict[str, object],
    *,
    page_start: int | None,
    page_size: int | None,
) -> None:
    if page_start is not None:
        data["pageStart"] = _page_start(page_start)
    if page_size is not None:
        data["pageSize"] = _check_page_size(page_size)


def _expect_object(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, dict):
        raise ProtocolError(
            "unexpected traffic response data shape",
            http_status=response.http_status,
            operation=response.operation,
        )
    return response


def _expect_array(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, list):
        raise ProtocolError(
            "unexpected traffic response data shape",
            http_status=response.http_status,
            operation=response.operation,
        )
    return response


class Traffic:
    """Synchronous facade over the domestic traffic usage endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def account_summary(self) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.account_summary",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            response = self._client.request(
                _ACCOUNT_SUMMARY,
                data={},
                headers=_FORM_HEADERS,
            )
            return _expect_object(response)

    def account_details(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.account_details",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {}
            _time_range(data, start_time=start_time, end_time=end_time)
            _paging(data, page_start=page_start, page_size=page_size)
            response = self._client.request(
                _ACCOUNT_DETAILS,
                data=data,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)

    def day_details(
        self,
        *,
        flow_time: int | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.day_details",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {}
            if flow_time is not None:
                data["flowTime"] = _millis("flowTime", flow_time)
            _paging(data, page_start=page_start, page_size=page_size)
            response = self._client.request(
                _DAY_DETAILS,
                data=data,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)

    def device_details(
        self,
        device_serial: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.device_details",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {
                "deviceSerial": _device_serial(device_serial),
            }
            _time_range(data, start_time=start_time, end_time=end_time)
            _paging(data, page_start=page_start, page_size=page_size)
            response = self._client.request(
                _DEVICE_DETAILS,
                data=data,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)


class AsyncTraffic:
    """Asynchronous facade over the domestic traffic usage endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def account_summary(self) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.account_summary",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            response = await self._client.request(
                _ACCOUNT_SUMMARY,
                data={},
                headers=_FORM_HEADERS,
            )
            return _expect_object(response)

    async def account_details(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.account_details",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {}
            _time_range(data, start_time=start_time, end_time=end_time)
            _paging(data, page_start=page_start, page_size=page_size)
            response = await self._client.request(
                _ACCOUNT_DETAILS,
                data=data,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)

    async def day_details(
        self,
        *,
        flow_time: int | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.day_details",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {}
            if flow_time is not None:
                data["flowTime"] = _millis("flowTime", flow_time)
            _paging(data, page_start=page_start, page_size=page_size)
            response = await self._client.request(
                _DAY_DETAILS,
                data=data,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)

    async def device_details(
        self,
        device_serial: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="traffic.device_details",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {
                "deviceSerial": _device_serial(device_serial),
            }
            _time_range(data, start_time=start_time, end_time=end_time)
            _paging(data, page_start=page_start, page_size=page_size)
            response = await self._client.request(
                _DEVICE_DETAILS,
                data=data,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)
