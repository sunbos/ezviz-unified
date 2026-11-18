"""Cloud VOD metadata queries for the EZVIZ client (sync and async).

Documented for the domestic region only (docs 5342/4187). ``files`` performs
one logical read-only query per call; under the existing invalid-token policy
the ``GET`` may be replayed at most once. The SDK never auto-paginates,
batches, polls, downloads media, or manages upstream resources. At least one selector is
required by the service; every supplied selector is sent verbatim — the
documented ``spaceId`` priority is resolved server-side, not by the SDK.

``file_node_ids`` accepts a non-string ``Sequence`` snapshotted to a tuple
before authentication; order and duplicates are preserved and joined into the
documented CSV wire form. Per-element commas are refused because this CSV has
no escaping (SDK design choice). ``space_id`` must be an ``int`` and rejects
``bool``; no undeclared positive-only bound is invented. ``space_name`` must
be a non-blank UTF-8-encodable string. All validation happens before any
authentication or network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
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

_OP_FILES = Operation(
    "vod.files",
    "GET",
    "/api/service/open/vod/files",
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


def _require_domestic(scope: str, method: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError(f"vod.{method} is only documented for the domestic region")


def _utf8(name: str, value: str) -> str:
    """SDK defensive choice: user text must be UTF-8 encodable for the wire."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be encodable as UTF-8") from None
    return value


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


class Vod:
    """Synchronous facade over the reviewed VOD file-metadata endpoint."""

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


class AsyncVod:
    """Asynchronous facade over the reviewed VOD file-metadata endpoint."""

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
