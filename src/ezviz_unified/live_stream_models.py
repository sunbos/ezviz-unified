"""Request builders and response checks for domestic cloud live streams.

Each ``prepare_*`` builder mirrors a public ``live_streams`` facade method,
validates its arguments, and returns a ``_Request`` describing the wire call.
``checked`` validates the decoded response envelope per operation and returns
the original ``ApiResponse`` untouched.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from .errors import ProtocolError
from .live_limits import _wire_int
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _require_domestic
from .storage_models import _integer
from .vod import _header, _naive, _Request, _stamp, _stream_id, _utf8

_STREAM_PATH = "/api/service/media/streammanage/stream"
_STATUS_PATH = "/api/service/media/streammanage/stream/status"
_LIST_PATH = "/api/service/media/streammanage/stream/list"
_RECORD_LIST_PATH = "/api/service/media/streammanage/stream/record/list"
_ADDRESS_PATH = "/api/service/media/streammanage/stream/address"

_DEVICE_ACCESS_TYPE = 1
_RTMP_ACCESS_TYPE = 2

_DEVICE_MAX_SPAN = timedelta(days=7)
_RECORDS_MAX_SPAN = timedelta(days=30)

_PAGE_SIZE_MAX = 100
_RECORD_DURATION_MIN = 900
_RECORD_DURATION_MAX = 7200
_DELAY_MIN = 15
_DELAY_MAX = 21600
_EXPIRE_MAX = 730 * 86400

_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    redact_message_urls=True,
)
# Query-string operations carry identifiers/timestamps in the URL.
_QUERY_POLICY = replace(_POLICY, redact_query_identifiers=True)
# The address response contains a media URL.
_ADDRESS_POLICY = replace(_POLICY, redact_media_urls=True)

# Reusable operation descriptors, built once; ``regions`` pins domestic so a
# request can never fall back to overseas endpoints.
_OP_CREATE_DEVICE = Operation(
    "live_streams.create_device",
    method="POST",
    path=_STREAM_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_POLICY,
)
_OP_CREATE_RTMP = Operation(
    "live_streams.create_rtmp",
    method="POST",
    path=_STREAM_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_QUERY_POLICY,
)
_OP_UPDATE_WINDOW = Operation(
    "live_streams.update_window",
    method="PUT",
    path=_STREAM_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_QUERY_POLICY,
)
_OP_DELETE = Operation(
    "live_streams.delete",
    method="DELETE",
    path=_STREAM_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_QUERY_POLICY,
)
_OP_SET_STATUS = Operation(
    "live_streams.set_status",
    method="PUT",
    path=_STATUS_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_QUERY_POLICY,
)
_OP_LIST = Operation(
    "live_streams.list",
    method="POST",
    path=_LIST_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=_POLICY,
)
_OP_RECORDS = Operation(
    "live_streams.records",
    method="GET",
    path=_RECORD_LIST_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=_QUERY_POLICY,
)
_OP_ADDRESS = Operation(
    "live_streams.address",
    method="POST",
    path=_ADDRESS_PATH,
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_ADDRESS_POLICY,
)

_CREATE_OPS = (_OP_CREATE_DEVICE.name, _OP_CREATE_RTMP.name)


def _ordered(start_time: object, end_time: object) -> tuple[datetime, datetime]:
    """Naive ``startTime``/``endTime`` pair with strict positive ordering."""
    start = _naive("startTime", start_time)
    end = _naive("endTime", end_time)
    if end <= start:
        raise ValueError("endTime must be after startTime")
    return start, end


def _choice(name: str, value: object, allowed: tuple[int, ...]) -> int:
    number = _integer(name, value)
    if number not in allowed:
        raise ValueError(f"{name} must be one of {allowed}")
    return number


def _bounded(name: str, value: object, minimum: int, maximum: int) -> int:
    number = _integer(name, value)
    if number < minimum or number > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _page_start(value: object) -> int:
    number = _integer("pageStart", value)
    if number < 0:
        raise ValueError("pageStart must be non-negative")
    return number


def _expire(value: object) -> int:
    number = _integer("expireTime", value)
    if number > _EXPIRE_MAX:
        raise ValueError("expireTime must not exceed 730 days in seconds")
    return number


def prepare_create_device(
    scope: str,
    device_serial: str,
    *,
    start_time: datetime,
    end_time: datetime,
    local_index: int | None = None,
) -> _Request:
    _require_domestic(scope)
    serial = _header("deviceSerial", device_serial)
    start, end = _ordered(start_time, end_time)
    if end - start > _DEVICE_MAX_SPAN:
        raise ValueError("endTime must be within 7 days of startTime")
    data: dict[str, str | int] = {}
    if local_index is not None:
        # localIndex is unbounded; huge values must fail before auth.
        data["localIndex"] = _wire_int("localIndex", _integer("localIndex", local_index))
    data["accessType"] = _DEVICE_ACCESS_TYPE
    data["startTime"] = _stamp(start)
    data["endTime"] = _stamp(end)
    return _Request(
        operation=_OP_CREATE_DEVICE,
        params={},
        data=data,
        headers={"deviceSerial": serial},
    )


def prepare_create_rtmp(
    scope: str,
    *,
    start_time: datetime,
    end_time: datetime,
    enable_record: int | None = None,
    record_space_id: str | None = None,
    record_duration: int | None = None,
    record_format: int | None = None,
    delay_time: int | None = None,
) -> _Request:
    _require_domestic(scope)
    start, end = _ordered(start_time, end_time)
    params: dict[str, str | int] = {
        "accessType": _RTMP_ACCESS_TYPE,
        "startTime": _stamp(start),
        "endTime": _stamp(end),
    }
    if enable_record is not None:
        params["enableRecord"] = _choice("enableRecord", enable_record, (0, 1))
    if record_space_id is not None:
        if not isinstance(record_space_id, str) or not record_space_id.strip():
            raise ValueError("recordSpaceId must be a non-blank string")
        params["recordSpaceId"] = _utf8("recordSpaceId", record_space_id)
    if record_duration is not None:
        params["recordDuration"] = _bounded(
            "recordDuration",
            record_duration,
            _RECORD_DURATION_MIN,
            _RECORD_DURATION_MAX,
        )
    if record_format is not None:
        params["recordFormat"] = _choice("recordFormat", record_format, (1, 2))
    if delay_time is not None:
        params["delayTime"] = _bounded("delayTime", delay_time, _DELAY_MIN, _DELAY_MAX)
    return _Request(
        operation=_OP_CREATE_RTMP,
        params=params,
        data=None,
        headers={},
    )


def prepare_update_window(
    scope: str,
    stream_id: str,
    *,
    start_time: datetime,
    end_time: datetime,
) -> _Request:
    _require_domestic(scope)
    sid = _stream_id(stream_id)
    start, end = _ordered(start_time, end_time)
    return _Request(
        operation=_OP_UPDATE_WINDOW,
        params={
            "streamId": sid,
            "startTime": _stamp(start),
            "endTime": _stamp(end),
        },
        data=None,
        headers={},
    )


def prepare_delete(scope: str, stream_id: str) -> _Request:
    _require_domestic(scope)
    return _Request(
        operation=_OP_DELETE,
        params={"streamId": _stream_id(stream_id)},
        data=None,
        headers={},
    )


def prepare_set_status(scope: str, stream_id: str, status: int) -> _Request:
    _require_domestic(scope)
    return _Request(
        operation=_OP_SET_STATUS,
        params={
            "streamId": _stream_id(stream_id),
            "status": _choice("status", status, (0, 1)),
        },
        data=None,
        headers={},
    )


def prepare_list(
    scope: str,
    *,
    device_serial: str | None = None,
    stream_id: str | None = None,
    page_start: int = 0,
    page_size: int = 50,
    access_type: int | None = None,
    status: int | None = None,
) -> _Request:
    _require_domestic(scope)
    headers: dict[str, str] = {}
    if device_serial is not None:
        headers["deviceSerial"] = _header("deviceSerial", device_serial)
    data: dict[str, str | int] = {}
    if stream_id is not None:
        data["streamId"] = _stream_id(stream_id)
    data["pageStart"] = _wire_int("pageStart", _page_start(page_start))
    data["pageSize"] = _bounded("pageSize", page_size, 1, _PAGE_SIZE_MAX)
    if access_type is not None:
        data["accessType"] = _choice("accessType", access_type, (1, 2))
    if status is not None:
        data["status"] = _choice("status", status, (0, 1))
    return _Request(
        operation=_OP_LIST,
        params={},
        data=data,
        headers=headers,
    )


def prepare_records(
    scope: str,
    stream_id: int,
    *,
    start_time: datetime,
    end_time: datetime,
    page_start: int = 0,
    page_size: int = 50,
) -> _Request:
    _require_domestic(scope)
    sid = _wire_int("streamId", _integer("streamId", stream_id))
    start, end = _ordered(start_time, end_time)
    if end - start > _RECORDS_MAX_SPAN:
        raise ValueError("endTime must be within 30 days of startTime")
    return _Request(
        operation=_OP_RECORDS,
        params={
            "streamId": sid,
            "startTime": _stamp(start),
            "endTime": _stamp(end),
            "pageStart": _wire_int("pageStart", _page_start(page_start)),
            "pageSize": _bounded("pageSize", page_size, 1, _PAGE_SIZE_MAX),
        },
        data=None,
        headers={},
    )


def prepare_address(
    scope: str,
    stream_id: int,
    protocol: int,
    *,
    address_type: int = 1,
    quality: int | None = None,
    support_h265: int | None = None,
    mute: int | None = None,
    expire_time: int | None = None,
) -> _Request:
    _require_domestic(scope)
    data: dict[str, str | int] = {
        "streamId": _wire_int("streamId", _integer("streamId", stream_id)),
        "protocol": _choice("protocol", protocol, (1, 2, 3)),
    }
    if quality is not None:
        data["quality"] = _choice("quality", quality, (1, 2))
    if support_h265 is not None:
        data["supportH265"] = _choice("supportH265", support_h265, (0, 1))
    if mute is not None:
        data["mute"] = _choice("mute", mute, (0, 1))
    data["type"] = _choice("type", address_type, (1, 2))
    if expire_time is not None:
        # Only upper-bounded, so very negative values can be unencodable.
        data["expireTime"] = _wire_int("expireTime", _expire(expire_time))
    return _Request(
        operation=_OP_ADDRESS,
        params={},
        data=data,
        headers={},
    )


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _fail(response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        "unexpected live stream response shape",
        operation=response.operation,
        http_status=response.http_status,
    )


def checked(response: ApiResponse) -> ApiResponse:
    """Validate the per-operation ``data`` shape.

    ``ApiResponse.code`` was already checked during decode and ``data`` is the
    payload extracted per the operation's policy, so only ``data`` is examined.
    Returns the original ``ApiResponse`` unchanged on success; raises
    ``ProtocolError`` (operation name string and HTTP status only) otherwise,
    including for operation names this module does not know.
    """
    operation = response.operation
    data = response.data
    if operation in _CREATE_OPS:
        valid = isinstance(data, dict) and _nonblank(data.get("streamId"))
    elif operation == _OP_LIST.name:
        valid = isinstance(data, list) and all(isinstance(item, dict) for item in data)
    elif operation == _OP_RECORDS.name:
        record_list = data.get("recordList") if isinstance(data, dict) else None
        valid = isinstance(record_list, list) and all(
            isinstance(item, dict) for item in record_list
        )
    elif operation == _OP_ADDRESS.name:
        valid = isinstance(data, dict) and _nonblank(data.get("address"))
    elif operation in (_OP_UPDATE_WINDOW.name, _OP_DELETE.name, _OP_SET_STATUS.name):
        # ACK operations: any data (including absent/None) passes through.
        valid = True
    else:
        raise _fail(response)
    if not valid:
        raise _fail(response)
    return response
