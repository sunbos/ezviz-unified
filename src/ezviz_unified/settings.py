"""Device settings facade for the unified EZVIZ client (sync and async).

Covers the reviewed switch operations (Wi-Fi configuration prompt, scene
lens occlusion / video tampering, sound source localization, indicator
light, all-day recording, and microphone) plus the overseas-only settings
batch: PIR detection area, doorbell chime, infrared switch, device
timezone, device language, the public timezone directory list, and the
public country/region directory. It also covers the domestic-only talk
speaker volume pair (docs 1718 and 1719).

Most endpoints are POST with form-encoded bodies and a top-level
code/msg/data envelope where the string "200" marks success. Two reviewed
operations differ: the public area directory, which the SDK deliberately
calls with GET because the source demonstrates a parameterless GET while
the declared POST's shape is undocumented; and the domestic talk speaker
volume pair, whose GET and POST share one path
(``/api/v3/device/talkSpeakerVolume``) and carry the access token in a
header rather than the form. The volume GET sends ``deviceSerial`` as a
query parameter with no body; the volume POST sends ``deviceSerial`` and
``volume`` as a URL-encoded form body under an explicit
``Content-Type: application/x-www-form-urlencoded`` header. Authenticated
operations carry the access token in the form (or, for the volume pair, in
the header); ``list_timezones`` and ``list_areas`` are explicit public
operations that carry no token at all.

Overseas batch notes from the reviewed sources: the PIR ``area`` string is
forwarded verbatim (no reordering, bitmask conversion, or grid inference);
chime ``duration`` is a plain integer whose unit and range are not
specified in the source; ``timeFormat`` is a required keyword-only SDK
argument even though the source does not mark it required; and
``daylightSaving`` is sent as the string "0"/"1". The timezone directory
``language`` accepts only ENGLISH or SIMPCN, while the device ``language``
accepts any non-blank UTF-8 string. The area directory returns the raw
``data`` array untouched: its documented item fields ``id`` (int),
``name`` (string), ``region`` (string) and ``telephoneCode`` (int) are not
validated per element, extra fields and an empty array are preserved, and
no cluster, ``apiDomain`` or ``authDomain`` is derived from it.

Domestic talk speaker volume pair (docs 1718/1719): domestic-only GET and
POST on one path, header token, strict int ``data.talkSpeakerVolume`` on
read, and no ``data`` body required on write.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from .cruise import _check_time
from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation, TokenLocation
from .vod import _header

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"
_OVERSEAS = "overseas"
_OVERSEAS_REGIONS = frozenset({_OVERSEAS})

_POLICY = ResponsePolicy(success_values=("200",))

_BODY_META_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    envelope_paths=("body",),
    redact_message_urls=True,
)

# Motion area coordinates reveal which zones a device watches, so both
# regional policies opt into the device-add redaction switch for this
# sensitive data.
_DOMESTIC_DEVICE_SETTINGS_POLICY = replace(
    _POLICY,
    redact_device_add_details=True,
)

_MOTION_AREA_OVERSEAS_POLICY = replace(
    _BODY_META_POLICY,
    redact_device_add_details=True,
)

_HUMAN_TRACKING_POLICY = replace(
    _DOMESTIC_DEVICE_SETTINGS_POLICY,
    code_path="meta.code",
    message_path="meta.message",
    success_values=(200,),
)

_SET_MOTION_TRACKING = Operation(
    name="settings.set_motion_tracking",
    method="POST",
    path="/api/lapp/device/mobile/status/set",
    regions=frozenset({_DOMESTIC}),
    token_location="form",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_INTELLIGENT_DETECTION_SWITCH = Operation(
    name="settings.get_intelligent_detection_switch",
    method="POST",
    path="/api/lapp/device/intelligence/detection/switch/status",
    regions=frozenset({_DOMESTIC}),
    token_location="form",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SET_INTELLIGENT_DETECTION_SWITCH = Operation(
    name="settings.set_intelligent_detection_switch",
    method="POST",
    path="/api/lapp/device/intelligence/detection/switch/set",
    regions=frozenset({_DOMESTIC}),
    token_location="form",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SELECT_ALARM_DETECTION_MODE = Operation(
    name="settings.select_alarm_detection_mode",
    method="POST",
    path="/api/v3/device/alarm/detect/switch/set",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_ALARM_DETECTION_MODE = Operation(
    name="settings.get_alarm_detection_mode",
    method="GET",
    path="/api/v3/device/alarm/detect/switch/get",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_MOTION_TRACKING = Operation(
    name="settings.get_motion_tracking",
    method="POST",
    path="/api/lapp/device/mobile/status/get",
    regions=frozenset({_DOMESTIC}),
    token_location="form",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_HUMAN_TRACKING = Operation(
    name="settings.get_human_tracking",
    method="GET",
    path="/api/v3/device/switch/human/track",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=True,
    policy=_HUMAN_TRACKING_POLICY,
)

_SET_HUMAN_TRACKING = Operation(
    name="settings.set_human_tracking",
    method="PUT",
    path="/api/v3/device/switch/human/track",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=False,
    policy=_HUMAN_TRACKING_POLICY,
)

_GET_MOTION_SENSITIVITY = Operation(
    name="settings.get_motion_sensitivity",
    method="POST",
    path="/api/lapp/device/algorithm/config/get",
    regions=frozenset({_DOMESTIC}),
    token_location="form",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SET_MOTION_SENSITIVITY = Operation(
    name="settings.set_motion_sensitivity",
    method="POST",
    path="/api/lapp/device/algorithm/config/set",
    regions=frozenset({_DOMESTIC}),
    token_location="form",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_DETECTION_SENSITIVITY = Operation(
    name="settings.get_detection_sensitivity",
    method="GET",
    path="/api/v3/device/detect/config/get",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SET_DETECTION_SENSITIVITY = Operation(
    name="settings.set_detection_sensitivity",
    method="POST",
    path="/api/v3/device/detect/config/set",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_MOTION_AREA = Operation(
    name="settings.get_motion_area",
    method="GET",
    path="/api/v3/device/motion/detect/get",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SET_MOTION_AREA = Operation(
    name="settings.set_motion_area",
    method="POST",
    path="/api/v3/device/motion/detect/set",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SET_MOTION_AREA_OVERSEAS = Operation(
    name="settings.set_motion_area",
    method="POST",
    path="/api/v3/device/motion/detect/set",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=False,
    policy=_MOTION_AREA_OVERSEAS_POLICY,
)

_GET_ALARM_SOUND_MODE = Operation(
    name="settings.get_alarm_sound_mode",
    method="GET",
    path="/api/v3/device/alarmSound/enabled/get",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=True,
    policy=_BODY_META_POLICY,
)

_SET_ALARM_SOUND_MODE = Operation(
    name="settings.set_alarm_sound_mode",
    method="POST",
    path="/api/v3/device/alarmSound/enabled/set",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=False,
    policy=_BODY_META_POLICY,
)

_PLAY_AUDITION = Operation(
    name="settings.play_audition",
    method="POST",
    path="/api/v3/device/audition",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=False,
    policy=_BODY_META_POLICY,
)

_GET_LIGHT_LINKAGE = Operation(
    name="settings.get_light_linkage",
    method="GET",
    path="/api/v3/device/switchStatus/get",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=True,
    policy=_BODY_META_POLICY,
)

_SET_LIGHT_LINKAGE = Operation(
    name="settings.set_light_linkage",
    method="POST",
    path="/api/v3/device/switchStatus/set",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=False,
    policy=_BODY_META_POLICY,
)

_GET_WORKING_MODE_PLANS = Operation(
    name="settings.get_working_mode_plans",
    method="GET",
    path="/api/v3/device/timing/plan/get",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=True,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_GET_TIMING_PLAN = Operation(
    name="settings.get_timing_plan",
    method="GET",
    path="/api/v3/device/timing/plan/get",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=True,
    policy=_BODY_META_POLICY,
)

_SET_HUASHU_4G_WORKING_MODE_PLAN = Operation(
    name="settings.set_huashu_4g_working_mode_plan",
    method="POST",
    path="/api/v3/device/timing/plan/set",
    regions=frozenset({_DOMESTIC}),
    token_location="header",
    read_only=False,
    policy=_DOMESTIC_DEVICE_SETTINGS_POLICY,
)

_SET_TIMING_PLAN = Operation(
    name="settings.set_timing_plan",
    method="POST",
    path="/api/v3/device/timing/plan/set",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=False,
    policy=_BODY_META_POLICY,
)

_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_TRACKING_FORM_HEADERS = {"Content-Type": _FORM_CONTENT_TYPE}

# Domestic talk speaker volume pair (docs 1718/1719): GET reads the value
# the device last reported, POST writes the speaker percentage. Both share
# one path and carry the access token in a header, never the form. The GET
# is read-only (eligible for the client's single token-refresh replay);
# the POST is never replayed.
_OP_GET_TALK_SPEAKER_VOLUME = Operation(
    "settings.get_talk_speaker_volume",
    "GET",
    "/api/v3/device/talkSpeakerVolume",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
_OP_SET_TALK_SPEAKER_VOLUME = Operation(
    "settings.set_talk_speaker_volume",
    "POST",
    "/api/v3/device/talkSpeakerVolume",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY,
)


class DeviceSwitch(StrEnum):
    """Reviewed per-device switch endpoints."""

    WIFI_PROMPT = "wifi_prompt"
    SCENE = "scene"
    SOUND_LOCALIZATION = "sound_localization"
    INDICATOR_LIGHT = "indicator_light"
    ALL_DAY_RECORD = "all_day_record"
    MICROPHONE = "microphone"


@dataclass(frozen=True)
class _SwitchSpec:
    base_path: str
    regions: frozenset[str]
    enable_as_string: bool
    allow_channel: bool


_SPECS: dict[DeviceSwitch, _SwitchSpec] = {
    DeviceSwitch.WIFI_PROMPT: _SwitchSpec(
        "/api/lapp/device/sound/switch",
        frozenset({"domestic", "overseas"}),
        enable_as_string=False,
        allow_channel=True,
    ),
    DeviceSwitch.SCENE: _SwitchSpec(
        "/api/lapp/device/scene/switch",
        frozenset({"domestic", "overseas"}),
        enable_as_string=False,
        allow_channel=True,
    ),
    DeviceSwitch.SOUND_LOCALIZATION: _SwitchSpec(
        "/api/lapp/device/ssl/switch",
        frozenset({"domestic", "overseas"}),
        enable_as_string=False,
        allow_channel=True,
    ),
    DeviceSwitch.INDICATOR_LIGHT: _SwitchSpec(
        "/api/lapp/device/light/switch",
        frozenset({"domestic"}),
        enable_as_string=False,
        allow_channel=True,
    ),
    DeviceSwitch.ALL_DAY_RECORD: _SwitchSpec(
        "/api/lapp/device/fullday/record/switch",
        frozenset({"domestic"}),
        enable_as_string=False,
        allow_channel=True,
    ),
    DeviceSwitch.MICROPHONE: _SwitchSpec(
        "/api/lapp/camera/video/sound",
        frozenset({"domestic", "overseas"}),
        enable_as_string=True,
        allow_channel=False,
    ),
}


def _resolve_switch(switch: DeviceSwitch | str) -> DeviceSwitch:
    if isinstance(switch, DeviceSwitch):
        return switch
    if isinstance(switch, str):
        try:
            return DeviceSwitch(switch)
        except ValueError:
            pass
    raise ValueError("switch must be a DeviceSwitch or its string value")


def _check_serial(value: object, *, domestic: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("device_serial must be a non-blank string")
    if domestic and any("a" <= ch <= "z" for ch in value):
        raise ValueError("device_serial letters must be uppercase for the domestic region")
    return value


def _check_channel(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("channel must be an int")
    return value


def _check_enabled(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("enabled must be a bool")
    return value


def _operation(
    switch: DeviceSwitch, spec: _SwitchSpec, suffix: str, *, read_only: bool
) -> Operation:
    return Operation(
        f"settings.{switch.value}.{'get' if read_only else 'set'}",
        "POST",
        f"{spec.base_path}/{suffix}",
        regions=spec.regions,
        read_only=read_only,
        policy=_POLICY,
    )


def _spec_for(switch: DeviceSwitch | str, scope: str) -> tuple[DeviceSwitch, _SwitchSpec]:
    resolved = _resolve_switch(switch)
    spec = _SPECS[resolved]
    if scope not in spec.regions:
        raise ValueError("device switch is not supported in this region")
    return resolved, spec


def _get_data(
    switch: DeviceSwitch | str, serial: object, scope: str
) -> tuple[Operation, dict[str, str]]:
    resolved, spec = _spec_for(switch, scope)
    data = {"deviceSerial": _check_serial(serial, domestic=scope == _DOMESTIC)}
    return _operation(resolved, spec, "status", read_only=True), data


def _set_data(
    switch: DeviceSwitch | str,
    serial: object,
    enabled: object,
    channel: int | None,
    scope: str,
) -> tuple[Operation, dict[str, str | int]]:
    resolved, spec = _spec_for(switch, scope)
    data: dict[str, str | int] = {
        "deviceSerial": _check_serial(serial, domestic=scope == _DOMESTIC),
        "enable": _enable_value(_check_enabled(enabled), as_string=spec.enable_as_string),
    }
    if spec.allow_channel:
        if channel is not None:
            data["channelNo"] = _check_channel(channel)
    elif channel is not None:
        raise ValueError("this device switch does not accept a channel")
    return _operation(resolved, spec, "set", read_only=False), data


def _enable_value(enabled: bool, *, as_string: bool) -> str | int:
    if as_string:
        return "1" if enabled else "0"
    return 1 if enabled else 0


def _require_overseas(scope: str) -> None:
    if scope != _OVERSEAS:
        raise ValueError("this operation is only supported in the overseas region")


def _require_domestic(scope: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError("this operation is only supported in the domestic region")


def _check_text(value: object, field: str) -> str:
    """Return a non-blank UTF-8 encodable string unchanged."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        encodable = False
    else:
        encodable = True
    if not encodable:
        raise ValueError(f"{field} must be encodable as UTF-8")
    return value


