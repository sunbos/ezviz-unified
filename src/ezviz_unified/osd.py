"""Domestic device OSD (on-screen display) facades.

Three independent entry points only:

* ``get_name`` -- read-only query for the current OSD text (``data.osd``).
* ``set_name`` -- write a single OSD line (endpoint 725).
* ``set_lines`` -- write the raw comma-delimited OSD block verbatim (5201).

No device discovery, capability pre-checks, read-after-write verification,
or automatic recovery is performed.  SDK read-only retry applies to
``get_name`` only; write failures surface the platform error and are never
replayed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .arming import _check_form_serial
from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text
from .vod import _utf8

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncOsd", "Osd"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_GET_NAME = Operation(
    "osd.get_name",
    "GET",
    "/api/v3/device/osd",
    regions=frozenset({"domestic"}),
    token_location="header",
    read_only=True,
    policy=ResponsePolicy(success_values=("200",)),
)

_SET_NAME = Operation(
    "osd.set_name",
    "POST",
    "/api/lapp/device/update/osd/name",
    regions=frozenset({"domestic"}),
    token_location="form",
    read_only=False,
    policy=ResponsePolicy(success_values=("200",)),
)

_SET_LINES = Operation(
    "osd.set_lines",
    "POST",
    "/api/v3/device/osd/multi/set",
    regions=frozenset({"domestic"}),
    token_location="header",
    read_only=False,
    policy=ResponsePolicy(
        code_path="meta.code",
        message_path="meta.message",
        data_path="data",
        success_values=(200,),
    ),
)


def _require_domestic(scope: str) -> None:
    if scope != "domestic":
        raise ValueError("OSD endpoints are only available for the domestic region")


def _set_name_form(device_serial: str, osd_name: str, channel_no: str | None) -> dict[str, str]:
    serial = _check_form_serial(device_serial, "domestic")
    form = {
        "deviceSerial": serial,
        "osdName": _check_text(osd_name, "osdName"),
    }
    if channel_no is not None:
        form["channelNo"] = _check_text(channel_no, "channelNo")
    return form


def _set_lines_form(
    device_serial: str,
    osd_names: str | None,
    channel_no: str | None,
) -> dict[str, str]:
    form = {"deviceSerial": _check_text(device_serial, "deviceSerial")}
    if osd_names is not None:
        if not isinstance(osd_names, str):
            raise ValueError("osdNames must be a string")
        form["osdNames"] = _utf8("osd_names", osd_names)
    if channel_no is not None:
        form["channelNo"] = _check_text(channel_no, "channelNo")
    return form


def _check_osd_payload(response: ApiResponse) -> ApiResponse:
    """Validate the payload shape and return the original response."""
    data = response.data
    if isinstance(data, dict) and isinstance(data.get("osd"), str):
        return response
    raise ProtocolError(
        "osd.get_name response did not contain a string 'data.osd' field",
        operation=response.operation,
        http_status=response.http_status,
    )


class Osd:
    """Synchronous facade over the domestic device OSD endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get_name(self, device_serial: str) -> ApiResponse:
        """Return the current OSD text (``data.osd`` may be an empty string)."""
        with _diagnostic_scope(self._client, entrypoint="osd.get_name", mode="sync"):
            _require_domestic(self._client.region.scope)
            serial = _check_text(device_serial, "deviceSerial")
            response = self._client.request(_GET_NAME, params={"deviceSerial": serial})
            return _check_osd_payload(response)

    def set_name(
        self,
        device_serial: str,
        osd_name: str,
        *,
        channel_no: str | None = None,
    ) -> ApiResponse:
        """Write a single OSD line via the 725 form endpoint."""
        with _diagnostic_scope(self._client, entrypoint="osd.set_name", mode="sync"):
            _require_domestic(self._client.region.scope)
            form = _set_name_form(device_serial, osd_name, channel_no)
            return self._client.request(_SET_NAME, data=form, headers=_FORM_HEADERS)

    def set_lines(
        self,
        device_serial: str,
        *,
        osd_names: str | None,
        channel_no: str | None = None,
    ) -> ApiResponse:
        """Write the raw comma-delimited OSD block via the 5201 endpoint."""
        with _diagnostic_scope(self._client, entrypoint="osd.set_lines", mode="sync"):
            _require_domestic(self._client.region.scope)
            form = _set_lines_form(device_serial, osd_names, channel_no)
            return self._client.request(_SET_LINES, data=form, headers=_FORM_HEADERS)


class AsyncOsd:
    """Asynchronous facade over the domestic device OSD endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get_name(self, device_serial: str) -> ApiResponse:
        """Return the current OSD text (``data.osd`` may be an empty string)."""
        with _diagnostic_scope(self._client, entrypoint="osd.get_name", mode="async"):
            _require_domestic(self._client.region.scope)
            serial = _check_text(device_serial, "deviceSerial")
            response = await self._client.request(_GET_NAME, params={"deviceSerial": serial})
            return _check_osd_payload(response)

    async def set_name(
        self,
        device_serial: str,
        osd_name: str,
        *,
        channel_no: str | None = None,
    ) -> ApiResponse:
        """Write a single OSD line via the 725 form endpoint."""
        with _diagnostic_scope(self._client, entrypoint="osd.set_name", mode="async"):
            _require_domestic(self._client.region.scope)
            form = _set_name_form(device_serial, osd_name, channel_no)
            return await self._client.request(_SET_NAME, data=form, headers=_FORM_HEADERS)

    async def set_lines(
        self,
        device_serial: str,
        *,
        osd_names: str | None,
        channel_no: str | None = None,
    ) -> ApiResponse:
        """Write the raw comma-delimited OSD block via the 5201 endpoint."""
        with _diagnostic_scope(self._client, entrypoint="osd.set_lines", mode="async"):
            _require_domestic(self._client.region.scope)
            form = _set_lines_form(device_serial, osd_names, channel_no)
            return await self._client.request(_SET_LINES, data=form, headers=_FORM_HEADERS)
