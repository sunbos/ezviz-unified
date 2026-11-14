"""Cloud Recording 2.0 explicit plan write operations for the unified client.

All operations are documented for the domestic region only (docs 2045, 2046,
2049, 2050 and 2062): one-off plan create/update/stop/delete plus instant plan
creation. The mixins below hold no state of their own and delegate to the
owning facade's ``_client``. Nothing here polls, retries, chains a stop with a
delete, handles device passwords, or touches media.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from .models import ApiResponse, ResponsePolicy
from .operations import Operation

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncCloudRecordingPlans", "CloudRecordingDevice", "CloudRecordingPlans"]

_DOMESTIC = "domestic"
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_JSON_CONTENT_TYPE = "application/json"
_MAX_ONE_OFF_DEVICES = 1000
_PLAN_NAME_RE = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}")

_POLICY_META = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    success_values=(200,),
)
_POLICY_TOP = ResponsePolicy(success_values=("200",))

_OP_CREATE_ONE_OFF = Operation(
    "cloud_recording.create_one_off_plan",
    "POST",
    "/api/service/cloudrecord/video/plan/oneOff",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_UPDATE_ONE_OFF = Operation(
    "cloud_recording.update_one_off_plan",
    "PUT",
    "/api/service/cloudrecord/video/plan/oneOff",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_STOP_ONE_OFF = Operation(
    "cloud_recording.stop_one_off_plan",
    "POST",
    "/api/service/cloudrecord/video/plan/oneOff/stop",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_DELETE_ONE_OFF = Operation(
    "cloud_recording.delete_one_off_plan",
    "DELETE",
    "/api/service/cloudrecord/video/plan/oneOff",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_CREATE_INSTANT = Operation(
    "cloud_recording.create_instant_plan",
    "POST",
    "/api/service/cloudrecord/video/plan/instant",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_TOP,
)


@dataclass(frozen=True)
class CloudRecordingDevice:
    """Device channel binding inside a recording plan request.

    ``device_serial`` and ``validate_code`` stay out of ``repr``. The SDK never
    infers whether a device is encrypted; ``validateCode`` is sent only when
    the caller supplies it, and only for operations that declare it: one-off
    plan create/update (docs 2045, 2050) do, while doc 2062 does not, so
    ``create_instant_plan`` rejects it. That reflects the reviewed contract
    surface, not a claim that the service can never support the field.
    """

    device_serial: str = field(repr=False)
    local_index: str
    validate_code: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.device_serial, str) or not self.device_serial.strip():
            raise ValueError("device_serial must be a non-blank string")
        if not isinstance(self.local_index, str) or not self.local_index.strip():
            raise ValueError("local_index must be a non-blank string")
        if self.validate_code is not None and (
            not isinstance(self.validate_code, str) or not self.validate_code.strip()
        ):
            raise ValueError("validate_code must be a non-blank string or None")


@dataclass(frozen=True)
class _Request:
    """Fully validated call state; every mapping is built fresh per call."""

    operation: Operation
    params: dict[str, str | int]
    data: dict[str, str | int] | None
    json: dict[str, object] | None
    headers: dict[str, str]


def _check_str(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _check_long(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int")
    return value


def _check_flag(name: str, value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool or None")
    return value


def _check_plan_name(value: object) -> str:
    name = _check_str("plan_name", value)
    if _PLAN_NAME_RE.fullmatch(name) is None:
        raise ValueError(
            "plan_name must be 1-32 characters of ASCII letters, digits, "
            "CJK ideographs, underscore or hyphen"
        )
    return name


def _check_stream_type(value: object) -> int:
    stream_type = _check_long("stream_type", value)
    if stream_type not in (1, 2):
        raise ValueError("stream_type must be 1 or 2")
    return stream_type


def _check_hours(value: object) -> int:
    hours = _check_long("hours", value)
    if hours < 1:
        raise ValueError("hours must be a positive int")
    return hours


def _check_naive(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError(f"{name} must be a naive datetime")
    if value.microsecond:
        raise ValueError(f"{name} must not carry microseconds")
    return value


def _compact_stamp(moment: datetime) -> str:
    return (
        f"{moment.year:04d}{moment.month:02d}{moment.day:02d}"
        f"{moment.hour:02d}{moment.minute:02d}{moment.second:02d}"
    )


def _check_devices(value: object, *, one_off: bool) -> tuple[CloudRecordingDevice, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("devices must be a sequence of CloudRecordingDevice")
    items = tuple(value)
    for item in items:
        if not isinstance(item, CloudRecordingDevice):
            raise ValueError("devices items must all be CloudRecordingDevice")
    if one_off and not 1 <= len(items) <= _MAX_ONE_OFF_DEVICES:
        raise ValueError(f"devices must contain between 1 and {_MAX_ONE_OFF_DEVICES} items")
    return items


def _wire_devices(devices: Sequence[CloudRecordingDevice]) -> list[dict[str, str]]:
    wired: list[dict[str, str]] = []
    for device in devices:
        info: dict[str, str] = {
            "deviceSerial": device.device_serial,
            "localIndex": device.local_index,
        }
        if device.validate_code is not None:
            info["validateCode"] = device.validate_code
        wired.append(info)
    return wired


def _form_flag(value: bool) -> str:
    return "true" if value else "false"


def _require_domestic(scope: str, method: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError(f"cloud_recording.{method} is only documented for the domestic region")


def _create_one_off_request(
    scope: str,
    *,
    plan_name: object,
    space_id: object,
    start_time: object,
    devices: object,
    end_time: object,
    template_id: object,
    stream_type: object,
    specified_end_time: object,
    auto_delete: object,
) -> _Request:
    _require_domestic(scope, "create_one_off_plan")
    end_flag = _check_flag("specified_end_time", specified_end_time)
    if end_flag is not False and end_time is None:
        raise ValueError("end_time is required unless specified_end_time is False")
    start = _check_naive("start_time", start_time)
    end = _check_naive("end_time", end_time) if end_time is not None else None
    if end is not None and end < start:
        raise ValueError("end_time must not be earlier than start_time")
    checked = _check_devices(devices, one_off=True)
    body: dict[str, object] = {
        "planName": _check_plan_name(plan_name),
        "spaceId": _check_long("space_id", space_id),
        "startTime": _compact_stamp(start),
        "devIndexInfos": _wire_devices(checked),
    }
    if end is not None:
        body["endTime"] = _compact_stamp(end)
    if template_id is not None:
        body["templateId"] = _check_long("template_id", template_id)
    if stream_type is not None:
        body["streamType"] = _check_stream_type(stream_type)
    if end_flag is not None:
        body["specifiedEndTime"] = end_flag
    delete_flag = _check_flag("auto_delete", auto_delete)
    if delete_flag is not None:
        body["autoDelete"] = delete_flag
    return _Request(_OP_CREATE_ONE_OFF, {}, None, body, {"Content-Type": _JSON_CONTENT_TYPE})


def _update_one_off_request(
    scope: str,
    plan_id: object,
    *,
    plan_name: object,
    space_id: object,
    start_time: object,
    end_time: object,
    devices: object,
    template_id: object,
    stream_type: object,
    specified_end_time: object,
) -> _Request:
    _require_domestic(scope, "update_one_off_plan")
    start = _check_naive("start_time", start_time)
    end = _check_naive("end_time", end_time)
    if end < start:
        raise ValueError("end_time must not be earlier than start_time")
    checked = _check_devices(devices, one_off=True)
    body: dict[str, object] = {
        "oneOffPlanId": _check_long("plan_id", plan_id),
        "planName": _check_plan_name(plan_name),
        "spaceId": _check_long("space_id", space_id),
        "startTime": _compact_stamp(start),
        "endTime": _compact_stamp(end),
        "devIndexInfos": _wire_devices(checked),
    }
    if template_id is not None:
        body["templateId"] = _check_long("template_id", template_id)
    if stream_type is not None:
        body["streamType"] = _check_stream_type(stream_type)
    end_flag = _check_flag("specified_end_time", specified_end_time)
    if end_flag is not None:
        body["specifiedEndTime"] = end_flag
    return _Request(_OP_UPDATE_ONE_OFF, {}, None, body, {"Content-Type": _JSON_CONTENT_TYPE})


def _stop_one_off_request(scope: str, plan_id: object, *, only_stop_preview: object) -> _Request:
    _require_domestic(scope, "stop_one_off_plan")
    data: dict[str, str | int] = {"oneOffPlanId": _check_long("plan_id", plan_id)}
    flag = _check_flag("only_stop_preview", only_stop_preview)
    if flag is not None:
        data["onlyStopPreview"] = _form_flag(flag)
    return _Request(_OP_STOP_ONE_OFF, {}, data, None, {"Content-Type": _FORM_CONTENT_TYPE})


def _delete_one_off_request(scope: str, plan_id: object) -> _Request:
    _require_domestic(scope, "delete_one_off_plan")
    data: dict[str, str | int] = {"oneOffPlanId": _check_long("plan_id", plan_id)}
    return _Request(_OP_DELETE_ONE_OFF, {}, data, None, {"Content-Type": _FORM_CONTENT_TYPE})


def _create_instant_request(
    scope: str,
    *,
    plan_name: object,
    space_id: object,
    hours: object,
    devices: object,
    template_id: object,
    stream_type: object,
) -> _Request:
    _require_domestic(scope, "create_instant_plan")
    checked = _check_devices(devices, one_off=False)
    if any(device.validate_code is not None for device in checked):
        raise ValueError(
            "validate_code is not declared for instant plans in doc 2062; "
            "omit it or use the one-off plan operations"
        )
    body: dict[str, object] = {
        "planName": _check_str("plan_name", plan_name),
        "spaceId": _check_long("space_id", space_id),
        "hours": _check_hours(hours),
        "devIndexInfos": _wire_devices(checked),
    }
    if template_id is not None:
        body["templateId"] = _check_long("template_id", template_id)
    if stream_type is not None:
        body["streamType"] = _check_stream_type(stream_type)
    return _Request(_OP_CREATE_INSTANT, {}, None, body, {"Content-Type": _JSON_CONTENT_TYPE})


class CloudRecordingPlans:
    """Synchronous one-off and instant plan write calls for ``CloudRecording``.

    Mixed into the facade; adds no instance state of its own.
    """

    __slots__ = ()
    _client: Client

    def _dispatch(self, request: _Request) -> ApiResponse:
        return self._client.request(
            request.operation,
            params=request.params,
            data=request.data,
            json=request.json,
            headers=request.headers,
        )

    def create_one_off_plan(
        self,
        *,
        plan_name: str,
        space_id: int,
        start_time: datetime,
        devices: Sequence[CloudRecordingDevice],
        end_time: datetime | None = None,
        template_id: int | None = None,
        stream_type: int | None = None,
        specified_end_time: bool | None = None,
        auto_delete: bool | None = None,
    ) -> ApiResponse:
        """Create a one-off recording plan (doc 2045)."""
        request = _create_one_off_request(
            self._client.region.scope,
            plan_name=plan_name,
            space_id=space_id,
            start_time=start_time,
            devices=devices,
            end_time=end_time,
            template_id=template_id,
            stream_type=stream_type,
            specified_end_time=specified_end_time,
            auto_delete=auto_delete,
        )
        return self._dispatch(request)

    def update_one_off_plan(
        self,
        plan_id: int,
        *,
        plan_name: str,
        space_id: int,
        start_time: datetime,
        end_time: datetime,
        devices: Sequence[CloudRecordingDevice],
        template_id: int | None = None,
        stream_type: int | None = None,
        specified_end_time: bool | None = None,
    ) -> ApiResponse:
        """Update the device list of a not-yet-started one-off plan (doc 2050)."""
        request = _update_one_off_request(
            self._client.region.scope,
            plan_id,
            plan_name=plan_name,
            space_id=space_id,
            start_time=start_time,
            end_time=end_time,
            devices=devices,
            template_id=template_id,
            stream_type=stream_type,
            specified_end_time=specified_end_time,
        )
        return self._dispatch(request)

    def stop_one_off_plan(
        self, plan_id: int, *, only_stop_preview: bool | None = None
    ) -> ApiResponse:
        """Stop a pending or running one-off plan (doc 2046)."""
        request = _stop_one_off_request(
            self._client.region.scope,
            plan_id,
            only_stop_preview=only_stop_preview,
        )
        return self._dispatch(request)

    def delete_one_off_plan(self, plan_id: int) -> ApiResponse:
        """Delete an already stopped or finished one-off plan (doc 2049)."""
        return self._dispatch(_delete_one_off_request(self._client.region.scope, plan_id))

    def create_instant_plan(
        self,
        *,
        plan_name: str,
        space_id: int,
        hours: int,
        devices: Sequence[CloudRecordingDevice],
        template_id: int | None = None,
        stream_type: int | None = None,
    ) -> ApiResponse:
        """Start an instant recording plan for the given hours (doc 2062).

        Doc 2062 does not declare ``validateCode``, so any device carrying
        ``validate_code`` is rejected before authentication. This reflects
        the reviewed contract surface only, not a claim that the service can
        never support it.
        """
        request = _create_instant_request(
            self._client.region.scope,
            plan_name=plan_name,
            space_id=space_id,
            hours=hours,
            devices=devices,
            template_id=template_id,
            stream_type=stream_type,
        )
        return self._dispatch(request)


class AsyncCloudRecordingPlans:
    """Asynchronous one-off and instant plan write calls for ``AsyncCloudRecording``.

    Mixed into the facade; adds no instance state of its own.
    """

    __slots__ = ()
    _client: AsyncClient

    async def _dispatch(self, request: _Request) -> ApiResponse:
        return await self._client.request(
            request.operation,
            params=request.params,
            data=request.data,
            json=request.json,
            headers=request.headers,
        )

    async def create_one_off_plan(
        self,
        *,
        plan_name: str,
        space_id: int,
        start_time: datetime,
        devices: Sequence[CloudRecordingDevice],
        end_time: datetime | None = None,
        template_id: int | None = None,
        stream_type: int | None = None,
        specified_end_time: bool | None = None,
        auto_delete: bool | None = None,
    ) -> ApiResponse:
        """Create a one-off recording plan (doc 2045)."""
        request = _create_one_off_request(
            self._client.region.scope,
            plan_name=plan_name,
            space_id=space_id,
            start_time=start_time,
            devices=devices,
            end_time=end_time,
            template_id=template_id,
            stream_type=stream_type,
            specified_end_time=specified_end_time,
            auto_delete=auto_delete,
        )
        return await self._dispatch(request)

    async def update_one_off_plan(
        self,
        plan_id: int,
        *,
        plan_name: str,
        space_id: int,
        start_time: datetime,
        end_time: datetime,
        devices: Sequence[CloudRecordingDevice],
        template_id: int | None = None,
        stream_type: int | None = None,
        specified_end_time: bool | None = None,
    ) -> ApiResponse:
        """Update the device list of a not-yet-started one-off plan (doc 2050)."""
        request = _update_one_off_request(
            self._client.region.scope,
            plan_id,
            plan_name=plan_name,
            space_id=space_id,
            start_time=start_time,
            end_time=end_time,
            devices=devices,
            template_id=template_id,
            stream_type=stream_type,
            specified_end_time=specified_end_time,
        )
        return await self._dispatch(request)

    async def stop_one_off_plan(
        self, plan_id: int, *, only_stop_preview: bool | None = None
    ) -> ApiResponse:
        """Stop a pending or running one-off plan (doc 2046)."""
        request = _stop_one_off_request(
            self._client.region.scope,
            plan_id,
            only_stop_preview=only_stop_preview,
        )
        return await self._dispatch(request)

    async def delete_one_off_plan(self, plan_id: int) -> ApiResponse:
        """Delete an already stopped or finished one-off plan (doc 2049)."""
        return await self._dispatch(_delete_one_off_request(self._client.region.scope, plan_id))

    async def create_instant_plan(
        self,
        *,
        plan_name: str,
        space_id: int,
        hours: int,
        devices: Sequence[CloudRecordingDevice],
        template_id: int | None = None,
        stream_type: int | None = None,
    ) -> ApiResponse:
        """Start an instant recording plan for the given hours (doc 2062).

        Doc 2062 does not declare ``validateCode``, so any device carrying
        ``validate_code`` is rejected before authentication. This reflects
        the reviewed contract surface only, not a claim that the service can
        never support it.
        """
        request = _create_instant_request(
            self._client.region.scope,
            plan_name=plan_name,
            space_id=space_id,
            hours=hours,
            devices=devices,
            template_id=template_id,
            stream_type=stream_type,
        )
        return await self._dispatch(request)
