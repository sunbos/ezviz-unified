"""Domestic OTAP thing-model endpoints for the unified EZVIZ client.

Wraps the four reviewed open.ys7.com operations (docs 1512, 1513, 1514 and
5891; call semantics from doc 1307015). The access token always travels in
an HTTP header and every operation is domestic-only: a client bound to the
overseas region gets a ``ValueError`` before any request leaves the SDK.

``invoke_action`` is a synchronous device call and the platform waits at
most 16 seconds (doc 1307015). This facade never changes the client
timeout, never polls, and never replays a write; callers that need the
full wait can construct ``Client(timeout>16)`` explicitly.
``latest_property`` returns the device shadow's desired/reported pair
verbatim — null ``value`` and ``time`` fields are meaningful answers, not
errors — and does not imply the device is online or that the two sides
agree.

The strict ``localIndex`` and identifier formats are doc 5891 rules only;
the v3 endpoints document these parameters as plain required strings, so
the limits are deliberately not backported (conflict C8).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncOtap", "Otap"]

_DOMESTIC = "domestic"
_JSON_CONTENT_TYPE = "application/json"
_LATEST_ID_LIMIT = 255  # doc 5891: identifier headers must be shorter than 255
_LATEST_INDEX_DIGITS = 10  # doc 5891: localIndex is at most 10 decimal digits

_POLICY_DATA = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)
# doc 1514 adds an optional second status layer at meta.moreInfo.deviceMeta;
# "0x00000000" is the only documented success spelling. deviceMeta is
# optional: when it is absent the platform meta.code alone decides
# (require_device_code=False). When it is present, its code is still
# checked by the SDK's existing device-code policy, and a malformed layer
# may surface as a protocol error rather than pass through unnormalized
# (conflict C9 stays open).
_POLICY_ACTION = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    device_code_path="meta.moreInfo.deviceMeta.code",
    device_message_path="meta.moreInfo.deviceMeta.errorMsg",
    device_success_values=("0x00000000",),
    require_device_code=False,
)

_OP_GET_PROPERTY = Operation(
    "otap.get_property",
    "GET",
    "/api/v3/device/otap/prop",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_DATA,
)
_OP_SET_PROPERTY = Operation(
    "otap.set_property",
    "PUT",
    "/api/v3/device/otap/prop",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    policy=_POLICY_DATA,
)
_OP_INVOKE_ACTION = Operation(
    "otap.invoke_action",
    "PUT",
    "/api/v3/device/otap/action",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    policy=_POLICY_ACTION,
)
_OP_LATEST_PROPERTY = Operation(
    "otap.latest_property",
    "GET",
    "/api/service/device/otap/prop/latest",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY_DATA,
)


def _header_value(name: str, value: object) -> str:
    """A required non-blank string the HTTP transport can carry.

    The printable-ASCII-only rejection (bytes 0x20-0x7E; every ASCII
    control character including DEL and every non-ASCII character is
    refused) is a local httpx transport limit, not a vendor-documented
    semantic rule. Letter case is never normalized and ``device_serial``
    is never auto-uppercased — no document defines either rule for these
    endpoints.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in value):
        raise ValueError(f"{name} contains characters HTTP headers cannot carry")
    return value


def _check_json_shape(node: object, active: set[int]) -> None:
    """Reject circular containers and non-string keys before auth."""
    if not isinstance(node, (Mapping, list, tuple)):
        return
    marker = id(node)
    if marker in active:
        raise ValueError("payload must not contain circular references")
    active.add(marker)
    try:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if not isinstance(key, str):
                    raise ValueError("payload keys must be strings")
                _check_json_shape(item, active)
        else:
            for item in node:
                _check_json_shape(item, active)
    finally:
        active.discard(marker)


