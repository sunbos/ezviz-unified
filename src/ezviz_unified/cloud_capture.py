"""Domestic cloud capture task endpoints (open.ys7.com docs 1388/1389/1370).

``create_interval_task`` (doc 1388) and ``create_timing_task`` (doc 1389)
submit frame capture tasks over stored media; ``task`` (doc 1370) reads task
state. All three endpoints are domestic-only and answer the ``meta.code``
envelope with an integer ``200`` success marker.

Disclosed SDK choices:

* datetimes must be naive (Python ``utcoffset`` semantics) whole seconds and
  are rendered ``yyyyMMddHHmmss`` with a four-digit year; no timezone or
  current-clock inference is performed;
* ``create_interval_task`` rejects ``end_time`` earlier than ``start_time``
  (equality is sent unchanged), validates ``project_id`` as ASCII
  ``[A-Za-z0-9_]{1,32}`` per the formal table, and requires
  ``result_space_id >= 1``; ``frame_interval`` is passed without unit
  conversion;
* ``create_timing_task`` applies only the documented non-blank/integer
  checks to ``project_id``/``result_space_id`` (the formal table lists no
  charset, length or sign bounds), snapshots and validates every
  ``timing_points`` item before any await, preserves ordering and
  duplicates, and accepts the documented ``frame_model=2`` despite
  contradictory statements on pages 1388/5324 -- no fallback to modes 0/1
  is made;
* ``dev_proto`` accepts ``jt808`` only on the interval endpoint; the timing
  endpoint documents just ``""`` and ``gb28181``;
* ``task_id`` reuses the ASCII ``[A-Za-z0-9_]{1,32}`` rule from the formal
  table, which may reject service-supported identifiers;
* selectors ``project_id``/``result_space_id`` require at least one
  non-None value; both may be sent together unchanged, preserving the
  documented ``resultSpaceId`` priority server-side.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from .cloud_recording import _check_naive, _compact_stamp
from .cloud_recording_v1 import _project_id_strict, _utf8
from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import _integer, _sequence_items, _text

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncCloudCapture", "CloudCapture"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_CAPTURE_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_CREATE_INTERVAL_TASK = Operation(
    "cloud_capture.create_interval_task",
    "POST",
    "/api/v3/open/cloud/video/frame/interval/start",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=False,
    policy=_CAPTURE_POLICY,
)
_CREATE_TIMING_TASK = Operation(
    "cloud_capture.create_timing_task",
    "POST",
    "/api/v3/open/cloud/video/frame/timing/start",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=False,
    policy=_CAPTURE_POLICY,
)
_TASK = Operation(
    "cloud_capture.task",
    "GET",
    "/api/v3/open/cloud/task/{taskId}",
    token_location="query",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_CAPTURE_POLICY,
)

_REC_TYPES = ("local", "cloud", "live")
_FRAME_MODELS = (0, 1, 2)
_STREAM_TYPES = ("1", "2")
_INTERVAL_DEV_PROTOS = ("", "gb28181", "jt808")
_TIMING_DEV_PROTOS = ("", "gb28181")


def _require_region(scope: str) -> None:
    if scope != "domestic":
        raise ValueError("cloud_capture is only supported in the domestic region")


def _device_serial(value: object) -> str:
    serial = _text("deviceSerial", value)
    _utf8("deviceSerial", serial)
    return serial


def _channel(value: object) -> int:
    # The doc requires an int channel but lists no sign bound or default, so
    # the SDK applies neither and sends the value unchanged.
    return _integer("channel", value)


def _rec_type(value: object) -> str:
    if not isinstance(value, str) or value not in _REC_TYPES:
        raise ValueError('recType must be one of "local", "cloud" or "live"')
    return value


def _frame_interval(value: object) -> int:
    interval = _integer("frameInterval", value)
    if interval < 1:
        raise ValueError("frameInterval must be >= 1")
    return interval


def _require_selector(project_id: object, result_space_id: object) -> None:
    if project_id is None and result_space_id is None:
        raise ValueError("at least one of projectId or resultSpaceId must be provided")


def _project_id_loose(value: object) -> str:
    # Doc 1389 lists projectId as a plain string: no 32/ASCII bound is applied.
    text = _text("projectId", value)
    _utf8("projectId", text)
    return text


def _result_space_id_bounded(value: object) -> int:
    space = _integer("resultSpaceId", value)
    if space < 1:
        raise ValueError("resultSpaceId must be >= 1")
    return space


def _frame_model(value: object) -> int:
    model = _integer("frameModel", value)
    if model not in _FRAME_MODELS:
        raise ValueError("frameModel must be 0, 1 or 2")
    return model


def _stream_type(value: object) -> str:
    if not isinstance(value, str) or value not in _STREAM_TYPES:
        raise ValueError('streamType must be "1" or "2"')
    return value


def _dev_proto(value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("devProto is not a documented value for this endpoint")
    return value


def _validate_code(value: object) -> str:
    code = _text("validateCode", value)
    _utf8("validateCode", code)
    return code


def _task_id(value: object) -> str:
    # Doc 1370's formal word class is read as ASCII [A-Za-z0-9_]{1,32}; the
    # conflicting "32 alphanumeric" prose is not used to tighten to exactly 32.
    return _project_id_strict("taskId", value)


def _capture_stamp(name: str, value: object) -> str:
    return _compact_stamp(_check_naive(name, value))


def _timing_stamps(values: object) -> str:
    items = _sequence_items("timingPoints", values)
    if not items:
        raise ValueError("timingPoints must not be empty")
    stamps = [_capture_stamp("timingPoints", item) for item in items]
    return ",".join(stamps)


def _interval_form(
    device_serial: object,
    channel: object,
    rec_type: object,
    start_time: object,
    end_time: object,
    frame_interval: object,
    project_id: object,
    result_space_id: object,
    frame_model: object,
    dev_proto: object,
    stream_type: object,
    validate_code: object,
) -> dict[str, object]:
    serial = _device_serial(device_serial)
    channel_no = _channel(channel)
    rec = _rec_type(rec_type)
    start = _check_naive("startTime", start_time)
    end = _check_naive("endTime", end_time)
    if end < start:
        raise ValueError("endTime must not be earlier than startTime")
    interval = _frame_interval(frame_interval)
    _require_selector(project_id, result_space_id)
    data: dict[str, object] = {
        "deviceSerial": serial,
        "channelNo": channel_no,
        "recType": rec,
        "startTime": _compact_stamp(start),
        "endTime": _compact_stamp(end),
        "frameInterval": interval,
    }
    if project_id is not None:
        data["projectId"] = _project_id_strict("projectId", project_id)
    if result_space_id is not None:
        data["resultSpaceId"] = _result_space_id_bounded(result_space_id)
    if frame_model is not None:
        data["frameModel"] = _frame_model(frame_model)
    if dev_proto is not None:
        data["devProto"] = _dev_proto(dev_proto, _INTERVAL_DEV_PROTOS)
    if stream_type is not None:
        data["streamType"] = _stream_type(stream_type)
    if validate_code is not None:
        data["validateCode"] = _validate_code(validate_code)
    return data


def _timing_form(
    device_serial: object,
    channel: object,
    rec_type: object,
    timing_points: object,
    project_id: object,
    result_space_id: object,
    frame_model: object,
    dev_proto: object,
    stream_type: object,
    validate_code: object,
) -> dict[str, object]:
    serial = _device_serial(device_serial)
    channel_no = _channel(channel)
    rec = _rec_type(rec_type)
    stamps = _timing_stamps(timing_points)
    _require_selector(project_id, result_space_id)
    data: dict[str, object] = {
        "deviceSerial": serial,
        "channelNo": channel_no,
        "recType": rec,
        "timingPoints": stamps,
    }
    if project_id is not None:
        data["projectId"] = _project_id_loose(project_id)
    if result_space_id is not None:
        data["resultSpaceId"] = _integer("resultSpaceId", result_space_id)
    if frame_model is not None:
        data["frameModel"] = _frame_model(frame_model)
    if dev_proto is not None:
        data["devProto"] = _dev_proto(dev_proto, _TIMING_DEV_PROTOS)
    if stream_type is not None:
        data["streamType"] = _stream_type(stream_type)
    if validate_code is not None:
        data["validateCode"] = _validate_code(validate_code)
    return data


class CloudCapture:
    """Synchronous facade over the domestic cloud capture endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def create_interval_task(
        self,
        device_serial: str,
        *,
        channel: int,
        rec_type: Literal["local", "cloud", "live"],
        start_time: datetime,
        end_time: datetime,
        frame_interval: int,
        project_id: str | None = None,
        result_space_id: int | None = None,
        frame_model: Literal[0, 1, 2] | None = None,
        dev_proto: Literal["", "gb28181", "jt808"] | None = None,
        stream_type: Literal["1", "2"] | None = None,
        validate_code: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="cloud_capture.create_interval_task",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data = _interval_form(
                device_serial,
                channel,
                rec_type,
                start_time,
                end_time,
                frame_interval,
                project_id,
                result_space_id,
                frame_model,
                dev_proto,
                stream_type,
                validate_code,
            )
            return self._client.request(
                _CREATE_INTERVAL_TASK,
                data=data,
                headers=_FORM_HEADERS,
            )

    def create_timing_task(
        self,
        device_serial: str,
        *,
        channel: int,
        rec_type: Literal["local", "cloud", "live"],
        timing_points: Sequence[datetime],
        project_id: str | None = None,
        result_space_id: int | None = None,
        frame_model: Literal[0, 1, 2] | None = None,
        dev_proto: Literal["", "gb28181"] | None = None,
        stream_type: Literal["1", "2"] | None = None,
        validate_code: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="cloud_capture.create_timing_task",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data = _timing_form(
                device_serial,
                channel,
                rec_type,
                timing_points,
                project_id,
                result_space_id,
                frame_model,
                dev_proto,
                stream_type,
                validate_code,
            )
            return self._client.request(
                _CREATE_TIMING_TASK,
                data=data,
                headers=_FORM_HEADERS,
            )

    def task(self, task_id: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="cloud_capture.task",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            return self._client.request(
                _TASK,
                path_params={"taskId": _task_id(task_id)},
            )


class AsyncCloudCapture:
    """Asynchronous facade over the domestic cloud capture endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_interval_task(
        self,
        device_serial: str,
        *,
        channel: int,
        rec_type: Literal["local", "cloud", "live"],
        start_time: datetime,
        end_time: datetime,
        frame_interval: int,
        project_id: str | None = None,
        result_space_id: int | None = None,
        frame_model: Literal[0, 1, 2] | None = None,
        dev_proto: Literal["", "gb28181", "jt808"] | None = None,
        stream_type: Literal["1", "2"] | None = None,
        validate_code: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="cloud_capture.create_interval_task",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data = _interval_form(
                device_serial,
                channel,
                rec_type,
                start_time,
                end_time,
                frame_interval,
                project_id,
                result_space_id,
                frame_model,
                dev_proto,
                stream_type,
                validate_code,
            )
            return await self._client.request(
                _CREATE_INTERVAL_TASK,
                data=data,
                headers=_FORM_HEADERS,
            )

    async def create_timing_task(
        self,
        device_serial: str,
        *,
        channel: int,
        rec_type: Literal["local", "cloud", "live"],
        timing_points: Sequence[datetime],
        project_id: str | None = None,
        result_space_id: int | None = None,
        frame_model: Literal[0, 1, 2] | None = None,
        dev_proto: Literal["", "gb28181"] | None = None,
        stream_type: Literal["1", "2"] | None = None,
        validate_code: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="cloud_capture.create_timing_task",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data = _timing_form(
                device_serial,
                channel,
                rec_type,
                timing_points,
                project_id,
                result_space_id,
                frame_model,
                dev_proto,
                stream_type,
                validate_code,
            )
            return await self._client.request(
                _CREATE_TIMING_TASK,
                data=data,
                headers=_FORM_HEADERS,
            )

    async def task(self, task_id: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="cloud_capture.task",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            return await self._client.request(
                _TASK,
                path_params={"taskId": _task_id(task_id)},
            )
