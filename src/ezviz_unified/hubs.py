"""Domestic HUB sub-device operations (open.ys7.com docs 1356/1357/1358).

All three endpoints are domestic-only LAPP queries. The parent's
``deviceSerial`` is sent by ``list_subdevices`` (1356) while
``subdevice_info`` (1357) and ``channels`` (1358) send the child's serial;
the SDK never infers parent/child identity or channel numbers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .storage_models import _text
from .vod import _utf8

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncHubs", "Hubs"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_LAPP_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_LIST_SUBDEVICES = Operation(
    "hubs.list_subdevices",
    "POST",
    "/api/lapp/hub/device/sub/list",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_SUBDEVICE_INFO = Operation(
    "hubs.subdevice_info",
    "POST",
    "/api/lapp/hub/device/sub/info",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_CHANNELS = Operation(
    "hubs.channels",
    "POST",
    "/api/lapp/hub/device/camera/list",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)


def _require_region(scope: str) -> None:
    if scope != "domestic":
        raise ValueError("hubs is only supported in the domestic region")


def _device_serial(value: object) -> str:
    serial = _text("deviceSerial", value)
    _utf8("deviceSerial", serial)
    if any("a" <= char <= "z" for char in serial):
        raise ValueError(
            "deviceSerial must use uppercase ASCII letters; "
            "the SDK never uppercases or strips silently"
        )
    return serial


def _version(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("version must be a str")
    _utf8("version", value)
    return value


class Hubs:
    """Synchronous facade over the domestic HUB query endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list_subdevices(self, hub_serial: str, *, version: str | None = None) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="hubs.list_subdevices",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            data = {"deviceSerial": _device_serial(hub_serial)}
            if version is not None:
                data["version"] = _version(version)
            return self._client.request(_LIST_SUBDEVICES, data=data, headers=_FORM_HEADERS)

    def subdevice_info(self, subdevice_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="hubs.subdevice_info",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            return self._client.request(
                _SUBDEVICE_INFO,
                data={"deviceSerial": _device_serial(subdevice_serial)},
                headers=_FORM_HEADERS,
            )

    def channels(self, subdevice_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="hubs.channels",
            mode="sync",
        ):
            _require_region(self._client.region.scope)
            return self._client.request(
                _CHANNELS,
                data={"deviceSerial": _device_serial(subdevice_serial)},
                headers=_FORM_HEADERS,
            )


class AsyncHubs:
    """Asynchronous facade over the domestic HUB query endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list_subdevices(self, hub_serial: str, *, version: str | None = None) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="hubs.list_subdevices",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            data = {"deviceSerial": _device_serial(hub_serial)}
            if version is not None:
                data["version"] = _version(version)
            return await self._client.request(_LIST_SUBDEVICES, data=data, headers=_FORM_HEADERS)

    async def subdevice_info(self, subdevice_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="hubs.subdevice_info",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            return await self._client.request(
                _SUBDEVICE_INFO,
                data={"deviceSerial": _device_serial(subdevice_serial)},
                headers=_FORM_HEADERS,
            )

    async def channels(self, subdevice_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="hubs.channels",
            mode="async",
        ):
            _require_region(self._client.region.scope)
            return await self._client.request(
                _CHANNELS,
                data={"deviceSerial": _device_serial(subdevice_serial)},
                headers=_FORM_HEADERS,
            )
