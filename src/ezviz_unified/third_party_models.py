"""Field builders and payload checks for domestic third-party protocol APIs.

Covers the ``dhreg``/``tiandy``/``huawei``/``jt808`` device-license surface:
project open/query, single and batch license application, task polling,
paged device listing, and license edit.  Builders only validate arguments
and return wire form fields; the facade performs the actual requests.
"""

from __future__ import annotations

from collections.abc import Sequence

from .errors import ProtocolError
from .live_limits import _wire_int
from .models import ApiResponse
from .settings import _require_domestic
from .storage_models import _integer, _text
from .vod import _utf8

_PROTOCOLS = frozenset({"dhreg", "huawei", "jt808", "tiandy"})
_JT808 = "jt808"


def protocol_for(scope: str, protocol: str) -> str:
    """Return *protocol* after enforcing domestic scope and the allowlist."""
    _require_domestic(scope)
    if not isinstance(protocol, str):
        raise ValueError("protocol must be a string")
    if protocol not in _PROTOCOLS:
        raise ValueError("protocol must be one of dhreg, huawei, jt808, tiandy")
    return protocol


def _opaque(name: str, value: object) -> str:
    return _utf8(name, _text(name, value))


def _credential(name: str, value: object) -> str:
    text = _text(name, value)
    if len(text) < 4 or len(text) > 20 or not text.isascii() or not text.isalnum():
        raise ValueError(f"{name} must be 4-20 ASCII alphanumeric characters")
    return text


def _set_channel_count(fields: dict[str, str], value: object) -> None:
    if value is None:
        return
    count = _integer("channelCount", value)
    if count < 0 or count > 256:
        raise ValueError("channelCount must be between 0 and 256")
    fields["channelCount"] = _wire_int("channelCount", count)


def _plate(name: str, value: object) -> str:
    text = _opaque(name, value)
    if len(text) not in (7, 8):
        raise ValueError(f"{name} must be 7 or 8 characters")
    second = text[1]
    if not second.isascii() or not second.isalpha():
        raise ValueError(f"{name} second character must be an ASCII letter")
    return text


def _color(name: str, value: object) -> int:
    number = _integer(name, value)
    if number < 0 or number > 9:
        raise ValueError(f"{name} must be between 0 and 9")
    return number


def _string_members(name: str, values: Sequence[str]) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be a sequence of strings")
    count = len(values)
    if count < 1 or count > 300:
        raise ValueError(f"{name} must contain between 1 and 300 members")
    items = []
    for item in values:
        text = _opaque(name, item)
        if "," in text:
            raise ValueError(f"{name} members must not contain delimiters")
        items.append(text)
    return items


def fields_open_project(protocol: str) -> dict[str, str]:
    """Open-project takes a form body with no business fields."""
    return {}


def fields_get_project(protocol: str) -> dict[str, str]:
    """Project query is a plain GET; no fields are sent."""
    return {}


def fields_apply_device(
    protocol: str,
    device_name: str,
    *,
    device_password: str | None = None,
    device_username: str | None = None,
    channel_count: int | None = None,
    device_code: str | None = None,
    vehicle_plate: str | None = None,
    vehicle_color: int | None = None,
) -> dict[str, str]:
    """Wire fields for a single license application."""
    name = _opaque("deviceName", device_name)
    if len(name) > 50:
        raise ValueError("deviceName must be at most 50 characters")
    fields = {"deviceName": name}
    if protocol == _JT808:
        if device_password is not None or device_username is not None:
            raise ValueError("jt808 devices must not carry license credentials")
        if vehicle_plate is None or vehicle_color is None:
            raise ValueError("jt808 requires vehicleLicensePlate and vehicleLicenseColor")
        fields["vehicleLicensePlate"] = _plate("vehicleLicensePlate", vehicle_plate)
        fields["vehicleLicenseColor"] = _wire_int(
            "vehicleLicenseColor", _color("vehicleLicenseColor", vehicle_color)
        )
    else:
        if device_password is None or device_username is None:
            raise ValueError("deviceLicense and deviceUserName are required")
        fields["deviceLicense"] = _credential("deviceLicense", device_password)
        fields["deviceUserName"] = _credential("deviceUserName", device_username)
        if vehicle_plate is not None or vehicle_color is not None:
            raise ValueError("vehicle fields are only valid for jt808")
    _set_channel_count(fields, channel_count)
    if device_code is not None:
        fields["deviceCode"] = _opaque("deviceCode", device_code)
    return fields