def _prepare_payload(payload: object) -> dict[str, object]:
    """Validate and deep-copy the caller's JSON object before auth.

    The wire body is the bare JSON object — never wrapped in
    ``{"Body": ...}`` (conflict C4). Taking a Mapping is an SDK choice for
    the two write methods and says nothing about other JSON roots the
    platform might accept. Any Mapping root (dict, ``MappingProxyType``,
    ...) is shallow-copied to a plain dict first and the JSON round-trip
    produces a fresh tree, so the caller's object is never mutated and no
    schema (e.g. timeZone) is inferred. Every serialization failure —
    unserializable values, non-string keys, circular or over-deep
    containers, lone surrogates — surfaces as one fixed ``ValueError``
    raised *outside* the ``except`` block: the caught exception can embed
    the raw payload text (``UnicodeEncodeError.object``), so it is never
    retained as ``__cause__`` or ``__context__``.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping of JSON values")
    try:
        root = dict(payload)
        _check_json_shape(root, set())
        text = json.dumps(root, ensure_ascii=False, allow_nan=False)
        text.encode("utf-8")  # lone surrogates fail here as UnicodeEncodeError
        decoded: object = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        # Deliberately no ``raise ... from`` or ``from None``: the fixed
        # error below must carry a completely clean exception chain.
        pass
    else:
        if isinstance(decoded, dict):
            return cast("dict[str, object]", decoded)
    raise ValueError("payload must contain only JSON-serializable values")


def _resource_headers(
    device_serial: object,
    resource_category: object,
    local_index: object,
    domain_identifier: object,
    identifier: object,
    *,
    identifier_arg: str,
    identifier_header: str,
    content_type: str | None = None,
) -> dict[str, str]:
    """The documented required headers — no more, no less.

    ``accessToken`` is injected by the client per attempt; callers never
    supply it themselves.
    """
    headers = {
        "deviceSerial": _header_value("device_serial", device_serial),
        "resourceCategory": _header_value("resource_category", resource_category),
        "localIndex": _header_value("local_index", local_index),
        "domainIdentifier": _header_value("domain_identifier", domain_identifier),
        identifier_header: _header_value(identifier_arg, identifier),
    }
    if content_type is not None:
        headers["Content-Type"] = content_type
    return headers


def _enforce_latest_limits(headers: Mapping[str, str]) -> None:
    """Apply the doc 5891 formats; scoped to ``latest_property`` only (C8)."""
    index = headers["localIndex"]
    if (
        len(index) > _LATEST_INDEX_DIGITS
        or not index.isdigit()
        or (index != "0" and index.startswith("0"))
    ):
        raise ValueError(
            "local_index must be a non-negative decimal of at most "
            f"{_LATEST_INDEX_DIGITS} digits without leading zeros"
        )
    for header_name, arg_name in (
        ("resourceCategory", "resource_category"),
        ("domainIdentifier", "domain_identifier"),
        ("propIdentifier", "property_identifier"),
    ):
        if len(headers[header_name]) >= _LATEST_ID_LIMIT:
            raise ValueError(f"{arg_name} must be shorter than {_LATEST_ID_LIMIT} characters")


class Otap:
    """Synchronous facade over the four reviewed domestic OTAP endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get_property(
        self,
        device_serial: str,
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        property_identifier: str,
    ) -> ApiResponse:
        """Read the platform-cached property value (doc 1512, read-only).

        This serves the value cached on the platform; it does not query the
        device live (doc 1307015).
        """
        with _diagnostic_scope(self._client, entrypoint="otap.get_property", mode="sync"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                property_identifier,
                identifier_arg="property_identifier",
                identifier_header="propIdentifier",
            )
            return self._client.request(_OP_GET_PROPERTY, headers=headers)

    def set_property(
        self,
        device_serial: str,
        payload: Mapping[str, object],
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        property_identifier: str,
    ) -> ApiResponse:
        """Send a bare JSON property object to the device (doc 1513, write).

        ``payload`` is the second positional-or-keyword parameter. Success
        is decided by ``meta.code=200`` alone even when
        ``moreInfo.msgDetail`` carries error text (conflict C1); the
        envelope passes through verbatim. The write is never replayed.
        """
        with _diagnostic_scope(self._client, entrypoint="otap.set_property", mode="sync"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                property_identifier,
                identifier_arg="property_identifier",
                identifier_header="propIdentifier",
                content_type=_JSON_CONTENT_TYPE,
            )
            body = _prepare_payload(payload)
            return self._client.request(_OP_SET_PROPERTY, json=body, headers=headers)

    def invoke_action(
        self,
        device_serial: str,
        payload: Mapping[str, object],
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        action_identifier: str,
    ) -> ApiResponse:
        """Invoke a device service synchronously (doc 1514, write).

        ``payload`` is the second positional-or-keyword parameter. The
        platform waits at most 16 seconds; use ``Client(timeout>16)`` to
        observe the full window. When ``meta.moreInfo.deviceMeta`` is
        present its ``code`` must equal ``"0x00000000"``; when it is absent
        the platform ``meta.code`` alone decides.
        """
        with _diagnostic_scope(self._client, entrypoint="otap.invoke_action", mode="sync"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                action_identifier,
                identifier_arg="action_identifier",
                identifier_header="actionIdentifier",
                content_type=_JSON_CONTENT_TYPE,
            )
            body = _prepare_payload(payload)
            return self._client.request(_OP_INVOKE_ACTION, json=body, headers=headers)

    def latest_property(
        self,
        device_serial: str,
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        property_identifier: str,
    ) -> ApiResponse:
        """Read desired/reported shadow values (doc 5891, read-only).

        Null ``value``/``time`` on either side is a valid answer, not an
        error; ``desired`` and ``reported`` are not guaranteed to match.
        """
        with _diagnostic_scope(self._client, entrypoint="otap.latest_property", mode="sync"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                property_identifier,
                identifier_arg="property_identifier",
                identifier_header="propIdentifier",
            )
            _enforce_latest_limits(headers)
            return self._client.request(_OP_LATEST_PROPERTY, headers=headers)


