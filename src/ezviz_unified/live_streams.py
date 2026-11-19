"""Domestic cloud live stream management facade.

``list`` and ``records`` are read-only views over issued streams and
their cloud record entries. ``create_device`` and ``create_rtmp``
issue new stream windows, ``address`` issues playback or RTMP
publishing (push) addresses, and ``update_window``, ``delete`` and
``set_status`` manage issued streams. The facade only submits
authenticated management requests; it never opens, pulls, pushes,
or replays media addresses on its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from . import live_stream_models as _models
from .diagnostic_report import _diagnostic_scope
from .vod import _Request

if TYPE_CHECKING:
    from .client import AsyncClient, Client
    from .models import ApiResponse

__all__ = ("AsyncLiveStreams", "LiveStreams")


class LiveStreams:
    """Synchronous facade for domestic live stream management."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def create_device(
        self,
        device_serial: str,
        *,
        start_time: datetime,
        end_time: datetime,
        local_index: int | None = None,
    ) -> ApiResponse:
        """Issue a stream window attached to a device channel."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.create_device", mode="sync"):
            request = _models.prepare_create_device(
                self._client.region.scope,
                device_serial,
                start_time=start_time,
                end_time=end_time,
                local_index=local_index,
            )
            return self._send(request)

    def create_rtmp(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        enable_record: int | None = None,
        record_space_id: str | None = None,
        record_duration: int | None = None,
        record_format: int | None = None,
        delay_time: int | None = None,
    ) -> ApiResponse:
        """Issue an RTMP ingest stream window."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.create_rtmp", mode="sync"):
            request = _models.prepare_create_rtmp(
                self._client.region.scope,
                start_time=start_time,
                end_time=end_time,
                enable_record=enable_record,
                record_space_id=record_space_id,
                record_duration=record_duration,
                record_format=record_format,
                delay_time=delay_time,
            )
            return self._send(request)

    def update_window(
        self,
        stream_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> ApiResponse:
        """Update the time window of an issued stream."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.update_window", mode="sync"):
            request = _models.prepare_update_window(
                self._client.region.scope,
                stream_id,
                start_time=start_time,
                end_time=end_time,
            )
            return self._send(request)

    def delete(self, stream_id: str) -> ApiResponse:
        """Delete an issued stream by its string stream ID."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.delete", mode="sync"):
            request = _models.prepare_delete(self._client.region.scope, stream_id)
            return self._send(request)

    def set_status(self, stream_id: str, status: int) -> ApiResponse:
        """Submit a stream status flag (0 disable, 1 enable)."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.set_status", mode="sync"):
            request = _models.prepare_set_status(self._client.region.scope, stream_id, status)
            return self._send(request)

    def list(
        self,
        *,
        device_serial: str | None = None,
        stream_id: str | None = None,
        page_start: int = 0,
        page_size: int = 50,
        access_type: int | None = None,
        status: int | None = None,
    ) -> ApiResponse:
        """List issued streams; read-only."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.list", mode="sync"):
            request = _models.prepare_list(
                self._client.region.scope,
                device_serial=device_serial,
                stream_id=stream_id,
                page_start=page_start,
                page_size=page_size,
                access_type=access_type,
                status=status,
            )
            return self._send(request)

    def records(
        self,
        stream_id: int,
        *,
        start_time: datetime,
        end_time: datetime,
        page_start: int = 0,
        page_size: int = 50,
    ) -> ApiResponse:
        """List cloud record entries for a stream; read-only."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.records", mode="sync"):
            request = _models.prepare_records(
                self._client.region.scope,
                stream_id,
                start_time=start_time,
                end_time=end_time,
                page_start=page_start,
                page_size=page_size,
            )
            return self._send(request)

    def address(
        self,
        stream_id: int,
        protocol: int,
        *,
        address_type: int = 1,
        quality: int | None = None,
        support_h265: int | None = None,
        mute: int | None = None,
        expire_time: int | None = None,
    ) -> ApiResponse:
        """Issue a playback or RTMP push address; never accessed automatically."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.address", mode="sync"):
            request = _models.prepare_address(
                self._client.region.scope,
                stream_id,
                protocol,
                address_type=address_type,
                quality=quality,
                support_h265=support_h265,
                mute=mute,
                expire_time=expire_time,
            )
            return self._send(request)

    def _send(self, request: _Request) -> ApiResponse:
        response = self._client.request(
            request.operation,
            params=request.params,
            data=request.data,
            headers=request.headers,
        )
        return _models.checked(response)


