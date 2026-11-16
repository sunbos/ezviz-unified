"""Boundary tests: diagnostics never run code on untrusted objects.

Red for two precise-type rules: only exact ``ValueError``/``TypeError``
may claim ``validation``; only an exact ``RegionConfig`` has ``scope``
read — subclass regions record ``unknown`` without one getter call.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ezviz_unified.auth_models import RegionConfig
from ezviz_unified.client import AsyncClient, Client
from ezviz_unified.components import AsyncComponents, Components
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.models import ResponsePolicy
from ezviz_unified.operations import Operation

_FACE_VOICE_SET = "cn.components.face_voice_video.set"
_ANON_OP = Operation(
    "test.anon.ping",
    "GET",
    "/api/test/ping",
    token_location="none",
    regions=frozenset({"domestic"}),
    read_only=True,
    policy=ResponsePolicy(
        code_path="code",
        message_path="message",
        data_path="data",
        success_values=(200,),
    ),
)
_MARKERS = (
    type("MValueError", (ValueError,), {}),
    type("MTypeError", (TypeError,), {}),
)


class _RejectingTransport(httpx.MockTransport):
    """Counts and refuses transport sends; close/aclose stay free."""

    def __init__(self) -> None:
        self.calls = 0
        super().__init__(self._reject)

    def _reject(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise AssertionError("transport must not be used")


class _CountingRegion(RegionConfig):
    """First armed ``scope`` read raises the stored instance, then works."""

    def __getattribute__(self, name: str) -> object:
        if name == "scope" and self.__dict__.get("_armed"):
            self.__dict__["scope_reads"] += 1
            pending = self.__dict__.pop("_pending", None)
            if pending is not None:
                raise pending
        return super().__getattribute__(name)


class _ExplodingPayload(dict):
    """``items()`` raises the stored unknown-subclass marker instance."""

    def items(self) -> object:
        raise self._error


def _client(
    region: RegionConfig,
    transport: _RejectingTransport,
    recorder: DiagnosticRecorder | None,
    *,
    aio: bool,
):
    cls = AsyncClient if aio else Client
    return cls(
        region=region,
        anonymous=True,
        transport_factory=lambda _origin: transport,
        diagnostic_recorder=recorder,
    )


async def _region_case(recorder_on: bool, aio: bool) -> None:
    marker = ValueError("poisoned first scope read")
    region = _CountingRegion(scope="domestic", auth_base_url="https://open.ys7.com")
    region.__dict__.update(_armed=False, scope_reads=0, _pending=marker)
    transport = _RejectingTransport()
    recorder = DiagnosticRecorder() if recorder_on else None
    client = _client(region, transport, recorder, aio=aio)
    region.__dict__["_armed"] = True
    try:
        with pytest.raises(ValueError) as raised:
            if aio:
                await client.request(_ANON_OP)
            else:
                client.request(_ANON_OP)
        assert raised.value is marker
        assert transport.calls == 0
        assert region.scope_reads == 1
    finally:
        if aio:
            await client.aclose()
        else:
            client.close()
    assert region.scope_reads == 1  # close must not read scope either
    if recorder is not None:
        assert {e["region"] for e in recorder.report()["events"]} == {"unknown"}


@pytest.mark.parametrize("aio", [False, True])
@pytest.mark.parametrize("recorder_on", [False, True])
def test_region_subclass_scope_never_read(aio: bool, recorder_on: bool) -> None:
    asyncio.run(_region_case(recorder_on, aio))


async def _payload_case(payload: object, aio: bool):
    recorder = DiagnosticRecorder()
    transport = _RejectingTransport()
    client = _client(RegionConfig.china(), transport, recorder, aio=aio)
    try:
        with pytest.raises((ValueError, TypeError)) as raised:
            if aio:
                await AsyncComponents(client).call(_FACE_VOICE_SET, "S", payload=payload)
            else:
                Components(client).call(_FACE_VOICE_SET, "S", payload=payload)
    finally:
        if aio:
            await client.aclose()
        else:
            client.close()
    assert transport.calls == 0
    return raised.value, [
        e["error_family"]
        for e in recorder.report()["events"]
        if e["entrypoint"] == "components.call" and e["outcome"] == "failed"
    ]


@pytest.mark.parametrize("aio", [False, True])
@pytest.mark.parametrize("marker", _MARKERS)
def test_unknown_subclass_marker_is_not_validation(aio: bool, marker) -> None:
    instance = marker("marker")
    payload = _ExplodingPayload()
    payload._error = instance
    caught, families = asyncio.run(_payload_case(payload, aio))
    assert caught is instance
    assert families == ["unknown"]


@pytest.mark.parametrize("aio", [False, True])
def test_exact_valueerror_stays_validation(aio: bool) -> None:
    caught, families = asyncio.run(_payload_case(object(), aio))
    assert type(caught) is ValueError
    assert families == ["validation"]


@pytest.mark.parametrize("marker", _MARKERS)
def test_capture_unknown_subclass_is_application(marker) -> None:
    recorder = DiagnosticRecorder()
    instance = marker("marker")
    with pytest.raises(marker) as raised:
        with recorder.capture():
            raise instance
    assert raised.value is instance
    assert [
        e["error_family"]
        for e in recorder.report()["events"]
        if e["kind"] == "application" and e["outcome"] == "failed"
    ] == ["application"]
