"""Domestic live concurrency limit queries and writes.

All three entrypoints share ``/api/v3/mall/online/user/limit/live`` and are
domestic-only; the access token travels only in the header. ``get`` is a
read-only GET that carries ``liveType`` solely in the query string and never
a body, while ``set`` (POST) and ``delete`` (DELETE) send an explicit
``application/x-www-form-urlencoded`` body — the SDK never substitutes a
query string or an alternate HTTP method for DELETE. ``liveType`` is the
exact uppercase string ``VTDU`` (jsdecode / private stream) or ``RTMP``
(HLS / RTMP / FLV); ``limitType`` is the integer 0 (per channel) or 1
(per user) and ``limitNumber`` is any integer the interpreter can encode,
with no SDK-invented bounds. A successful ``get`` must carry a list payload
whose elements pass through untouched; writes return the ``ApiResponse``
unchanged, with no replay, pre-read, or synthesized success.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import _integer

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncLiveLimits", "LiveLimits"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_META_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_LIVE_TYPES = frozenset({"VTDU", "RTMP"})

_GET = Operation(
    name="live_limits.get",
    method="GET",
    path="/api/v3/mall/online/user/limit/live",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=True,
    policy=_META_POLICY,
)
_SET = Operation(
    name="live_limits.set",
    method="POST",
    path="/api/v3/mall/online/user/limit/live",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=False,
    policy=_META_POLICY,
)
_DELETE = Operation(
    name="live_limits.delete",
    method="DELETE",
    path="/api/v3/mall/online/user/limit/live",
    regions=_DOMESTIC_ONLY,
    token_location="header",
    read_only=False,
    policy=_META_POLICY,
)


def _require_domestic(client: Client | AsyncClient) -> None:
    if client.region.scope != "domestic":
        raise ValueError("live concurrency limit operations require the domestic region")


def _check_live_type(live_type: str) -> str:
    if not isinstance(live_type, str) or live_type not in _LIVE_TYPES:
        raise ValueError("live_type must be the exact uppercase string 'VTDU' or 'RTMP'")
    return live_type


def _wire_int(name: str, value: int) -> str:
    try:
        return str(value)
    except ValueError:
        raise ValueError(
            f"{name} exceeds the interpreter's integer string conversion limit"
        ) from None


def _set_form(live_type: str, limit_type: int, limit_number: int) -> dict[str, str]:
    checked_live_type = _check_live_type(live_type)
    checked_limit_type = _integer("limitType", limit_type)
    if checked_limit_type not in (0, 1):
        raise ValueError("limit_type must be the integer 0 (per channel) or 1 (per user)")
    checked_limit_number = _integer("limitNumber", limit_number)
    return {
        "liveType": checked_live_type,
        "limitType": _wire_int("limitType", checked_limit_type),
        "limitNumber": _wire_int("limitNumber", checked_limit_number),
    }


def _check_get_response(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, list):
        raise ProtocolError(
            "live_limits.get returned a non-list data payload",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


class LiveLimits:
    """Synchronous facade over the domestic live concurrency limits."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, live_type: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="live_limits.get",
            mode="sync",
        ):
            _require_domestic(self._client)
            checked_live_type = _check_live_type(live_type)
            response = self._client.request(_GET, params={"liveType": checked_live_type})
            return _check_get_response(response)

    def set(self, live_type: str, *, limit_type: int, limit_number: int) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="live_limits.set",
            mode="sync",
        ):
            _require_domestic(self._client)
            form = _set_form(live_type, limit_type, limit_number)
            return self._client.request(_SET, data=form, headers=_FORM_HEADERS)

    def delete(self, live_type: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="live_limits.delete",
            mode="sync",
        ):
            _require_domestic(self._client)
            form = {"liveType": _check_live_type(live_type)}
            return self._client.request(_DELETE, data=form, headers=_FORM_HEADERS)


class AsyncLiveLimits:
    """Asynchronous facade over the domestic live concurrency limits."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, live_type: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="live_limits.get",
            mode="async",
        ):
            _require_domestic(self._client)
            checked_live_type = _check_live_type(live_type)
            response = await self._client.request(_GET, params={"liveType": checked_live_type})
            return _check_get_response(response)

    async def set(self, live_type: str, *, limit_type: int, limit_number: int) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="live_limits.set",
            mode="async",
        ):
            _require_domestic(self._client)
            form = _set_form(live_type, limit_type, limit_number)
            return await self._client.request(_SET, data=form, headers=_FORM_HEADERS)

    async def delete(self, live_type: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="live_limits.delete",
            mode="async",
        ):
            _require_domestic(self._client)
            form = {"liveType": _check_live_type(live_type)}
            return await self._client.request(_DELETE, data=form, headers=_FORM_HEADERS)
