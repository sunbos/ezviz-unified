"""Process-control semantics for diagnostic recording internals.

Ordinary ``Exception`` faults inside the recorder stay fail-open and are
counted under ``recording_errors``. ``KeyboardInterrupt``/``SystemExit``/
``CancelledError`` and other non-``Exception`` ``BaseException`` instances
reaching an internal position must propagate as the same object: entry
interruption skips the business body, exit interruption may override a
finished body, and any already-installed scope is unwound back to the
pre-enter parent (``None`` at the root).
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import patch

import httpx
import pytest

import ezviz_unified.diagnostic_report as dr
from ezviz_unified.auth_models import RegionConfig
from ezviz_unified.client import AsyncClient, Client
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from ezviz_unified.models import ResponsePolicy
from ezviz_unified.operations import Operation

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
    KeyboardInterrupt("ctrl-c"),
    SystemExit(7),
    asyncio.CancelledError("cancel"),
)
_POSITIONS = ("construct", "push_mid", "enter_emit", "pop", "exit_emit")
_POSITION_SENDS = dict(zip(_POSITIONS, (0, 0, 0, 1, 1)))


class _CountingTransport(httpx.MockTransport):
    """Counts sends and returns a fixed success payload."""

    def __init__(self) -> None:
        self.calls = 0
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"code": 200, "message": "ok", "data": {}})


def _client(transport, recorder, *, aio: bool):
    cls = AsyncClient if aio else Client
    return cls(
        region=RegionConfig.china(),
        anonymous=True,
        transport_factory=lambda _origin: transport,
        diagnostic_recorder=recorder,
    )


def _half_push(recorder, marker):
    """Push that installed the scope in the ContextVar before being cut."""

    def _push(scope):
        recorder._scope_var.set(scope)
        raise marker

    return _push


def _position_patch(recorder, position, marker):
    """Patch the internal point named *position* to raise *marker*."""
    if position == "construct":
        return patch.object(dr, "_Scope", side_effect=marker)
    if position == "push_mid":
        return patch.object(recorder, "_push_scope", side_effect=_half_push(recorder, marker))
    if position == "pop":
        real_pop = recorder._pop_scope

        def _pop(token):
            scope = recorder._current_scope()
            if (
                scope is not None
                and scope._kind == "call"
                and scope._entrypoint == "client.request"
            ):
                raise marker
            return real_pop(token)

        return patch.object(recorder, "_pop_scope", side_effect=_pop)
    real_append = recorder._append
    if position == "enter_emit":

        def _append(event):
            if event["outcome"] == "started":
                raise marker
            return real_append(event)
    else:  # exit_emit: only the outer call's terminal event raises.

        def _append(event):
            if (
                event.get("kind") == "call"
                and event.get("entrypoint") == "client.request"
                and event["outcome"] != "started"
            ):
                raise marker
            return real_append(event)

    return patch.object(recorder, "_append", side_effect=_append)


async def _request(client, aio):
    if aio:
        return await client.request(_ANON_OP)
    return client.request(_ANON_OP)


async def _close(client, aio):
    if aio:
        await client.aclose()
    else:
        client.close()


@pytest.mark.parametrize("aio", [False, True])
@pytest.mark.parametrize("position", _POSITIONS)
def test_ordinary_internal_fault_stays_fail_open(aio, position):
    asyncio.run(_ordinary_case(aio, position))


async def _ordinary_case(aio, position):
    recorder = DiagnosticRecorder()
    transport = _CountingTransport()
    client = _client(transport, recorder, aio=aio)
    try:
        with _position_patch(recorder, position, RuntimeError("internal boom")):
            await _request(client, aio)
    finally:
        await _close(client, aio)
    assert transport.calls == 1
    assert recorder.report()["recording_errors"] >= 1


@pytest.mark.parametrize("aio", [False, True])
def test_business_exception_identity_preserved(aio):
    asyncio.run(_identity_case(aio))


async def _identity_case(aio):
    recorder = DiagnosticRecorder()
    transport = _CountingTransport()
    client = _client(transport, recorder, aio=aio)
    marker = RuntimeError("business boom")
    try:
        with (
            patch.object(client, "_transport_for_origin", side_effect=marker),
            patch.object(recorder, "_append", side_effect=RuntimeError("rec")),
        ):
            with pytest.raises(RuntimeError) as raised:
                await _request(client, aio)
    finally:
        await _close(client, aio)
    assert raised.value is marker
    assert transport.calls == 0
    assert recorder.report()["recording_errors"] >= 1


@pytest.mark.parametrize("aio", [False, True])
@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("marker", _MARKERS)
def test_control_exception_never_swallowed(aio, position, marker):
    asyncio.run(_control_case(aio, position, marker))


async def _control_case(aio, position, marker):
    recorder = DiagnosticRecorder()
    transport = _CountingTransport()
    client = _client(transport, recorder, aio=aio)
    try:
        with _position_patch(recorder, position, marker):
            with pytest.raises(type(marker)) as raised:
                await _request(client, aio)
    finally:
        await _close(client, aio)
    assert raised.value is marker
    assert transport.calls == _POSITION_SENDS[position]
    assert recorder._current_scope() is None


def test_nested_control_restores_parent_scope():
    recorder = DiagnosticRecorder()
    transport = _CountingTransport()
    client = _client(transport, recorder, aio=False)
    marker = KeyboardInterrupt("ctrl-c")
    try:
        with recorder.capture():
            parent = recorder._current_scope()
            assert parent is not None
            with (
                patch.object(
                    recorder,
                    "_push_scope",
                    side_effect=_half_push(recorder, marker),
                ),
                pytest.raises(KeyboardInterrupt) as raised,
            ):
                client.request(_ANON_OP)
            assert raised.value is marker
            assert recorder._current_scope() is parent
    finally:
        client.close()
    assert transport.calls == 0
    assert recorder._current_scope() is None


def test_real_sigint_inside_append_propagates():
    recorder = DiagnosticRecorder()
    transport = _CountingTransport()
    client = _client(transport, recorder, aio=False)

    def _sigint(event):
        signal.raise_signal(signal.SIGINT)

    try:
        with patch.object(recorder, "_append", side_effect=_sigint):
            with pytest.raises(KeyboardInterrupt):
                client.request(_ANON_OP)
    finally:
        client.close()
    assert transport.calls == 0
    assert recorder._current_scope() is None


@pytest.mark.parametrize("aio", [False, True])
def test_body_cancel_recorded_and_propagates(aio):
    asyncio.run(_cancel_case(aio))


async def _cancel_case(aio):
    recorder = DiagnosticRecorder()
    transport = _CountingTransport()
    client = _client(transport, recorder, aio=aio)
    marker = asyncio.CancelledError("user cancel")
    try:
        with patch.object(client, "_transport_for_origin", side_effect=marker):
            with pytest.raises(asyncio.CancelledError) as raised:
                await _request(client, aio)
        assert raised.value is marker
        assert transport.calls == 0
        events = recorder.report()["events"]
        assert [e["outcome"] for e in events] == [
            "started",
            "started",
            "cancelled",
            "cancelled",
        ]
        assert {e["error_family"] for e in events if e["outcome"] == "cancelled"} == {"interrupted"}
    finally:
        await _close(client, aio)