def fields_apply_batch(
    protocol: str,
    *,
    device_count: int | None = None,
    device_codes: Sequence[str] | None = None,
    device_password: str | None = None,
    device_username: str | None = None,
    channel_count: int | None = None,
    vehicle_plates: Sequence[str] | None = None,
    vehicle_colors: Sequence[int] | None = None,
) -> dict[str, str]:
    """Wire fields for a batch license application returning only a taskId."""
    if device_count is None and device_codes is None:
        raise ValueError("deviceCount or deviceCode entries are required")
    count = None
    if device_codes is None and device_count is not None:
        count = _integer("deviceCount", device_count)
        if count < 1 or count > 300:
            raise ValueError("deviceCount must be between 1 and 300")
    codes = None
    if device_codes is not None:
        codes = _string_members("deviceCode", device_codes)
        if len(codes) < 1 or len(codes) > 300:
            raise ValueError("deviceCode requires between 1 and 300 entries")
    fields: dict[str, str] = {}
    if codes is not None:
        fields["deviceCode"] = ",".join(codes)
    elif count is not None:
        fields["deviceCount"] = _wire_int("deviceCount", count)
    if device_password is not None:
        fields["deviceLicense"] = (
            "" if device_password == "" else _credential("deviceLicense", device_password)
        )
    if device_username is not None:
        fields["deviceUserName"] = (
            "" if device_username == "" else _credential("deviceUserName", device_username)
        )
    _set_channel_count(fields, channel_count)
    if protocol == _JT808:
        if vehicle_plates is None or vehicle_colors is None:
            raise ValueError("jt808 requires vehicleLicensePlate and vehicleLicenseColor")
        plates = [
            _plate("vehicleLicensePlate", item)
            for item in _string_members("vehicleLicensePlate", vehicle_plates)
        ]
        if not isinstance(vehicle_colors, Sequence) or isinstance(
            vehicle_colors, (str, bytes, bytearray)
        ):
            raise ValueError("vehicleLicenseColor must be a sequence of integers")
        if len(vehicle_colors) != len(plates):
            raise ValueError("vehicleLicenseColor must match the number of vehicleLicense plates")
        colors = [_color("vehicleLicenseColor", item) for item in vehicle_colors]
        if len(plates) != len(colors):
            raise ValueError("vehicleLicensePlate and vehicleLicenseColor must align")
        expected = len(codes) if codes is not None else count
        if len(plates) != expected:
            raise ValueError("vehicle entries must equal the requested device count")
        fields["vehicleLicensePlate"] = ",".join(plates)
        fields["vehicleLicenseColor"] = ",".join(
            _wire_int("vehicleLicenseColor", item) for item in colors
        )
    elif vehicle_plates is not None or vehicle_colors is not None:
        raise ValueError("vehicle fields are only valid for jt808")
    return fields


def fields_get_task(protocol: str, task_id: int) -> dict[str, str]:
    return {"taskId": _wire_int("taskId", _integer("taskId", task_id))}


def fields_list_devices(
    protocol: str,
    *,
    device_serial: str | None = None,
    task_id: int | None = None,
    start_index: int = 0,
    page_size: int = 10,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if device_serial is not None:
        fields["deviceSerial"] = _opaque("deviceSerial", device_serial)
    if task_id is not None:
        fields["taskId"] = _wire_int("taskId", _integer("taskId", task_id))
    fields["startIndex"] = _wire_int("startIndex", _integer("startIndex", start_index))
    size = _integer("pageSize", page_size)
    if size < 1 or size > 50:
        raise ValueError("pageSize must be between 1 and 50")
    fields["pageSize"] = _wire_int("pageSize", size)
    return fields


def fields_edit_device(
    protocol: str,
    device_serial: str,
    *,
    device_name: str | None = None,
    device_password: str | None = None,
    device_username: str | None = None,
    vehicle_plate: str | None = None,
    vehicle_color: int | None = None,
) -> dict[str, str]:
    """Wire fields for a license edit; serial alone is valid for non-JT."""
    fields = {"deviceSerial": _opaque("deviceSerial", device_serial)}
    if device_name is not None:
        fields["deviceName"] = _opaque("deviceName", device_name)
    if device_password is not None:
        fields["deviceLicense"] = _credential("deviceLicense", device_password)
    if device_username is not None:
        fields["deviceUserName"] = _credential("deviceUserName", device_username)
    if protocol == _JT808:
        if vehicle_plate is None or vehicle_color is None:
            raise ValueError("jt808 requires vehicleLicensePlate and vehicleLicenseColor")
        fields["vehicleLicensePlate"] = _plate("vehicleLicensePlate", vehicle_plate)
        fields["vehicleLicenseColor"] = _wire_int(
            "vehicleLicenseColor", _color("vehicleLicenseColor", vehicle_color)
        )
    elif vehicle_plate is not None or vehicle_color is not None:
        raise ValueError("vehicle fields are only valid for jt808")
    return fields


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _shape(response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        "unexpected third-party protocol response shape",
        operation=response.operation,
        http_status=response.http_status,
    )


def _page_ok(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    rows = data.get("data")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return False
    if "nextIndex" in data and data["nextIndex"] is not None and not _is_int(data["nextIndex"]):
        return False
    if "pageSize" in data and not _is_int(data["pageSize"]):
        return False
    return True


def checked(response: ApiResponse) -> ApiResponse:
    """Validate the decoded ``data`` payload for the given operation."""
    operation = response.operation
    data = response.data
    if operation == "third_party_protocols.edit_device":
        return response
    if operation == "third_party_protocols.open_project":
        ok = (
            isinstance(data, dict)
            and _nonblank(data.get("productKey"))
            and _is_int(data.get("createTime"))
        )
    elif operation == "third_party_protocols.get_project":
        ok = (
            isinstance(data, dict)
            and _nonblank(data.get("productKey"))
            and _is_int(data.get("createTime"))
            and _nonblank(data.get("dasDomain"))
            and (_nonblank(data.get("dasPort")) or _is_int(data.get("dasPort")))
        )
    elif operation == "third_party_protocols.apply_device":
        ok = isinstance(data, dict) and _nonblank(data.get("deviceSerial"))
    elif operation == "third_party_protocols.apply_batch":
        ok = isinstance(data, dict) and _is_int(data.get("taskId"))
    elif operation == "third_party_protocols.get_task":
        ok = isinstance(data, dict) and all(
            _is_int(data.get(key)) for key in ("taskId", "status", "deviceCount", "createTime")
        )
    elif operation == "third_party_protocols.list_devices":
        ok = _page_ok(data)
    else:
        ok = False
    if not ok:
        raise _shape(response)
    return response