def _check_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an int")
    return value


def _check_chime_type(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (1, 2, 3):
        raise ValueError("chime_type must be the int 1, 2, or 3")
    return value


def _check_time_format(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1, 2):
        raise ValueError("time_format must be the int 0, 1, or 2")
    return value


def _check_daylight_saving(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("daylight_saving must be a bool or None")
    return value


def _check_timezone_language(value: object) -> str:
    if not isinstance(value, str) or value not in ("ENGLISH", "SIMPCN"):
        raise ValueError("language must be ENGLISH or SIMPCN")
    return value


def _put_channel(data: dict[str, str | int], channel: int | None) -> None:
    if channel is not None:
        data["channelNo"] = _check_channel(channel)


def _overseas_op(
    name: str,
    path: str,
    *,
    read_only: bool,
    token_location: TokenLocation = "form",
) -> Operation:
    return Operation(
        f"settings.{name}",
        "POST",
        path,
        token_location=token_location,
        regions=_OVERSEAS_REGIONS,
        read_only=read_only,
        policy=_POLICY,
    )


def _overseas_get_op(name: str, path: str, *, read_only: bool) -> Operation:
    """Build a GET operation without folding the method into ``_overseas_op``."""
    return Operation(
        f"settings.{name}",
        "GET",
        path,
        token_location="none",
        regions=_OVERSEAS_REGIONS,
        read_only=read_only,
        policy=_POLICY,
    )


def _motion_sensitivity_form(device_serial: str) -> dict[str, str]:
    return {
        "deviceSerial": _check_serial(
            _check_text(device_serial, "deviceSerial"),
            domestic=True,
        ),
    }


def _motion_sensitivity_write_form(
    device_serial: str,
    *,
    value: int,
    channel_no: int | None,
) -> dict[str, str | int]:
    form: dict[str, str | int] = dict(_motion_sensitivity_form(device_serial))
    level = _check_int(value, "value")
    if level < 0 or level > 6:
        raise ValueError("value must be an integer between 0 and 6")
    form["type"] = 0
    form["value"] = level
    if channel_no is not None:
        form["channelNo"] = _check_int(channel_no, "channelNo")
    return form


def _is_text_or_int(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, str) or isinstance(value, int)


def _protocol_error(message: str, response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        message,
        operation=response.operation,
        http_status=response.http_status,
    )


def _motion_sensitivity_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, list):
        raise _protocol_error("motion sensitivity data is not a list", response)
    for row in data:
        if not isinstance(row, dict):
            raise _protocol_error(
                "motion sensitivity row is malformed",
                response,
            )
        if (
            not _is_text_or_int(row.get("channel"))
            or not isinstance(row.get("type"), str)
            or not _is_text_or_int(row.get("value"))
        ):
            raise _protocol_error(
                "motion sensitivity row is malformed",
                response,
            )
    return response


def _detection_sensitivity_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, list):
        raise _protocol_error(
            "detection sensitivity data is not a list",
            response,
        )
    for row in data:
        if not isinstance(row, dict):
            raise _protocol_error(
                "detection sensitivity row is malformed",
                response,
            )
        channel = row.get("channel")
        if (
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or not isinstance(row.get("type"), str)
            or not isinstance(row.get("value"), str)
        ):
            raise _protocol_error(
                "detection sensitivity row is malformed",
                response,
            )
    return response


