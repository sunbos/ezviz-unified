"""Overseas detector list queries and display-name updates.

The three list endpoints are overseas-only LAPP form POSTs that send the
parent serial as ``deviceSerial``; ``linked_cameras`` also sends
``detectorSerial``. The SDK never infers serials, channel numbers, or
pagination, and ``accessToken`` is injected by the client as a form
field rather than passed by the caller. A separate form operation renames an
explicitly identified detector without first querying a list.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text, _require_overseas

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncDetectors", "Detectors"]

_OVERSEAS_ONLY = frozenset({"overseas"})

_RESULT_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
    envelope_paths=("result",),
    redact_message_urls=True,
    redact_form_serials=True,
)

_BARE_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
    envelope_paths=("",),
    redact_message_urls=True,
    redact_form_serials=True,
)

# Documented rename codes and additional standard response codes.
_RENAME_CODES = (
    200,
    400,
    403,
    404,
    500,
    10001,
    10002,
    10005,
    10031,
    20002,
    20006,
    20007,
    20008,
    20014,
    20018,
    49999,
)

_RENAME = Operation(
    "detectors.rename",
    "POST",
    "/api/lapp/detector/name/change",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=False,
    policy=replace(
        _BARE_POLICY,
        redact_device_add_details=True,
        exposed_business_codes=tuple(
            value for code in _RENAME_CODES for value in (code, str(code))
        ),
    ),
)

_LIST = Operation(
    "detectors.list",
    "POST",
    "/api/lapp/detector/list",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=True,
    policy=_RESULT_POLICY,
)
_BINDABLE_CAMERAS = Operation(
    "detectors.bindable_cameras",
    "POST",
    "/api/lapp/detector/ipc/list/bindable",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=True,
    policy=_BARE_POLICY,
)
_LINKED_CAMERAS = Operation(
    "detectors.linked_cameras",
    "POST",
    "/api/lapp/detector/ipc/list/bind",
    regions=_OVERSEAS_ONLY,
    token_location="form",
    read_only=True,
    policy=_BARE_POLICY,
)


def _rename_fields(a1_serial: str, detector_serial: str, new_name: str) -> dict[str, str]:
    """Validate the rename arguments and build the request payload fields."""
    device_serial = _check_text(a1_serial, "deviceSerial")
    detector = _check_text(detector_serial, "detectorSerial")
    name = _check_text(new_name, "newName")
    if len(name) >= 50:
        raise ValueError("newName must be shorter than 50 characters")
    for char in name:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F:
            raise ValueError("newName must not contain control characters")
    return {"deviceSerial": device_serial, "detectorSerial": detector, "newName": name}


def _check_list_response(response: ApiResponse) -> ApiResponse:
    """Return ``response`` unchanged when ``data`` is a list of dicts."""
    data = response.data
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ProtocolError(
            "unexpected detector list response shape",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


class Detectors:
    """Synchronous facade over the overseas detector endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self, a1_serial: str) -> ApiResponse:
        """POST ``/api/lapp/detector/list`` for ``a1_serial``.

        ``deviceSerial`` is the only caller-supplied form field; the
        client injects ``accessToken``. ``response.data`` must be a list
        of dict rows or :class:`ProtocolError` is raised; rows are
        returned verbatim with no key remapping.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="detectors.list",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            fields = {"deviceSerial": _check_text(a1_serial, "deviceSerial")}
            response = self._client.request(_LIST, data=fields)
            return _check_list_response(response)

    def rename(self, a1_serial: str, detector_serial: str, *, new_name: str) -> ApiResponse:
        """Change the display name of an explicitly associated detector."""
        with _diagnostic_scope(self._client, entrypoint="detectors.rename", mode="sync"):
            _require_overseas(self._client.region.scope)
            fields = _rename_fields(a1_serial, detector_serial, new_name)
            return self._client.request(_RENAME, data=fields)

    def bindable_cameras(self, a1_serial: str) -> ApiResponse:
        """POST ``/api/lapp/detector/ipc/list/bindable`` for ``a1_serial``.

        ``deviceSerial`` is the only caller-supplied form field; the
        client injects ``accessToken``. ``response.data`` must be a list
        of dict rows or :class:`ProtocolError` is raised; rows are
        returned verbatim with no key remapping.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="detectors.bindable_cameras",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            fields = {"deviceSerial": _check_text(a1_serial, "deviceSerial")}
            response = self._client.request(_BINDABLE_CAMERAS, data=fields)
            return _check_list_response(response)

    def linked_cameras(self, a1_serial: str, detector_serial: str) -> ApiResponse:
        """POST ``/api/lapp/detector/ipc/list/bind`` for a detector.

        Sends ``deviceSerial`` from ``a1_serial`` and ``detectorSerial``
        from ``detector_serial`` as form fields; the client injects
        ``accessToken``. ``response.data`` must be a list of dict rows
        or :class:`ProtocolError` is raised; rows are returned verbatim
        with no key remapping.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="detectors.linked_cameras",
            mode="sync",
        ):
            _require_overseas(self._client.region.scope)
            fields = {
                "deviceSerial": _check_text(a1_serial, "deviceSerial"),
                "detectorSerial": _check_text(detector_serial, "detectorSerial"),
            }
            response = self._client.request(_LINKED_CAMERAS, data=fields)
            return _check_list_response(response)


class AsyncDetectors:
    """Asynchronous facade over the overseas detector endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list(self, a1_serial: str) -> ApiResponse:
        """POST ``/api/lapp/detector/list`` for ``a1_serial``.

        ``deviceSerial`` is the only caller-supplied form field; the
        client injects ``accessToken``. ``response.data`` must be a list
        of dict rows or :class:`ProtocolError` is raised; rows are
        returned verbatim with no key remapping.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="detectors.list",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            fields = {"deviceSerial": _check_text(a1_serial, "deviceSerial")}
            response = await self._client.request(_LIST, data=fields)
            return _check_list_response(response)

    async def rename(self, a1_serial: str, detector_serial: str, *, new_name: str) -> ApiResponse:
        """Change the display name of an explicitly associated detector."""
        with _diagnostic_scope(self._client, entrypoint="detectors.rename", mode="async"):
            _require_overseas(self._client.region.scope)
            fields = _rename_fields(a1_serial, detector_serial, new_name)
            return await self._client.request(_RENAME, data=fields)

    async def bindable_cameras(self, a1_serial: str) -> ApiResponse:
        """POST ``/api/lapp/detector/ipc/list/bindable`` for ``a1_serial``.

        ``deviceSerial`` is the only caller-supplied form field; the
        client injects ``accessToken``. ``response.data`` must be a list
        of dict rows or :class:`ProtocolError` is raised; rows are
        returned verbatim with no key remapping.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="detectors.bindable_cameras",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            fields = {"deviceSerial": _check_text(a1_serial, "deviceSerial")}
            response = await self._client.request(_BINDABLE_CAMERAS, data=fields)
            return _check_list_response(response)

    async def linked_cameras(self, a1_serial: str, detector_serial: str) -> ApiResponse:
        """POST ``/api/lapp/detector/ipc/list/bind`` for a detector.

        Sends ``deviceSerial`` from ``a1_serial`` and ``detectorSerial``
        from ``detector_serial`` as form fields; the client injects
        ``accessToken``. ``response.data`` must be a list of dict rows
        or :class:`ProtocolError` is raised; rows are returned verbatim
        with no key remapping.
        """
        with _diagnostic_scope(
            self._client,
            entrypoint="detectors.linked_cameras",
            mode="async",
        ):
            _require_overseas(self._client.region.scope)
            fields = {
                "deviceSerial": _check_text(a1_serial, "deviceSerial"),
                "detectorSerial": _check_text(detector_serial, "detectorSerial"),
            }
            response = await self._client.request(_LINKED_CAMERAS, data=fields)
            return _check_list_response(response)
