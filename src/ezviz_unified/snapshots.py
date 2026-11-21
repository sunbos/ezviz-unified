"""Device snapshot (capture) facades for the unified EZVIZ client.

Covers the reviewed capture operations: the shared lapp capture endpoint
used by both the domestic and overseas regions, and the domestic-only
variant that stores the picture into a developer-managed result space.
Both capture operations carry the accessToken in the form, issue a brand-new
capture command (read_only=False, possibly billed), and return the raw
ApiResponse payload — picUrl / fileNodeId are exposed verbatim and are
never fetched or followed. The overseas Linktom lookup retrieves an existing
picture reference by its device SDK capture UUID.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text, _require_overseas

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"
_OVERSEAS = "overseas"

# /api/lapp/device/capture answers code/msg/data with string "200".
_LAPP_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
)

# /api/service/open/capture/device/capture answers meta.code/meta.message/data
# with integer 200.
_SPACE_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)

_PICTURE_LOOKUP_CODES = (
    200,
    400,
    403,
    404,
    500,
    10001,
    10002,
    10005,
    10031,
    20301,
    20302,
    20303,
    49999,
)

_PICTURE_LOOKUP_OP = Operation(
    "snapshots.get_by_uuid",
    "POST",
    "/api/lapp/device/uuid/picture",
    token_location="form",
    regions=frozenset({_OVERSEAS}),
    read_only=True,
    policy=replace(
        _LAPP_POLICY,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(
            value for code in _PICTURE_LOOKUP_CODES for value in (code, str(code))
        ),
    ),
)

_CAPTURE_OP = Operation(
    "snapshots.capture",
    "POST",
    "/api/lapp/device/capture",
    token_location="form",
    regions=frozenset({_DOMESTIC, _OVERSEAS}),
    read_only=False,
    policy=_LAPP_POLICY,
)

_CAPTURE_TO_SPACE_OP = Operation(
    "snapshots.capture_to_space",
    "POST",
    "/api/service/open/capture/device/capture",
    token_location="form",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_SPACE_POLICY,
)


def _check_serial(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("device_serial must be a non-blank string")
    return value


def _check_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int")
    return value


def _check_quality(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
        raise ValueError("quality must be an int between 0 and 4")
    return value


def _picture_lookup_form(capture_uuid: str, size: int) -> dict[str, str | int]:
    uid = _check_text(capture_uuid, "capture_uuid")
    value = _check_int(size, "size")
    if not 0 <= value <= 1280:
        raise ValueError("size must be within 0..1280")
    return {"uuid": uid, "size": value}


def _picture_lookup_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, dict) or not isinstance(data.get("picUrl"), str):
        raise ProtocolError(
            "unexpected snapshots.get_by_uuid response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _capture_call(
    serial: object, channel: object, quality: object, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    if scope not in {_DOMESTIC, _OVERSEAS}:
        raise ValueError("snapshots.capture is not supported in this region")
    checked_serial = _check_serial(serial)
    if scope == _DOMESTIC and any("a" <= ch <= "z" for ch in checked_serial):
        raise ValueError("device_serial letters must be uppercase in the domestic region")
    data: dict[str, str | int] = {
        "deviceSerial": checked_serial,
        "channelNo": _check_int(channel, "channel"),
    }
    if quality is not None:
        if scope != _OVERSEAS:
            raise ValueError("quality is only supported in the overseas region")
        data["quality"] = _check_quality(quality)
    return _CAPTURE_OP, data


def _capture_to_space_call(
    serial: object, result_space_id: object, channel: object, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    if scope != _DOMESTIC:
        raise ValueError("snapshots.capture_to_space is only supported in the domestic region")
    data: dict[str, str | int] = {
        "deviceSerial": _check_serial(serial),
        "resultSpaceId": _check_int(result_space_id, "result_space_id"),
    }
    if channel is not None:
        data["channelNo"] = str(_check_int(channel, "channel"))
    return _CAPTURE_TO_SPACE_OP, data


class Snapshots:
    """Synchronous facade over the reviewed device capture and reference lookup operations."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get_by_uuid(self, capture_uuid: str, *, size: int) -> ApiResponse:
        """Return the picture reference for an existing authorized Linktom device SDK capture."""
        with _diagnostic_scope(self._client, entrypoint="snapshots.get_by_uuid", mode="sync"):
            _require_overseas(self._client.region.scope)
            response = self._client.request(
                _PICTURE_LOOKUP_OP,
                data=_picture_lookup_form(capture_uuid, size),
            )
            return _picture_lookup_response(response)

    def capture(
        self, device_serial: str, *, channel: int, quality: int | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="snapshots.capture", mode="sync"):
            op, data = _capture_call(device_serial, channel, quality, self._client.region.scope)
            return self._client.request(op, data=data)

    def capture_to_space(
        self, device_serial: str, result_space_id: int, *, channel: int | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="snapshots.capture_to_space", mode="sync"):
            op, data = _capture_to_space_call(
                device_serial, result_space_id, channel, self._client.region.scope
            )
            return self._client.request(op, data=data)


class AsyncSnapshots:
    """Asynchronous facade over the reviewed device capture and reference lookup operations."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get_by_uuid(self, capture_uuid: str, *, size: int) -> ApiResponse:
        """Return the picture reference for an existing authorized Linktom device SDK capture."""
        with _diagnostic_scope(self._client, entrypoint="snapshots.get_by_uuid", mode="async"):
            _require_overseas(self._client.region.scope)
            response = await self._client.request(
                _PICTURE_LOOKUP_OP,
                data=_picture_lookup_form(capture_uuid, size),
            )
            return _picture_lookup_response(response)

    async def capture(
        self, device_serial: str, *, channel: int, quality: int | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="snapshots.capture", mode="async"):
            op, data = _capture_call(device_serial, channel, quality, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def capture_to_space(
        self, device_serial: str, result_space_id: int, *, channel: int | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="snapshots.capture_to_space",
            mode="async",
        ):
            op, data = _capture_to_space_call(
                device_serial, result_space_id, channel, self._client.region.scope
            )
            return await self._client.request(op, data=data)
