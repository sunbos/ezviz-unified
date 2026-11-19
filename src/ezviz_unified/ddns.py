"""Overseas DDNS read operations (LAPP ddns get/list/share list).

All three endpoints are overseas-only, read-only POST form queries with
string ``code="200"`` success and the access token traveling in the form
body. ``get`` resolves one authorized device — shared devices included —
by ``deviceSerial`` and/or ``domain``; at least one selector is required
and, when both are sent, the platform prioritizes the serial. ``list``
returns the account's devices (shared included) and ``share_list`` only
the shared ones, so the two may overlap. Both accept optional pagination;
omitting it lets the server apply its defaults, and the SDK always fetches
exactly one page. The SDK never resolves domains, never probes mapped
ports, and never paginates automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .devices import _check_page_size
from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text, _require_overseas
from .traffic import _page_start

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncDdns", "Ddns"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_LAPP_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
)

_OVERSEAS_ONLY = frozenset({"overseas"})

_GET = Operation(
    name="ddns.get",
    method="POST",
    path="/api/lapp/ddns/get",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=True,
    policy=_LAPP_POLICY,
)
_LIST = Operation(
    name="ddns.list",
    method="POST",
    path="/api/lapp/ddns/list",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=True,
    policy=_LAPP_POLICY,
)
_SHARE_LIST = Operation(
    name="ddns.share_list",
    method="POST",
    path="/api/lapp/ddns/share/list",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=True,
    policy=_LAPP_POLICY,
)


def _unexpected_shape(response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        "unexpected DDNS response shape",
        http_status=response.http_status,
        operation=response.operation,
    )


def _check_get_response(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, dict):
        raise _unexpected_shape(response)
    return response


def _check_list_response(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise _unexpected_shape(response)
    raw = response.raw
    if isinstance(raw, dict) and "page" in raw and not isinstance(raw["page"], dict):
        raise _unexpected_shape(response)
    return response


def _get_form(device_serial: str | None, domain: str | None) -> dict[str, str]:
    if device_serial is None and domain is None:
        raise ValueError("ddns.get requires device_serial or domain")
    form: dict[str, str] = {}
    if device_serial is not None:
        form["deviceSerial"] = _check_text(device_serial, "deviceSerial")
    if domain is not None:
        form["domain"] = _check_text(domain, "domain")
    return form


def _paged_form(page_start: int | None, page_size: int | None) -> dict[str, object]:
    form: dict[str, object] = {}
    if page_start is not None:
        form["pageStart"] = _page_start(page_start)
    if page_size is not None:
        form["pageSize"] = _check_page_size(page_size)
    return form


class Ddns:
    """Synchronous facade over the overseas DDNS read endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get(
        self,
        *,
        device_serial: str | None = None,
        domain: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="ddns.get",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            response = self._client.request(
                _GET,
                data=_get_form(device_serial, domain),
                headers=_FORM_HEADERS,
            )
            return _check_get_response(response)

    def list(
        self,
        *,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="ddns.list",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            response = self._client.request(
                _LIST,
                data=_paged_form(page_start, page_size),
                headers=_FORM_HEADERS,
            )
            return _check_list_response(response)

    def share_list(
        self,
        *,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="ddns.share_list",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            response = self._client.request(
                _SHARE_LIST,
                data=_paged_form(page_start, page_size),
                headers=_FORM_HEADERS,
            )
            return _check_list_response(response)


class AsyncDdns:
    """Asynchronous facade over the overseas DDNS read endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(
        self,
        *,
        device_serial: str | None = None,
        domain: str | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="ddns.get",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            response = await self._client.request(
                _GET,
                data=_get_form(device_serial, domain),
                headers=_FORM_HEADERS,
            )
            return _check_get_response(response)

    async def list(
        self,
        *,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="ddns.list",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            response = await self._client.request(
                _LIST,
                data=_paged_form(page_start, page_size),
                headers=_FORM_HEADERS,
            )
            return _check_list_response(response)

    async def share_list(
        self,
        *,
        page_start: int | None = None,
        page_size: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="ddns.share_list",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            response = await self._client.request(
                _SHARE_LIST,
                data=_paged_form(page_start, page_size),
                headers=_FORM_HEADERS,
            )
            return _check_list_response(response)
