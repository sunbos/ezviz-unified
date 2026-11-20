"""Device recording metadata query facade for the unified EZVIZ client (sync and async)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_serial as _settings_serial
from .settings import _check_text, _require_domestic
from .traffic import _millis

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"
_OVERSEAS = "overseas"

_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    success_values=(200,),
)

_STORED_POLICY = ResponsePolicy(
    success_values=("200",), redact_device_add_details=True, redact_form_serials=True
)
_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_BY_TIME = Operation(
    "recordings.by_time",
    "POST",
    "/api/lapp/video/by/time",
    token_location="form",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_STORED_POLICY,
)

_BY_TEXT_TIME = Operation(
    "recordings.by_text_time",
    "POST",
    "/api/lapp/video/by/time",
    token_location="form",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_STORED_POLICY,
)

_OP_LOCAL_DOMESTIC = Operation(
    "recordings.local",
    "GET",
    "/api/v3/device/local/video/unify/query",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
_OP_LOCAL_OVERSEAS = Operation(
    "recordings.local",
    "GET",
    "/api/v3/das/device/local/video/query",
    token_location="header",
    regions=frozenset({_OVERSEAS}),
    read_only=True,
    policy=_POLICY,
)


@dataclass(frozen=True)
class _LocalQuery:
    operation: Operation
    headers: dict[str, str]
    params: dict[str, str | int]


def _check_serial(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("device_serial must be a non-blank string")
    return value


def _check_channel(value: object) -> str:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("channel must be a non-blank string")
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("channel must be a non-blank string or an int")
    return str(value)


def _check_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int")
    return value


def _check_record_type(value: object, *, domestic: bool) -> int:
    record_type = _check_int("record_type", value)
    upper = 5 if domestic else 4
    if not 1 <= record_type <= upper:
        raise ValueError(f"record_type must be between 1 and {upper}")
    return record_type


def _check_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    return value


def _domestic_timestamp(name: str, value: object) -> str:
    moment = _check_datetime(name, value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    if moment.microsecond:
        raise ValueError(f"{name} must not carry microseconds")
    stamp: float | None = None
    try:
        stamp = moment.timestamp()
    except (OverflowError, OSError, ValueError):
        pass
    if stamp is None or not stamp.is_integer():
        raise ValueError(f"{name} cannot be represented as whole Unix seconds")
    return str(int(stamp))


def _overseas_timestamp(name: str, value: object) -> str:
    moment = _check_datetime(name, value)
    if moment.tzinfo is not None and moment.utcoffset() is not None:
        raise ValueError(f"{name} must be a naive datetime")
    if moment.microsecond:
        raise ValueError(f"{name} must not carry microseconds")
    return moment.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _reject_overseas(name: str, value: object) -> None:
    if value is not None:
        raise ValueError(f"{name} is only supported for the domestic region")


def _build_local_query(
    region_scope: str,
    device_serial: object,
    *,
    start_time: object,
    end_time: object,
    channel: object,
    record_type: object,
    page_size: object,
    query_by_nvr: object,
    location: object,
) -> _LocalQuery:
    serial = _check_serial(device_serial)
    domestic = region_scope == _DOMESTIC
    headers = {"deviceSerial": serial}
    params: dict[str, str | int] = {}

    if domestic:
        if start_time is None or end_time is None:
            raise ValueError("start_time and end_time are both required for the domestic region")
        start = _check_datetime("start_time", start_time)
        end = _check_datetime("end_time", end_time)
        if start.tzinfo != end.tzinfo:
            raise ValueError("start_time and end_time must share the same tzinfo")
        start_stamp = _domestic_timestamp("start_time", start)
        end_stamp = _domestic_timestamp("end_time", end)
        params["startTime"] = start_stamp
        params["endTime"] = end_stamp
        if int(start_stamp) > int(end_stamp):
            raise ValueError("start_time must not be later than end_time")
        if start.date() != end.date():
            raise ValueError("start_time and end_time must fall on the same day")
        if channel is not None:
            headers["localIndex"] = _check_channel(channel)
        if record_type is not None:
            params["recordType"] = _check_record_type(record_type, domestic=True)
        if page_size is not None:
            size = _check_int("page_size", page_size)
            if not 1 <= size <= 500:
                raise ValueError("page_size must be between 1 and 500")
            params["pageSize"] = size
        if query_by_nvr is not None:
            if not isinstance(query_by_nvr, bool):
                raise ValueError("query_by_nvr must be a bool")
            params["isQueryByNvr"] = 1 if query_by_nvr else 0
        if location is not None:
            place = _check_int("location", location)
            if place not in (1, 2):
                raise ValueError("location must be 1 or 2")
            params["location"] = place
        return _LocalQuery(_OP_LOCAL_DOMESTIC, headers, params)

    _reject_overseas("page_size", page_size)
    _reject_overseas("query_by_nvr", query_by_nvr)
    _reject_overseas("location", location)
    if start_time is not None:
        params["startTime"] = _overseas_timestamp("start_time", start_time)
    if end_time is not None:
        params["endTime"] = _overseas_timestamp("end_time", end_time)
    if start_time is not None and end_time is not None:
        start = _check_datetime("start_time", start_time)
        end = _check_datetime("end_time", end_time)
        if start > end:
            raise ValueError("start_time must not be later than end_time")
    if channel is not None:
        headers["channelNo"] = _check_channel(channel)
    if record_type is not None:
        params["recordType"] = _check_record_type(record_type, domestic=False)
    return _LocalQuery(_OP_LOCAL_OVERSEAS, headers, params)


def _stored_serial(value: object) -> str:
    return _settings_serial(_check_text(value, "device_serial"), domestic=True)


def _stored_text_time(value: object, field: str) -> str:
    text = _check_text(value, field)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", text) is None:
        raise ValueError(f"{field} must match YYYY-MM-DD HH:MM:SS")
    try:
        datetime.fromisoformat(text)
    except ValueError:
        pass
    else:
        return text
    raise ValueError("invalid calendar timestamp")


def _rec_type(value: object) -> int:
    rec_type = _check_int("rec_type", value)
    if rec_type not in (0, 1, 2):
        raise ValueError("rec_type must be 0, 1, or 2")
    return rec_type


def _by_time_form(
    device_serial: str,
    *,
    channel: int | None,
    start_time_ms: int | None,
    end_time_ms: int | None,
    rec_type: int | None,
    version: str | None,
    page_size: int | None,
) -> dict[str, str | int]:
    form: dict[str, str | int] = {"deviceSerial": _stored_serial(device_serial)}
    if channel is not None:
        form["channelNo"] = _check_int("channel", channel)
    start = None if start_time_ms is None else _millis("start_time_ms", start_time_ms)
    end = None if end_time_ms is None else _millis("end_time_ms", end_time_ms)
    if start is not None and end is not None and start > end:
        raise ValueError("start_time_ms exceeds end_time_ms")
    if start is not None:
        form["startTime"] = start
    if end is not None:
        form["endTime"] = end
    rec = None if rec_type is None else _rec_type(rec_type)
    if rec is not None:
        form["recType"] = rec
    if version is not None:
        if not isinstance(version, str) or version != "2.0":
            raise ValueError("version must be '2.0'")
        form["version"] = version
    if page_size is not None:
        page = _check_int("page_size", page_size)
        if rec == 1:
            if not 1 <= page <= 1000:
                raise ValueError("page_size must be between 1 and 1000")
        elif rec == 2:
            if not 1 <= page <= 500:
                raise ValueError("page_size must be between 1 and 500")
        else:
            raise ValueError("page_size requires rec_type 1 or 2")
        form["pageSize"] = page
    return form


def _by_text_time_form(
    *,
    device_serial: str | None,
    channel: str | None,
    start_time: str | None,
    stop_time: str | None,
    rec_type: int | None,
) -> dict[str, str | int]:
    form: dict[str, str | int] = {}
    if device_serial is not None:
        form["deviceSerial"] = _stored_serial(device_serial)
    if channel is not None:
        form["channelNo"] = _check_text(channel, "channel")
    start = None if start_time is None else _stored_text_time(start_time, "start_time")
    stop = None if stop_time is None else _stored_text_time(stop_time, "stop_time")
    if start is not None and stop is not None and start > stop:
        raise ValueError("start_time exceeds stop_time")
    if start is not None:
        form["startTime"] = start
    if stop is not None:
        form["stopTime"] = stop
    if rec_type is not None:
        form["recType"] = _rec_type(rec_type)
    return form


def _require_recording_data(response: ApiResponse, *, allow_file_page: bool) -> ApiResponse:
    data = response.data
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return response
    if allow_file_page and isinstance(data, dict):
        files = data.get("files")
        is_all = data.get("isAll")
        next_file_time = data.get("nextFileTime")
        if (
            isinstance(files, list)
            and all(isinstance(item, dict) for item in files)
            and type(is_all) is bool
            and type(next_file_time) is int
            and -(1 << 63) <= next_file_time <= (1 << 63) - 1
            and (not is_all or next_file_time == 0)
        ):
            return response
    raise ProtocolError(
        "unexpected recordings response payload",
        operation=response.operation,
        http_status=response.http_status,
    )


class Recordings:
    """Synchronous facade over the reviewed local and stored recording queries."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def by_time(
        self,
        device_serial: str,
        *,
        channel: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        rec_type: int | None = None,
        version: str | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """List device recordings within an optional millisecond time window."""
        with _diagnostic_scope(self._client, entrypoint="recordings.by_time", mode="sync"):
            _require_domestic(self._client.region.scope)
            form = _by_time_form(
                device_serial,
                channel=channel,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                rec_type=rec_type,
                version=version,
                page_size=page_size,
            )
            response = self._client.request(_BY_TIME, data=form, headers=_FORM_HEADERS)
            return _require_recording_data(response, allow_file_page=True)

    def by_text_time(
        self,
        *,
        device_serial: str | None = None,
        channel: str | None = None,
        start_time: str | None = None,
        stop_time: str | None = None,
        rec_type: int | None = None,
    ) -> ApiResponse:
        """List recordings filtered by optional fixed-format text timestamps."""
        with _diagnostic_scope(self._client, entrypoint="recordings.by_text_time", mode="sync"):
            _require_domestic(self._client.region.scope)
            form = _by_text_time_form(
                device_serial=device_serial,
                channel=channel,
                start_time=start_time,
                stop_time=stop_time,
                rec_type=rec_type,
            )
            response = self._client.request(_BY_TEXT_TIME, data=form, headers=_FORM_HEADERS)
            return _require_recording_data(response, allow_file_page=False)

    def local(
        self,
        device_serial: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        channel: str | int | None = None,
        record_type: int | None = None,
        page_size: int | None = None,
        query_by_nvr: bool | None = None,
        location: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="recordings.local", mode="sync"):
            query = _build_local_query(
                self._client.region.scope,
                device_serial,
                start_time=start_time,
                end_time=end_time,
                channel=channel,
                record_type=record_type,
                page_size=page_size,
                query_by_nvr=query_by_nvr,
                location=location,
            )
            return self._client.request(
                query.operation,
                params=query.params,
                headers=query.headers,
            )


