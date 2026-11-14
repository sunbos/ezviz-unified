"""Shared target helpers for the single-device inspection examples.

Reads the explicit inspection target from the environment and validates it
before any client exists. Importing this module performs no I/O.
"""

import os
from dataclasses import dataclass, field

from examples._common import ConfigError


@dataclass(frozen=True)
class InspectionTarget:
    """Validated inspection target.

    ``device_serial`` is hidden from the default ``repr`` only; printing the
    field explicitly still exposes it.
    """

    device_serial: str = field(repr=False)
    channel: int


def _parse_channel(raw: str) -> int:
    """Return the channel as a strict signed ASCII decimal integer.

    Blank values, bool-style text, underscores, surrounding whitespace and
    non-ASCII digits are all rejected. No vendor channel range is invented
    here, so any signed integer value is returned unchanged. Digit strings
    that exceed the interpreter integer conversion limit are rejected as
    invalid input instead of leaking a raw ``ValueError``.
    """
    digits = raw[1:] if raw[:1] in ("+", "-") else raw
    if not digits or any(not "0" <= char <= "9" for char in digits):
        raise ConfigError("EZVIZ_CHANNEL")
    try:
        return int(raw)
    except ValueError:
        raise ConfigError("EZVIZ_CHANNEL") from None


def load_target(region: str) -> InspectionTarget:
    """Read and validate the inspection target before any client exists.

    ``EZVIZ_DEVICE_SERIAL`` is required and must be non-blank; for the
    domestic region it must not contain ASCII lowercase letters. The raw
    value is preserved verbatim and is never printed by the examples.

    ``EZVIZ_CHANNEL`` is optional and defaults to 1. When set it must be a
    signed ASCII decimal integer such as ``2``, ``+1`` or ``-1``.
    """
    serial = os.environ.get("EZVIZ_DEVICE_SERIAL")
    if not serial or not serial.strip():
        raise ConfigError("EZVIZ_DEVICE_SERIAL")
    if region == "domestic" and any("a" <= char <= "z" for char in serial):
        raise ConfigError("EZVIZ_DEVICE_SERIAL")
    raw_channel = os.environ.get("EZVIZ_CHANNEL")
    channel = 1 if raw_channel is None else _parse_channel(raw_channel)
    return InspectionTarget(device_serial=serial, channel=channel)
