"""Domestic device-add share link management.

``create`` issues POST ``/api/service/device/add/tokenUrl`` to mint a
device-adding URL, while ``records`` and ``list`` issue the read-only
GETs ``/api/service/device/add/tokenNote`` and
``/api/service/device/add/tokenUrls``. All three endpoints are
domestic-only; the managed ``accessToken`` header is injected by the
client rather than supplied by the caller. The SDK never infers
serials, cursors, or pagination, and never opens, follows, paginates,
or revokes the returned links.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text, _require_domestic
from .storage_models import _integer
from .vod import _header, _utf8

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncDeviceAddLinks", "DeviceAddLinks"]

_DOMESTIC_ONLY = frozenset({"domestic"})

_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    redact_device_add_details=True,
)

_CREATE = Operation(
    "device_add_links.create",
    "POST",
    "/api/service/device/add/tokenUrl",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=False,
    policy=_POLICY,
)
_RECORDS = Operation(
    "device_add_links.records",
    "GET",
    "/api/service/device/add/tokenNote",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=True,
    policy=_POLICY,
)
_LIST = Operation(
    "device_add_links.list",
    "GET",
    "/api/service/device/add/tokenUrls",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=True,
    policy=_POLICY,
)


def _is_genuine_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _build_create_body(expire_days: int, note: str | None) -> dict[str, str | int]:
    """Return the plain JSON body for ``create`` after validation."""
    days = _integer("expire_days", expire_days)
    if not 1 <= days <= 7:
        raise ValueError("expire_days must be within 1..7")
    body: dict[str, str | int] = {"expireTime": days}
    if note is not None:
        if not isinstance(note, str):
            raise ValueError("note must be a str or None")
        _utf8("note", note)
        if len(note) > 256:
            raise ValueError("note must be at most 256 characters")
        body["note"] = note
    return body


def _build_page_params(cursor: str | None, page_size: int) -> dict[str, str | int]:
    """Return the query mapping shared by ``records`` and ``list``."""
    size = _integer("page_size", page_size)
    if not 1 <= size <= 50:
        raise ValueError("page_size must be within 1..50")
    params: dict[str, str | int] = {"pageSize": size}
    if cursor is not None:
        params["id"] = _check_text(cursor, "cursor")
    return params


def _records_headers(device_serial: str | None) -> dict[str, str] | None:
    if device_serial is None:
        return None
    return {"deviceSerial": _header("device_serial", device_serial)}


def _fail(response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        "unexpected device add link response shape",
        operation=response.operation,
        http_status=response.http_status,
    )


def _check_create_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, dict):
        raise _fail(response)
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise _fail(response)
    return response


def _check_records_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise _fail(response)
    for row in data:
        if "id" in row and not _is_genuine_int(row["id"]):
            raise _fail(response)
        if "bindTime" in row and not _is_genuine_int(row["bindTime"]):
            raise _fail(response)
    return response


def _check_list_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise _fail(response)
    for row in data:
        if "id" in row and not isinstance(row["id"], str):
            raise _fail(response)
        if "expireTime" in row and not _is_genuine_int(row["expireTime"]):
            raise _fail(response)
        if "expired" in row and not isinstance(row["expired"], bool):
            raise _fail(response)
        if "url" in row and row["url"] is not None and not isinstance(row["url"], str):
            raise _fail(response)
    return response


class DeviceAddLinks:
    """Synchronous facade over the domestic device-add link endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def create(self, expire_days: int, *, note: str | None = None) -> ApiResponse:
        """POST ``/api/service/device/add/tokenUrl`` once.

        ``expire_days`` is an int in 1..7 sent as ``expireTime``;
        ``note`` is omitted when ``None`` and otherwise sent verbatim
        (empty string allowed, at most 256 UTF-8 characters). The
        client injects the ``accessToken`` header. ``response.data``
        must be a dict carrying a nonblank ``url`` string or
        :class:`ProtocolError` is raised.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="device_add_links.create",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            body = _build_create_body(expire_days, note)
            response = self._client.request(
                _CREATE,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            return _check_create_response(response)

    def records(
        self,
        *,
        device_serial: str | None = None,
        cursor: str | None = None,
        page_size: int = 10,
    ) -> ApiResponse:
        """GET ``/api/service/device/add/tokenNote``.

        ``device_serial`` is sent as the ``deviceSerial`` header when
        given; ``page_size`` is an int in 1..50 sent as ``pageSize``
        and ``cursor`` is sent verbatim as ``id`` when given. Rows
        must be dicts whose ``id``/``bindTime``, when present, are
        genuine ints; unknown keys are retained verbatim.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="device_add_links.records",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            params = _build_page_params(cursor, page_size)
            headers = _records_headers(device_serial)
            response = self._client.request(
                _RECORDS,
                params=params,
                headers=headers,
            )
            return _check_records_response(response)

    def list(
        self,
        *,
        cursor: str | None = None,
        page_size: int = 10,
    ) -> ApiResponse:
        """GET ``/api/service/device/add/tokenUrls``.

        ``page_size`` is an int in 1..50 sent as ``pageSize`` and
        ``cursor`` is sent verbatim as ``id`` when given. Rows must be
        dicts whose ``id`` is a str and whose ``expireTime``,
        ``expired``, and ``url`` (str or ``None``) match their
        documented types when present; unknown keys are retained.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="device_add_links.list",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            params = _build_page_params(cursor, page_size)
            response = self._client.request(_LIST, params=params)
            return _check_list_response(response)


class AsyncDeviceAddLinks:
    """Asynchronous facade over the domestic device-add link endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(
        self,
        expire_days: int,
        *,
        note: str | None = None,
    ) -> ApiResponse:
        """Async twin of :meth:`DeviceAddLinks.create`."""
        with _diagnostic_scope(
            self._client,
            entrypoint="device_add_links.create",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            body = _build_create_body(expire_days, note)
            response = await self._client.request(
                _CREATE,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            return _check_create_response(response)

    async def records(
        self,
        *,
        device_serial: str | None = None,
        cursor: str | None = None,
        page_size: int = 10,
    ) -> ApiResponse:
        """Async twin of :meth:`DeviceAddLinks.records`."""
        with _diagnostic_scope(
            self._client,
            entrypoint="device_add_links.records",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            params = _build_page_params(cursor, page_size)
            headers = _records_headers(device_serial)
            response = await self._client.request(
                _RECORDS,
                params=params,
                headers=headers,
            )
            return _check_records_response(response)

    async def list(
        self,
        *,
        cursor: str | None = None,
        page_size: int = 10,
    ) -> ApiResponse:
        """Async twin of :meth:`DeviceAddLinks.list`."""
        with _diagnostic_scope(
            self._client,
            entrypoint="device_add_links.list",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            params = _build_page_params(cursor, page_size)
            response = await self._client.request(_LIST, params=params)
            return _check_list_response(response)
