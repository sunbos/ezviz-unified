"""Cloud VOD metadata queries and remux/playback-address calls for the EZVIZ client.

Documented for the domestic region only (docs 5342/4187/3971/3972/4941/3973/4751/4757/3964).
``files``, ``task``, ``tasks`` and ``task_files`` are read-only queries; under the existing
invalid-token policy their ``GET`` may be replayed at most once.
``create_remux_task`` is declared non-idempotent task creation by its spec
(doc 3971). ``recording_urls`` (doc 3973) is described as idempotent for the
same parameters, but it is not documented as read-only or side-effect free;
its ``Operation.read_only=False`` only disables automatic replay and does not
claim the service declared it side-effect free. The SDK never
auto-paginates, batches, polls, downloads media, resolves playback addresses
into fetched content, or manages upstream resources.

``files`` performs one logical read-only query per call. At least one selector is
required by the service; every supplied selector is sent verbatim — the
documented ``spaceId`` priority is resolved server-side, not by the SDK.

``file_node_ids`` accepts a non-string ``Sequence`` snapshotted to a tuple
before authentication; order and duplicates are preserved and joined into the
documented CSV wire form. Per-element commas are refused because this CSV has
no escaping (SDK design choice). ``space_id`` must be an ``int`` and rejects
``bool``; no undeclared positive-only bound is invented. ``space_name`` must
be a non-blank UTF-8-encodable string.

``device_serial`` and ``local_index`` are sent as HTTP headers under the
documented ``deviceSerial``/``localIndex`` names. Header values must be
non-blank ASCII strings, reject C0 control characters and DEL, and reject
leading or trailing spaces while preserving interior spaces; ``local_index``
has no default and is never coerced to an int. Time bounds are naive
whole-second ``datetime`` values formatted ``yyyy-MM-dd HH:mm:ss`` with a
four-digit year; no timezone is inferred or converted. ``tasks`` optional
``start_time``/``end_time`` are opaque UTF-8 strings passed verbatim, not
parsed as datetimes and not required as a pair. All validation happens before
any authentication or network access.

``task_files`` (doc 3964) is a separate endpoint from ``task`` (doc 3972) and
from ``cloud_recording_v1.task_files`` (doc 1373): ``taskId`` is one path
segment, the token rides in the ``accessToken`` request header, and the request
has no query, no body and no pagination. The facade validates only that
``task_id`` is a non-blank UTF-8 string; single-segment rules, the one-time
percent-encoding and ``.``/``..`` rejection come from the shared path template
binder — no other endpoint's length or charset cap is carried over. The source
declares the call unsupported for managed sub-accounts; the SDK records that
constraint but never guesses the caller's account role. On success ``data``
must be a list of file objects: a missing or non-list ``data`` is a protocol
violation. ``fileName`` may arrive as a string or an object (the page's field
table and its example disagree); both shapes, absent fields and unknown
extensions pass through untouched. Returned ``fileUrl``, ``coverPicUrl`` and
``folderNode`` values are inert metadata: they never trigger a second request,
a signature parse or a media fetch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import _integer, _node_ids, _text

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"

_POLICY_META = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)
# The stream-archive reads may surface server messages carrying media URLs;
# those are masked in rendered diagnostics while status, code and data stay.
_POLICY_META_REDACT_URLS = replace(_POLICY_META, redact_message_urls=True)

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_OP_FILES = Operation(
    "vod.files",
    "GET",
    "/api/service/open/vod/files",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)
_OP_CREATE_REMUX_TASK = Operation(
    "vod.create_remux_task",
    "POST",
    "/api/service/open/vod/media/trans/code",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_TASK = Operation(
    "vod.task",
    "GET",
    "/api/service/open/vod/task",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)
# Doc 3964 returns file download/cover URLs and node ids in ``data``; the
# upstream message may echo them, so rendered errors mask the whole upstream
# message and any media URLs (same treatment as cloud_recording_v1.task_files).
# E96 documents 200/500/400/406; 403/404 and the auth (10002) and permission
# (10031) codes are also exposed verbatim, anything else renders as ``***``.
_TASK_FILES_CODES = (200, 400, 403, 404, 406, 500, 10002, 10031)
_OP_TASK_FILES = Operation(
    "vod.task_files",
    "GET",
    "/api/service/open/cloud/vod/task/files/{taskId}",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(v for code in _TASK_FILES_CODES for v in (code, str(code))),
    ),
)
_OP_TASKS = Operation(
    "vod.tasks",
    "GET",
    "/api/service/open/vod/tasks",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)
_OP_RECORDING_URLS = Operation(
    "vod.recording_urls",
    "POST",
    "/api/service/open/vod/file/address/hls",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_STREAM_SOURCE_FILES = Operation(
    "vod.stream_source_files",
    "POST",
    "/api/service/media/streammanage/stream/file/info",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META_REDACT_URLS,
)
_OP_STREAM_INDEX_FILE = Operation(
    "vod.stream_index_file",
    "POST",
    "/api/service/media/streammanage/stream/index/file",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META_REDACT_URLS,
)
# Doc 4473 lists spaces of the VOD business: token in the header, paged by
# pageStart/pageSize. ``data`` is an object holding ``result[]`` plus
# total/pageStart/pageSize — it is never flattened into a bare array. The
# caller's ``spaceIdName`` selector and returned ``spaceName`` values may be
# echoed by the upstream message, so rendered errors mask the whole upstream
# message; E163 documents 200/500/400 and the auth/permission codes are also
# exposed verbatim.
_SPACE_LIST_CODES = (200, 400, 403, 404, 500, 10002, 10031)
_OP_SPACE_LIST = Operation(
    "vod.space_list",
    "GET",
    "/api/service/open/vod/space/list",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        # Media-URL masking also arms the controlled-log http-version
        # normalizer: a server-controlled non-standard ``http_version`` on the
        # httpx INFO record is rendered as ``HTTP`` instead of passing through
        # (the reason phrase is already blanked and the query stripped).
        redact_media_urls=True,
        # Opt in to encoded-identifier masking for query selectors: once the
        # shared identifier table covers ``spaceIdName``, its raw and encoded
        # forms are collected into the request's redaction secrets without a
        # further policy change here.
        redact_query_identifiers=True,
        exposed_business_codes=tuple(v for code in _SPACE_LIST_CODES for v in (code, str(code))),
    ),
)


@dataclass(frozen=True)
class _Request:
    operation: Operation
    params: dict[str, str | int]
    data: dict[str, str | int] | None
    headers: dict[str, str]


def _require_domestic(scope: str, method: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError(f"vod.{method} is only documented for the domestic region")


def _utf8(name: str, value: str) -> str:
    """SDK defensive choice: user text must be UTF-8 encodable for the wire."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        encodable = False
    else:
        encodable = True
    # Raised outside the except block so no UnicodeEncodeError (which embeds
    # the offending value) is left on the new exception's __context__.
    if not encodable:
        raise ValueError(f"{name} must be encodable as UTF-8") from None
    return value


