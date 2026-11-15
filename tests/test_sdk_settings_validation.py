"""Regression tests for settings facade ``device_serial`` naming and text validation.

Pins two behaviors through the public API only, with no network access:

* every public ``Settings``/``AsyncSettings`` method takes a
  ``device_serial`` parameter that accepts ``device_serial=...`` keyword
  calls and forwards it as the ``deviceSerial`` form field;
* an invalid (lone-surrogate) ``device_serial`` makes ``get_language``
  raise a fixed ``ValueError`` whose ``__cause__`` and ``__context__`` are
  both ``None``, raised before any request is issued; valid text is sent
  unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ezviz_unified.models import ApiResponse
from ezviz_unified.settings import AsyncSettings, Settings

_VALID_SERIAL = "DEV001"
_LONE_SURROGATE = "\ud800"
_VALID_TEXT = "SN\u4e2d\u6587\u00c9\U0001f642"


class _Region:
    scope = "overseas"


def _api_response() -> ApiResponse:
    """Build a minimal ApiResponse without depending on optional fields."""

    return ApiResponse(200, "200", data={})


class _Recorder:
    """Fake sync client: overseas scope, records calls, returns an ApiResponse.

    No transport exists here at all, so an unexpected call only appends to
    ``calls`` — it can never touch the network.
    """

    def __init__(self) -> None:
        self.region = _Region()
        self.calls: list[tuple[Any, Any]] = []
        self.response = _api_response()

    def request(self, op: object, *, data: object = None) -> ApiResponse:
        self.calls.append((op, data))
        return self.response


class _AsyncRecorder(_Recorder):
    """Fake async client with the same contract."""

    async def request(self, op: object, *, data: object = None) -> ApiResponse:
        self.calls.append((op, data))
        return self.response


def _make(kind: str) -> tuple[Any, Any]:
    recorder = _AsyncRecorder() if kind == "async" else _Recorder()
    facade = AsyncSettings(recorder) if kind == "async" else Settings(recorder)
    return facade, recorder


def _invoke(kind: str, facade: Any, method: str, kwargs: dict[str, Any]) -> Any:
    call = getattr(facade, method)(**kwargs)
    if kind == "async":
        return asyncio.run(call)
    return call


_PUBLIC_SERIAL_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_switch", {"device_serial": _VALID_SERIAL, "switch": "wifi_prompt"}),
    (
        "set_switch",
        {"device_serial": _VALID_SERIAL, "switch": "wifi_prompt", "enabled": True},
    ),
    ("get_pir_area", {"device_serial": _VALID_SERIAL}),
    ("set_pir_area", {"device_serial": _VALID_SERIAL, "area": "1,0,1,0"}),
    ("get_chime", {"device_serial": _VALID_SERIAL}),
    (
        "set_chime",
        {"device_serial": _VALID_SERIAL, "chime_type": 1, "duration": 5},
    ),
    ("get_infrared", {"device_serial": _VALID_SERIAL}),
    ("set_infrared", {"device_serial": _VALID_SERIAL, "enabled": True}),
    (
        "set_timezone",
        {
            "device_serial": _VALID_SERIAL,
            "timezone": "Asia/Shanghai",
            "time_format": 0,
        },
    ),
    ("get_language", {"device_serial": _VALID_SERIAL}),
    ("set_language", {"device_serial": _VALID_SERIAL, "language": "ENGLISH"}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "method_kwargs",
    _PUBLIC_SERIAL_CALLS,
    ids=[name for name, _ in _PUBLIC_SERIAL_CALLS],
)
def test_public_methods_accept_device_serial_keyword(kind: str, method_kwargs) -> None:
    """Every public serial-taking method accepts ``device_serial=...``."""

    method, kwargs = method_kwargs
    facade, recorder = _make(kind)
    result = _invoke(kind, facade, method, kwargs)
    assert result is recorder.response
    assert len(recorder.calls) == 1
    assert recorder.calls[0][1]["deviceSerial"] == _VALID_SERIAL


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_get_language_surrogate_raises_plain_value_error_before_any_request(
    kind: str,
) -> None:
    """A lone-surrogate device_serial raises a fixed ValueError with no chain."""

    facade, recorder = _make(kind)
    with pytest.raises(ValueError) as exc_info:
        _invoke(kind, facade, "get_language", {"device_serial": _LONE_SURROGATE})
    exc = exc_info.value
    assert type(exc) is ValueError
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert recorder.calls == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_get_language_forwards_valid_text_unchanged(kind: str) -> None:
    """Valid non-ASCII text reaches the form body byte-for-byte unchanged."""

    facade, recorder = _make(kind)
    _invoke(kind, facade, "get_language", {"device_serial": _VALID_TEXT})
    assert len(recorder.calls) == 1
    assert recorder.calls[0][1]["deviceSerial"] == _VALID_TEXT
