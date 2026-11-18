"""Domestic passenger-flow statistics endpoints (open.ys7.com docs 688-693).

All six endpoints are domestic-only LAPP form POSTs whose business success
code is the string ``"200"``. ``switch_status`` (688) reads the device-level
counting switch and never sends ``channelNo``; ``daily`` (690) and ``hourly``
(691) require an explicit channel; ``set_switch`` (689) and ``set_config``
(692) are write acknowledgements that are never replayed automatically; and
``get_config`` (693) reads the counting ``line``/``direction`` geometry back.

The SDK preserves every ``ApiResponse`` untouched: it never fills missing
report rows, rewrites coordinates, drops unknown fields, or treats a write
ack as proof the device applied the change.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from ._passenger_flow_forms import (
    config_form,
    daily_form,
    device_form,
    hourly_form,
    switch_form,
)
from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncPassengerFlow", "PassengerFlow"]

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

_LAPP_POLICY = ResponsePolicy(
    code_path="code",
    message_path="msg",
    data_path="data",
    success_values=("200",),
)

_DOMESTIC_ONLY = frozenset({"domestic"})

_SWITCH_STATUS = Operation(
    "passenger_flow.switch_status",
    "POST",
    "/api/lapp/passengerflow/switch/status",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_SET_SWITCH = Operation(
    "passenger_flow.set_switch",
    "POST",
    "/api/lapp/passengerflow/switch/set",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=False,
    policy=_LAPP_POLICY,
)
_DAILY = Operation(
    "passenger_flow.daily",
    "POST",
    "/api/lapp/passengerflow/daily",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_HOURLY = Operation(
    "passenger_flow.hourly",
    "POST",
    "/api/lapp/passengerflow/hourly",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)
_SET_CONFIG = Operation(
    "passenger_flow.set_config",
    "POST",
    "/api/lapp/passengerflow/config/set",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=False,
    policy=_LAPP_POLICY,
)
_GET_CONFIG = Operation(
    "passenger_flow.get_config",
    "POST",
    "/api/lapp/passengerflow/config/get",
    token_location="form",
    regions=_DOMESTIC_ONLY,
    read_only=True,
    policy=_LAPP_POLICY,
)


def _shape_error(response: ApiResponse) -> ProtocolError:
    return ProtocolError(
        "unexpected passenger flow response data shape",
        http_status=response.http_status,
        operation=response.operation,
    )


def _expect_object(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, dict):
        raise _shape_error(response)
    return response


def _expect_array(response: ApiResponse) -> ApiResponse:
    if not isinstance(response.data, list):
        raise _shape_error(response)
    return response


def _expect_geometry(response: ApiResponse) -> ApiResponse:
    data = response.data
    if not (
        isinstance(data, dict)
        and isinstance(data.get("line"), dict)
        and isinstance(data.get("direction"), dict)
    ):
        raise _shape_error(response)
    return response


class PassengerFlow:
    """Synchronous facade over the domestic passenger-flow endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def switch_status(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.switch_status",
            mode="sync",
        ):
            form = device_form(self._client.region.scope, device_serial)
            response = self._client.request(
                _SWITCH_STATUS,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_object(response)

    def set_switch(
        self,
        device_serial: str,
        *,
        enable: int,
        channel_no: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.set_switch",
            mode="sync",
        ):
            form = switch_form(
                self._client.region.scope,
                device_serial,
                enable,
                channel_no,
            )
            return self._client.request(
                _SET_SWITCH,
                data=form,
                headers=_FORM_HEADERS,
            )

    def daily(
        self,
        device_serial: str,
        channel_no: int,
        *,
        day: datetime | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.daily",
            mode="sync",
        ):
            form = daily_form(
                self._client.region.scope,
                device_serial,
                channel_no,
                day,
            )
            response = self._client.request(
                _DAILY,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_object(response)

    def hourly(
        self,
        device_serial: str,
        channel_no: int,
        *,
        date_ms: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.hourly",
            mode="sync",
        ):
            form = hourly_form(
                self._client.region.scope,
                device_serial,
                channel_no,
                date_ms,
            )
            response = self._client.request(
                _HOURLY,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)

    def set_config(
        self,
        device_serial: str,
        *,
        line: Mapping[str, str],
        direction: Mapping[str, str],
        channel_no: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.set_config",
            mode="sync",
        ):
            form = config_form(
                self._client.region.scope,
                device_serial,
                line,
                direction,
                channel_no,
            )
            return self._client.request(
                _SET_CONFIG,
                data=form,
                headers=_FORM_HEADERS,
            )

    def get_config(
        self,
        device_serial: str,
        *,
        channel_no: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.get_config",
            mode="sync",
        ):
            form = device_form(
                self._client.region.scope,
                device_serial,
                channel_no,
            )
            response = self._client.request(
                _GET_CONFIG,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_geometry(response)


class AsyncPassengerFlow:
    """Asynchronous facade over the domestic passenger-flow endpoints."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def switch_status(self, device_serial: str) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.switch_status",
            mode="async",
        ):
            form = device_form(self._client.region.scope, device_serial)
            response = await self._client.request(
                _SWITCH_STATUS,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_object(response)

    async def set_switch(
        self,
        device_serial: str,
        *,
        enable: int,
        channel_no: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.set_switch",
            mode="async",
        ):
            form = switch_form(
                self._client.region.scope,
                device_serial,
                enable,
                channel_no,
            )
            return await self._client.request(
                _SET_SWITCH,
                data=form,
                headers=_FORM_HEADERS,
            )

    async def daily(
        self,
        device_serial: str,
        channel_no: int,
        *,
        day: datetime | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.daily",
            mode="async",
        ):
            form = daily_form(
                self._client.region.scope,
                device_serial,
                channel_no,
                day,
            )
            response = await self._client.request(
                _DAILY,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_object(response)

    async def hourly(
        self,
        device_serial: str,
        channel_no: int,
        *,
        date_ms: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.hourly",
            mode="async",
        ):
            form = hourly_form(
                self._client.region.scope,
                device_serial,
                channel_no,
                date_ms,
            )
            response = await self._client.request(
                _HOURLY,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_array(response)

    async def set_config(
        self,
        device_serial: str,
        *,
        line: Mapping[str, str],
        direction: Mapping[str, str],
        channel_no: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.set_config",
            mode="async",
        ):
            form = config_form(
                self._client.region.scope,
                device_serial,
                line,
                direction,
                channel_no,
            )
            return await self._client.request(
                _SET_CONFIG,
                data=form,
                headers=_FORM_HEADERS,
            )

    async def get_config(
        self,
        device_serial: str,
        *,
        channel_no: int | None = None,
    ) -> ApiResponse:
        with _diagnostic_scope(
            self._client,
            entrypoint="passenger_flow.get_config",
            mode="async",
        ):
            form = device_form(
                self._client.region.scope,
                device_serial,
                channel_no,
            )
            response = await self._client.request(
                _GET_CONFIG,
                data=form,
                headers=_FORM_HEADERS,
            )
            return _expect_geometry(response)