class AsyncRecordings:
    """Asynchronous facade over the reviewed local and stored recording queries."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def by_time(
        self,
        device_serial: str,
        *,
        channel: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        rec_type: int | None = None,
        version: str | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """List device recordings within an optional millisecond time window."""
        with _diagnostic_scope(self._client, entrypoint="recordings.by_time", mode="async"):
            _require_domestic(self._client.region.scope)
            form = _by_time_form(
                device_serial,
                channel=channel,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                rec_type=rec_type,
                version=version,
                page_size=page_size,
            )
            response = await self._client.request(_BY_TIME, data=form, headers=_FORM_HEADERS)
            return _require_recording_data(response, allow_file_page=True)

    async def by_text_time(
        self,
        *,
        device_serial: str | None = None,
        channel: str | None = None,
        start_time: str | None = None,
        stop_time: str | None = None,
        rec_type: int | None = None,
    ) -> ApiResponse:
        """List recordings filtered by optional fixed-format text timestamps."""
        with _diagnostic_scope(self._client, entrypoint="recordings.by_text_time", mode="async"):
            _require_domestic(self._client.region.scope)
            form = _by_text_time_form(
                device_serial=device_serial,
                channel=channel,
                start_time=start_time,
                stop_time=stop_time,
                rec_type=rec_type,
            )
            response = await self._client.request(_BY_TEXT_TIME, data=form, headers=_FORM_HEADERS)
            return _require_recording_data(response, allow_file_page=False)

    async def local(
        self,
        device_serial: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        channel: str | int | None = None,
        record_type: int | None = None,
        page_size: int | None = None,
        query_by_nvr: bool | None = None,
        location: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(self._client, entrypoint="recordings.local", mode="async"):
            query = _build_local_query(
                self._client.region.scope,
                device_serial,
                start_time=start_time,
                end_time=end_time,
                channel=channel,
                record_type=record_type,
                page_size=page_size,
                query_by_nvr=query_by_nvr,
                location=location,
            )
            return await self._client.request(
                query.operation,
                params=query.params,
                headers=query.headers,
            )
