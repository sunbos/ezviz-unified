"""Domestic device voice catalog operations.

All four operations are domestic-only and share
``/api/route/voice/v3/devices/voices``; every parameter travels in the
query string (managed ``accessToken`` included) with no request body and
no business ``Content-Type``. ``list`` (GET) reads the device's custom
voice materials from the top-level ``voiceInfos`` array, ``create``
(POST) registers one material from its ``voiceUrl`` and returns the
top-level ``voiceInfo`` object whose integer ``voiceId`` is required,
while ``rename`` (PUT) and ``delete`` (DELETE) address one material
selected by its integer ``voiceId`` together with the material's
``voiceName`` and ``voiceUrl``. Serials, names and URLs are opaque
non-blank UTF-8 text: the SDK never normalizes, signs, probes, downloads
or extension-checks them, and never substitutes an alternate method or
parameter location. Success requires integer ``meta.code == 200``; the
PUT and DELETE acknowledgements may omit ``data``, GET rows pass through
untouched with unknown fields and raw timestamps preserved, and a
successful POST is never treated as proof the device already synced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .diagnostic_report import _diagnostic_scope
from .errors import ProtocolError
from .live_limits import _wire_int
from .models import ApiResponse, ResponsePolicy
from .operations import Operation
from .settings import _check_text
from .storage_models import _integer

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncDeviceVoice", "DeviceVoice"]

_DOMESTIC_ONLY = frozenset({"domestic"})

_LIST_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="voiceInfos",
    success_values=(200,),
    redact_message_urls=True,
)
_CREATE_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="voiceInfo",
    success_values=(200,),
    redact_message_urls=True,
)
_ACK_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    redact_message_urls=True,
)

_LIST = Operation(
    name="device_voice.list",
    method="GET",
    path="/api/route/voice/v3/devices/voices",
    regions=_DOMESTIC_ONLY,
    token_location="query",
    read_only=True,
    policy=_LIST_POLICY,
)
_CREATE = Operation(
    name="device_voice.create",
    method="POST",
    path="/api/route/voice/v3/devices/voices",
    regions=_DOMESTIC_ONLY,
    token_location="query",
    read_only=False,
    policy=_CREATE_POLICY,
)
_RENAME = Operation(
    name="device_voice.rename",
    method="PUT",
    path="/api/route/voice/v3/devices/voices",
    regions=_DOMESTIC_ONLY,
    token_location="query",
    read_only=False,
    policy=_ACK_POLICY,
)
_DELETE = Operation(
    name="device_voice.delete",
    method="DELETE",
    path="/api/route/voice/v3/devices/voices",
    regions=_DOMESTIC_ONLY,
    token_location="query",
    read_only=False,
    policy=_ACK_POLICY,
)


def _require_domestic(client: Client | AsyncClient) -> None:
    if client.region.scope != "domestic":
        raise ValueError("device voice operations require the domestic region")


def _material_params(
    device_serial: str,
    voice_name: str,
    voice_url: str,
) -> dict[str, str]:
    return {
        "deviceSerial": _check_text(device_serial, "deviceSerial"),
        "voiceName": _check_text(voice_name, "voiceName"),
        "voiceUrl": _check_text(voice_url, "voiceUrl"),
    }


def _selector_params(
    device_serial: str,
    voice_id: int,
    voice_name: str,
    voice_url: str,
) -> dict[str, str]:
    return {
        "deviceSerial": _check_text(device_serial, "deviceSerial"),
        "voiceId": _wire_int("voiceId", _integer("voiceId", voice_id)),
        "voiceName": _check_text(voice_name, "voiceName"),
        "voiceUrl": _check_text(voice_url, "voiceUrl"),
    }


def _check_voice_rows(response: ApiResponse) -> ApiResponse:
    rows = response.data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ProtocolError(
            "device_voice.list returned an unexpected voiceInfos payload",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


def _check_voice_info(response: ApiResponse) -> ApiResponse:
    info = response.data
    if not isinstance(info, dict) or type(info.get("voiceId")) is not int:
        raise ProtocolError(
            "device_voice.create returned an unexpected voiceInfo payload",
            operation=response.operation,
            http_status=response.http_status,
        )
    return response


class DeviceVoice:
    """Synchronous domestic device voice catalog client."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self, device_serial: str) -> ApiResponse:
        """List the device's custom voice materials (``voiceInfos``)."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.list", mode="sync"):
            _require_domestic(self._client)
            serial = _check_text(device_serial, "deviceSerial")
            response = self._client.request(_LIST, params={"deviceSerial": serial})
            return _check_voice_rows(response)

    def create(
        self,
        device_serial: str,
        *,
        voice_name: str,
        voice_url: str,
    ) -> ApiResponse:
        """Create one voice material; ``data`` carries ``voiceInfo``."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.create", mode="sync"):
            _require_domestic(self._client)
            params = _material_params(device_serial, voice_name, voice_url)
            response = self._client.request(_CREATE, params=params)
            return _check_voice_info(response)

    def rename(
        self,
        device_serial: str,
        voice_id: int,
        *,
        voice_name: str,
        voice_url: str,
    ) -> ApiResponse:
        """Rename one voice material selected by ``voiceId``/``voiceUrl``."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.rename", mode="sync"):
            _require_domestic(self._client)
            params = _selector_params(device_serial, voice_id, voice_name, voice_url)
            return self._client.request(_RENAME, params=params)

    def delete(
        self,
        device_serial: str,
        voice_id: int,
        *,
        voice_name: str,
        voice_url: str,
    ) -> ApiResponse:
        """Delete one voice material selected by ``voiceId``/``voiceUrl``."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.delete", mode="sync"):
            _require_domestic(self._client)
            params = _selector_params(device_serial, voice_id, voice_name, voice_url)
            return self._client.request(_DELETE, params=params)


class AsyncDeviceVoice:
    """Asynchronous domestic device voice catalog client."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list(self, device_serial: str) -> ApiResponse:
        """List the device's custom voice materials (``voiceInfos``)."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.list", mode="async"):
            _require_domestic(self._client)
            serial = _check_text(device_serial, "deviceSerial")
            response = await self._client.request(_LIST, params={"deviceSerial": serial})
            return _check_voice_rows(response)

    async def create(
        self,
        device_serial: str,
        *,
        voice_name: str,
        voice_url: str,
    ) -> ApiResponse:
        """Create one voice material; ``data`` carries ``voiceInfo``."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.create", mode="async"):
            _require_domestic(self._client)
            params = _material_params(device_serial, voice_name, voice_url)
            response = await self._client.request(_CREATE, params=params)
            return _check_voice_info(response)

    async def rename(
        self,
        device_serial: str,
        voice_id: int,
        *,
        voice_name: str,
        voice_url: str,
    ) -> ApiResponse:
        """Rename one voice material selected by ``voiceId``/``voiceUrl``."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.rename", mode="async"):
            _require_domestic(self._client)
            params = _selector_params(device_serial, voice_id, voice_name, voice_url)
            return await self._client.request(_RENAME, params=params)

    async def delete(
        self,
        device_serial: str,
        voice_id: int,
        *,
        voice_name: str,
        voice_url: str,
    ) -> ApiResponse:
        """Delete one voice material selected by ``voiceId``/``voiceUrl``."""
        with _diagnostic_scope(self._client, entrypoint="device_voice.delete", mode="async"):
            _require_domestic(self._client)
            params = _selector_params(device_serial, voice_id, voice_name, voice_url)
            return await self._client.request(_DELETE, params=params)