class AsyncOtap:
    """Asynchronous facade over the four reviewed domestic OTAP endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get_property(
        self,
        device_serial: str,
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        property_identifier: str,
    ) -> ApiResponse:
        """Async variant of :meth:`Otap.get_property`."""
        with _diagnostic_scope(self._client, entrypoint="otap.get_property", mode="async"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                property_identifier,
                identifier_arg="property_identifier",
                identifier_header="propIdentifier",
            )
            return await self._client.request(_OP_GET_PROPERTY, headers=headers)

    async def set_property(
        self,
        device_serial: str,
        payload: Mapping[str, object],
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        property_identifier: str,
    ) -> ApiResponse:
        """Async variant of :meth:`Otap.set_property`."""
        with _diagnostic_scope(self._client, entrypoint="otap.set_property", mode="async"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                property_identifier,
                identifier_arg="property_identifier",
                identifier_header="propIdentifier",
                content_type=_JSON_CONTENT_TYPE,
            )
            body = _prepare_payload(payload)
            return await self._client.request(_OP_SET_PROPERTY, json=body, headers=headers)

    async def invoke_action(
        self,
        device_serial: str,
        payload: Mapping[str, object],
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        action_identifier: str,
    ) -> ApiResponse:
        """Async variant of :meth:`Otap.invoke_action`."""
        with _diagnostic_scope(self._client, entrypoint="otap.invoke_action", mode="async"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                action_identifier,
                identifier_arg="action_identifier",
                identifier_header="actionIdentifier",
                content_type=_JSON_CONTENT_TYPE,
            )
            body = _prepare_payload(payload)
            return await self._client.request(_OP_INVOKE_ACTION, json=body, headers=headers)

    async def latest_property(
        self,
        device_serial: str,
        *,
        resource_category: str,
        local_index: str,
        domain_identifier: str,
        property_identifier: str,
    ) -> ApiResponse:
        """Async variant of :meth:`Otap.latest_property`."""
        with _diagnostic_scope(self._client, entrypoint="otap.latest_property", mode="async"):
            headers = _resource_headers(
                device_serial,
                resource_category,
                local_index,
                domain_identifier,
                property_identifier,
                identifier_arg="property_identifier",
                identifier_header="propIdentifier",
            )
            _enforce_latest_limits(headers)
            return await self._client.request(_OP_LATEST_PROPERTY, headers=headers)