def _check_detection_type(detection_type: object) -> str:
    if not isinstance(detection_type, str) or detection_type not in ("3", "4"):
        raise ValueError("detection_type must be the string '3' or '4'")
    return detection_type


def _check_detection_value(value: object) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError("value must be a non-empty ASCII digit string")
    significant = value.lstrip("0")
    if not significant or len(significant) > 3 or (len(significant) == 3 and significant > "100"):
        raise ValueError("value must be between 1 and 100")
    return value


def _detection_sensitivity_form(
    detection_type: object,
    value: object,
) -> dict[str, str]:
    return {
        "type": _check_detection_type(detection_type),
        "value": _check_detection_value(value),
    }


def _detection_sensitivity_write_headers(
    device_serial: str,
    channel_no: str,
) -> dict[str, str]:
    headers = _device_channel_headers(device_serial, channel_no)
    headers["Content-Type"] = _FORM_CONTENT_TYPE
    return headers


def _check_motion_area_read_scope(scope: object) -> None:
    """Reject the unresolved overseas read before parameters or requests."""
    if scope == _OVERSEAS:
        raise ValueError("overseas motion area read origin is unresolved")


def _device_channel_headers(device_serial: str, channel_no: str) -> dict[str, str]:
    """Return validated device and channel headers for v3 settings."""
    return {
        "deviceSerial": _header("device_serial", device_serial),
        "channelNo": _header("channel_no", channel_no),
    }


def _motion_area_value(area: str) -> str:
    """Return the opaque area string after rejecting control characters."""
    text = _check_text(area, "area")
    for char in text:
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            raise ValueError("area must not contain control characters")
    return text


def _intelligent_detection_form(
    device_serial: str, detection_type: str | None
) -> dict[str, str | int]:
    form: dict[str, str | int] = {
        "deviceSerial": _check_serial(_check_text(device_serial, "device_serial"), domestic=True),
    }
    if detection_type is not None:
        if type(detection_type) is not str or detection_type not in ("302", "304"):
            raise ValueError("detection_type must be '302' or '304'")
        form["type"] = detection_type
    return form