def _header(name: str, value: object) -> str:
    """Validate an HTTP header value (root SDK safety choice).

    The value must be a non-blank ASCII string with no C0 control characters
    or DEL and no leading/trailing spaces; ordinary interior spaces are kept.
    """
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if not value.isascii():
        raise ValueError(f"{name} must be ASCII for an HTTP header")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{name} must not contain control characters")
    if value != value.strip():
        raise ValueError(f"{name} must not have leading or trailing spaces")
    return value


def _naive(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError(f"{name} must be a naive datetime")
    if value.microsecond:
        raise ValueError(f"{name} must not carry microseconds")
    return value


def _stamp(moment: datetime) -> str:
    """Format as ``yyyy-MM-dd HH:mm:ss`` with a four-digit year."""
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d} "
        f"{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    )


_REMUX_MIN_SPAN = timedelta(seconds=10)
_REMUX_MAX_SPAN = timedelta(seconds=172800)
_URLS_MAX_SPAN = timedelta(seconds=172800)
_TASK_BUS_TYPES = ("1", "7", "71")
_REMUX_FORMATS = ("HLS", "MP4")
_URL_FORMATS = ("MP4", "HLS")
_EXPIRE_MIN = 1
_EXPIRE_MAX = 604800


def _remux_request(
    scope: str,
    device_serial: str,
    *,
    local_index: str,
    bus_type: str,
    start_time: datetime,
    end_time: datetime,
    format: str | None,
    space_id: int | None,
    result_space_id: int | None,
    validate_code: str | None,
    acceleration: int | None,
) -> _Request:
    _require_domestic(scope, "create_remux_task")
    headers = {
        "deviceSerial": _header("device_serial", device_serial),
        "localIndex": _header("local_index", local_index),
    }
    if not isinstance(bus_type, str) or bus_type not in _TASK_BUS_TYPES:
        raise ValueError("bus_type must be one of: 1, 7, 71")
    start = _naive("start_time", start_time)
    end = _naive("end_time", end_time)
    span = end - start
    if not _REMUX_MIN_SPAN <= span <= _REMUX_MAX_SPAN:
        raise ValueError("end_time - start_time must be within 10..172800 seconds")
    if bus_type == "7" and space_id is None:
        raise ValueError("space_id is required when bus_type is 7")
    data: dict[str, str | int] = {
        "busType": bus_type,
        "startTime": _stamp(start),
        "endTime": _stamp(end),
    }
    if format is not None:
        if not isinstance(format, str) or format not in _REMUX_FORMATS:
            raise ValueError("format must be HLS or MP4")
        if bus_type == "71" and format == "HLS":
            raise ValueError("format HLS is not allowed when bus_type is 71")
        data["format"] = format
    if space_id is not None:
        data["spaceId"] = _integer("space_id", space_id)
    if result_space_id is not None:
        data["resultSpaceId"] = _integer("result_space_id", result_space_id)
    if validate_code is not None:
        data["validateCode"] = _utf8("validate_code", _text("validate_code", validate_code))
    if acceleration is not None:
        accel = _integer("acceleration", acceleration)
        if accel not in (0, 1):
            raise ValueError("acceleration must be 0 or 1")
        data["acceleration"] = accel
    return _Request(_OP_CREATE_REMUX_TASK, {}, data, headers)


