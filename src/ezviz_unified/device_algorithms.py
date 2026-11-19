"""Domestic device algorithm inventory queries (730/731).

Both endpoints are domestic-only, read-only GET requests that still
carry a form body (``curl -X GET --data-urlencode`` semantics) with an
explicit ``Content-Type`` header; the access token travels only in the
header. ``list_devices`` (731) uses ``device_serial`` as a search
keyword, mutually exclusive with any pagination input, while
``list_supported`` (730) selects one exact device. The SDK never moves
parameters into the query string and never substitutes an alternate
method or query shape; the shared read-only refresh behavior (for
example an HTTP 200 + code 10002 token retry) still applies, but no
auto-pagination or extra operations are added. Response rows pass
through untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text
from .traffic import _page_start

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncDeviceAlgorithms", "DeviceAlgorithms"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_META_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_LIST_DEVICES = Operation(
    name="device_algorithms.list_devices",
    method="GET",
    path="/api/v3/intelligent/model/device",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=True,
    policy=_META_POLICY,
)
_LIST_SUPPORTED = Operation(
    name="device_algorithms.list_supported",
    method="GET",
    path="/api/v3/intelligent/model/device/support",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=True,
    policy=_META_POLICY,
)


def _require_domestic(client: Client | AsyncClient) -> None:
    if client.region.scope != "domestic":
        raise ValueError("device algorithm queries require the domestic region")


def _check_page_size(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 8 <= value <= 50:
        raise ValueError("page_size must be an integer from 8 to 50")
    return str(value)


def _unexpected_shape(response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        "unexpected device algorithms response shape",
        http_status=response.http_status,
        operation=response.operation,
    )


def _check_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise _unexpected_shape(response)
    return response


def _list_devices_form(
    device_serial: str | None,
    page_start: int | None,
    page_size: int | None,
) -> dict[str, str]:
    if device_serial is not None and (page_start is not None or page_size is not None):
        raise ValueError("device_serial cannot be combined with pagination")
    form: dict[str, str] = {}
    if device_serial is not None:
        form["deviceSerial"] = _check_text(device_serial, "deviceSerial")
    if page_start is not None:
        form["pageStart"] = _page_start(page_start)
    if page_size is not None:
        form["pageSize"] = _check_page_size(page_size)
    return form


def _list_supported_form(device_serial: str) -> dict[str, str]:
    return {"deviceSerial": _check_text(device_serial, "deviceSerial")}


class DeviceAlgorithms:
    """Synchronous facade over the domestic device-algorithm queries."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list_devices(
        self,
        *,
        device_serial: str | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="device_algorithms.list_devices",
            mode="sync",
        ):
            _require_domestic(self._client)
            form = _list_devices_form(device_serial, page_start, page_size)
            response = self._client.request(_LIST_DEVICES, data=form, headers=_FORM_HEADERS)
            return _check_response(response)

    def list_supported(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="device_algorithms.list_supported",
            mode="sync",
        ):
            _require_domestic(self._client)
            form = _list_supported_form(device_serial)
            response = self._client.request(_LIST_SUPPORTED, data=form, headers=_FORM_HEADERS)
            return _check_response(response)


class AsyncDeviceAlgorithms:
    """Asynchronous facade over the domestic device-algorithm queries."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list_devices(
        self,
        *,
        device_serial: str | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="device_algorithms.list_devices",
            mode="async",
        ):
            _require_domestic(self._client)
            form = _list_devices_form(device_serial, page_start, page_size)
            response = await self._client.request(_LIST_DEVICES, data=form, headers=_FORM_HEADERS)
            return _check_response(response)

    async def list_supported(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="device_algorithms.list_supported",
            mode="async",
        ):
            _require_domestic(self._client)
            form = _list_supported_form(device_serial)
            response = await self._client.request(_LIST_SUPPORTED, data=form, headers=_FORM_HEADERS)
            return _check_response(response)