class AsyncLiveStreams:
    """Asynchronous facade for domestic live stream management."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_device(
        self,
        device_serial: str,
        *,
        start_time: datetime,
        end_time: datetime,
        local_index: int | None = None,
    ) -> ApiResponse:
        """Issue a stream window attached to a device channel."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.create_device", mode="async"):
            request = _models.prepare_create_device(
                self._client.region.scope,
                device_serial,
                start_time=start_time,
                end_time=end_time,
                local_index=local_index,
            )
            return await self._send(request)

    async def create_rtmp(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        enable_record: int | None = None,
        record_space_id: str | None = None,
        record_duration: int | None = None,
        record_format: int | None = None,
        delay_time: int | None = None,
    ) -> ApiResponse:
        """Issue an RTMP ingest stream window."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.create_rtmp", mode="async"):
            request = _models.prepare_create_rtmp(
                self._client.region.scope,
                start_time=start_time,
                end_time=end_time,
                enable_record=enable_record,
                record_space_id=record_space_id,
                record_duration=record_duration,
                record_format=record_format,
                delay_time=delay_time,
            )
            return await self._send(request)

    async def update_window(
        self,
        stream_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> ApiResponse:
        """Update the time window of an issued stream."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.update_window", mode="async"):
            request = _models.prepare_update_window(
                self._client.region.scope,
                stream_id,
                start_time=start_time,
                end_time=end_time,
            )
            return await self._send(request)

    async def delete(self, stream_id: str) -> ApiResponse:
        """Delete an issued stream by its string stream ID."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.delete", mode="async"):
            request = _models.prepare_delete(self._client.region.scope, stream_id)
            return await self._send(request)

    async def set_status(self, stream_id: str, status: int) -> ApiResponse:
        """Submit a stream status flag (0 disable, 1 enable)."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.set_status", mode="async"):
            request = _models.prepare_set_status(self._client.region.scope, stream_id, status)
            return await self._send(request)

    async def list(
        self,
        *,
        device_serial: str | None = None,
        stream_id: str | None = None,
        page_start: int = 0,
        page_size: int = 50,
        access_type: int | None = None,
        status: int | None = None,
    ) -> ApiResponse:
        """List issued streams; read-only."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.list", mode="async"):
            request = _models.prepare_list(
                self._client.region.scope,
                device_serial=device_serial,
                stream_id=stream_id,
                page_start=page_start,
                page_size=page_size,
                access_type=access_type,
                status=status,
            )
            return await self._send(request)

    async def records(
        self,
        stream_id: int,
        *,
        start_time: datetime,
        end_time: datetime,
        page_start: int = 0,
        page_size: int = 50,
    ) -> ApiResponse:
        """List cloud record entries for a stream; read-only."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.records", mode="async"):
            request = _models.prepare_records(
                self._client.region.scope,
                stream_id,
                start_time=start_time,
                end_time=end_time,
                page_start=page_start,
                page_size=page_size,
            )
            return await self._send(request)

    async def address(
        self,
        stream_id: int,
        protocol: int,
        *,
        address_type: int = 1,
        quality: int | None = None,
        support_h265: int | None = None,
        mute: int | None = None,
        expire_time: int | None = None,
    ) -> ApiResponse:
        """Issue a playback or RTMP push address; never accessed automatically."""
        with _diagnostic_scope(self._client, entrypoint="live_streams.address", mode="async"):
            request = _models.prepare_address(
                self._client.region.scope,
                stream_id,
                protocol,
                address_type=address_type,
                quality=quality,
                support_h265=support_h265,
                mute=mute,
                expire_time=expire_time,
            )
            return await self._send(request)

    async def _send(self, request: _Request) -> ApiResponse:
        response = await self._client.request(
            request.operation,
            params=request.params,
            data=request.data,
            headers=request.headers,
        )
        return _models.checked(response)
