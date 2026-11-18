"""Form builders for the domestic passenger-flow LAPP endpoints.

Only business keys are mapped; the transport layer supplies accessToken
and performs form-encoding of the values returned here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

from .arming import _check_form_serial
from .storage_models import _integer
from .traffic import _millis

_DOMESTIC = "domestic"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)
_COORD_KEYS = ("x1", "y1", "x2", "y2")
_LINE_RE = re.compile(r"[01](?:\.[0-9]{1,7})?\Z")
_DIRECTION_RE = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")


def _domestic(scope: str) -> None:
    if scope != _DOMESTIC:
        raise ValueError("passenger flow is only supported in the domestic region")


def _channel_text(channel_no: int) -> str:
    number = _integer("channelNo", channel_no)
    try:
        return str(number)
    except ValueError:  # interpreter int->str digit guard
        raise ValueError("channelNo cannot be encoded as a decimal string") from None


def _day_millis(day: object) -> int:
    if not isinstance(day, datetime) or day.utcoffset() is None:
        raise ValueError("day must be a timezone-aware datetime at local midnight")
    if (day.hour, day.minute, day.second, day.microsecond) != (0, 0, 0, 0):
        raise ValueError("day must be a timezone-aware datetime at local midnight")
    tz = day.tzinfo
    if not isinstance(tz, timezone):
        # Dynamic zones can skip local midnight entirely (e.g. a spring forward
        # at 00:00). Confirm the instant maps back to the same naive local value
        # and the same utcoffset; a genuine fold keeps both, an imaginary time
        # does not. datetime.timezone is a known fixed offset with no gaps and
        # is exempt so extreme year 1/9999 values keep working.
        try:
            back = day.astimezone(timezone.utc).astimezone(tz)
        except (OverflowError, OSError, TypeError, ValueError):
            raise ValueError("day must round-trip through UTC in its own timezone") from None
        if (
            back.replace(tzinfo=None) != day.replace(tzinfo=None)
            or back.utcoffset() != day.utcoffset()
        ):
            raise ValueError("day must be an existing local midnight in its own timezone")
    delta = day - _EPOCH
    millis = delta // _ONE_MS
    if delta % _ONE_MS:  # sub-millisecond utcoffset cannot be represented
        raise ValueError("day utcoffset must be a whole number of milliseconds")
    return millis


def _line_text(text: str) -> bool:
    if _LINE_RE.fullmatch(text) is None:
        return False
    # "1." may only carry zero fractional digits so the value stays in [0, 1].
    return not text.startswith("1.") or set(text[2:]) == {"0"}


def _direction_text(text: str) -> bool:
    return _DIRECTION_RE.fullmatch(text) is not None


def _coords(value: Mapping[str, str], name: str, check: Callable[[str], bool]) -> str:
    if not isinstance(value, Mapping) or set(value) != set(_COORD_KEYS):
        raise ValueError(f"{name} must be a mapping with exactly the string keys x1, y1, x2, y2")
    ordered = {}
    for key in _COORD_KEYS:
        text = value[key]
        if not isinstance(text, str) or not check(text):
            raise ValueError(f"{name} coordinate values must be plain decimal strings")
        ordered[key] = text
    # Hand the compact JSON to the form encoder verbatim; never pre-encode.
    return json.dumps(ordered, separators=(",", ":"))


def device_form(
    scope: str,
    serial: str,
    channel_no: int | None = None,
    *,
    require_channel: bool = False,
) -> dict[str, str]:
    _domestic(scope)
    form = {"deviceSerial": _check_form_serial(serial, scope)}
    if channel_no is None:
        if require_channel:
            raise ValueError("channelNo is required for this endpoint")
    else:
        form["channelNo"] = _channel_text(channel_no)
    return form


def switch_form(scope: str, serial: str, enable: int, channel_no: int | None) -> dict[str, str]:
    form = device_form(scope, serial, channel_no)
    state = _integer("enable", enable)
    if state not in (0, 1):
        raise ValueError("enable must be 0 or 1")
    form["enable"] = str(state)
    return form


def daily_form(scope: str, serial: str, channel_no: int, day: datetime | None) -> dict[str, str]:
    form = device_form(scope, serial, channel_no, require_channel=True)
    if day is not None:
        form["date"] = str(_day_millis(day))
    return form


def hourly_form(scope: str, serial: str, channel_no: int, date_ms: int | None) -> dict[str, str]:
    form = device_form(scope, serial, channel_no, require_channel=True)
    if date_ms is not None:
        form["date"] = str(_millis("date", date_ms))
    return form


def config_form(
    scope: str,
    serial: str,
    line: Mapping[str, str],
    direction: Mapping[str, str],
    channel_no: int | None,
) -> dict[str, str]:
    form = device_form(scope, serial, channel_no)
    form["line"] = _coords(line, "line", _line_text)
    form["direction"] = _coords(direction, "direction", _direction_text)
    return form
