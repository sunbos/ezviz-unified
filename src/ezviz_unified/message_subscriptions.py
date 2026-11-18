"""Domestic LAPP message-queue operations (open.ys7.com docs 983/984).

Both endpoints are domestic-only form posts. ``create_consumer`` binds one
of the fixed ``group1``..``group5`` names into the URL path while
``consume`` reads the next batch for a caller-owned ``consumerId`` and
commits the previous one. The SDK never manages consumer lifecycle,
deduplicates deliveries, spawns background work, or replays these
side-effecting calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import _integer, _text
from .vod import _utf8

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncMessageSubscriptions", "MessageSubscriptions"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_LAPP_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_GROUPS = frozenset({"group1", "group2", "group3", "group4", "group5"})

_CREATE_CONSUMER = Operation(
    "message_subscriptions.create_consumer",
    "POST",
    "/api/lapp/mq/v2/consumer/{consumerName}",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=False,
    policy=_LAPP_POLICY,
)
_CONSUME = Operation(
    "message_subscriptions.consume",
    "POST",
    "/api/lapp/mq/v2/consumer/messages",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=False,
    policy=_LAPP_POLICY,
)


def _require_region(scope: str) -> None:
    if scope != "domestic":
        raise ValueError("message_subscriptions is only supported in the domestic region")


def _group_name(value: object) -> str:
    if not isinstance(value, str) or value not in _GROUPS:
        raise ValueError("group must be exactly one of group1..group5")
    return value


def _consumer_id(value: object) -> str:
    consumer_id = _text("consumerId", value)
    _utf8("consumerId", consumer_id)
    return consumer_id


class MessageSubscriptions:
    """Synchronous facade over the domestic LAPP message-queue endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def create_consumer(
        self,
        group: Literal["group1", "group2", "group3", "group4", "group5"],
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="message_subscriptions.create_consumer",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            return self._client.request(
                _CREATE_CONSUMER,
                path_params={"consumerName": _group_name(group)},
                data={},
                headers=_FORM_HEADERS,
            )

    def consume(self, consumer_id: str, *, offset: int | None = None) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="message_subscriptions.consume",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {"consumerId": _consumer_id(consumer_id)}
            if offset is not None:
                data["offset"] = _integer("offset", offset)
            return self._client.request(_CONSUME, data=data, headers=_FORM_HEADERS)


class AsyncMessageSubscriptions:
    """Asynchronous facade over the domestic LAPP message-queue endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_consumer(
        self,
        group: Literal["group1", "group2", "group3", "group4", "group5"],
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="message_subscriptions.create_consumer",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            return await self._client.request(
                _CREATE_CONSUMER,
                path_params={"consumerName": _group_name(group)},
                data={},
                headers=_FORM_HEADERS,
            )

    async def consume(self, consumer_id: str, *, offset: int | None = None) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="message_subscriptions.consume",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data: dict[str, object] = {"consumerId": _consumer_id(consumer_id)}
            if offset is not None:
                data["offset"] = _integer("offset", offset)
            return await self._client.request(_CONSUME, data=data, headers=_FORM_HEADERS)
