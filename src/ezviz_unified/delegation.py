"""B1/B2 account delegation facade for the unified EZVIZ client (sync and async).

Covers the five reviewed domestic trusteeship endpoints (docs
1890-1894, open.ys7.com, archived 2026-11-14). One method belongs to
the B2 client — applying for an authorization code — and four
independent methods belong to the B1 owner client: authorizing the
account, listing delegatable resources, delegating a single device or
channel, and relieving it again. The docs record neither a required
call order nor a lifetime for the issued sign, and the SDK chains
none of these calls automatically; each method is invoked on its own.
Every endpoint is a POST that carries ``accessToken`` in the request
header, sends ``application/x-www-form-urlencoded`` fields, and
answers the ``meta.code`` (integer 200) / ``meta.message`` /
``data`` envelope.

The caller's own token fixes the B1/B2 role; the SDK never detects,
switches, caches, or signs on behalf of either party, and counterparty
appKey values are explicit parameters only — never defaulted from the
client's own credentials. ``authorization_code`` (the vendor ``sign``)
is an opaque credential string: forwarded verbatim, collected for
diagnostic redaction through the ``sign`` sensitive key, and never
parsed for the ``#`` shape shown in the doc example. ``state`` and
``authorization_type`` are opaque pass-through strings with no invented
defaults or enums, and ``local_index`` travels in a header only when the
caller explicitly intends a channel-level operation. ``page_size``
honors the documented maximum of 20; the docs declare no lower bounds,
so none are synthesized here — the platform owns that contract.

Only ``list_resources`` is read-only and therefore eligible for the
client's single token-refresh retry; the credential-issuing and mutation
calls are never replayed automatically. Boolean ``data`` (including
``false``) and the resource list's nullable ``capturePic`` and
undocumented fields are returned verbatim — ``meta.code == 200`` asserts
service-level success only, not a guaranteed grant. All five operations
are domestic-only: overseas clients raise before any request and no
overseas endpoint is inferred. The namespace stays isolated from
``subaccounts`` (``/api/lapp/ram/*``) and ``devices.list_managed``
(``/api/lapp/trust/device/list``), and the documented paid-account /
backstage-activation gate is neither enforced nor bypassed by the SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, NamedTuple

from .models import ApiResponse, ResponsePolicy
from .operations import Operation

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncDelegation", "Delegation"]

_DOMESTIC = "domestic"
_DOMESTIC_ONLY = frozenset({_DOMESTIC})
_BASE = "/api/v3/lapp/trust/sign"
_PAGE_SIZE_MAX = 20  # documented maximum; no lower bound is declared

_META_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    success_values=(200,),
)


def _op(name: str, path: str, *, read_only: bool = False) -> Operation:
    return Operation(
        f"delegation.{name}",
        "POST",
        path,
        token_location="header",
        regions=_DOMESTIC_ONLY,
        read_only=read_only,
        policy=_META_POLICY,
    )


_OP_APPLY_CODE = _op("apply_code", f"{_BASE}/apply")
_OP_AUTHORIZE_ACCOUNT = _op("authorize_account", f"{_BASE}/userAuthorize")
_OP_LIST_RESOURCES = _op("list_resources", f"{_BASE}/resource/list", read_only=True)
_OP_AUTHORIZE_DEVICE = _op("authorize_device", f"{_BASE}/resource/authorize")
_OP_RELIEVE_DEVICE = _op("relieve_device", f"{_BASE}/resource/relieve")


def _require_domestic(scope: str, method: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError(f"delegation.{method} is only documented for the domestic region")


def _check_str(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _check_header(name: str, value: object) -> str:
    """Header-bound fields additionally obey local HTTPX transport limits."""
    text = _check_str(name, value)
    if not text.isascii() or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError(f"{name} must be ASCII without control characters to travel in a header")
    return text


def _check_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int")
    return value


def _check_page_size(value: object) -> int:
    size = _check_int("page_size", value)
    if size > _PAGE_SIZE_MAX:
        raise ValueError(f"page_size exceeds the documented maximum {_PAGE_SIZE_MAX}")
    return size


class _Plan(NamedTuple):
    """Validated request parts shared by the sync and async facades."""

    operation: Operation
    data: Mapping[str, object]
    headers: Mapping[str, str] | None


def _apply_code_plan(owner_app_key: object, scope: str) -> _Plan:
    _require_domestic(scope, "apply_code")
    return _Plan(
        _OP_APPLY_CODE,
        {"appKey": _check_str("owner_app_key", owner_app_key)},
        None,
    )


def _authorize_account_plan(authorization_code: object, scope: str) -> _Plan:
    _require_domestic(scope, "authorize_account")
    return _Plan(
        _OP_AUTHORIZE_ACCOUNT,
        {"sign": _check_str("authorization_code", authorization_code)},
        None,
    )


def _list_resources_plan(
    authorization_code: object,
    device_serial: object,
    authorization_type: object,
    page_start: object,
    page_size: object,
    scope: str,
) -> _Plan:
    _require_domestic(scope, "list_resources")
    form: dict[str, object] = {"sign": _check_str("authorization_code", authorization_code)}
    if authorization_type is not None:
        form["authorizeType"] = _check_str("authorization_type", authorization_type)
    if page_start is not None:
        form["pageStart"] = _check_int("page_start", page_start)
    if page_size is not None:
        form["pageSize"] = _check_page_size(page_size)
    headers: dict[str, str] = {}
    if device_serial is not None:
        headers["deviceSerial"] = _check_header("device_serial", device_serial)
    return _Plan(_OP_LIST_RESOURCES, form, headers or None)


def _authorize_device_plan(
    authorization_code: object,
    device_serial: object,
    state: object,
    local_index: object,
    scope: str,
) -> _Plan:
    _require_domestic(scope, "authorize_device")
    form: dict[str, object] = {"sign": _check_str("authorization_code", authorization_code)}
    if state is not None:
        form["state"] = _check_str("state", state)
    headers = {"deviceSerial": _check_header("device_serial", device_serial)}
    if local_index is not None:
        headers["localIndex"] = _check_header("local_index", local_index)
    return _Plan(_OP_AUTHORIZE_DEVICE, form, headers)


def _relieve_device_plan(
    recipient_app_key: object,
    device_serial: object,
    state: object,
    local_index: object,
    scope: str,
) -> _Plan:
    _require_domestic(scope, "relieve_device")
    form = {
        "appKey": _check_str("recipient_app_key", recipient_app_key),
        "state": _check_str("state", state),
    }
    headers = {"deviceSerial": _check_header("device_serial", device_serial)}
    if local_index is not None:
        headers["localIndex"] = _check_header("local_index", local_index)
    return _Plan(_OP_RELIEVE_DEVICE, form, headers)


def _send(client: Client, plan: _Plan) -> ApiResponse:
    return client.request(plan.operation, data=plan.data, headers=plan.headers)


async def _asend(client: AsyncClient, plan: _Plan) -> ApiResponse:
    return await client.request(plan.operation, data=plan.data, headers=plan.headers)


class Delegation:
    """Synchronous facade over the reviewed B1/B2 delegation operations.

    Each method issues exactly the documented request for its endpoint;
    nothing chains apply -> authorize -> list -> grant, nothing
    paginates, and write calls are never replayed after a token refresh.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def apply_code(self, owner_app_key: str) -> ApiResponse:
        """Apply for a delegation authorization code (B2 caller, doc 1890).

        Sends only ``appKey=owner_app_key`` — the B1 owner's key, never
        the client's own credentials. ``data.sign``/``data.appKey`` pass
        through verbatim.
        """
        plan = _apply_code_plan(owner_app_key, self._client.region.scope)
        return _send(self._client, plan)

    def authorize_account(self, authorization_code: str) -> ApiResponse:
        """Grant account-level delegation (B1 caller, doc 1891).

        ``data`` is a Boolean returned untouched — ``false`` is kept.
        """
        plan = _authorize_account_plan(authorization_code, self._client.region.scope)
        return _send(self._client, plan)

    def list_resources(
        self,
        authorization_code: str,
        *,
        device_serial: str | None = None,
        authorization_type: str | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        """List delegatable resources one page at a time (B1 caller, doc 1892).

        ``device_serial`` travels as an optional header;
        ``authorization_type`` is forwarded verbatim with no enum check;
        ``page_size`` is bounded only by the documented maximum of 20 and
        no undocumented lower bound is invented. ``None`` arguments are
        omitted so the platform defaults (pageStart 0, pageSize 10)
        stand. The flat ``data`` array — nullable ``capturePic``,
        ``cameraInfo`` of any size, no total/hasMore marker — is returned
        verbatim; callers drive pagination. This is the only read-only
        member and the only one eligible for the token-refresh retry.
        """
        plan = _list_resources_plan(
            authorization_code,
            device_serial,
            authorization_type,
            page_start,
            page_size,
            self._client.region.scope,
        )
        return _send(self._client, plan)

    def authorize_device(
        self,
        authorization_code: str,
        device_serial: str,
        *,
        state: str | None = None,
        local_index: str | None = None,
    ) -> ApiResponse:
        """Delegate one device — or channel via ``local_index`` — (B1, doc 1893).

        ``device_serial``/``local_index`` travel in headers; ``state`` is
        an opaque optional string sent only when the caller provides it.
        """
        plan = _authorize_device_plan(
            authorization_code,
            device_serial,
            state,
            local_index,
            self._client.region.scope,
        )
        return _send(self._client, plan)

    def relieve_device(
        self,
        recipient_app_key: str,
        device_serial: str,
        state: str,
        *,
        local_index: str | None = None,
    ) -> ApiResponse:
        """Relieve one device's delegation (B1 caller, doc 1894).

        Contractually asymmetric with authorize: the form carries the B2
        ``recipient_app_key`` plus a required ``state`` — ``sign`` is
        never sent here.
        """
        plan = _relieve_device_plan(
            recipient_app_key,
            device_serial,
            state,
            local_index,
            self._client.region.scope,
        )
        return _send(self._client, plan)


class AsyncDelegation:
    """Asynchronous facade over the reviewed B1/B2 delegation operations."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def apply_code(self, owner_app_key: str) -> ApiResponse:
        plan = _apply_code_plan(owner_app_key, self._client.region.scope)
        return await _asend(self._client, plan)

    async def authorize_account(self, authorization_code: str) -> ApiResponse:
        plan = _authorize_account_plan(authorization_code, self._client.region.scope)
        return await _asend(self._client, plan)

    async def list_resources(
        self,
        authorization_code: str,
        *,
        device_serial: str | None = None,
        authorization_type: str | None = None,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        plan = _list_resources_plan(
            authorization_code,
            device_serial,
            authorization_type,
            page_start,
            page_size,
            self._client.region.scope,
        )
        return await _asend(self._client, plan)

    async def authorize_device(
        self,
        authorization_code: str,
        device_serial: str,
        *,
        state: str | None = None,
        local_index: str | None = None,
    ) -> ApiResponse:
        plan = _authorize_device_plan(
            authorization_code,
            device_serial,
            state,
            local_index,
            self._client.region.scope,
        )
        return await _asend(self._client, plan)

    async def relieve_device(
        self,
        recipient_app_key: str,
        device_serial: str,
        state: str,
        *,
        local_index: str | None = None,
    ) -> ApiResponse:
        plan = _relieve_device_plan(
            recipient_app_key,
            device_serial,
            state,
            local_index,
            self._client.region.scope,
        )
        return await _asend(self._client, plan)