def _task_request(scope: str, task_id: str) -> _Request:
    _require_domestic(scope, "task")
    params: dict[str, str | int] = {"taskId": _utf8("task_id", _text("task_id", task_id))}
    return _Request(_OP_TASK, params, None, {})


def _task_files_request(scope: str, task_id: str) -> str:
    """Validate region and facade-level ``task_id``; the path binder does the rest.

    ``taskId`` is one path segment on this endpoint, not a query parameter and
    not a 1373-style paged task file list. ``_utf8``/``_text`` guarantee a
    non-blank UTF-8 string before authentication; ``render_path`` then rejects
    ``/``, ``\\``, control characters and lone ``.``/``..`` and percent-encodes
    the segment exactly once, registering the raw and encoded forms as secrets.
    No other endpoint's task-id length or charset cap is reused here.
    """
    _require_domestic(scope, "task_files")
    return _utf8("task_id", _text("task_id", task_id))


def _check_task_files(response: ApiResponse) -> ApiResponse:
    """Require ``data`` to be a list of file objects; keep every shape as-is.

    Doc 3964 types ``fileName`` as ``object`` while its own example carries a
    string literal — both forms, missing fields and unknown row or envelope
    extensions pass through untouched. Only the container level is checked.
    """
    rows = response.data
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ProtocolError(
            "unexpected vod.task_files response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


_SPACE_LIST_SORT_FIELDS = ("createTime",)
_SPACE_LIST_SORT_RULES = ("asc", "desc")


def _space_list_request(
    scope: str,
    *,
    page_start: int,
    page_size: int,
    sort_field: str | None,
    sort_rule: str | None,
    space_id_name: str | None,
) -> _Request:
    """Build the VOD space-list query (doc 4473); token stays in the header.

    ``pageStart``/``pageSize`` are documented required ints — the page
    documents ``pageStart`` as "页码,默认0", so a negative start or a
    non-positive size is refused before authentication along with non-ints
    and bools. ``spaceIdName`` matches one space by id or name.
    """
    _require_domestic(scope, "space_list")
    start = _integer("page_start", page_start)
    if start < 0:
        raise ValueError("page_start must be a non-negative integer")
    size = _integer("page_size", page_size)
    if size <= 0:
        raise ValueError("page_size must be a positive integer")
    params: dict[str, str | int] = {
        "pageStart": start,
        "pageSize": size,
    }
    if sort_field is not None:
        if not isinstance(sort_field, str) or sort_field not in _SPACE_LIST_SORT_FIELDS:
            raise ValueError("sort_field must be createTime")
        params["sortField"] = sort_field
    if sort_rule is not None:
        if not isinstance(sort_rule, str) or sort_rule not in _SPACE_LIST_SORT_RULES:
            raise ValueError("sort_rule must be asc or desc")
        params["sortRule"] = sort_rule
    if space_id_name is not None:
        params["spaceIdName"] = _utf8("space_id_name", _text("space_id_name", space_id_name))
    return _Request(_OP_SPACE_LIST, params, None, {})


def _check_space_list(response: ApiResponse) -> ApiResponse:
    """Require ``data`` to be an object whose ``result`` is a list of objects.

    Doc 4473 (E71) types ``result`` as ``array<object>`` nested alongside
    ``total``/``pageStart``/``pageSize``; a missing ``data``, a non-list
    ``result`` or a non-object row is a protocol violation. Empty rows,
    missing optional fields and unknown extensions pass through untouched.
    """
    data = response.data
    if not isinstance(data, dict):
        raise ProtocolError(
            "unexpected vod.space_list response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    rows = data.get("result")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ProtocolError(
            "unexpected vod.space_list response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _tasks_request(
    scope: str,
    task_type: int,
    *,
    page_start: int | None,
    page_size: int | None,
    start_time: str | None,
    end_time: str | None,
) -> _Request:
    _require_domestic(scope, "tasks")
    kind = _integer("task_type", task_type)
    if kind not in (7, 8):
        raise ValueError("task_type must be 7 or 8")
    params: dict[str, str | int] = {"taskType": kind}
    if page_start is not None:
        params["pageStart"] = _integer("page_start", page_start)
    if page_size is not None:
        params["pageSize"] = _integer("page_size", page_size)
    if start_time is not None:
        params["startTime"] = _utf8("start_time", _text("start_time", start_time))
    if end_time is not None:
        params["endTime"] = _utf8("end_time", _text("end_time", end_time))
    return _Request(_OP_TASKS, params, None, {})


def _recording_urls_request(
    scope: str,
    device_serial: str,
    *,
    local_index: str,
    start_time: datetime,
    end_time: datetime,
    format: str,
    expire_seconds: int | None,
    space_id: int | None,
) -> _Request:
    _require_domestic(scope, "recording_urls")
    headers = {
        "deviceSerial": _header("device_serial", device_serial),
        "localIndex": _header("local_index", local_index),
    }
    start = _naive("start_time", start_time)
    end = _naive("end_time", end_time)
    span = end - start
    if span < timedelta(0) or span > _URLS_MAX_SPAN:
        raise ValueError("end_time - start_time must be within 0..172800 seconds")
    if not isinstance(format, str) or format not in _URL_FORMATS:
        raise ValueError("format must be MP4 or HLS")
    data: dict[str, str | int] = {
        "startTime": _stamp(start),
        "endTime": _stamp(end),
        "format": format,
    }
    if expire_seconds is not None:
        expire = _integer("expire_seconds", expire_seconds)
        if not _EXPIRE_MIN <= expire <= _EXPIRE_MAX:
            raise ValueError("expire_seconds must be within 1..604800")
        data["expireSeconds"] = expire
    if space_id is not None:
        data["spaceId"] = _integer("space_id", space_id)
    return _Request(_OP_RECORDING_URLS, {}, data, headers)


def _files_request(
    scope: str,
    file_node_ids: Sequence[str] | None,
    *,
    space_id: int | None,
    space_name: str | None,
) -> _Request:
    _require_domestic(scope, "files")
    params: dict[str, str | int] = {}
    if file_node_ids is not None:
        params["fileNodeIds"] = _utf8("file_node_ids", _node_ids(file_node_ids, limit=50))
    if space_id is not None:
        params["spaceId"] = _integer("space_id", space_id)
    if space_name is not None:
        params["spaceName"] = _utf8("space_name", _text("space_name", space_name))
    if not params:
        raise ValueError("vod.files requires at least one selector")
    return _Request(_OP_FILES, params, None, {})


_STREAM_INDEX_MAX_SPAN = timedelta(days=1)


def _stream_id(value: object) -> str:
    """Opaque stream id: non-blank UTF-8 string, sent verbatim.

    No length cap, case coercion, stripping or alphabet restriction is
    invented beyond the documented required String.
    """
    return _utf8("stream_id", _text("stream_id", value))


def _query_date(value: object) -> str:
    """Strict ASCII ``YYYY-MM-DD`` calendar date (doc 4751).

    ``date.fromisoformat`` must parse it and round-trip to the identical
    string, so padded, compact or non-calendar values are rejected.
    """
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("query_date must be a YYYY-MM-DD date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("query_date must be a valid YYYY-MM-DD date") from None
    if parsed.isoformat() != value:
        raise ValueError("query_date must be a valid YYYY-MM-DD date")
    return value


def _stream_source_files_request(
    scope: str,
    stream_id: str,
    *,
    file_type: int,
    query_date: str | None,
) -> _Request:
    _require_domestic(scope, "stream_source_files")
    kind = _integer("file_type", file_type)
    if kind not in (1, 2):
        raise ValueError("file_type must be 1 (recording) or 2 (snapshot)")
    if kind == 1 and query_date is None:
        raise ValueError("query_date is required when file_type is 1")
    data: dict[str, str | int] = {
        "streamId": _stream_id(stream_id),
        "fileType": str(kind),
    }
    if query_date is not None:
        data["queryDate"] = _query_date(query_date)
    return _Request(_OP_STREAM_SOURCE_FILES, {}, data, dict(_FORM_HEADERS))


def _stream_index_file_request(
    scope: str,
    stream_id: str,
    *,
    start_time: datetime,
    stop_time: datetime,
) -> _Request:
    _require_domestic(scope, "stream_index_file")
    start = _naive("start_time", start_time)
    stop = _naive("stop_time", stop_time)
    span = stop - start
    if span < timedelta(0) or span > _STREAM_INDEX_MAX_SPAN:
        raise ValueError("stop_time - start_time must be within 0..86400 seconds")
    data: dict[str, str | int] = {
        "streamId": _stream_id(stream_id),
        "startTime": _stamp(start),
        "stopTime": _stamp(stop),
    }
    return _Request(_OP_STREAM_INDEX_FILE, {}, data, dict(_FORM_HEADERS))


def _expect_stream_file_info(response: ApiResponse, container: type) -> ApiResponse:
    """Check ``data`` and ``data.fileInfo`` container levels only.

    Row fields, unknown keys and timestamp strings pass through untouched;
    a wrong container level is a protocol violation with no raw payload.
    """
    data = response.data
    if not isinstance(data, dict) or not isinstance(data.get("fileInfo"), container):
        raise ProtocolError(
            "unexpected stream archive response shape",
            http_status=response.http_status,
            operation=response.operation,
        )
    return response


class Vod:
    """Synchronous facade over the reviewed VOD endpoints (metadata, remux, addresses)."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def files(
        self,
        file_node_ids: Sequence[str] | None = None,
        *,
        space_id: int | None = None,
        space_name: str | None = None,
    ) -> ApiResponse:
        """Query VOD file metadata by node ids, space id, or space name."""
        with _diagnostic_scope(self._client, entrypoint="vod.files", mode="sync"):
            request = _files_request(
                self._client.region.scope,
                file_node_ids,
                space_id=space_id,
                space_name=space_name,
            )
            return self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    def create_remux_task(
        self,
        device_serial: str,
        *,
        local_index: str,
        bus_type: Literal["1", "7", "71"],
        start_time: datetime,
        end_time: datetime,
        format: Literal["HLS", "MP4"] | None = None,
        space_id: int | None = None,
        result_space_id: int | None = None,
        validate_code: str | None = None,
        acceleration: Literal[0, 1] | None = None,
    ) -> ApiResponse:
        """Create an asynchronous cloud-recording remux/transcode task.

        Non-idempotent: ``read_only=False`` only disables automatic replay.
        Returns the raw ``ApiResponse``; it does not become a task handle.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.create_remux_task", mode="sync"):
            request = _remux_request(
                self._client.region.scope,
                device_serial,
                local_index=local_index,
                bus_type=bus_type,
                start_time=start_time,
                end_time=end_time,
                format=format,
                space_id=space_id,
                result_space_id=result_space_id,
                validate_code=validate_code,
                acceleration=acceleration,
            )
            return self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    def task(self, task_id: str) -> ApiResponse:
        """Query one remux task by id (read-only)."""
        with _diagnostic_scope(self._client, entrypoint="vod.task", mode="sync"):
            request = _task_request(self._client.region.scope, task_id)
            return self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    def tasks(
        self,
        task_type: Literal[7, 8],
        *,
        page_start: int | None = None,
        page_size: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> ApiResponse:
        """List remux tasks of one task type (read-only). Never auto-pages."""
        with _diagnostic_scope(self._client, entrypoint="vod.tasks", mode="sync"):
            request = _tasks_request(
                self._client.region.scope,
                task_type,
                page_start=page_start,
                page_size=page_size,
                start_time=start_time,
                end_time=end_time,
            )
            return self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    def task_files(self, task_id: str) -> ApiResponse:
        """List the files of one VOD task by path-segment id (read-only, doc 3964).

        ``taskId`` is a single URL path segment and the token is sent in the
        ``accessToken`` header; the request has no query, body or pagination.
        Returned ``fileUrl``/``coverPicUrl``/``folderNode`` values are metadata
        only — the SDK never issues a follow-up request or opens a media URL.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.task_files", mode="sync"):
            bound = _task_files_request(self._client.region.scope, task_id)
            response = self._client.request(
                _OP_TASK_FILES,
                path_params={"taskId": bound},
            )
            return _check_task_files(response)

    def space_list(
        self,
        *,
        page_start: int,
        page_size: int,
        sort_field: Literal["createTime"] | None = None,
        sort_rule: Literal["asc", "desc"] | None = None,
        space_id_name: str | None = None,
    ) -> ApiResponse:
        """List VOD recording spaces one page at a time (read-only, doc 4473).

        Token is sent in the ``accessToken`` header; ``pageStart``/``pageSize``
        are required. ``data`` is returned as the documented object containing
        ``result``/``total``/``pageStart``/``pageSize`` — never flattened.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.space_list", mode="sync"):
            request = _space_list_request(
                self._client.region.scope,
                page_start=page_start,
                page_size=page_size,
                sort_field=sort_field,
                sort_rule=sort_rule,
                space_id_name=space_id_name,
            )
            response = self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )
            return _check_space_list(response)

    def recording_urls(
        self,
        device_serial: str,
        *,
        local_index: str,
        start_time: datetime,
        end_time: datetime,
        format: Literal["MP4", "HLS"],
        expire_seconds: int | None = None,
        space_id: int | None = None,
    ) -> ApiResponse:
        """Request playback addresses for a cloud-recording window.

        ``read_only=False`` only disables automatic replay; it does not claim
        the call is side-effect free. Returns the raw ``ApiResponse`` and never
        downloads media.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.recording_urls", mode="sync"):
            request = _recording_urls_request(
                self._client.region.scope,
                device_serial,
                local_index=local_index,
                start_time=start_time,
                end_time=end_time,
                format=format,
                expire_seconds=expire_seconds,
                space_id=space_id,
            )
            return self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    def stream_source_files(
        self,
        stream_id: str,
        *,
        file_type: int,
        query_date: str | None = None,
    ) -> ApiResponse:
        """List archived recordings/snapshots of one stream (read-only, doc 4751).

        ``file_type`` is ``1`` (recording) or ``2`` (snapshot); ``query_date``
        is a strict ``YYYY-MM-DD`` string, mandatory for recordings and
        optional for snapshots. The SDK never opens or downloads any returned
        URL.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.stream_source_files", mode="sync"):
            request = _stream_source_files_request(
                self._client.region.scope,
                stream_id,
                file_type=file_type,
                query_date=query_date,
            )
            response = self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )
            return _expect_stream_file_info(response, list)

    def stream_index_file(
        self,
        stream_id: str,
        *,
        start_time: datetime,
        stop_time: datetime,
    ) -> ApiResponse:
        """Query the stream index file for one window (read-only, doc 4757).

        Bounds are naive whole-second datetimes sent as ``startTime``/
        ``stopTime``; the window may cross midnight and must be within
        0..86400 seconds. The SDK never opens or downloads any returned URL.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.stream_index_file", mode="sync"):
            request = _stream_index_file_request(
                self._client.region.scope,
                stream_id,
                start_time=start_time,
                stop_time=stop_time,
            )
            response = self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )
            return _expect_stream_file_info(response, dict)


class AsyncVod:
    """Asynchronous facade over the reviewed VOD endpoints (metadata, remux, addresses)."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def files(
        self,
        file_node_ids: Sequence[str] | None = None,
        *,
        space_id: int | None = None,
        space_name: str | None = None,
    ) -> ApiResponse:
        """Query VOD file metadata by node ids, space id, or space name."""
        with _diagnostic_scope(self._client, entrypoint="vod.files", mode="async"):
            request = _files_request(
                self._client.region.scope,
                file_node_ids,
                space_id=space_id,
                space_name=space_name,
            )
            return await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    async def create_remux_task(
        self,
        device_serial: str,
        *,
        local_index: str,
        bus_type: Literal["1", "7", "71"],
        start_time: datetime,
        end_time: datetime,
        format: Literal["HLS", "MP4"] | None = None,
        space_id: int | None = None,
        result_space_id: int | None = None,
        validate_code: str | None = None,
        acceleration: Literal[0, 1] | None = None,
    ) -> ApiResponse:
        """Create an asynchronous cloud-recording remux/transcode task.

        Non-idempotent: ``read_only=False`` only disables automatic replay.
        Returns the raw ``ApiResponse``; it does not become a task handle.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.create_remux_task", mode="async"):
            request = _remux_request(
                self._client.region.scope,
                device_serial,
                local_index=local_index,
                bus_type=bus_type,
                start_time=start_time,
                end_time=end_time,
                format=format,
                space_id=space_id,
                result_space_id=result_space_id,
                validate_code=validate_code,
                acceleration=acceleration,
            )
            return await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    async def task(self, task_id: str) -> ApiResponse:
        """Query one remux task by id (read-only)."""
        with _diagnostic_scope(self._client, entrypoint="vod.task", mode="async"):
            request = _task_request(self._client.region.scope, task_id)
            return await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    async def tasks(
        self,
        task_type: Literal[7, 8],
        *,
        page_start: int | None = None,
        page_size: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> ApiResponse:
        """List remux tasks of one task type (read-only). Never auto-pages."""
        with _diagnostic_scope(self._client, entrypoint="vod.tasks", mode="async"):
            request = _tasks_request(
                self._client.region.scope,
                task_type,
                page_start=page_start,
                page_size=page_size,
                start_time=start_time,
                end_time=end_time,
            )
            return await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    async def task_files(self, task_id: str) -> ApiResponse:
        """List the files of one VOD task by path-segment id (read-only, doc 3964).

        ``taskId`` is a single URL path segment and the token is sent in the
        ``accessToken`` header; the request has no query, body or pagination.
        Returned ``fileUrl``/``coverPicUrl``/``folderNode`` values are metadata
        only — the SDK never issues a follow-up request or opens a media URL.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.task_files", mode="async"):
            bound = _task_files_request(self._client.region.scope, task_id)
            response = await self._client.request(
                _OP_TASK_FILES,
                path_params={"taskId": bound},
            )
            return _check_task_files(response)

    async def space_list(
        self,
        *,
        page_start: int,
        page_size: int,
        sort_field: Literal["createTime"] | None = None,
        sort_rule: Literal["asc", "desc"] | None = None,
        space_id_name: str | None = None,
    ) -> ApiResponse:
        """List VOD recording spaces one page at a time (read-only, doc 4473).

        Token is sent in the ``accessToken`` header; ``pageStart``/``pageSize``
        are required. ``data`` is returned as the documented object containing
        ``result``/``total``/``pageStart``/``pageSize`` — never flattened.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.space_list", mode="async"):
            request = _space_list_request(
                self._client.region.scope,
                page_start=page_start,
                page_size=page_size,
                sort_field=sort_field,
                sort_rule=sort_rule,
                space_id_name=space_id_name,
            )
            response = await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )
            return _check_space_list(response)

    async def recording_urls(
        self,
        device_serial: str,
        *,
        local_index: str,
        start_time: datetime,
        end_time: datetime,
        format: Literal["MP4", "HLS"],
        expire_seconds: int | None = None,
        space_id: int | None = None,
    ) -> ApiResponse:
        """Request playback addresses for a cloud-recording window.

        ``read_only=False`` only disables automatic replay; it does not claim
        the call is side-effect free. Returns the raw ``ApiResponse`` and never
        downloads media.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.recording_urls", mode="async"):
            request = _recording_urls_request(
                self._client.region.scope,
                device_serial,
                local_index=local_index,
                start_time=start_time,
                end_time=end_time,
                format=format,
                expire_seconds=expire_seconds,
                space_id=space_id,
            )
            return await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )

    async def stream_source_files(
        self,
        stream_id: str,
        *,
        file_type: int,
        query_date: str | None = None,
    ) -> ApiResponse:
        """List archived recordings/snapshots of one stream (read-only, doc 4751).

        ``file_type`` is ``1`` (recording) or ``2`` (snapshot); ``query_date``
        is a strict ``YYYY-MM-DD`` string, mandatory for recordings and
        optional for snapshots. The SDK never opens or downloads any returned
        URL.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.stream_source_files", mode="async"):
            request = _stream_source_files_request(
                self._client.region.scope,
                stream_id,
                file_type=file_type,
                query_date=query_date,
            )
            response = await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )
            return _expect_stream_file_info(response, list)

    async def stream_index_file(
        self,
        stream_id: str,
        *,
        start_time: datetime,
        stop_time: datetime,
    ) -> ApiResponse:
        """Query the stream index file for one window (read-only, doc 4757).

        Bounds are naive whole-second datetimes sent as ``startTime``/
        ``stopTime``; the window may cross midnight and must be within
        0..86400 seconds. The SDK never opens or downloads any returned URL.
        """
        with _diagnostic_scope(self._client, entrypoint="vod.stream_index_file", mode="async"):
            request = _stream_index_file_request(
                self._client.region.scope,
                stream_id,
                start_time=start_time,
                stop_time=stop_time,
            )
            response = await self._client.request(
                request.operation,
                params=request.params,
                data=request.data,
                headers=request.headers,
            )
            return _expect_stream_file_info(response, dict)
