"""Third-party protocol project and device licence clients.

Covers the domestic ``/api/service/thirdproto/{protocolType}/`` endpoints for
opening protocol projects, applying device licences, polling batch licence
tasks, paging registered devices, and editing licence fields. Writes submit
``application/x-www-form-urlencoded`` bodies and always set the form
Content-Type header explicitly, even when the request body is empty.
The separate GB license endpoint reads one page of rows for a single product key
through a GET form request.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from . import third_party_models as _models
from .diagnostic_report import _diagnostic_scope
from .live_limits import _wire_int
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text, _require_domestic
from .storage_models import _integer

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    redact_message_urls=True,
    redact_registration_fields=True,
)

_OP_OPEN_PROJECT = Operation(
    "third_party_protocols.open_project",
    "POST",
    "/api/service/thirdproto/{protocolType}/project",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_POLICY,
)
_OP_LIST_GB_LICENSES = Operation(
    "third_party_protocols.list_gb_licenses",
    "GET",
    "/api/v3/device/register/gb/license/list",
    token_location="form",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=replace(_POLICY, redact_device_add_details=True),
)

_OP_GET_PROJECT = Operation(
    "third_party_protocols.get_project",
    "GET",
    "/api/service/thirdproto/{protocolType}/project",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=_POLICY,
)
_OP_APPLY_DEVICE = Operation(
    "third_party_protocols.apply_device",
    "POST",
    "/api/service/thirdproto/{protocolType}/deviceLicense/apply",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_POLICY,
)
_OP_APPLY_BATCH = Operation(
    "third_party_protocols.apply_batch",
    "POST",
    "/api/service/thirdproto/{protocolType}/licenses/apply",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_POLICY,
)
_OP_GET_TASK = Operation(
    "third_party_protocols.get_task",
    "GET",
    "/api/service/thirdproto/{protocolType}/licenses/task",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=_POLICY,
)
_OP_LIST_DEVICES = Operation(
    "third_party_protocols.list_devices",
    "GET",
    "/api/service/thirdproto/{protocolType}/devices/page",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=_POLICY,
)
_OP_EDIT_DEVICE = Operation(
    "third_party_protocols.edit_device",
    "POST",
    "/api/service/thirdproto/{protocolType}/licenses/edit",
    token_location="header",
    regions=frozenset({"domestic"}),
    read_only=False,
    policy=_POLICY,
)


def _gb_license_form(
    product_key: str, page_index: int | None, page_size: int | None
) -> dict[str, str]:
    fields = {"productKey": _check_text(product_key, "product_key")}
    if page_index is not None:
        index = _integer("page_index", page_index)
        if index < 0:
            raise ValueError("page_index must be greater than or equal to 0")
        fields["pageIndex"] = _wire_int("page_index", index)
    if page_size is not None:
        size = _integer("page_size", page_size)
        if size < 1 or size > 50:
            raise ValueError("page_size must be between 1 and 50")
        fields["pageSize"] = _wire_int("page_size", size)
    return fields


def _gb_license_response(response: ApiResponse) -> ApiResponse:
    if isinstance(response.data, list) and all(isinstance(row, dict) for row in response.data):
        return response
    raise _models._shape(response)


class ThirdPartyProtocols:
    """Third-party protocol operations bound to a single client."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def open_project(self, protocol: str) -> ApiResponse:
        "Open project for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.open_project",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_open_project(selected)
            return self._send(_OP_OPEN_PROJECT, selected, fields)

    def get_project(self, protocol: str) -> ApiResponse:
        "Get project for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.get_project",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_get_project(selected)
            return self._send(_OP_GET_PROJECT, selected, fields)

    def apply_device(
        self,
        protocol: str,
        device_name: str,
        *,
        device_password: str | None = None,
        device_username: str | None = None,
        channel_count: int | None = None,
        device_code: str | None = None,
        vehicle_plate: str | None = None,
        vehicle_color: int | None = None,
    ) -> ApiResponse:
        "Apply device for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.apply_device",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_apply_device(
                selected,
                device_name,
                device_password=device_password,
                device_username=device_username,
                channel_count=channel_count,
                device_code=device_code,
                vehicle_plate=vehicle_plate,
                vehicle_color=vehicle_color,
            )
            return self._send(_OP_APPLY_DEVICE, selected, fields)

    def apply_batch(
        self,
        protocol: str,
        *,
        device_count: int | None = None,
        device_codes: Sequence[str] | None = None,
        device_password: str | None = None,
        device_username: str | None = None,
        channel_count: int | None = None,
        vehicle_plates: Sequence[str] | None = None,
        vehicle_colors: Sequence[int] | None = None,
    ) -> ApiResponse:
        "Apply batch for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.apply_batch",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_apply_batch(
                selected,
                device_count=device_count,
                device_codes=device_codes,
                device_password=device_password,
                device_username=device_username,
                channel_count=channel_count,
                vehicle_plates=vehicle_plates,
                vehicle_colors=vehicle_colors,
            )
            return self._send(_OP_APPLY_BATCH, selected, fields)

    def get_task(self, protocol: str, task_id: int) -> ApiResponse:
        "Get task for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.get_task",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_get_task(selected, task_id)
            return self._send(_OP_GET_TASK, selected, fields)

    def list_gb_licenses(
        self, product_key: str, *, page_index: int | None = None, page_size: int | None = None
    ) -> ApiResponse:
        """Read one page of GB license rows for a product key."""
        with _diagnostic_scope(
            self._client, entrypoint="third_party_protocols.list_gb_licenses", mode="sync"
        ):
            _require_domestic(self._client.region.scope)
            fields = _gb_license_form(product_key, page_index, page_size)
            response = self._client.request(
                _OP_LIST_GB_LICENSES,
                data=fields,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return _gb_license_response(response)

    def list_devices(
        self,
        protocol: str,
        *,
        device_serial: str | None = None,
        task_id: int | None = None,
        start_index: int = 0,
        page_size: int = 10,
    ) -> ApiResponse:
        "List devices for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.list_devices",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_list_devices(
                selected,
                device_serial=device_serial,
                task_id=task_id,
                start_index=start_index,
                page_size=page_size,
            )
            return self._send(_OP_LIST_DEVICES, selected, fields)

    def edit_device(
        self,
        protocol: str,
        device_serial: str,
        *,
        device_name: str | None = None,
        device_password: str | None = None,
        device_username: str | None = None,
        vehicle_plate: str | None = None,
        vehicle_color: int | None = None,
    ) -> ApiResponse:
        "Edit device for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.edit_device",
            mode="sync",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_edit_device(
                selected,
                device_serial,
                device_name=device_name,
                device_password=device_password,
                device_username=device_username,
                vehicle_plate=vehicle_plate,
                vehicle_color=vehicle_color,
            )
            return self._send(_OP_EDIT_DEVICE, selected, fields)

    def _send(self, op: Operation, protocol: str, fields: dict[str, str]) -> ApiResponse:
        response = self._client.request(
            op,
            path_params={"protocolType": protocol},
            params=(fields or None) if op.read_only else None,
            data=(fields or None) if not op.read_only else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
            if not op.read_only
            else None,
        )
        return _models.checked(response)


class AsyncThirdPartyProtocols:
    """Third-party protocol operations bound to a single client."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def open_project(self, protocol: str) -> ApiResponse:
        "Open project for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.open_project",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_open_project(selected)
            return await self._send(_OP_OPEN_PROJECT, selected, fields)

    async def get_project(self, protocol: str) -> ApiResponse:
        "Get project for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.get_project",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_get_project(selected)
            return await self._send(_OP_GET_PROJECT, selected, fields)

    async def apply_device(
        self,
        protocol: str,
        device_name: str,
        *,
        device_password: str | None = None,
        device_username: str | None = None,
        channel_count: int | None = None,
        device_code: str | None = None,
        vehicle_plate: str | None = None,
        vehicle_color: int | None = None,
    ) -> ApiResponse:
        "Apply device for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.apply_device",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_apply_device(
                selected,
                device_name,
                device_password=device_password,
                device_username=device_username,
                channel_count=channel_count,
                device_code=device_code,
                vehicle_plate=vehicle_plate,
                vehicle_color=vehicle_color,
            )
            return await self._send(_OP_APPLY_DEVICE, selected, fields)

    async def apply_batch(
        self,
        protocol: str,
        *,
        device_count: int | None = None,
        device_codes: Sequence[str] | None = None,
        device_password: str | None = None,
        device_username: str | None = None,
        channel_count: int | None = None,
        vehicle_plates: Sequence[str] | None = None,
        vehicle_colors: Sequence[int] | None = None,
    ) -> ApiResponse:
        "Apply batch for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.apply_batch",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_apply_batch(
                selected,
                device_count=device_count,
                device_codes=device_codes,
                device_password=device_password,
                device_username=device_username,
                channel_count=channel_count,
                vehicle_plates=vehicle_plates,
                vehicle_colors=vehicle_colors,
            )
            return await self._send(_OP_APPLY_BATCH, selected, fields)

    async def get_task(self, protocol: str, task_id: int) -> ApiResponse:
        "Get task for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.get_task",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_get_task(selected, task_id)
            return await self._send(_OP_GET_TASK, selected, fields)

    async def list_gb_licenses(
        self, product_key: str, *, page_index: int | None = None, page_size: int | None = None
    ) -> ApiResponse:
        """Read one page of GB license rows for a product key."""
        with _diagnostic_scope(
            self._client, entrypoint="third_party_protocols.list_gb_licenses", mode="async"
        ):
            _require_domestic(self._client.region.scope)
            fields = _gb_license_form(product_key, page_index, page_size)
            response = await self._client.request(
                _OP_LIST_GB_LICENSES,
                data=fields,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return _gb_license_response(response)

    async def list_devices(
        self,
        protocol: str,
        *,
        device_serial: str | None = None,
        task_id: int | None = None,
        start_index: int = 0,
        page_size: int = 10,
    ) -> ApiResponse:
        "List devices for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.list_devices",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_list_devices(
                selected,
                device_serial=device_serial,
                task_id=task_id,
                start_index=start_index,
                page_size=page_size,
            )
            return await self._send(_OP_LIST_DEVICES, selected, fields)

    async def edit_device(
        self,
        protocol: str,
        device_serial: str,
        *,
        device_name: str | None = None,
        device_password: str | None = None,
        device_username: str | None = None,
        vehicle_plate: str | None = None,
        vehicle_color: int | None = None,
    ) -> ApiResponse:
        "Edit device for one domestic protocol."
        with _diagnostic_scope(
            self._client,
            entrypoint="third_party_protocols.edit_device",
            mode="async",
        ):
            selected = _models.protocol_for(self._client.region.scope, protocol)
            fields = _models.fields_edit_device(
                selected,
                device_serial,
                device_name=device_name,
                device_password=device_password,
                device_username=device_username,
                vehicle_plate=vehicle_plate,
                vehicle_color=vehicle_color,
            )
            return await self._send(_OP_EDIT_DEVICE, selected, fields)

    async def _send(self, op: Operation, protocol: str, fields: dict[str, str]) -> ApiResponse:
        response = await self._client.request(
            op,
            path_params={"protocolType": protocol},
            params=(fields or None) if op.read_only else None,
            data=(fields or None) if not op.read_only else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
            if not op.read_only
            else None,
        )
        return _models.checked(response)
