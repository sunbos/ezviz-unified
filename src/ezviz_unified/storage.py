"""Unified storage space and file endpoints for the EZVIZ client (sync and async).

All operations are documented for the domestic region only. Every method
returns the ``ApiResponse`` untouched: the SDK performs no automatic
pagination, file recovery, media download, polling, or pre/post resource
management. Deleting a space removes every file it contains and cannot be
undone; the SDK never cleans up or stops upstream resources on the caller's
behalf. Write operations are never auto-replayed even where the source
documentation marks them idempotent. Cross-product endpoints (docs
5341/5342), legacy 1.0 archive retrieval, and fileNodeId/legacy fileId
mapping are intentionally out of scope.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import (
    StorageFileQuery,
    _biz_type,
    _download_query,
    _integer,
    _name,
    _node_ids,
    _retention,
    _space_fields,
    _spaces_query,
)

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

_POLICY_META = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)

_OP_CREATE_SPACE = Operation(
    "storage.create_space",
    "POST",
    "/api/service/open/storage/engine/space",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_UPDATE_SPACE = Operation(
    "storage.update_space",
    "PUT",
    "/api/service/open/storage/engine/space",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_DELETE_SPACE = Operation(
    "storage.delete_space",
    "DELETE",
    "/api/service/open/storage/engine/space",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_SPACES = Operation(
    "storage.spaces",
    "GET",
    "/api/service/open/storage/engine/space/listById",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)
_OP_SET_FILE_RETENTION = Operation(
    "storage.set_file_retention",
    "POST",
    "/api/service/open/storage/engine/file/batchExpire",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=False,
    policy=_POLICY_META,
)
_OP_SEARCH_FILES = Operation(
    "storage.search_files",
    "POST",
    "/api/service/open/storage/engine/file/listById",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_META,
)
_OP_DOWNLOAD_URLS = Operation(
    "storage.download_urls",
    "GET",
    "/api/service/open/storage/engine/file/downloadurls",
    token_location="query",
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


def _require_domestic(scope: str, method: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError(f"storage.{method} is only documented for the domestic region")


def _create_space_request(
    scope: str,
    name: object,
    *,
    biz_type: object,
    expire_days: int | None,
    storage_type: int | None,
    flow_limit: int | None,
    permission: int | None,
) -> _Request:
    _require_domestic(scope, "create_space")
    data = _space_fields(
        name=_name(name),
        expire_days=expire_days,
        storage_type=storage_type,
        flow_limit=flow_limit,
        permission=permission,
    )
    data["bizType"] = _biz_type(biz_type)
    return _Request(_OP_CREATE_SPACE, {}, data, {"Content-Type": _FORM_CONTENT_TYPE})


def _update_space_request(
    scope: str,
    space_id: object,
    *,
    name: str | None,
    expire_days: int | None,
    storage_type: int | None,
    flow_limit: int | None,
    permission: int | None,
) -> _Request:
    _require_domestic(scope, "update_space")
    data = _space_fields(
        name=name,
        expire_days=expire_days,
        storage_type=storage_type,
        flow_limit=flow_limit,
        permission=permission,
    )
    if not data:
        raise ValueError("update_space requires at least one explicit change")
    data["spaceId"] = _integer("space_id", space_id)
    return _Request(_OP_UPDATE_SPACE, {}, data, {"Content-Type": _FORM_CONTENT_TYPE})


def _delete_space_request(scope: str, space_id: object) -> _Request:
    _require_domestic(scope, "delete_space")
    data: dict[str, str | int] = {"spaceId": _integer("space_id", space_id)}
    return _Request(_OP_DELETE_SPACE, {}, data, {"Content-Type": _FORM_CONTENT_TYPE})


def _spaces_request(
    scope: str,
    *,
    start_time: datetime,
    end_time: datetime,
    last_space_id: int | None,
    page_size: int | None,
    biz_type: str | None,
    sort_rule: str | None,
    sort_field: str | None,
) -> _Request:
    _require_domestic(scope, "spaces")
    params = _spaces_query(
        start_time=start_time,
        end_time=end_time,
        last_space_id=last_space_id,
        page_size=page_size,
        biz_type=biz_type,
        sort_rule=sort_rule,
        sort_field=sort_field,
    )
    return _Request(_OP_SPACES, params, None, {})


def _set_file_retention_request(
    scope: str, file_node_ids: Sequence[str], expire_days: object
) -> _Request:
    _require_domestic(scope, "set_file_retention")
    data: dict[str, str | int] = {
        "fileNodeIds": _node_ids(file_node_ids),
        "expireDays": _retention(expire_days),
    }
    return _Request(_OP_SET_FILE_RETENTION, {}, data, {"Content-Type": _FORM_CONTENT_TYPE})


def _search_files_request(scope: str, query: object) -> _Request:
    _require_domestic(scope, "search_files")
    if not isinstance(query, StorageFileQuery):
        raise ValueError("query must be a StorageFileQuery")
    return _Request(_OP_SEARCH_FILES, {}, query.to_form(), {"Content-Type": _FORM_CONTENT_TYPE})


def _download_urls_request(
    scope: str, file_node_ids: Sequence[str], *, expire_seconds: int | None
) -> _Request:
    _require_domestic(scope, "download_urls")
    params = _download_query(file_node_ids, expire_seconds=expire_seconds)
    return _Request(_OP_DOWNLOAD_URLS, params, None, {})


class Storage:
    """Synchronous facade over the reviewed unified storage endpoints."""

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

    def create_space(
        self,
        name: str,
        *,
        biz_type: str,
        expire_days: int | None = None,
        storage_type: int | None = None,
        flow_limit: int | None = None,
        permission: int | None = None,
    ) -> ApiResponse:
        """Create a unified storage space for one business type (doc 5236)."""
        with _diagnostic_scope(self._client, entrypoint="storage.create_space", mode="sync"):
            request = _create_space_request(
                self._client.region.scope,
                name,
                biz_type=biz_type,
                expire_days=expire_days,
                storage_type=storage_type,
                flow_limit=flow_limit,
                permission=permission,
            )
            return self._call(request)

    def update_space(
        self,
        space_id: int,
        *,
        name: str | None = None,
        expire_days: int | None = None,
        storage_type: int | None = None,
        flow_limit: int | None = None,
        permission: int | None = None,
    ) -> ApiResponse:
        """Update fields of an existing space; at least one change is required (doc 5237)."""
        with _diagnostic_scope(self._client, entrypoint="storage.update_space", mode="sync"):
            request = _update_space_request(
                self._client.region.scope,
                space_id,
                name=name,
                expire_days=expire_days,
                storage_type=storage_type,
                flow_limit=flow_limit,
                permission=permission,
            )
            return self._call(request)

    def delete_space(self, space_id: int) -> ApiResponse:
        """Delete a space together with all its files; irreversible (doc 5238)."""
        with _diagnostic_scope(self._client, entrypoint="storage.delete_space", mode="sync"):
            return self._call(_delete_space_request(self._client.region.scope, space_id))

    def spaces(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        last_space_id: int | None = None,
        page_size: int | None = None,
        biz_type: str | None = None,
        sort_rule: str | None = None,
        sort_field: str | None = None,
    ) -> ApiResponse:
        """Page through spaces by creation time; cursors pass through verbatim (doc 5230)."""
        with _diagnostic_scope(self._client, entrypoint="storage.spaces", mode="sync"):
            request = _spaces_request(
                self._client.region.scope,
                start_time=start_time,
                end_time=end_time,
                last_space_id=last_space_id,
                page_size=page_size,
                biz_type=biz_type,
                sort_rule=sort_rule,
                sort_field=sort_field,
            )
            return self._call(request)

    def set_file_retention(self, file_node_ids: Sequence[str], expire_days: int) -> ApiResponse:
        """Batch-update the retention days of existing file nodes (doc 5232)."""
        with _diagnostic_scope(self._client, entrypoint="storage.set_file_retention", mode="sync"):
            request = _set_file_retention_request(
                self._client.region.scope, file_node_ids, expire_days
            )
            return self._call(request)

    def search_files(self, query: StorageFileQuery) -> ApiResponse:
        """Search file metadata with a StorageFileQuery selector set (doc 5233)."""
        with _diagnostic_scope(self._client, entrypoint="storage.search_files", mode="sync"):
            return self._call(_search_files_request(self._client.region.scope, query))

    def download_urls(
        self, file_node_ids: Sequence[str], *, expire_seconds: int | None = None
    ) -> ApiResponse:
        """Fetch signed download URLs for file nodes; no media download occurs (doc 5234)."""
        with _diagnostic_scope(self._client, entrypoint="storage.download_urls", mode="sync"):
            request = _download_urls_request(
                self._client.region.scope, file_node_ids, expire_seconds=expire_seconds
            )
            return self._call(request)


class AsyncStorage:
    """Asynchronous facade over the reviewed unified storage endpoints."""

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

    async def create_space(
        self,
        name: str,
        *,
        biz_type: str,
        expire_days: int | None = None,
        storage_type: int | None = None,
        flow_limit: int | None = None,
        permission: int | None = None,
    ) -> ApiResponse:
        """Create a unified storage space for one business type (doc 5236)."""
        with _diagnostic_scope(self._client, entrypoint="storage.create_space", mode="async"):
            request = _create_space_request(
                self._client.region.scope,
                name,
                biz_type=biz_type,
                expire_days=expire_days,
                storage_type=storage_type,
                flow_limit=flow_limit,
                permission=permission,
            )
            return await self._call(request)

    async def update_space(
        self,
        space_id: int,
        *,
        name: str | None = None,
        expire_days: int | None = None,
        storage_type: int | None = None,
        flow_limit: int | None = None,
        permission: int | None = None,
    ) -> ApiResponse:
        """Update fields of an existing space; at least one change is required (doc 5237)."""
        with _diagnostic_scope(self._client, entrypoint="storage.update_space", mode="async"):
            request = _update_space_request(
                self._client.region.scope,
                space_id,
                name=name,
                expire_days=expire_days,
                storage_type=storage_type,
                flow_limit=flow_limit,
                permission=permission,
            )
            return await self._call(request)

    async def delete_space(self, space_id: int) -> ApiResponse:
        """Delete a space together with all its files; irreversible (doc 5238)."""
        with _diagnostic_scope(self._client, entrypoint="storage.delete_space", mode="async"):
            return await self._call(_delete_space_request(self._client.region.scope, space_id))

    async def spaces(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        last_space_id: int | None = None,
        page_size: int | None = None,
        biz_type: str | None = None,
        sort_rule: str | None = None,
        sort_field: str | None = None,
    ) -> ApiResponse:
        """Page through spaces by creation time; cursors pass through verbatim (doc 5230)."""
        with _diagnostic_scope(self._client, entrypoint="storage.spaces", mode="async"):
            request = _spaces_request(
                self._client.region.scope,
                start_time=start_time,
                end_time=end_time,
                last_space_id=last_space_id,
                page_size=page_size,
                biz_type=biz_type,
                sort_rule=sort_rule,
                sort_field=sort_field,
            )
            return await self._call(request)

    async def set_file_retention(
        self, file_node_ids: Sequence[str], expire_days: int
    ) -> ApiResponse:
        """Batch-update the retention days of existing file nodes (doc 5232)."""
        with _diagnostic_scope(
            self._client,
            entrypoint="storage.set_file_retention",
            mode="async",
        ):
            request = _set_file_retention_request(
                self._client.region.scope, file_node_ids, expire_days
            )
            return await self._call(request)

    async def search_files(self, query: StorageFileQuery) -> ApiResponse:
        """Search file metadata with a StorageFileQuery selector set (doc 5233)."""
        with _diagnostic_scope(self._client, entrypoint="storage.search_files", mode="async"):
            return await self._call(_search_files_request(self._client.region.scope, query))

    async def download_urls(
        self, file_node_ids: Sequence[str], *, expire_seconds: int | None = None
    ) -> ApiResponse:
        """Fetch signed download URLs for file nodes; no media download occurs (doc 5234)."""
        with _diagnostic_scope(self._client, entrypoint="storage.download_urls", mode="async"):
            request = _download_urls_request(
                self._client.region.scope, file_node_ids, expire_seconds=expire_seconds
            )
            return await self._call(request)
