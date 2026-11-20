"""Device query facade for the unified EZVIZ client (sync and async)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .management import AsyncDeviceManagement, DeviceManagement
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text, _require_domestic, _require_overseas
from .vod import _header

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"
_MAX_DOMESTIC_PAGE_START = 400
_MIN_PAGE_SIZE = 1
_MAX_PAGE_SIZE = 50

_POLICY = ResponsePolicy(success_values=("200",))

_OP_LIST_BY_ID = Operation(
    "devices.list_by_id",
    "POST",
    "/api/lapp/device/list",
    regions=frozenset({_DOMESTIC}),
    token_location="query",
    read_only=True,
    policy=replace(
        _POLICY,
        redact_device_add_details=True,
        redact_message_urls=True,
    ),
)


def _valid_device_cursor(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    for char in value:
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F or 0xD800 <= code <= 0xDFFF:
            return False
    return True


def _device_cursor_params(
    cursor: str | None, page_start: int | None, page_size: int | None
) -> dict[str, str | int]:
    params: dict[str, str | int] = {}
    if cursor is not None:
        if not _valid_device_cursor(cursor):
            raise ValueError("invalid device cursor")
        params["id"] = cursor
    if page_start is not None:
        start = _check_int("page_start", page_start)
        if not 0 <= start <= 100:
            raise ValueError("page_start must be between 0 and 100")
        params["pageStart"] = start
    if page_size is not None:
        params["pageSize"] = _check_page_size(page_size)
    return params


def _check_device_cursor_page(response: ApiResponse) -> ApiResponse:
    raw = response.raw
    page = raw.get("page") if isinstance(raw, dict) else None
    rows = response.data
    if (
        isinstance(rows, list)
        and all(isinstance(row, dict) for row in rows)
        and isinstance(page, dict)
        and "nextId" in page
    ):
        next_id = page["nextId"]
        if (next_id is None or next_id == "" or _valid_device_cursor(next_id)) and all(
            k not in page or type(page[k]) is int for k in ("total", "size")
        ):
            return response
    raise ProtocolError(
        "invalid device cursor page",
        operation=response.operation,
        http_status=response.http_status,
    )


_OP_LIST_MANAGED = Operation(
    "devices.list_managed",
    "POST",
    "/api/lapp/trust/device/list",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=_POLICY,
)


def _managed_list_data(scope: str, page_start: int, page_size: int) -> dict[str, str]:
    if scope != _DOMESTIC:
        raise ValueError("managed device listing is only documented for the domestic region")
    start = _check_int("page_start", page_start)
    if start < 0:
        raise ValueError("page_start must be non-negative")
    return {"version": "3.0", "pageStart": str(start), "pageSize": str(_check_page_size(page_size))}


_OP_LIST = Operation(
    "devices.list",
    "POST",
    "/api/lapp/device/list",
    read_only=True,
    policy=_POLICY,
)
_OP_GET = Operation(
    "devices.get",
    "POST",
    "/api/lapp/device/info",
    read_only=True,
    policy=_POLICY,
)
_OP_GET_UTC = Operation(
    "devices.get_utc",
    "POST",
    "/api/lapp/device/info",
    regions=frozenset({"overseas"}),
    token_location="form",
    read_only=True,
    policy=_POLICY,
)
_OP_CHANNELS = Operation(
    "devices.channels",
    "POST",
    "/api/lapp/device/camera/list",
    read_only=True,
    policy=_POLICY,
)
_OP_STATUS = Operation(
    "devices.status",
    "POST",
    "/api/lapp/device/status/get",
    read_only=True,
    policy=_POLICY,
)
_OP_CHARGING_STATUS = Operation(
    "devices.charging_status",
    "GET",
    "/api/v3/device/power/status/get",
    regions=frozenset({"domestic"}),
    token_location="header",
    read_only=True,
    policy=ResponsePolicy(success_values=("200",), redact_message_urls=True),
)


def _check_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int")
    return value


def _check_page_start(value: object, *, domestic: bool) -> int:
    page_start = _check_int("page_start", value)
    if page_start < 0:
        raise ValueError("page_start must be a non-negative int")
    if domestic and page_start > _MAX_DOMESTIC_PAGE_START:
        raise ValueError("page_start must not exceed 400 for the domestic region")
    return page_start


def _check_page_size(value: object) -> int:
    page_size = _check_int("page_size", value)
    if not _MIN_PAGE_SIZE <= page_size <= _MAX_PAGE_SIZE:
        raise ValueError("page_size must be between 1 and 50")
    return page_size


def _check_device_serial(value: object, *, require_uppercase: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("device_serial must be a non-blank string")
    if require_uppercase and any("a" <= ch <= "z" for ch in value):
        raise ValueError("device_serial letters must be uppercase for the domestic region")
    return value


def _utc_form(device_serial: object) -> dict[str, str]:
    return {"deviceSerial": _check_text(device_serial, "device_serial"), "version": "utc"}


def _check_utc_response(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, dict):
        raise ProtocolError(
            "unexpected devices.get_utc response shape",
            http_status=response.http_status,
            operation=response.operation,
        )
    return response


def _charging_status_headers(scope: str, device_serial: object) -> dict[str, str]:
    """Validate region and serial before authentication; the serial is sent
    verbatim under ``deviceSerial`` (no case normalization)."""
    _require_domestic(scope)
    return {"deviceSerial": _header("device_serial", device_serial)}


def _check_charging_status_response(response: ApiResponse) -> ApiResponse:
    """Require ``data.valueInfo.powerStatus`` to be a string, passed through
    verbatim; a wrong container level or non-string value is a protocol
    violation reported without the raw payload."""
    data = response.data
    info = data.get("valueInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get("powerStatus"), str):
        raise ProtocolError(
            "unexpected devices.charging_status response shape",
            http_status=response.http_status,
            operation=response.operation,
        )
    return response


class Devices(DeviceManagement):
    """Synchronous facade over the reviewed device query operations."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def _domestic(self) -> bool:
        return self._client.region.scope == _DOMESTIC

    def list(self, page_start: int = 0, page_size: int = 10) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.list", mode="sync"):
            data = {
                "pageStart": _check_page_start(page_start, domestic=self._domestic()),
                "pageSize": _check_page_size(page_size),
            }
            return self._client.request(_OP_LIST, data=data)

    def list_by_id(
        self,
        *,
        cursor: str | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.list_by_id", mode="sync"):
            _require_domestic(self._client.region.scope)
            params = _device_cursor_params(
                cursor=cursor,
                page_start=page_start,
                page_size=page_size,
            )
            response = self._client.request(_OP_LIST_BY_ID, params=params)
            return _check_device_cursor_page(response)

    def list_managed(self, page_start: int = 0, page_size: int = 10) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.list_managed", mode="sync"):
            return self._client.request(
                _OP_LIST_MANAGED,
                data=_managed_list_data(self._client.region.scope, page_start, page_size),
            )

    def get(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.get", mode="sync"):
            serial = _check_device_serial(device_serial, require_uppercase=False)
            return self._client.request(_OP_GET, data={"deviceSerial": serial})

    def get_utc(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.get_utc", mode="sync"):
            _require_overseas(self._client.region.scope)
            response = self._client.request(_OP_GET_UTC, data=_utc_form(device_serial))
            return _check_utc_response(response)

    def channels(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.channels", mode="sync"):
            serial = _check_device_serial(device_serial, require_uppercase=self._domestic())
            return self._client.request(_OP_CHANNELS, data={"deviceSerial": serial})

    def status(self, device_serial: str, channel: int = 1) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.status", mode="sync"):
            serial = _check_device_serial(device_serial, require_uppercase=self._domestic())
            data = {"deviceSerial": serial, "channel": _check_int("channel", channel)}
            return self._client.request(_OP_STATUS, data=data)

    def charging_status(self, device_serial: str) -> ApiResponse:
        """Read the battery charging status (domestic region only).

        Performs one logical read: a GET to
        ``/api/v3/device/power/status/get`` with no query or body. The
        caller-supplied serial travels in the ``deviceSerial`` header and
        the SDK-managed ``accessToken`` header is added per attempt; as a
        declared read-only operation it may be replayed once after the
        existing single refresh when the platform reports code ``10002``.
        On success ``data.valueInfo.powerStatus`` must be a string —
        documented values are "0" (not charging), "1" (charging),
        "2" (full), "3" (no battery) and "4" (battery faulty); any other
        string is passed through verbatim.
        """
        with _diagnostic_scope(self._client, entrypoint="devices.charging_status", mode="sync"):
            headers = _charging_status_headers(self._client.region.scope, device_serial)
            response = self._client.request(_OP_CHARGING_STATUS, headers=headers)
            return _check_charging_status_response(response)


class AsyncDevices(AsyncDeviceManagement):
    """Asynchronous facade over the reviewed device query operations."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _domestic(self) -> bool:
        return self._client.region.scope == _DOMESTIC

    async def list(self, page_start: int = 0, page_size: int = 10) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.list", mode="async"):
            data = {
                "pageStart": _check_page_start(page_start, domestic=self._domestic()),
                "pageSize": _check_page_size(page_size),
            }
            return await self._client.request(_OP_LIST, data=data)

    async def list_by_id(
        self,
        *,
        cursor: str | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.list_by_id", mode="async"):
            _require_domestic(self._client.region.scope)
            params = _device_cursor_params(
                cursor=cursor,
                page_start=page_start,
                page_size=page_size,
            )
            response = await self._client.request(_OP_LIST_BY_ID, params=params)
            return _check_device_cursor_page(response)

    async def list_managed(self, page_start: int = 0, page_size: int = 10) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.list_managed", mode="async"):
            return await self._client.request(
                _OP_LIST_MANAGED,
                data=_managed_list_data(self._client.region.scope, page_start, page_size),
            )

    async def get(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.get", mode="async"):
            serial = _check_device_serial(device_serial, require_uppercase=False)
            return await self._client.request(_OP_GET, data={"deviceSerial": serial})

    async def get_utc(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.get_utc", mode="async"):
            _require_overseas(self._client.region.scope)
            response = await self._client.request(_OP_GET_UTC, data=_utc_form(device_serial))
            return _check_utc_response(response)

    async def channels(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.channels", mode="async"):
            serial = _check_device_serial(device_serial, require_uppercase=self._domestic())
            return await self._client.request(_OP_CHANNELS, data={"deviceSerial": serial})

    async def status(self, device_serial: str, channel: int = 1) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="devices.status", mode="async"):
            serial = _check_device_serial(device_serial, require_uppercase=self._domestic())
            data = {"deviceSerial": serial, "channel": _check_int("channel", channel)}
            return await self._client.request(_OP_STATUS, data=data)

    async def charging_status(self, device_serial: str) -> ApiResponse:
        """Read the battery charging status (domestic region only).

        Async variant of :meth:`Devices.charging_status`: one logical GET
        carrying the caller-supplied ``deviceSerial`` header plus the
        SDK-managed ``accessToken`` header, replayed at most once after
        the existing single refresh on platform code ``10002``.
        """
        with _diagnostic_scope(self._client, entrypoint="devices.charging_status", mode="async"):
            headers = _charging_status_headers(self._client.region.scope, device_serial)
            response = await self._client.request(_OP_CHARGING_STATUS, headers=headers)
            return _check_charging_status_response(response)
