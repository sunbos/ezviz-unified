"""Cloud Recording 1.0 file operations for the unified client (sync and async).

All operations are documented for the domestic region only and share the
``meta.code``/``meta.message``/``data`` response policy with ``success_values=(200,)``.
Every method returns the ``ApiResponse`` untouched: the SDK performs no
automatic polling, no unfreeze/download orchestration, and no media download.
``unfreeze_file`` is a write operation and is never auto-replayed even though
the source marks it idempotent; read operations follow the existing
single invalid-token refresh-and-replay rule. The ``cloud_recording_v1``
methods are independent file operations (docs 5341/1380, 1373, 1374, 1375, 1377).
No 1.0 ``projectId``/``fileId`` is ever mapped onto unified-storage ``fileNodeId``
semantics.

Validation rules that are SDK choices rather than explicit official facts are
marked inline. All user text must be UTF-8 encodable and non-blank; error
messages carry the parameter name and rule only, never the rejected value.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text
from .storage_models import _integer, _text

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_DOWNLOAD_EXPIRE_MIN = 60
_DOWNLOAD_EXPIRE_MAX = 604800

_POLICY_META = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)

_OP_UNFREEZE = Operation(
    "cloud_recording_v1.unfreeze_file",
    "POST",
    "/api/open/cloud/v1/file/unfreeze",
    token_location="form",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_TASK_FILE_CODES = (200, 400, 403, 404, 500, 10002, 10031)
_TASK_FILE_TYPES = {
    "projectId": str,
    "fileId": str,
    "expireTime": str,
    "taskId": str,
    "fileType": int,
    "status": int,
    "fileSize": int,
    "duration": int,
    "storageType": int,
    "resultSpaceId": int,
    "downloadUrls": list,
}

_OP_TASK_FILES = Operation(
    "cloud_recording_v1.task_files",
    "GET",
    "/api/v3/open/cloud/task/files",
    regions=frozenset({_DOMESTIC}),
    token_location="query",
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(v for code in _TASK_FILE_CODES for v in (code, str(code))),
    ),
)

_PROJECT_FILE_CODES = (200, 400, 403, 404, 429, 500, 10002, 10031)
_PROJECT_FILE_TYPES = {
    "projectId": str,
    "fileId": str,
    "taskId": str,
    "createTime": str,
    "fileType": int,
    "status": int,
    "fileSize": int,
    "duration": int,
    "storageType": int,
}

_OP_PROJECT_FILES = Operation(
    "cloud_recording_v1.project_files",
    "GET",
    "/api/open/cloud/v1/files",
    regions=frozenset({_DOMESTIC}),
    token_location="query",
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(v for code in _PROJECT_FILE_CODES for v in (code, str(code))),
    ),
)

_PROJECT_LIST_CODES = (200, 400, 403, 404, 500, 10002, 10031)
_PROJECT_ROW_TYPES = {
    "projectId": str,
    "projectName": str,
    "expireDays": int,
    "totalSize": int,
    "providerType": str,
    "storageType": int,
    "totalFlow": int,
    "flowLimit": int,
    "permission": int,
    "projectTag": str,
    "createTime": str,
    "updateTime": str,
    "bizType": str,
}

_OP_PROJECTS = Operation(
    "cloud_recording_v1.projects",
    "GET",
    "/api/open/cloud/v1/projects",
    regions=frozenset({_DOMESTIC}),
    token_location="query",
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(v for code in _PROJECT_LIST_CODES for v in (code, str(code))),
    ),
)

_TASK_LIST_CODES = (200, 400, 403, 500, 10002, 10031)
_TASK_ROW_TYPES = {
    "taskId": str,
    "projectId": str,
    "deviceSerial": str,
    "taskType": int,
    "taskStatus": int,
    "taskDetail": dict,
    "fileNum": int,
    "totalSize": int,
    "startTime": str,
    "endTime": str,
    "createTime": str,
}

_OP_TASKS = Operation(
    "cloud_recording_v1.tasks",
    "GET",
    "/api/v3/open/cloud/tasks",
    regions=frozenset({_DOMESTIC}),
    token_location="query",
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(v for code in _TASK_LIST_CODES for v in (code, str(code))),
    ),
)

_OP_FILE_INFO = Operation(
    "cloud_recording_v1.file_info",
    "GET",
    "/api/open/cloud/v1/file",
    token_location="query",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)
_OP_DOWNLOAD_URLS = Operation(
    "cloud_recording_v1.download_urls",
    "GET",
    "/api/service/cloudrecord/file/official/download",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)


_OP_PROJECT = Operation(
    "cloud_recording_v1.project",
    "GET",
    "/api/open/cloud/v1/project/{projectId}",
    regions=frozenset({_DOMESTIC}),
    token_location="query",
    read_only=True,
    policy=replace(
        _POLICY_META,
        redact_device_add_details=True,
        redact_media_urls=True,
        exposed_business_codes=tuple(v for code in _PROJECT_LIST_CODES for v in (code, str(code))),
    ),
)


@dataclass(frozen=True)
class _Request:
    operation: Operation
    params: dict[str, str | int]
    data: dict[str, str | int] | None
    headers: dict[str, str]


def _utf8(name: str, value: str) -> str:
    # SDK defensive choice for this batch: every caller-supplied text field must
    # be UTF-8 encodable. A lone surrogate or non-encodable str is rejected
    # before the wire; the message carries the rule only, not the value.
    # The raise stays outside the ``except`` block so the rejection's
    # ``__context__`` never retains the ``UnicodeEncodeError`` — that
    # exception's ``.object`` would otherwise recover the caller's raw input.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        encodable = False
    else:
        encodable = True
    if not encodable:
        raise ValueError(f"{name} must be UTF-8 encodable")
    return value


def _text_field(name: str, value: object) -> str:
    """Non-blank UTF-8 string, returned verbatim (no strip/normalize)."""
    return _utf8(name, _text(name, value))


def _require_domestic(scope: str, method: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError(f"cloud_recording_v1.{method} is only documented for the domestic region")


def _is_ascii_word(char: str) -> bool:
    return "a" <= char <= "z" or "A" <= char <= "Z" or "0" <= char <= "9" or char == "_"


def _project_id_strict(name: str, value: object) -> str:
    """ASCII ``[A-Za-z0-9_]{1,32}`` project id for unfreeze/download (docs 5341/1377).

    The official ``\\w`` engine is unspecified, so ASCII is an SDK choice and is
    disclosed as possibly rejecting service-supported Unicode project ids.
    """
    text = _text_field(name, value)
    if len(text) > 32 or not all(_is_ascii_word(c) for c in text):
        raise ValueError(f"{name} must be 1..32 ASCII letters, digits or underscores")
    return text


def _project_id_loose(name: str, value: object) -> str:
    """Non-blank UTF-8 project id for file_info (doc 1375); no length/charset cap.

    The 1375 source declares only a required String ``projectId`` and no
    length or character-set constraint, so the 32-char ASCII bound used by the
    sibling operations is deliberately not applied here.
    """
    return _text_field(name, value)


def _file_id(value: object) -> str:
    """Shared 1.0 file id: ordinary word ``1..32``, or ``E1``-prefixed up to 128.

    ``E1`` itself is allowed (no minimum-33 rule); the ``E1`` branch may carry
    ``$`` and other opaque separators and is not subjected to the ordinary
    word-character set. The 128 cap comes from docs 1380/1377. The value is
    kept opaque — never normalized, stripped, or re-encoded.
    """
    text = _text_field("file_id", value)
    if text.startswith("E1"):
        if len(text) > 128:
            raise ValueError("file_id must be at most 128 characters")
        return text
    if len(text) > 32 or not all(_is_ascii_word(c) for c in text):
        raise ValueError(
            "file_id must be 1..32 ASCII letters, digits or underscores, "
            "or an E1-prefixed id up to 128 characters"
        )
    return text


def _content_type(value: object) -> str:
    """Open ``contentType`` query value (doc 1377): non-blank UTF-8, sent verbatim.

    The five listed values are not an explicitly closed set, so this SDK choice
    keeps the parameter an open string; it is a query parameter, never an HTTP
    request header and never a forced default.
    """
    return _text_field("content_type", value)


def _expire_seconds(value: object) -> int:
    """``expireSeconds`` ``60..604800`` inclusive (doc 1377); bool rejected."""
    seconds = _integer("expire_seconds", value)
    if not _DOWNLOAD_EXPIRE_MIN <= seconds <= _DOWNLOAD_EXPIRE_MAX:
        raise ValueError("expire_seconds must be within 60..604800")
    return seconds


def _yyyymmddhhmmss_text(name: str, value: object) -> str:
    """Validated 14-digit ASCII ``yyyyMMddHHmmss`` text for docs 1374/1371.

    The format ``String`` carries no timezone; the SDK only checks that the
    value parses as a real calendar date+time (no local tz is attached) and
    returns the caller's digits verbatim for the wire. Rejected values raise
    ``ValueError`` carrying the rule only, never the value.
    """
    text = _text_field(name, value)
    if len(text) != 14 or not text.isascii() or not text.isdigit():
        raise ValueError(f"{name} must be a 14-digit yyyyMMddHHmmss ASCII timestamp")
    # The second raise stays outside the ``except`` block: ``strptime``'s own
    # ``ValueError`` can embed the rejected digits in its args, so retaining it
    # on ``__context__`` would leak the caller's input back out.
    try:
        datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        valid = False
    else:
        valid = True
    if not valid:
        raise ValueError(f"{name} must be a valid yyyyMMddHHmmss calendar time")
    return text


def _project_files_request(
    scope: str,
    project_id: object,
    *,
    start_time: object,
    end_time: object,
    page_number: object,
    page_size: object,
) -> _Request:
    _require_domestic(scope, "project_files")
    params: dict[str, str | int] = {
        "projectId": _project_id_loose("project_id", project_id),
    }
    start = end = None
    if start_time is not None:
        start_text = _yyyymmddhhmmss_text("start_time", start_time)
        start = datetime.strptime(start_text, "%Y%m%d%H%M%S")
        params["startTime"] = start_text
    if end_time is not None:
        end_text = _yyyymmddhhmmss_text("end_time", end_time)
        end = datetime.strptime(end_text, "%Y%m%d%H%M%S")
        params["endTime"] = end_text
    # Doc 1374 FAQ: start and end must be the same day. The refusal only fires
    # when both are explicitly given and their calendar dates differ; a single
    # bound or the platform default is never locally completed or compared.
    if start is not None and end is not None and start.date() != end.date():
        raise ValueError("start_time and end_time must be on the same calendar day")
    if page_number is not None:
        number = _integer("page_number", page_number)
        if number < 0:
            raise ValueError("page_number must be >= 0")
        params["pageNumber"] = number
    if page_size is not None:
        size = _integer("page_size", page_size)
        if not 1 <= size <= 20:
            raise ValueError("page_size must be in range 1..20")
        params["pageSize"] = size
    return _Request(_OP_PROJECT_FILES, params, None, {})


def _project_file_row_valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for field, expected in _PROJECT_FILE_TYPES.items():
        value = row.get(field)
        if value is None:
            continue
        if type(value) is not expected:
            return False
    return True


def _check_project_files(response: ApiResponse) -> ApiResponse:
    rows = response.data
    if not isinstance(rows, list) or not all(_project_file_row_valid(row) for row in rows):
        raise ProtocolError(
            "unexpected cloud_recording_v1.project_files response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _projects_request(
    scope: str,
    *,
    page_number: object,
    page_size: object,
) -> _Request:
    _require_domestic(scope, "projects")
    params: dict[str, str | int] = {}
    if page_number is not None:
        number = _integer("page_number", page_number)
        if number < 0:
            raise ValueError("page_number must be >= 0")
        params["pageNumber"] = number
    if page_size is not None:
        size = _integer("page_size", page_size)
        if not 1 <= size <= 20:
            raise ValueError("page_size must be in range 1..20")
        params["pageSize"] = size
    return _Request(_OP_PROJECTS, params, None, {})


def _project_row_valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for field, expected in _PROJECT_ROW_TYPES.items():
        value = row.get(field)
        if value is None:
            continue
        if type(value) is not expected:
            return False
    return True


def _check_projects(response: ApiResponse) -> ApiResponse:
    rows = response.data
    if not isinstance(rows, list) or not all(_project_row_valid(row) for row in rows):
        raise ProtocolError(
            "unexpected cloud_recording_v1.projects response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _project_request(scope: str, project_id: object) -> str:
    """Validate the single-project GET inputs (doc 1363); ``projectId`` binds the path segment."""
    _require_domestic(scope, "project")
    return _project_id_strict("project_id", project_id)


def _check_project(response: ApiResponse) -> ApiResponse:
    if not _project_row_valid(response.data):
        raise ProtocolError(
            "unexpected cloud_recording_v1.project response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _tasks_request(
    scope: str,
    *,
    start_time: object,
    end_time: object,
    page_number: object,
    page_size: object,
) -> _Request:
    _require_domestic(scope, "tasks")
    params: dict[str, str | int] = {}
    start = end = None
    if start_time is not None:
        start_text = _yyyymmddhhmmss_text("start_time", start_time)
        start = datetime.strptime(start_text, "%Y%m%d%H%M%S")
        params["startTime"] = start_text
    if end_time is not None:
        end_text = _yyyymmddhhmmss_text("end_time", end_time)
        end = datetime.strptime(end_text, "%Y%m%d%H%M%S")
        params["endTime"] = end_text
    # Doc 1371 FAQ: start and end must be the same day — same refusal rule as
    # project_files (doc 1374): only fires when both bounds are explicitly
    # given and their calendar dates differ.
    if start is not None and end is not None and start.date() != end.date():
        raise ValueError("start_time and end_time must be on the same calendar day")
    if page_number is not None:
        number = _integer("page_number", page_number)
        if number < 0:
            raise ValueError("page_number must be >= 0")
        params["pageNumber"] = number
    if page_size is not None:
        size = _integer("page_size", page_size)
        # The 1371 source documents only "default 10" — no upper bound is
        # proven, so the SDK validates type and >=1 only and does NOT borrow
        # the 20/50 caps from the sibling endpoints.
        if size < 1:
            raise ValueError("page_size must be >= 1")
        params["pageSize"] = size
    return _Request(_OP_TASKS, params, None, {})


def _task_row_valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for field, expected in _TASK_ROW_TYPES.items():
        value = row.get(field)
        if value is None:
            continue
        if type(value) is not expected:
            return False
    return True


def _check_tasks(response: ApiResponse) -> ApiResponse:
    rows = response.data
    if not isinstance(rows, list) or not all(_task_row_valid(row) for row in rows):
        raise ProtocolError(
            "unexpected cloud_recording_v1.tasks response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _task_files_request(
    scope: str,
    task_id: str,
    *,
    page_number: int,
    page_size: int,
    sort_field: str | None,
    sort_rule: str | None,
    has_url: bool | None,
) -> _Request:
    _require_domestic(scope, "task_files")
    task_id = _check_text(task_id, "task_id")
    page_number = _integer("page_number", page_number)
    page_size = _integer("page_size", page_size)
    if page_number < 0:
        raise ValueError("page_number must be >= 0")
    if not 1 <= page_size <= 50:
        raise ValueError("page_size must be in range 1..50")
    params: dict[str, str | int] = {
        "taskId": task_id,
        "pageNumber": page_number,
        "pageSize": page_size,
    }
    if sort_field is not None:
        params["sortField"] = _check_text(sort_field, "sort_field")
    if sort_rule is not None:
        if type(sort_rule) is not str or sort_rule not in ("asc", "desc"):
            raise ValueError("sort_rule must be 'asc' or 'desc'")
        params["sortRule"] = sort_rule
    if has_url is not None:
        if type(has_url) is not bool:
            raise ValueError("has_url must be a bool")
        params["hasUrl"] = "true" if has_url else "false"
    return _Request(_OP_TASK_FILES, params, None, {})


def _task_file_row_valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for field, expected in _TASK_FILE_TYPES.items():
        value = row.get(field)
        if value is None:
            continue
        if type(value) is not expected:
            return False
    return True


def _check_task_files(response: ApiResponse) -> ApiResponse:
    rows = response.data
    if not isinstance(rows, list) or not all(_task_file_row_valid(row) for row in rows):
        raise ProtocolError(
            "unexpected cloud_recording_v1.task_files response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _unfreeze_request(scope: str, project_id: object, file_id: object) -> _Request:
    _require_domestic(scope, "unfreeze_file")
    data: dict[str, str | int] = {
        "projectId": _project_id_strict("project_id", project_id),
        "fileId": _file_id(file_id),
    }
    return _Request(_OP_UNFREEZE, {}, data, {"Content-Type": _FORM_CONTENT_TYPE})


def _file_info_request(scope: str, project_id: object, file_id: object) -> _Request:
    _require_domestic(scope, "file_info")
    params: dict[str, str | int] = {
        "projectId": _project_id_loose("project_id", project_id),
        "fileId": _file_id(file_id),
    }
    return _Request(_OP_FILE_INFO, params, None, {})


def _download_urls_request(
    scope: str,
    project_id: object,
    file_id: object,
    *,
    content_type: object,
    expire_seconds: object,
) -> _Request:
    _require_domestic(scope, "download_urls")
    params: dict[str, str | int] = {
        "projectId": _project_id_strict("project_id", project_id),
        "fileId": _file_id(file_id),
    }
    if content_type is not None:
        params["contentType"] = _content_type(content_type)
    if expire_seconds is not None:
        params["expireSeconds"] = _expire_seconds(expire_seconds)
    return _Request(_OP_DOWNLOAD_URLS, params, None, {})


class CloudRecordingV1:
    """Synchronous facade over the reviewed cloud recording 1.0 file endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def _call(self, request: _Request) -> ApiResponse:
        return self._client.request(
            request.operation,
            params=request.params,
            data=request.data,
            headers=request.headers,
        )

    def unfreeze_file(self, project_id: str, file_id: str) -> ApiResponse:
        """Trigger archive retrieval for one file; success is only the trigger (docs 5341/1380)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.unfreeze_file", mode="sync"
        ):
            return self._call(_unfreeze_request(self._client.region.scope, project_id, file_id))

    def task_files(
        self,
        task_id: str,
        *,
        page_number: int,
        page_size: int,
        sort_field: str | None = None,
        sort_rule: str | None = None,
        has_url: bool | None = None,
    ) -> ApiResponse:
        """Read one page of files produced by an existing domestic task."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.task_files", mode="sync"
        ):
            request = _task_files_request(
                self._client.region.scope,
                task_id,
                page_number=page_number,
                page_size=page_size,
                sort_field=sort_field,
                sort_rule=sort_rule,
                has_url=has_url,
            )
            response = self._call(request)
            return _check_task_files(response)

    def file_info(self, project_id: str, file_id: str) -> ApiResponse:
        """Query one file's details; the raw ``data``/unknown fields pass through (doc 1375)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.file_info", mode="sync"
        ):
            return self._call(_file_info_request(self._client.region.scope, project_id, file_id))

    def project_files(
        self,
        project_id: str,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        page_number: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """Read one page of one domestic project's file metadata (doc 1374).

        ``start_time``/``end_time`` take ``yyyyMMddHHmmss`` digits; when both
        are given they must fall on the same calendar day, matching the source
        FAQ. The listing carries no download URLs; ``data``/``raw`` pass
        through untouched and nothing here downloads media.
        """
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.project_files", mode="sync"
        ):
            request = _project_files_request(
                self._client.region.scope,
                project_id,
                start_time=start_time,
                end_time=end_time,
                page_number=page_number,
                page_size=page_size,
            )
            response = self._call(request)
            return _check_project_files(response)

    def project(self, project_id: str) -> ApiResponse:
        """Read one domestic cloud-recording 1.0 project by id (doc 1363).

        ``project_id`` uses the strict 1..32 ASCII ``[A-Za-z0-9_]`` form shared
        with the other project endpoints and is bound into the URL path. The
        returned ``data`` object passes through untouched, including null
        optional fields and unknown extensions.
        """
        with _diagnostic_scope(self._client, entrypoint="cloud_recording_v1.project", mode="sync"):
            bound = _project_request(self._client.region.scope, project_id)
            response = self._client.request(
                _OP_PROJECT,
                path_params={"projectId": bound},
            )
            return _check_project(response)

    def projects(
        self,
        *,
        page_number: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """Read one page of the domestic cloud-recording 1.0 project list (doc 1364).

        Both paging fields are optional: ``page_number`` starts at 0,
        ``page_size`` is 1..20 (the platform defaults to 0/10). ``data`` is a
        project-metadata array and may be empty; it passes through untouched.
        """
        with _diagnostic_scope(self._client, entrypoint="cloud_recording_v1.projects", mode="sync"):
            request = _projects_request(
                self._client.region.scope,
                page_number=page_number,
                page_size=page_size,
            )
            response = self._call(request)
            return _check_projects(response)

    def tasks(
        self,
        *,
        page_number: int | None = None,
        page_size: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> ApiResponse:
        """Read one page of the domestic cloud-recording 1.0 task list (doc 1371).

        ``start_time``/``end_time`` take ``yyyyMMddHHmmss`` digits; when both
        are given they must fall on the same calendar day, matching the source
        FAQ. ``page_size`` has no proven upper bound in the source — only the
        type and >=1 are checked. ``data`` passes through untouched.
        """
        with _diagnostic_scope(self._client, entrypoint="cloud_recording_v1.tasks", mode="sync"):
            request = _tasks_request(
                self._client.region.scope,
                start_time=start_time,
                end_time=end_time,
                page_number=page_number,
                page_size=page_size,
            )
            response = self._call(request)
            return _check_tasks(response)

    def download_urls(
        self,
        project_id: str,
        file_id: str,
        *,
        content_type: str | None = None,
        expire_seconds: int | None = None,
    ) -> ApiResponse:
        """Fetch signed download/playback URLs; no media download occurs (doc 1377)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.download_urls", mode="sync"
        ):
            request = _download_urls_request(
                self._client.region.scope,
                project_id,
                file_id,
                content_type=content_type,
                expire_seconds=expire_seconds,
            )
            return self._call(request)