def _alarm_detection_mode_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, dict):
        raise ProtocolError(
            "invalid alarm detection mode response",
            operation=response.operation,
            http_status=response.http_status,
        )
    value_info = data.get("valueInfo")
    if not isinstance(value_info, dict) or type(value_info.get("type")) is not int:
        raise ProtocolError(
            "invalid alarm detection mode response",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _tracking_switch_response(
    response: ApiResponse, *, message: str = "invalid tracking switch response"
) -> ApiResponse:
    data = response.data
    if (
        type(data) is not dict
        or type(data.get("enable")) is not int
        or ("deviceSerial" in data and type(data["deviceSerial"]) is not str)
        or ("channelNo" in data and type(data["channelNo"]) is not int)
    ):
        raise ProtocolError(
            message,
            operation=response.operation,
            http_status=response.http_status,
        ) from None
    return response


def _motion_area_response(response: ApiResponse) -> ApiResponse:
    """Return the response when the motion area payload shape is valid."""
    data = response.data
    area = data.get("area") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or not isinstance(area, list)
        or any(type(item) is not int for item in area)
        or ("deviceSerial" in data and not isinstance(data["deviceSerial"], str))
        or any(
            key in data and type(data[key]) is not int for key in ("channelNo", "rows", "columns")
        )
    ):
        raise ProtocolError(
            "unexpected settings.get_motion_area response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _pir_area_get_request(
    serial: object, channel: int | None, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {"deviceSerial": _check_text(serial, "device_serial")}
    _put_channel(data, channel)
    return _overseas_op("get_pir_area", "/api/lapp/device/pir/get", read_only=True), data


def _pir_area_set_request(
    serial: object, area: object, channel: int | None, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {
        "deviceSerial": _check_text(serial, "device_serial"),
        "area": _check_text(area, "area"),
    }
    _put_channel(data, channel)
    return _overseas_op("set_pir_area", "/api/lapp/device/pir/set", read_only=False), data


def _light_linkage_response(response: ApiResponse) -> ApiResponse:
    """Return the response when the light linkage payload shape is valid."""
    data = response.data
    if (
        not isinstance(data, dict)
        or type(data.get("type")) is not int
        or type(data.get("enable")) is not int
        or ("subSerial" in data and not isinstance(data["subSerial"], str))
    ):
        raise ProtocolError(
            "unexpected settings.get_light_linkage response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _check_week(value: object) -> str:
    if not isinstance(value, str) or not all(
        len(day) == 1 and "0" <= day <= "6" for day in value.split(",")
    ):
        raise ValueError("week must be comma-separated digits 0-6")
    return value


def _timing_plan_form(
    enabled: bool, start_time: str, end_time: str, week: str, mode: int | None
) -> dict[str, str]:
    form = {
        "enable": "1" if _check_enabled(enabled) else "0",
        "startTime": _check_time("start_time", start_time),
        "endTime": _check_time("end_time", end_time),
        "week": _check_week(week),
    }
    if mode is not None:
        mode_value = int(_check_int(mode, "mode"))
        if not 0 <= mode_value <= 3:
            raise ValueError("mode must be an integer between 0 and 3")
        form["eventArg"] = str(mode_value)
    return form


def _timing_plan_data_valid(data: dict[str, object]) -> bool:
    if "subSerial" in data and not isinstance(data["subSerial"], str):
        return False
    if "subType" in data and type(data["subType"]) is not int:
        return False
    week_plans = data.get("weekPlans")
    if not isinstance(week_plans, list):
        return False
    for group in week_plans:
        if not isinstance(group, dict):
            return False
        if "weekDay" in group and not isinstance(group["weekDay"], str):
            return False
        if "timePlan" not in group:
            continue
        time_plan = group["timePlan"]
        if not isinstance(time_plan, list):
            return False
        for period in time_plan:
            if not isinstance(period, dict) or not all(
                isinstance(period[key], str)
                for key in ("beginTime", "endTime", "eventArg")
                if key in period
            ):
                return False
    return True


def _working_mode_plan_params(
    channel: int | None,
    timing_plan_type: int | None,
) -> dict[str, int]:
    params: dict[str, int] = {}
    if channel is not None:
        params["channelNo"] = _check_int(channel, "channelNo")
    if timing_plan_type is not None:
        if type(timing_plan_type) is not int or timing_plan_type not in (17, 19):
            raise ValueError("timingPlanType must be 17 or 19")
        params["timingPlanType"] = timing_plan_type
    return params


def _check_working_mode_plans(response: ApiResponse) -> ApiResponse:
    if isinstance(response.data, list) and all(
        isinstance(row, dict)
        and _timing_plan_data_valid(row)
        and ("enable" not in row or type(row["enable"]) is int)
        for row in response.data
    ):
        return response
    raise ProtocolError(
        "unexpected working mode plans response",
        operation=response.operation,
        http_status=response.http_status,
    )


def _huashu_4g_plan_form(
    enabled: bool,
    start_time: str,
    end_time: str,
    week: str,
    stop_after_execution: bool | None,
) -> dict[str, str]:
    if stop_after_execution is not None and type(stop_after_execution) is not bool:
        raise ValueError("stop_after_execution must be a bool")
    form = _timing_plan_form(enabled, start_time, end_time, week, mode=None)
    if stop_after_execution is not None:
        form["eventArg"] = "1" if stop_after_execution else "0"
    return form


def _timing_plan_response(response: ApiResponse) -> ApiResponse:
    """Return the response when the timing plan payload shape is valid."""
    data = response.data
    if not isinstance(data, dict) or not _timing_plan_data_valid(data):
        raise ProtocolError(
            "unexpected settings.get_timing_plan response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _set_alarm_sound_mode_params(mode: int) -> dict[str, str]:
    value = int.__int__(_check_int(mode, "mode"))
    if value not in (0, 1, 2):
        raise ValueError("mode must be 0, 1 or 2")
    return {"type": str(value)}


def _play_audition_params(voice_index: int, volume: int | None) -> dict[str, str]:
    voice = int.__int__(_check_int(voice_index, "voice_index"))
    if voice not in (200, 201, 202):
        raise ValueError("voice_index must be 200, 201 or 202")
    params = {"voiceIndex": str(voice)}
    if volume is not None:
        level = int.__int__(_check_int(volume, "volume"))
        if not 1 <= level <= 100:
            raise ValueError("volume must be between 1 and 100")
        params["volume"] = str(level)
    return params


def _alarm_sound_mode_response(response: ApiResponse) -> ApiResponse:
    """Return the response when the alarm sound mode payload shape is valid."""
    data = response.data
    if not isinstance(data, dict) or type(data.get("alarmSoundMode")) is not int:
        raise ProtocolError(
            "unexpected settings.get_alarm_sound_mode response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _chime_get_request(
    serial: object, channel: int | None, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {"deviceSerial": _check_text(serial, "device_serial")}
    _put_channel(data, channel)
    return _overseas_op("get_chime", "/api/lapp/device/chime/get", read_only=True), data


def _chime_set_request(
    serial: object,
    chime_type: object,
    duration: object,
    channel: int | None,
    scope: str,
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {
        "deviceSerial": _check_text(serial, "device_serial"),
        "type": _check_chime_type(chime_type),
        "duration": _check_int(duration, "duration"),
    }
    _put_channel(data, channel)
    return _overseas_op("set_chime", "/api/lapp/device/chime/set", read_only=False), data


def _infrared_get_request(
    serial: object, channel: int | None, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {"deviceSerial": _check_text(serial, "device_serial")}
    _put_channel(data, channel)
    return _overseas_op(
        "get_infrared", "/api/lapp/device/infrared/switch/get", read_only=True
    ), data


def _infrared_set_request(
    serial: object, enabled: object, channel: int | None, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {
        "deviceSerial": _check_text(serial, "device_serial"),
        "enable": 1 if _check_enabled(enabled) else 0,
    }
    _put_channel(data, channel)
    return _overseas_op(
        "set_infrared", "/api/lapp/device/infrared/switch/set", read_only=False
    ), data


def _timezone_list_request(language: object, scope: str) -> tuple[Operation, dict[str, str] | None]:
    _require_overseas(scope)
    data: dict[str, str] | None = None
    if language is not None:
        data = {"language": _check_timezone_language(language)}
    return (
        _overseas_op(
            "list_timezones",
            "/api/lapp/timezone/list",
            read_only=True,
            token_location="none",
        ),
        data,
    )


def _area_list_request(scope: str) -> Operation:
    _require_overseas(scope)
    return _overseas_get_op("list_areas", "/api/lapp/area/list", read_only=True)


def _timezone_set_request(
    serial: object,
    timezone: object,
    time_format: object,
    daylight_saving: object,
    scope: str,
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    data: dict[str, str | int] = {
        "deviceSerial": _check_text(serial, "device_serial"),
        "timezone": _check_text(timezone, "timezone"),
        "timeFormat": _check_time_format(time_format),
    }
    saving = _check_daylight_saving(daylight_saving)
    if saving is not None:
        data["daylightSaving"] = "1" if saving else "0"
    return _overseas_op("set_timezone", "/api/lapp/device/timezone/set", read_only=False), data


def _language_get_request(serial: object, scope: str) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    return (
        _overseas_op("get_language", "/api/lapp/device/language/get", read_only=True),
        {"deviceSerial": _check_text(serial, "device_serial")},
    )


def _language_set_request(
    serial: object, language: object, scope: str
) -> tuple[Operation, dict[str, str | int]]:
    _require_overseas(scope)
    return (
        _overseas_op("set_language", "/api/lapp/device/language/set", read_only=False),
        {
            "deviceSerial": _check_text(serial, "device_serial"),
            "language": _check_text(language, "language"),
        },
    )


def _check_volume(value: object) -> int:
    """Return ``value`` as an integer speaker percentage in the range 0-100.

    The HTTP doc marks ``volume`` only as Int; the 0-100 percentage range
    is confirmed by the separate device SDK doc. ``volume=0`` is a valid
    write.
    """
    volume = _check_int(value, "volume")
    if not 0 <= volume <= 100:
        raise ValueError("volume must be an integer between 0 and 100")
    return volume


def _talk_speaker_volume_get_params(serial: object, scope: str) -> dict[str, str]:
    """Query for the domestic GET: reject non-domestic first, serial verbatim."""
    _require_domestic(scope)
    return {"deviceSerial": _check_text(serial, "deviceSerial")}


def _talk_speaker_volume_set_request(
    serial: object, volume: object, scope: str
) -> tuple[dict[str, str | int], dict[str, str]]:
    """Form body and Content-Type header for the domestic POST.

    The access token is injected into the header by the client; only the
    documented form fields and Content-Type travel here.
    """
    _require_domestic(scope)
    data: dict[str, str | int] = {
        "deviceSerial": _check_text(serial, "deviceSerial"),
        "volume": _check_volume(volume),
    }
    return data, {"Content-Type": _FORM_CONTENT_TYPE}


def _require_talk_speaker_volume(response: ApiResponse) -> ApiResponse:
    """Return ``response`` when ``data.talkSpeakerVolume`` is a genuine int.

    A missing/non-dict ``data``, an absent key, or any non-int (including
    bool) is a protocol error; the int is preserved verbatim.
    """
    data = response.data
    if not isinstance(data, dict) or type(data.get("talkSpeakerVolume")) is not int:
        raise ProtocolError(
            "talk speaker volume response has no integer data.talkSpeakerVolume",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


class Settings:
    """Synchronous facade over device switch and overseas settings operations."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get_switch(self, device_serial: str, switch: DeviceSwitch | str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="settings.get_switch", mode="sync"):
            op, data = _get_data(switch, device_serial, self._client.region.scope)
            return self._client.request(op, data=data)

    def set_switch(
        self,
        device_serial: str,
        switch: DeviceSwitch | str,
        enabled: bool,
        *,
        channel: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="settings.set_switch", mode="sync"):
            op, data = _set_data(switch, device_serial, enabled, channel, self._client.region.scope)
            return self._client.request(op, data=data)

    def get_intelligent_detection_switch(
        self, device_serial: str, *, detection_type: str | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_intelligent_detection_switch", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            form = _intelligent_detection_form(device_serial, detection_type)
            response = self._client.request(
                _GET_INTELLIGENT_DETECTION_SWITCH, data=form, headers=_TRACKING_FORM_HEADERS
            )
            return _tracking_switch_response(
                response, message="invalid intelligent detection switch response"
            )

    def set_intelligent_detection_switch(
        self,
        device_serial: str,
        *,
        enabled: bool,
        detection_type: str | None = None,
        channel_no: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_intelligent_detection_switch", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            form = _intelligent_detection_form(device_serial, detection_type)
            form["enable"] = _enable_value(_check_enabled(enabled), as_string=True)
            if channel_no is not None:
                form["channelNo"] = _check_text(channel_no, "channel_no")
            return self._client.request(
                _SET_INTELLIGENT_DETECTION_SWITCH, data=form, headers=_TRACKING_FORM_HEADERS
            )

    def select_alarm_detection_mode(
        self, device_serial: str, *, detection_type: str
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.select_alarm_detection_mode", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            if type(detection_type) is not str or detection_type not in ("1", "5"):
                raise ValueError("detection_type must be '1' or '5'")
            return self._client.request(
                _SELECT_ALARM_DETECTION_MODE,
                headers={
                    "deviceSerial": _header("device_serial", device_serial),
                    "Content-Type": _FORM_CONTENT_TYPE,
                },
                data={"type": detection_type},
            )

    def get_alarm_detection_mode(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_alarm_detection_mode", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            response = self._client.request(
                _GET_ALARM_DETECTION_MODE,
                headers={"deviceSerial": _header("device_serial", device_serial)},
            )
            return _alarm_detection_mode_response(response)

    def set_motion_tracking(
        self, device_serial: str, *, enabled: bool, channel_no: str | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_motion_tracking", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            form: dict[str, str | int] = {
                "deviceSerial": _check_serial(
                    _check_text(device_serial, "device_serial"), domestic=True
                ),
                "enable": _enable_value(_check_enabled(enabled), as_string=True),
            }
            if channel_no is not None:
                form["channelNo"] = _check_text(channel_no, "channel_no")
            return self._client.request(
                _SET_MOTION_TRACKING, data=form, headers=_TRACKING_FORM_HEADERS
            )

    def get_motion_tracking(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_motion_tracking", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            form: dict[str, str] = {
                "deviceSerial": _check_serial(
                    _check_text(device_serial, "device_serial"), domestic=True
                )
            }
            response = self._client.request(
                _GET_MOTION_TRACKING, data=form, headers=_TRACKING_FORM_HEADERS
            )
            return _tracking_switch_response(response)

    def get_human_tracking(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="settings.get_human_tracking", mode="sync"):
            _require_domestic(self._client.region.scope)
            response = self._client.request(
                _GET_HUMAN_TRACKING,
                headers={
                    "Content-Type": _FORM_CONTENT_TYPE,
                    "deviceSerial": _header("device_serial", device_serial),
                },
            )
            return _tracking_switch_response(response)

    def set_human_tracking(self, device_serial: str, *, enabled: bool) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="settings.set_human_tracking", mode="sync"):
            _require_domestic(self._client.region.scope)
            return self._client.request(
                _SET_HUMAN_TRACKING,
                data={"enable": _enable_value(_check_enabled(enabled), as_string=False)},
                headers={
                    "Content-Type": _FORM_CONTENT_TYPE,
                    "deviceSerial": _header("device_serial", device_serial),
                },
            )

    def get_motion_sensitivity(self, device_serial: str) -> ApiResponse:
        """Return the motion detection sensitivity of a domestic device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_motion_sensitivity",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            response = self._client.request(
                _GET_MOTION_SENSITIVITY,
                data=_motion_sensitivity_form(device_serial),
                headers={"Content-Type": _FORM_CONTENT_TYPE},
            )
            return _motion_sensitivity_response(response)

    def set_motion_sensitivity(
        self,
        device_serial: str,
        *,
        value: int,
        channel_no: int | None = None,
    ) -> ApiResponse:
        """Set the motion detection sensitivity of a domestic device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_motion_sensitivity",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            return self._client.request(
                _SET_MOTION_SENSITIVITY,
                data=_motion_sensitivity_write_form(
                    device_serial,
                    value=value,
                    channel_no=channel_no,
                ),
                headers={"Content-Type": _FORM_CONTENT_TYPE},
            )

    def get_detection_sensitivity(
        self,
        device_serial: str,
        *,
        channel_no: str,
    ) -> ApiResponse:
        """Return the detection sensitivity of a domestic device channel."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_detection_sensitivity",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            response = self._client.request(
                _GET_DETECTION_SENSITIVITY,
                headers=_device_channel_headers(device_serial, channel_no),
            )
            return _detection_sensitivity_response(response)

    def set_detection_sensitivity(
        self,
        device_serial: str,
        *,
        channel_no: str,
        detection_type: str,
        value: str,
    ) -> ApiResponse:
        """Set detection sensitivity on a domestic device channel.

        Requires the device to report support_sensibility_adjust=1; the SDK
        does not probe capabilities or restore previous values.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_detection_sensitivity",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            return self._client.request(
                _SET_DETECTION_SENSITIVITY,
                data=_detection_sensitivity_form(detection_type, value),
                headers=_detection_sensitivity_write_headers(
                    device_serial,
                    channel_no,
                ),
            )

    def get_motion_area(self, device_serial: str, *, channel_no: str) -> ApiResponse:
        """Return the motion detection area of a domestic device.

        Overseas reads have no confirmed origin and fail before any request.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_motion_area",
            mode="sync",
        ):
            _check_motion_area_read_scope(self._client.region.scope)
            response = self._client.request(
                _GET_MOTION_AREA,
                headers=_device_channel_headers(device_serial, channel_no),
            )
            return _motion_area_response(response)

    def set_motion_area(
        self,
        device_serial: str,
        *,
        channel_no: str,
        area: str,
    ) -> ApiResponse:
        """Set the motion detection area for a domestic or overseas device.

        Domestic devices receive the area in a form body while overseas
        devices receive it in the query string.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_motion_area",
            mode="sync",
        ):
            headers = _device_channel_headers(device_serial, channel_no)
            value = _motion_area_value(area)
            if self._client.region.scope == _OVERSEAS:
                return self._client.request(
                    _SET_MOTION_AREA_OVERSEAS,
                    headers=headers,
                    params={"area": value},
                )
            return self._client.request(
                _SET_MOTION_AREA,
                headers={**headers, "Content-Type": _FORM_CONTENT_TYPE},
                data={"area": value},
            )

    def get_pir_area(self, device_serial: str, *, channel: int | None = None) -> ApiResponse:
        """Read the PIR detection area configuration (overseas region only).

        POSTs ``/api/lapp/device/pir/get`` with ``deviceSerial`` and an
        optional ``channelNo`` as form fields.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_pir_area", mode="sync"):
            op, data = _pir_area_get_request(device_serial, channel, self._client.region.scope)
            return self._client.request(op, data=data)

    def set_pir_area(
        self, device_serial: str, area: str, *, channel: int | None = None
    ) -> ApiResponse:
        """Write the PIR detection area configuration (overseas region only).

        POSTs ``/api/lapp/device/pir/set``. ``area`` is forwarded verbatim as
        the ``area`` form field: no reordering, bitmask conversion, or grid
        inference is applied.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_pir_area", mode="sync"):
            op, data = _pir_area_set_request(
                device_serial, area, channel, self._client.region.scope
            )
            return self._client.request(op, data=data)

    def get_light_linkage(self, device_serial: str) -> ApiResponse:
        """Return the motion-linked light status for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_light_linkage",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = self._client.request(
                _GET_LIGHT_LINKAGE,
                headers={"deviceSerial": serial},
                params={"type": "301"},
            )
            return _light_linkage_response(response)

    def set_light_linkage(self, device_serial: str, enabled: bool) -> ApiResponse:
        """Set the motion-linked light status for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_light_linkage",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            flag = _check_enabled(enabled)
            return self._client.request(
                _SET_LIGHT_LINKAGE,
                headers={"deviceSerial": serial},
                params={"type": "301", "enable": "1" if flag else "0"},
            )

    def get_working_mode_plans(
        self, device_serial: str, *, channel: int | None = None, timing_plan_type: int | None = None
    ) -> ApiResponse:
        """Return one domestic device's working mode plans."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_working_mode_plans",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = self._client.request(
                _GET_WORKING_MODE_PLANS,
                headers={"deviceSerial": serial},
                params=_working_mode_plan_params(channel, timing_plan_type),
            )
            return _check_working_mode_plans(response)

    def set_huashu_4g_working_mode_plan(
        self,
        device_serial: str,
        *,
        enabled: bool,
        start_time: str,
        end_time: str,
        week: str,
        stop_after_execution: bool | None = None,
    ) -> ApiResponse:
        """Set the working mode plan on a supported Huashu 4G camera."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_huashu_4g_working_mode_plan",
            mode="sync",
        ):
            _require_domestic(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            form = _huashu_4g_plan_form(enabled, start_time, end_time, week, stop_after_execution)
            return self._client.request(
                _SET_HUASHU_4G_WORKING_MODE_PLAN,
                headers={
                    "deviceSerial": serial,
                    "Content-Type": _FORM_CONTENT_TYPE,
                },
                data=form,
            )

    def get_timing_plan(self, device_serial: str) -> ApiResponse:
        """Return the timing plan for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_timing_plan",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = self._client.request(
                _GET_TIMING_PLAN,
                headers={"deviceSerial": serial},
            )
            return _timing_plan_response(response)

    def set_timing_plan(
        self,
        device_serial: str,
        *,
        enabled: bool,
        start_time: str,
        end_time: str,
        week: str,
        mode: int | None = None,
    ) -> ApiResponse:
        """Set the timing plan for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_timing_plan",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            form = _timing_plan_form(enabled, start_time, end_time, week, mode)
            return self._client.request(
                _SET_TIMING_PLAN,
                headers={
                    "deviceSerial": serial,
                    "Content-Type": _FORM_CONTENT_TYPE,
                },
                data=form,
            )

    def get_alarm_sound_mode(self, device_serial: str) -> ApiResponse:
        """Return the alarm sound mode for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_alarm_sound_mode",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = self._client.request(
                _GET_ALARM_SOUND_MODE,
                headers={"deviceSerial": serial},
            )
            return _alarm_sound_mode_response(response)

    def set_alarm_sound_mode(self, device_serial: str, mode: int) -> ApiResponse:
        """Set the alarm sound mode for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_alarm_sound_mode",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            params = _set_alarm_sound_mode_params(mode)
            return self._client.request(
                _SET_ALARM_SOUND_MODE,
                headers={"deviceSerial": serial},
                params=params,
            )

    def play_audition(
        self, device_serial: str, voice_index: int, *, volume: int | None = None
    ) -> ApiResponse:
        """Play an alarm sound audition on an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.play_audition",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            params = _play_audition_params(voice_index, volume)
            return self._client.request(
                _PLAY_AUDITION,
                headers={"deviceSerial": serial},
                params=params,
            )

    def get_chime(self, device_serial: str, *, channel: int | None = None) -> ApiResponse:
        """Read the doorbell chime configuration (overseas region only).

        POSTs ``/api/lapp/device/chime/get`` with ``deviceSerial`` and an
        optional ``channelNo``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_chime", mode="sync"):
            op, data = _chime_get_request(device_serial, channel, self._client.region.scope)
            return self._client.request(op, data=data)

    def set_chime(
        self,
        device_serial: str,
        chime_type: int,
        duration: int,
        *,
        channel: int | None = None,
    ) -> ApiResponse:
        """Set the doorbell chime (overseas region only).

        POSTs ``/api/lapp/device/chime/set``. ``chime_type`` must be the int
        1, 2, or 3 and is sent as ``type``; ``duration`` is sent as-is as an
        int because the source does not specify its unit or range (negative
        values are not rejected).
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_chime", mode="sync"):
            op, data = _chime_set_request(
                device_serial, chime_type, duration, channel, self._client.region.scope
            )
            return self._client.request(op, data=data)

    def get_infrared(self, device_serial: str, *, channel: int | None = None) -> ApiResponse:
        """Read the infrared (night vision) switch state (overseas only).

        POSTs ``/api/lapp/device/infrared/switch/get`` with ``deviceSerial``
        and an optional ``channelNo``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_infrared", mode="sync"):
            op, data = _infrared_get_request(device_serial, channel, self._client.region.scope)
            return self._client.request(op, data=data)

    def set_infrared(
        self, device_serial: str, enabled: bool, *, channel: int | None = None
    ) -> ApiResponse:
        """Set the infrared (night vision) switch (overseas region only).

        POSTs ``/api/lapp/device/infrared/switch/set``; ``enabled`` is sent
        as the integer ``enable`` field (1 or 0).
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_infrared", mode="sync"):
            op, data = _infrared_set_request(
                device_serial, enabled, channel, self._client.region.scope
            )
            return self._client.request(op, data=data)

    def list_timezones(self, *, language: str | None = None) -> ApiResponse:
        """List the public timezone directory (overseas region only).

        POSTs ``/api/lapp/timezone/list`` as an explicit public request: no
        token is attached regardless of client credentials. ``language`` may
        be "ENGLISH" or "SIMPCN"; ``None`` leaves the choice to the
        service default.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.list_timezones", mode="sync"):
            op, data = _timezone_list_request(language, self._client.region.scope)
            return self._client.request(op, data=data)

    def list_areas(self) -> ApiResponse:
        """List the public country/region directory (overseas region only).

        Sends exactly one GET to ``/api/lapp/area/list`` as an explicit
        public request: no query, no body, and no token regardless of
        client credentials. The source also declares a POST variant whose
        request shape is undocumented; the SDK deliberately selects GET
        only. ``.data`` is the raw directory array — documented item
        fields are ``id`` (int), ``name`` (string), ``region`` (string)
        and ``telephoneCode`` (int); extra fields and an empty array are
        preserved verbatim and no cluster, ``apiDomain`` or ``authDomain``
        is derived from them. The caller chooses the business origin via
        ``RegionConfig.overseas(api_base_url=...)``; the documented public
        origin is ``https://open.ezvizlife.com``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.list_areas", mode="sync"):
            return self._client.request(_area_list_request(self._client.region.scope))

    def set_timezone(
        self,
        device_serial: str,
        timezone: str,
        *,
        time_format: int,
        daylight_saving: bool | None = None,
    ) -> ApiResponse:
        """Set the device timezone (overseas region only).

        POSTs ``/api/lapp/device/timezone/set`` with ``timezone`` and
        ``timeFormat``. ``time_format`` is a required keyword-only int
        (0/1/2); requiring it is a local SDK design choice, not a rule
        marked in the source. ``daylight_saving`` is an optional bool sent
        as the string "1"/"0" via ``daylightSaving`` and omitted when None.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_timezone", mode="sync"):
            op, data = _timezone_set_request(
                device_serial, timezone, time_format, daylight_saving, self._client.region.scope
            )
            return self._client.request(op, data=data)

    def get_language(self, device_serial: str) -> ApiResponse:
        """Read the device language (overseas region only).

        POSTs ``/api/lapp/device/language/get`` with ``deviceSerial``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_language", mode="sync"):
            op, data = _language_get_request(device_serial, self._client.region.scope)
            return self._client.request(op, data=data)

    def set_language(self, device_serial: str, language: str) -> ApiResponse:
        """Set the device language (overseas region only).

        POSTs ``/api/lapp/device/language/set``. ``language`` is forwarded
        verbatim as a non-blank UTF-8 string and is not limited to the
        timezone directory's ENGLISH/SIMPCN set.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_language", mode="sync"):
            op, data = _language_set_request(device_serial, language, self._client.region.scope)
            return self._client.request(op, data=data)

    def get_talk_speaker_volume(self, device_serial: str) -> ApiResponse:
        """Read the device-reported talk speaker volume (domestic only).

        Sends one GET to ``/api/v3/device/talkSpeakerVolume`` with
        ``deviceSerial`` as the only query parameter and no body; the
        access token travels in the request header. On success
        ``data.talkSpeakerVolume`` must be a genuine int and is returned
        verbatim.
        """
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_talk_speaker_volume", mode="sync"
        ):
            params = _talk_speaker_volume_get_params(device_serial, self._client.region.scope)
            response = self._client.request(operation=_OP_GET_TALK_SPEAKER_VOLUME, params=params)
            return _require_talk_speaker_volume(response)

    def set_talk_speaker_volume(self, device_serial: str, volume: int) -> ApiResponse:
        """Write the talk speaker volume percentage (domestic only).

        POSTs ``/api/v3/device/talkSpeakerVolume`` with ``deviceSerial``
        and ``volume`` as a URL-encoded form body under an explicit
        ``Content-Type: application/x-www-form-urlencoded`` header; the
        access token travels in the request header. ``volume`` must be an
        int in the 0-100 percentage range (per the device SDK doc; the
        HTTP doc says only Int). The acknowledgement is returned verbatim
        and the write is never replayed.
        """
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_talk_speaker_volume", mode="sync"
        ):
            data, headers = _talk_speaker_volume_set_request(
                device_serial, volume, self._client.region.scope
            )
            return self._client.request(
                operation=_OP_SET_TALK_SPEAKER_VOLUME, data=data, headers=headers
            )


class AsyncSettings:
    """Asynchronous facade over device switch and overseas settings operations."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get_switch(self, device_serial: str, switch: DeviceSwitch | str) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="settings.get_switch", mode="async"):
            op, data = _get_data(switch, device_serial, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def set_switch(
        self,
        device_serial: str,
        switch: DeviceSwitch | str,
        enabled: bool,
        *,
        channel: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="settings.set_switch", mode="async"):
            op, data = _set_data(switch, device_serial, enabled, channel, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def get_intelligent_detection_switch(
        self, device_serial: str, *, detection_type: str | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_intelligent_detection_switch", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            form = _intelligent_detection_form(device_serial, detection_type)
            response = await self._client.request(
                _GET_INTELLIGENT_DETECTION_SWITCH, data=form, headers=_TRACKING_FORM_HEADERS
            )
            return _tracking_switch_response(
                response, message="invalid intelligent detection switch response"
            )

    async def set_intelligent_detection_switch(
        self,
        device_serial: str,
        *,
        enabled: bool,
        detection_type: str | None = None,
        channel_no: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_intelligent_detection_switch", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            form = _intelligent_detection_form(device_serial, detection_type)
            form["enable"] = _enable_value(_check_enabled(enabled), as_string=True)
            if channel_no is not None:
                form["channelNo"] = _check_text(channel_no, "channel_no")
            return await self._client.request(
                _SET_INTELLIGENT_DETECTION_SWITCH, data=form, headers=_TRACKING_FORM_HEADERS
            )

    async def select_alarm_detection_mode(
        self, device_serial: str, *, detection_type: str
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.select_alarm_detection_mode", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            if type(detection_type) is not str or detection_type not in ("1", "5"):
                raise ValueError("detection_type must be '1' or '5'")
            return await self._client.request(
                _SELECT_ALARM_DETECTION_MODE,
                headers={
                    "deviceSerial": _header("device_serial", device_serial),
                    "Content-Type": _FORM_CONTENT_TYPE,
                },
                data={"type": detection_type},
            )

    async def get_alarm_detection_mode(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_alarm_detection_mode", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            response = await self._client.request(
                _GET_ALARM_DETECTION_MODE,
                headers={"deviceSerial": _header("device_serial", device_serial)},
            )
            return _alarm_detection_mode_response(response)

    async def set_motion_tracking(
        self, device_serial: str, *, enabled: bool, channel_no: str | None = None
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_motion_tracking", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            form: dict[str, str | int] = {
                "deviceSerial": _check_serial(
                    _check_text(device_serial, "device_serial"), domestic=True
                ),
                "enable": _enable_value(_check_enabled(enabled), as_string=True),
            }
            if channel_no is not None:
                form["channelNo"] = _check_text(channel_no, "channel_no")
            return await self._client.request(
                _SET_MOTION_TRACKING, data=form, headers=_TRACKING_FORM_HEADERS
            )

    async def get_motion_tracking(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_motion_tracking", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            form: dict[str, str] = {
                "deviceSerial": _check_serial(
                    _check_text(device_serial, "device_serial"), domestic=True
                )
            }
            response = await self._client.request(
                _GET_MOTION_TRACKING, data=form, headers=_TRACKING_FORM_HEADERS
            )
            return _tracking_switch_response(response)

    async def get_human_tracking(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_human_tracking", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            response = await self._client.request(
                _GET_HUMAN_TRACKING,
                headers={
                    "Content-Type": _FORM_CONTENT_TYPE,
                    "deviceSerial": _header("device_serial", device_serial),
                },
            )
            return _tracking_switch_response(response)

    async def set_human_tracking(self, device_serial: str, *, enabled: bool) -> ApiResponse:
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_human_tracking", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            return await self._client.request(
                _SET_HUMAN_TRACKING,
                data={"enable": _enable_value(_check_enabled(enabled), as_string=False)},
                headers={
                    "Content-Type": _FORM_CONTENT_TYPE,
                    "deviceSerial": _header("device_serial", device_serial),
                },
            )

    async def get_motion_sensitivity(self, device_serial: str) -> ApiResponse:
        """Return the motion detection sensitivity of a domestic device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_motion_sensitivity",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            response = await self._client.request(
                _GET_MOTION_SENSITIVITY,
                data=_motion_sensitivity_form(device_serial),
                headers={"Content-Type": _FORM_CONTENT_TYPE},
            )
            return _motion_sensitivity_response(response)

    async def set_motion_sensitivity(
        self,
        device_serial: str,
        *,
        value: int,
        channel_no: int | None = None,
    ) -> ApiResponse:
        """Set the motion detection sensitivity of a domestic device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_motion_sensitivity",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            return await self._client.request(
                _SET_MOTION_SENSITIVITY,
                data=_motion_sensitivity_write_form(
                    device_serial,
                    value=value,
                    channel_no=channel_no,
                ),
                headers={"Content-Type": _FORM_CONTENT_TYPE},
            )

    async def get_detection_sensitivity(
        self,
        device_serial: str,
        *,
        channel_no: str,
    ) -> ApiResponse:
        """Return the detection sensitivity of a domestic device channel."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_detection_sensitivity",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            response = await self._client.request(
                _GET_DETECTION_SENSITIVITY,
                headers=_device_channel_headers(device_serial, channel_no),
            )
            return _detection_sensitivity_response(response)

    async def set_detection_sensitivity(
        self,
        device_serial: str,
        *,
        channel_no: str,
        detection_type: str,
        value: str,
    ) -> ApiResponse:
        """Set detection sensitivity on a domestic device channel.

        Requires the device to report support_sensibility_adjust=1; the SDK
        does not probe capabilities or restore previous values.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_detection_sensitivity",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            return await self._client.request(
                _SET_DETECTION_SENSITIVITY,
                data=_detection_sensitivity_form(detection_type, value),
                headers=_detection_sensitivity_write_headers(
                    device_serial,
                    channel_no,
                ),
            )

    async def get_motion_area(self, device_serial: str, *, channel_no: str) -> ApiResponse:
        """Return the motion detection area of a domestic device.

        Overseas reads have no confirmed origin and fail before any request.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_motion_area",
            mode="async",
        ):
            _check_motion_area_read_scope(self._client.region.scope)
            response = await self._client.request(
                _GET_MOTION_AREA,
                headers=_device_channel_headers(device_serial, channel_no),
            )
            return _motion_area_response(response)

    async def set_motion_area(
        self,
        device_serial: str,
        *,
        channel_no: str,
        area: str,
    ) -> ApiResponse:
        """Set the motion detection area for a domestic or overseas device.

        Domestic devices receive the area in a form body while overseas
        devices receive it in the query string.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_motion_area",
            mode="async",
        ):
            headers = _device_channel_headers(device_serial, channel_no)
            value = _motion_area_value(area)
            if self._client.region.scope == _OVERSEAS:
                return await self._client.request(
                    _SET_MOTION_AREA_OVERSEAS,
                    headers=headers,
                    params={"area": value},
                )
            return await self._client.request(
                _SET_MOTION_AREA,
                headers={**headers, "Content-Type": _FORM_CONTENT_TYPE},
                data={"area": value},
            )

    async def get_pir_area(self, device_serial: str, *, channel: int | None = None) -> ApiResponse:
        """Read the PIR detection area configuration (overseas region only).

        POSTs ``/api/lapp/device/pir/get`` with ``deviceSerial`` and an
        optional ``channelNo`` as form fields.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_pir_area", mode="async"):
            op, data = _pir_area_get_request(device_serial, channel, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def set_pir_area(
        self, device_serial: str, area: str, *, channel: int | None = None
    ) -> ApiResponse:
        """Write the PIR detection area configuration (overseas region only).

        POSTs ``/api/lapp/device/pir/set``. ``area`` is forwarded verbatim as
        the ``area`` form field: no reordering, bitmask conversion, or grid
        inference is applied.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_pir_area", mode="async"):
            op, data = _pir_area_set_request(
                device_serial, area, channel, self._client.region.scope
            )
            return await self._client.request(op, data=data)

    async def get_light_linkage(self, device_serial: str) -> ApiResponse:
        """Return the motion-linked light status for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_light_linkage",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = await self._client.request(
                _GET_LIGHT_LINKAGE,
                headers={"deviceSerial": serial},
                params={"type": "301"},
            )
            return _light_linkage_response(response)

    async def set_light_linkage(self, device_serial: str, enabled: bool) -> ApiResponse:
        """Set the motion-linked light status for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_light_linkage",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            flag = _check_enabled(enabled)
            return await self._client.request(
                _SET_LIGHT_LINKAGE,
                headers={"deviceSerial": serial},
                params={"type": "301", "enable": "1" if flag else "0"},
            )

    async def get_working_mode_plans(
        self, device_serial: str, *, channel: int | None = None, timing_plan_type: int | None = None
    ) -> ApiResponse:
        """Return one domestic device's working mode plans."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_working_mode_plans",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = await self._client.request(
                _GET_WORKING_MODE_PLANS,
                headers={"deviceSerial": serial},
                params=_working_mode_plan_params(channel, timing_plan_type),
            )
            return _check_working_mode_plans(response)

    async def set_huashu_4g_working_mode_plan(
        self,
        device_serial: str,
        *,
        enabled: bool,
        start_time: str,
        end_time: str,
        week: str,
        stop_after_execution: bool | None = None,
    ) -> ApiResponse:
        """Set the working mode plan on a supported Huashu 4G camera."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_huashu_4g_working_mode_plan",
            mode="async",
        ):
            _require_domestic(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            form = _huashu_4g_plan_form(enabled, start_time, end_time, week, stop_after_execution)
            return await self._client.request(
                _SET_HUASHU_4G_WORKING_MODE_PLAN,
                headers={
                    "deviceSerial": serial,
                    "Content-Type": _FORM_CONTENT_TYPE,
                },
                data=form,
            )

    async def get_timing_plan(self, device_serial: str) -> ApiResponse:
        """Return the timing plan for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_timing_plan",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = await self._client.request(
                _GET_TIMING_PLAN,
                headers={"deviceSerial": serial},
            )
            return _timing_plan_response(response)

    async def set_timing_plan(
        self,
        device_serial: str,
        *,
        enabled: bool,
        start_time: str,
        end_time: str,
        week: str,
        mode: int | None = None,
    ) -> ApiResponse:
        """Set the timing plan for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_timing_plan",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            form = _timing_plan_form(enabled, start_time, end_time, week, mode)
            return await self._client.request(
                _SET_TIMING_PLAN,
                headers={
                    "deviceSerial": serial,
                    "Content-Type": _FORM_CONTENT_TYPE,
                },
                data=form,
            )

    async def get_alarm_sound_mode(self, device_serial: str) -> ApiResponse:
        """Return the alarm sound mode for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.get_alarm_sound_mode",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            response = await self._client.request(
                _GET_ALARM_SOUND_MODE,
                headers={"deviceSerial": serial},
            )
            return _alarm_sound_mode_response(response)

    async def set_alarm_sound_mode(self, device_serial: str, mode: int) -> ApiResponse:
        """Set the alarm sound mode for an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.set_alarm_sound_mode",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            params = _set_alarm_sound_mode_params(mode)
            return await self._client.request(
                _SET_ALARM_SOUND_MODE,
                headers={"deviceSerial": serial},
                params=params,
            )

    async def play_audition(
        self, device_serial: str, voice_index: int, *, volume: int | None = None
    ) -> ApiResponse:
        """Play an alarm sound audition on an overseas device."""
        with _diagnostic_scope(
            self._client,
            entrypoint="settings.play_audition",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            serial = _header("device_serial", device_serial)
            params = _play_audition_params(voice_index, volume)
            return await self._client.request(
                _PLAY_AUDITION,
                headers={"deviceSerial": serial},
                params=params,
            )

    async def get_chime(self, device_serial: str, *, channel: int | None = None) -> ApiResponse:
        """Read the doorbell chime configuration (overseas region only).

        POSTs ``/api/lapp/device/chime/get`` with ``deviceSerial`` and an
        optional ``channelNo``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_chime", mode="async"):
            op, data = _chime_get_request(device_serial, channel, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def set_chime(
        self,
        device_serial: str,
        chime_type: int,
        duration: int,
        *,
        channel: int | None = None,
    ) -> ApiResponse:
        """Set the doorbell chime (overseas region only).

        POSTs ``/api/lapp/device/chime/set``. ``chime_type`` must be the int
        1, 2, or 3 and is sent as ``type``; ``duration`` is sent as-is as an
        int because the source does not specify its unit or range (negative
        values are not rejected).
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_chime", mode="async"):
            op, data = _chime_set_request(
                device_serial, chime_type, duration, channel, self._client.region.scope
            )
            return await self._client.request(op, data=data)

    async def get_infrared(self, device_serial: str, *, channel: int | None = None) -> ApiResponse:
        """Read the infrared (night vision) switch state (overseas only).

        POSTs ``/api/lapp/device/infrared/switch/get`` with ``deviceSerial``
        and an optional ``channelNo``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_infrared", mode="async"):
            op, data = _infrared_get_request(device_serial, channel, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def set_infrared(
        self, device_serial: str, enabled: bool, *, channel: int | None = None
    ) -> ApiResponse:
        """Set the infrared (night vision) switch (overseas region only).

        POSTs ``/api/lapp/device/infrared/switch/set``; ``enabled`` is sent
        as the integer ``enable`` field (1 or 0).
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_infrared", mode="async"):
            op, data = _infrared_set_request(
                device_serial, enabled, channel, self._client.region.scope
            )
            return await self._client.request(op, data=data)

    async def list_timezones(self, *, language: str | None = None) -> ApiResponse:
        """List the public timezone directory (overseas region only).

        POSTs ``/api/lapp/timezone/list`` as an explicit public request: no
        token is attached regardless of client credentials. ``language`` may
        be "ENGLISH" or "SIMPCN"; ``None`` leaves the choice to the
        service default.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.list_timezones", mode="async"):
            op, data = _timezone_list_request(language, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def list_areas(self) -> ApiResponse:
        """List the public country/region directory (overseas region only).

        Sends exactly one GET to ``/api/lapp/area/list`` as an explicit
        public request: no query, no body, and no token regardless of
        client credentials. The source also declares a POST variant whose
        request shape is undocumented; the SDK deliberately selects GET
        only. ``.data`` is the raw directory array — documented item
        fields are ``id`` (int), ``name`` (string), ``region`` (string)
        and ``telephoneCode`` (int); extra fields and an empty array are
        preserved verbatim and no cluster, ``apiDomain`` or ``authDomain``
        is derived from them. The caller chooses the business origin via
        ``RegionConfig.overseas(api_base_url=...)``; the documented public
        origin is ``https://open.ezvizlife.com``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.list_areas", mode="async"):
            return await self._client.request(_area_list_request(self._client.region.scope))

    async def set_timezone(
        self,
        device_serial: str,
        timezone: str,
        *,
        time_format: int,
        daylight_saving: bool | None = None,
    ) -> ApiResponse:
        """Set the device timezone (overseas region only).

        POSTs ``/api/lapp/device/timezone/set`` with ``timezone`` and
        ``timeFormat``. ``time_format`` is a required keyword-only int
        (0/1/2); requiring it is a local SDK design choice, not a rule
        marked in the source. ``daylight_saving`` is an optional bool sent
        as the string "1"/"0" via ``daylightSaving`` and omitted when None.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_timezone", mode="async"):
            op, data = _timezone_set_request(
                device_serial, timezone, time_format, daylight_saving, self._client.region.scope
            )
            return await self._client.request(op, data=data)

    async def get_language(self, device_serial: str) -> ApiResponse:
        """Read the device language (overseas region only).

        POSTs ``/api/lapp/device/language/get`` with ``deviceSerial``.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.get_language", mode="async"):
            op, data = _language_get_request(device_serial, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def set_language(self, device_serial: str, language: str) -> ApiResponse:
        """Set the device language (overseas region only).

        POSTs ``/api/lapp/device/language/set``. ``language`` is forwarded
        verbatim as a non-blank UTF-8 string and is not limited to the
        timezone directory's ENGLISH/SIMPCN set.
        """
        with _diagnostic_scope(self._client, entrypoint="settings.set_language", mode="async"):
            op, data = _language_set_request(device_serial, language, self._client.region.scope)
            return await self._client.request(op, data=data)

    async def get_talk_speaker_volume(self, device_serial: str) -> ApiResponse:
        """Read the device-reported talk speaker volume (domestic only).

        Async variant of :meth:`Settings.get_talk_speaker_volume`.
        """
        with _diagnostic_scope(
            self._client, entrypoint="settings.get_talk_speaker_volume", mode="async"
        ):
            params = _talk_speaker_volume_get_params(device_serial, self._client.region.scope)
            response = await self._client.request(
                operation=_OP_GET_TALK_SPEAKER_VOLUME, params=params
            )
            return _require_talk_speaker_volume(response)

    async def set_talk_speaker_volume(self, device_serial: str, volume: int) -> ApiResponse:
        """Write the talk speaker volume percentage (domestic only).

        Async variant of :meth:`Settings.set_talk_speaker_volume`.
        """
        with _diagnostic_scope(
            self._client, entrypoint="settings.set_talk_speaker_volume", mode="async"
        ):
            data, headers = _talk_speaker_volume_set_request(
                device_serial, volume, self._client.region.scope
            )
            return await self._client.request(
                operation=_OP_SET_TALK_SPEAKER_VOLUME, data=data, headers=headers
            )
