"""Single-device read-only inspection orchestration (sync and async).

These helpers sequence the existing device-query and diagnostics facades
for one known device. They add no endpoints, transports, retries,
pagination, or background work, and they never close the borrowed client.
Only ``EzvizError`` failures are captured per operation; authentication
failures re-raise unchanged and stop the sequence, while validation
errors, programmer errors, and cancellation propagate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .devices import AsyncDevices, Devices, _check_device_serial, _check_int
from .diagnostics import AsyncDiagnostics, Diagnostics
from .errors import AuthenticationError, EzvizError
from .models import ApiResponse

if TYPE_CHECKING:
    from .client import AsyncClient, Client

_DOMESTIC = "domestic"

_SyncStep = tuple[str, Callable[[], ApiResponse]]
_AsyncStep = tuple[str, Callable[[], Awaitable[ApiResponse]]]


def _summary_code(code: object) -> str | int | None:
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.isascii() and code.isdigit():
        return code
    return None


def _summary_http_status(http_status: object) -> int | None:
    if isinstance(http_status, bool) or not isinstance(http_status, int):
        return None
    return http_status


def _result_summary(result: InspectionResult) -> dict[str, Any]:
    row: dict[str, Any] = {
        "operation": result.operation,
        "status": "ok" if result.succeeded else "failed",
    }
    error = result.error
    if error is not None:
        row["error_type"] = type(error).__name__
        code = _summary_code(error.code)
        if code is not None:
            row["code"] = code
        http_status = _summary_http_status(error.http_status)
        if http_status is not None:
            row["http_status"] = http_status
    return row


@dataclass(frozen=True)
class InspectionResult:
    """Outcome of one read-only operation.

    Exactly one of ``response``/``error`` must be set; both are excluded
    from ``repr`` so payloads and error text stay out of logs.
    """

    operation: str
    response: ApiResponse | None = field(default=None, repr=False)
    error: EzvizError | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.response is None) == (self.error is None):
            raise ValueError("exactly one of response or error must be set")
        if not isinstance(self.operation, str):
            raise ValueError("operation must be a str")
        if self.response is not None and not isinstance(self.response, ApiResponse):
            raise ValueError("response must be an ApiResponse")
        if self.error is not None and not isinstance(self.error, EzvizError):
            raise ValueError("error must be an EzvizError")

    @property
    def succeeded(self) -> bool:
        """Whether the operation returned a response."""
        return self.response is not None


@dataclass(frozen=True)
class DeviceInspection:
    """Ordered per-operation outcomes of one device inspection run.

    ``device_serial`` is excluded from ``repr``; ``public_summary`` returns
    a fresh redacted mapping without identifiers, payloads, or error text.
    Original payloads are kept by reference; no deep immutability or
    atomic-snapshot semantics are implied.
    """

    region: str
    device_serial: str = field(repr=False)
    channel: int = 1
    results: tuple[InspectionResult, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))

    @property
    def all_succeeded(self) -> bool:
        """Whether results are non-empty and every operation succeeded."""
        return bool(self.results) and all(result.succeeded for result in self.results)

    def public_summary(self) -> dict[str, Any]:
        """Return a fresh redacted mapping safe to share or log.

        Contains ``region``, ``all_succeeded``, and per-operation rows with
        ``operation``/``status`` plus ``error_type`` and any well-formed
        ``code``/``http_status`` on failures. Never includes the device
        serial, channel, raw payloads, error messages, URLs, or credentials.
        """
        return {
            "region": self.region,
            "all_succeeded": self.all_succeeded,
            "results": [_result_summary(result) for result in self.results],
        }


def _steps(
    devices: Devices,
    diagnostics: Diagnostics,
    device_serial: str,
    channel: int,
) -> tuple[_SyncStep, ...]:
    return (
        ("devices.get", lambda: devices.get(device_serial)),
        ("devices.channels", lambda: devices.channels(device_serial)),
        ("devices.status", lambda: devices.status(device_serial, channel=channel)),
        (
            "diagnostics.capabilities",
            lambda: diagnostics.capabilities(device_serial, channel=channel),
        ),
        ("diagnostics.version", lambda: diagnostics.version(device_serial)),
    )


def _async_steps(
    devices: AsyncDevices,
    diagnostics: AsyncDiagnostics,
    device_serial: str,
    channel: int,
) -> tuple[_AsyncStep, ...]:
    return (
        ("devices.get", lambda: devices.get(device_serial)),
        ("devices.channels", lambda: devices.channels(device_serial)),
        ("devices.status", lambda: devices.status(device_serial, channel=channel)),
        (
            "diagnostics.capabilities",
            lambda: diagnostics.capabilities(device_serial, channel=channel),
        ),
        ("diagnostics.version", lambda: diagnostics.version(device_serial)),
    )


def _collect(steps: tuple[_SyncStep, ...]) -> tuple[InspectionResult, ...]:
    results: list[InspectionResult] = []
    for operation, call in steps:
        try:
            response = call()
        except AuthenticationError:
            raise
        except EzvizError as exc:
            results.append(InspectionResult(operation=operation, error=exc))
        else:
            results.append(InspectionResult(operation=operation, response=response))
    return tuple(results)


async def _collect_async(steps: tuple[_AsyncStep, ...]) -> tuple[InspectionResult, ...]:
    results: list[InspectionResult] = []
    for operation, call in steps:
        try:
            response = await call()
        except AuthenticationError:
            raise
        except EzvizError as exc:
            results.append(InspectionResult(operation=operation, error=exc))
        else:
            results.append(InspectionResult(operation=operation, response=response))
    return tuple(results)


def inspect_device(client: Client, device_serial: str, *, channel: int = 1) -> DeviceInspection:
    """Inspect one known device via the fixed five-call read-only sequence.

    Runs ``devices.get``, ``devices.channels``, ``devices.status``,
    ``diagnostics.capabilities``, then ``diagnostics.version`` in order.
    Inputs are validated before the first call; values are never rewritten.
    Per-operation ``EzvizError`` failures are recorded and the sequence
    continues; ``AuthenticationError`` propagates unchanged and stops the
    run. The borrowed client is never closed. The sequence issues at most
    five sequential logical calls with no paging or writes; the client SDK
    may still authenticate, refresh, or retry, so the actual HTTP request
    count can exceed five.
    """
    _check_device_serial(device_serial, require_uppercase=client.region.scope == _DOMESTIC)
    _check_int("channel", channel)
    results = _collect(_steps(client.devices, client.diagnostics, device_serial, channel))
    return DeviceInspection(
        region=client.region.scope,
        device_serial=device_serial,
        channel=channel,
        results=results,
    )


async def inspect_device_async(
    client: AsyncClient, device_serial: str, *, channel: int = 1
) -> DeviceInspection:
    """Async variant of :func:`inspect_device` with identical semantics."""
    _check_device_serial(device_serial, require_uppercase=client.region.scope == _DOMESTIC)
    _check_int("channel", channel)
    results = await _collect_async(
        _async_steps(client.devices, client.diagnostics, device_serial, channel)
    )
    return DeviceInspection(
        region=client.region.scope,
        device_serial=device_serial,
        channel=channel,
        results=results,
    )
