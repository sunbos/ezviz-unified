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

from dataclasses import dataclass
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

_GET_ALARM_SOUND_MODE = Operation(
    name="settings.get_alarm_sound_mode",
    method="GET",
    path="/api/v3/device/alarmSound/enabled/get",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=True,
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

_GET_TIMING_PLAN = Operation(
    name="settings.get_timing_plan",
    method="GET",
    path="/api/v3/device/timing/plan/get",
    regions=_OVERSEAS_REGIONS,
    token_location="header",
    read_only=True,
    policy=_BODY_META_POLICY,
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
