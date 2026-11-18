"""Cloud Recording 1.0 file operations for the unified client (sync and async).

All three operations are documented for the domestic region only and share the
``meta.code``/``meta.message``/``data`` response policy with ``success_values=(200,)``.
Every method returns the ``ApiResponse`` untouched: the SDK performs no
automatic polling, no unfreeze/download orchestration, and no media download.
``unfreeze_file`` is a write operation and is never auto-replayed even though
the source marks it idempotent; the two read operations follow the existing
single invalid-token refresh-and-replay rule. The three ``cloud_recording_v1``
methods are independent file operations (docs 5341/1380, 1375, 1377). No 1.0
``projectId``/``fileId`` is ever mapped onto unified-storage ``fileNodeId``
semantics.

Validation rules that are SDK choices rather than explicit official facts are
marked inline. All user text must be UTF-8 encodable and non-blank; error
messages carry the parameter name and rule only, never the rejected value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
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
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be UTF-8 encodable") from None
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

    def file_info(self, project_id: str, file_id: str) -> ApiResponse:
        """Query one file's details; the raw ``data``/unknown fields pass through (doc 1375)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.file_info", mode="sync"
        ):
            return self._call(_file_info_request(self._client.region.scope, project_id, file_id))

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

    async def file_info(self, project_id: str, file_id: str) -> ApiResponse:
        """Query one file's details; the raw ``data``/unknown fields pass through (doc 1375)."""
        with _diagnostic_scope(
            self._client, entrypoint="cloud_recording_v1.file_info", mode="async"
        ):
            return await self._call(
                _file_info_request(self._client.region.scope, project_id, file_id)
            )

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