class AsyncCloudRecordingV1:
    """Asynchronous facade over the reviewed cloud recording 1.0 file endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def _call(self, request: _Request) -> ApiResponse:
        return await self._client.request(
            request.operation,
            params=request.params,
            data=request.data,
            headers=request.headers,
        )

    async def unfreeze_file(self, project_id: str, file_id: str) -> ApiResponse:
        """Trigger archive retrieval for one file; success is only the trigger (docs 5341/1380)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.unfreeze_file", mode="async"
        ):
            return await self._call(
                _unfreeze_request(self._client.region.scope, project_id, file_id)
            )

    async def task_files(
        self,
        task_id: str,
        *,
        page_number: int,
        page_size: int,
        sort_field: str | None = None,
        sort_rule: str | None = None,
        has_url: bool | None = None,
    ) -> ApiResponse:
        """Read one page of files produced by an existing domestic task."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.task_files", mode="async"
        ):
            request = _task_files_request(
                self._client.region.scope,
                task_id,
                page_number=page_number,
                page_size=page_size,
                sort_field=sort_field,
                sort_rule=sort_rule,
                has_url=has_url,
            )
            response = await self._call(request)
            return _check_task_files(response)

    async def file_info(self, project_id: str, file_id: str) -> ApiResponse:
        """Query one file's details; the raw ``data``/unknown fields pass through (doc 1375)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.file_info", mode="async"
        ):
            return await self._call(
                _file_info_request(self._client.region.scope, project_id, file_id)
            )

    async def project_files(
        self,
        project_id: str,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        page_number: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """Read one page of one domestic project's file metadata (doc 1374).

        Same rules as the synchronous counterpart: both explicit time bounds
        must share one calendar day; omitted optional fields are not
        serialized; the response passes through without media access.
        """
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.project_files", mode="async"
        ):
            request = _project_files_request(
                self._client.region.scope,
                project_id,
                start_time=start_time,
                end_time=end_time,
                page_number=page_number,
                page_size=page_size,
            )
            response = await self._call(request)
            return _check_project_files(response)

    async def project(self, project_id: str) -> ApiResponse:
        """Read one domestic cloud-recording 1.0 project by id (doc 1363).

        Same rules as the synchronous counterpart: the id is the strict 1..32
        ASCII word form bound into the URL path; ``data``/``raw`` pass
        through untouched.
        """
        with _diagnostic_scope(self._client, entrypoint="cloud_recording_v1.project", mode="async"):
            bound = _project_request(self._client.region.scope, project_id)
            response = await self._client.request(
                _OP_PROJECT,
                path_params={"projectId": bound},
            )
            return _check_project(response)

    async def projects(
        self,
        *,
        page_number: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """Read one page of the domestic cloud-recording 1.0 project list (doc 1364).

        Same rules as the synchronous counterpart: omitted optional fields are
        not serialized; the response passes through without media access.
        """
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.projects", mode="async"
        ):
            request = _projects_request(
                self._client.region.scope,
                page_number=page_number,
                page_size=page_size,
            )
            response = await self._call(request)
            return _check_projects(response)

    async def tasks(
        self,
        *,
        page_number: int | None = None,
        page_size: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> ApiResponse:
        """Read one page of the domestic cloud-recording 1.0 task list (doc 1371).

        Same rules as the synchronous counterpart: both explicit time bounds
        must share one calendar day; omitted optional fields are not
        serialized; the response passes through without media access.
        """
        with _diagnostic_scope(self._client, entrypoint="cloud_recording_v1.tasks", mode="async"):
            request = _tasks_request(
                self._client.region.scope,
                start_time=start_time,
                end_time=end_time,
                page_number=page_number,
                page_size=page_size,
            )
            response = await self._call(request)
            return _check_tasks(response)

    async def download_urls(
        self,
        project_id: str,
        file_id: str,
        *,
        content_type: str | None = None,
        expire_seconds: int | None = None,
    ) -> ApiResponse:
        """Fetch signed download/playback URLs; no media download occurs (doc 1377)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.download_urls", mode="async"
        ):
            request = _download_urls_request(
                self._client.region.scope,
                project_id,
                file_id,
                content_type=content_type,
                expire_seconds=expire_seconds,
            )
            return await self._call(request)
