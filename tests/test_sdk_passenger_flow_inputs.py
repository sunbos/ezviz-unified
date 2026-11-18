"""Offline input-validation tests for ``Client.passenger_flow`` / ``AsyncClient.passenger_flow``.

Every rejection case here must fail BEFORE any auth or business transport is
invoked: a real ``DiagnosticRecorder`` captures diagnostics and the final
transports are ``httpx.MockTransport`` handlers that fail the test if they are
ever called.

Accepted-input cases run a real ``Client`` / ``AsyncClient`` through two
``httpx.MockTransport`` handlers — a synthetic token reply and a synthetic
business envelope — and assert on the captured request path and form fields.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest

from ezviz_unified import AsyncClient, Client, DiagnosticRecorder, RegionConfig
from ezviz_unified._passenger_flow_forms import daily_form, device_form

SERIAL = "FLOW999TEST"
_VALID_LINE = {"x1": "0.1", "y1": "0.2", "x2": "0.3", "y2": "0.4"}
_VALID_DIR = {"x1": "1", "y1": "0", "x2": "0", "y2": "1"}
_DAY = datetime(2024, 3, 5, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))
_SIGNED64_MIN = -(2**63)
_SIGNED64_MAX = 2**63 - 1

_TOKEN = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}


def _fail_transport(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    pytest.fail(f"transport invoked for invalid input: {request.method} {request.url}")


def _client(region: RegionConfig, recorder: DiagnosticRecorder) -> Client:
    return Client(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=region,
        auth_transport=httpx.MockTransport(_fail_transport),
        transport_factory=lambda origin: httpx.MockTransport(_fail_transport),
        diagnostic_recorder=recorder,
    )


def _async_client(region: RegionConfig, recorder: DiagnosticRecorder) -> AsyncClient:
    return AsyncClient(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=region,
        auth_transport=httpx.MockTransport(_fail_transport),
        transport_factory=lambda origin: httpx.MockTransport(_fail_transport),
        diagnostic_recorder=recorder,
    )


def _call(client, method: str, *args, **kwargs):
    if isinstance(client, AsyncClient):

        async def _run():
            async with client as c:
                return await getattr(c.passenger_flow, method)(*args, **kwargs)

        return asyncio.run(_run())
    with client as c:
        return getattr(c.passenger_flow, method)(*args, **kwargs)


def _assert_failed(recorder: DiagnosticRecorder, entrypoint: str, mode: str) -> None:
    """The entrypoint's ``call`` scope must emit exactly started then failed."""
    events = [
        e
        for e in recorder.report()["events"]
        if e.get("kind") == "call" and e.get("entrypoint") == entrypoint
    ]
    assert [e["outcome"] for e in events] == ["started", "failed"]
    assert all(e["mode"] == mode for e in events)


def _both(region: RegionConfig):
    r1, r2 = DiagnosticRecorder(), DiagnosticRecorder()
    return [
        (_client(region, r1), r1, "sync"),
        (_async_client(region, r2), r2, "async"),
    ]


# --- positive helper: real clients over two MockTransports --------------------


def _form_of(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _run_ok(method: str, args: tuple, kwargs: dict, business_reply: dict):
    """Drive one accepted call in both modes; return captured evidence.

    Two ``httpx.MockTransport`` handlers are wired through real ``Client`` /
    ``AsyncClient`` context managers: the auth transport returns a synthetic
    token envelope, the business transport records the request and replies
    with ``business_reply``. Asserts exactly one auth and one business POST,
    and yields ``(form, response, mode)`` per mode.
    """
    results = []
    for mode in ("sync", "async"):
        auth_calls: list[httpx.Request] = []
        biz_calls: list[httpx.Request] = []

        def auth(request: httpx.Request) -> httpx.Response:
            auth_calls.append(request)
            return httpx.Response(200, json=_TOKEN)

        def biz(request: httpx.Request) -> httpx.Response:
            biz_calls.append(request)
            return httpx.Response(200, json=business_reply)

        auth_t = httpx.MockTransport(auth)
        biz_t = httpx.MockTransport(biz)
        if mode == "sync":
            with Client(
                "SYNTHKEY",
                "SYNTHSECRET",
                region=RegionConfig.china(),
                auth_transport=auth_t,
                transport_factory=lambda origin: biz_t,
            ) as client:
                response = getattr(client.passenger_flow, method)(*args, **kwargs)
        else:

            async def _run():
                async with AsyncClient(
                    "SYNTHKEY",
                    "SYNTHSECRET",
                    region=RegionConfig.china(),
                    auth_transport=auth_t,
                    transport_factory=lambda origin: biz_t,
                ) as client:
                    return await getattr(client.passenger_flow, method)(*args, **kwargs)

            response = asyncio.run(_run())
        assert len(auth_calls) == 1, mode
        assert len(biz_calls) == 1, mode
        results.append((_form_of(biz_calls[0]), response, mode))
    return results


# --- serial validation on all six representative endpoints -------------------

_CALLS = [
    ("switch_status", (), {}),
    ("set_switch", (), {"enable": 1}),
    ("daily", (1,), {"day": _DAY}),
    ("hourly", (1,), {"date_ms": 0}),
    ("set_config", (), {"line": _VALID_LINE, "direction": _VALID_DIR}),
    ("get_config", (), {}),
]

_BAD_SERIALS = [
    " flow999test",  # leading whitespace
    "flow999test ",  # trailing whitespace
    "flow999test",  # lowercase
    "FLOW999\ud800",  # lone surrogate
    "",  # empty
    "  ",  # whitespace-only
]


@pytest.mark.parametrize("method,args,kwargs", _CALLS)
@pytest.mark.parametrize("serial", _BAD_SERIALS)
def test_serial_rejected(method, args, kwargs, serial):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, method, serial, *args, **kwargs)
        _assert_failed(recorder, f"passenger_flow.{method}", mode)
        # The rejected serial is a private input: it must never reach the
        # serialized diagnostic report.
        if serial:
            assert serial not in recorder.to_json()


def test_serial_with_inner_whitespace_accepted():
    # "FLOW 999" is non-blank, uppercase and UTF-8-clean; neither the source
    # docs nor the frozen SDK reject interior whitespace, so it must flow into
    # the deviceSerial form field unmodified (checked via the same
    # validation path the call uses, without touching the network).
    assert device_form("domestic", "FLOW 999")["deviceSerial"] == "FLOW 999"


# --- overseas region always rejects passenger flow ----------------------------


@pytest.mark.parametrize("method,args,kwargs", _CALLS)
def test_overseas_rejected(method, args, kwargs):
    for client, recorder, mode in _both(RegionConfig.overseas()):
        with pytest.raises(ValueError):
            _call(client, method, SERIAL, *args, **kwargs)
        _assert_failed(recorder, f"passenger_flow.{method}", mode)


# --- channel_no on mandatory daily / hourly -----------------------------------


@pytest.mark.parametrize("method", ["daily", "hourly"])
@pytest.mark.parametrize("channel", [None, True, False, 1.5, "1"])
def test_mandatory_channel_invalid(method, channel):
    kwargs = {"day": _DAY} if method == "daily" else {"date_ms": 0}
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, method, SERIAL, channel, **kwargs)
        _assert_failed(recorder, f"passenger_flow.{method}", mode)


@pytest.mark.skipif(
    sys.get_int_max_str_digits() == 0,
    reason="interpreter int->str digit guard is disabled; oversized ints convert fine",
)
def test_mandatory_channel_oversized_int_rejected():
    oversized = 10 ** (sys.get_int_max_str_digits() + 10)
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "daily", SERIAL, oversized, day=_DAY)
        _assert_failed(recorder, "passenger_flow.daily", mode)


@pytest.mark.parametrize("method", ["daily", "hourly"])
@pytest.mark.parametrize("channel", [-1, 0, 2**63])
def test_mandatory_channel_unbounded_ints_accepted(method, channel):
    # Beyond the bool/type checks the SDK invents no range limit: every genuine
    # int clears validation, is serialized verbatim into channelNo, and reaches
    # the business transport. A ValueError here would mean an invented bound.
    kwargs = {"day": _DAY} if method == "daily" else {"date_ms": 0}
    # daily reads back a data object; hourly reads back a data list.
    reply = {"code": "200", "data": {} if method == "daily" else []}
    for form, _response, mode in _run_ok(method, (SERIAL, channel), kwargs, reply):
        assert form["channelNo"] == str(channel), mode


# --- set_switch enable: int 0/1 only, bool rejected ---------------------------


@pytest.mark.parametrize("enable", [True, False, 2, -1, 1.0, "1"])
def test_switch_enable_invalid(enable):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "set_switch", SERIAL, enable=enable)
        _assert_failed(recorder, "passenger_flow.set_switch", mode)


@pytest.mark.parametrize("enable", [0, 1])
def test_switch_enable_valid_inputs_accepted(enable):
    # enable 0/1 are the only legal values: they clear validation, serialize to
    # the literal form value, and the write ack omits ``data`` entirely.
    reply = {"code": "200"}
    for form, response, mode in _run_ok("set_switch", (SERIAL,), {"enable": enable}, reply):
        assert form["enable"] == str(enable), mode
        assert response.data is None, mode


def test_switch_channel_no_absent_vs_explicit_zero():
    # channel_no is optional: omitted it is absent from the form, while an
    # explicit 0 is a genuine int and is serialized verbatim as "0".
    reply = {"code": "200"}
    absent = _run_ok("set_switch", (SERIAL,), {"enable": 1}, reply)
    for form, _r, mode in absent:
        assert "channelNo" not in form, mode
    explicit = _run_ok("set_switch", (SERIAL,), {"enable": 1, "channel_no": 0}, reply)
    for form, _r, mode in explicit:
        assert form["channelNo"] == "0", mode


# --- daily: aware local-midnight datetime, zero micros ------------------------

_AWARE_MIDNIGHT = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
_AWARE_NONMIDNIGHT = datetime(2024, 6, 1, 1, 0, 0, tzinfo=timezone.utc)
_NAIVE = datetime(2024, 6, 1, 0, 0, 0)
_MICROS = datetime(2024, 6, 1, 0, 0, 0, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("day", [_NAIVE, _AWARE_NONMIDNIGHT, _MICROS, "2024-06-01", 0])
def test_daily_day_invalid(day):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "daily", SERIAL, 1, day=day)
        _assert_failed(recorder, "passenger_flow.daily", mode)


# --- daily: nonexistent local midnight (DST gap at 00:00) ---------------------
#
# A synthetic, self-contained TZif v1 payload parsed by ``ZoneInfo.from_file`` —
# no host tzdata is needed. Both transitions occur exactly at a UTC midnight:
#
#   UTC                       local (STD=+0 / DST=+1)
#   2018-11-04T00:00:00Z      01:00 -> ... spring forward: local 00:00 skipped
#   2020-11-01T00:00:00Z      01:00 -> 00:00 fall back: local 00:00 occurs twice
#
# fold=0/fold=1 at the 2020 instant map to 2020-10-31T23:00Z and 2020-11-01T00:00Z.

_GAP_TS, _FOLD_TS = 1541289600, 1604188800  # 2018-11-04T00:00Z, 2020-11-01T00:00Z
_MIDNIGHT_ZONE = ZoneInfo.from_file(
    io.BytesIO(
        b"TZif"
        + bytes(16)  # magic + reserved header bytes
        + struct.pack(">6I", 0, 0, 0, 2, 2, 8)  # counts: 2 transitions, 2 types
        + struct.pack(">2i", _GAP_TS, _FOLD_TS)
        + bytes([1, 0])  # post-transition type indexes
        + struct.pack(">iBBiBB", 0, 0, 0, 3600, 1, 4)  # STD, DST(+1h, "DST")
        + b"STD\0DST\0"
    ),
    key="Synthetic/Midnight",
)


def _midnight(year, month, day, fold=0):
    return datetime(year, month, day, 0, 0, 0, fold=fold, tzinfo=_MIDNIGHT_ZONE)


_NONEXISTENT_MIDNIGHT = _midnight(2018, 11, 4)


def test_daily_nonexistent_local_midnight_rejected():
    # The transition jumps local 00:00 straight to 01:00, so the wall value
    # 00:00:00 is imaginary: fold=0 uses the pre-transition offset +0 and
    # resolves forward to 01:00; fold=1 uses the post-transition offset +1
    # and resolves backward into the previous day. Pin that behavior so the
    # SDK's rejection below happens for the intended reason — under neither
    # fold does the instant map back to the naive midnight.
    fold0 = _midnight(2018, 11, 4, fold=0)
    fold1 = _midnight(2018, 11, 4, fold=1)
    assert fold0.utcoffset() == timedelta(0)
    assert fold1.utcoffset() == timedelta(hours=1)
    back0 = fold0.astimezone(timezone.utc).astimezone(_MIDNIGHT_ZONE)
    back1 = fold1.astimezone(timezone.utc).astimezone(_MIDNIGHT_ZONE)
    assert back0.replace(tzinfo=None) == datetime(2018, 11, 4, 1, 0, 0)
    assert back1.replace(tzinfo=None) == datetime(2018, 11, 3, 23, 0, 0)
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "daily", SERIAL, 1, day=_NONEXISTENT_MIDNIGHT)
        _assert_failed(recorder, "passenger_flow.daily", mode)


def test_daily_genuine_fixed_offset_midnight_accepted():
    # A genuine UTC+8 midnight is exempt from the dynamic-zone round-trip:
    # validation must pass and the form value is the epoch-millis of that
    # instant (checked through the same builder the call uses, offline).
    expected = str(int(_DAY.timestamp() * 1000))
    assert daily_form("domestic", SERIAL, 1, _DAY)["date"] == expected


def test_daily_ambiguous_fold_midnight_accepted():
    # Local 00:00 on the fall-back date occurs twice: fold=0 maps to
    # 2020-10-31T23:00Z and fold=1 to 2020-11-01T00:00Z. Both are genuine
    # instants, so validation clears and each epoch-millis is serialized
    # verbatim into ``date`` — verified end to end through the public call.
    reply = {"code": "200", "data": {}}
    for fold, expected in ((0, "1604185200000"), (1, "1604188800000")):
        day = _midnight(2020, 11, 1, fold=fold)
        assert str(int(day.timestamp() * 1000)) == expected
        for form, _response, mode in _run_ok("daily", (SERIAL, 1), {"day": day}, reply):
            assert form["date"] == expected, (mode, fold)


# --- hourly: signed64 integer, reject bool/float/outside ----------------------


@pytest.mark.parametrize(
    "date_ms",
    [True, False, 1.5, "0", _SIGNED64_MIN - 1, _SIGNED64_MAX + 1],
)
def test_hourly_date_ms_invalid(date_ms):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "hourly", SERIAL, 1, date_ms=date_ms)
        _assert_failed(recorder, "passenger_flow.hourly", mode)


# --- set_config: exact four str keys, finite decimal, range/precision on line -

_BAD_COORDS = [
    {"x1": "0.1", "y1": "0.2", "x2": "0.3"},  # missing key
    {"x1": "0.1", "y1": "0.2", "x2": "0.3", "y2": "0.4", "z": "0"},  # extra key
    {"x1": "nan", "y1": "0.2", "x2": "0.3", "y2": "0.4"},
    {"x1": "inf", "y1": "0.2", "x2": "0.3", "y2": "0.4"},
    {"x1": "1e-1", "y1": "0.2", "x2": "0.3", "y2": "0.4"},  # exponent
    {"x1": True, "y1": "0.2", "x2": "0.3", "y2": "0.4"},  # bool
    {"x1": 0.1, "y1": "0.2", "x2": "0.3", "y2": "0.4"},  # numeric
]


@pytest.mark.parametrize("line", _BAD_COORDS)
def test_set_config_line_shape_invalid(line):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "set_config", SERIAL, line=line, direction=_VALID_DIR)
        _assert_failed(recorder, "passenger_flow.set_config", mode)


@pytest.mark.parametrize(
    "line",
    [
        {"x1": "-0.1", "y1": "0.2", "x2": "0.3", "y2": "0.4"},  # <0
        {"x1": "1.1", "y1": "0.2", "x2": "0.3", "y2": "0.4"},  # >1
        {"x1": "0.12345678", "y1": "0.2", "x2": "0.3", "y2": "0.4"},  # 8 frac digits
    ],
)
def test_set_config_line_range_precision(line):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "set_config", SERIAL, line=line, direction=_VALID_DIR)
        _assert_failed(recorder, "passenger_flow.set_config", mode)


@pytest.mark.parametrize(
    "direction",
    [
        {"x1": "nan", "y1": "0", "x2": "0", "y2": "1"},
        {"x1": "1e-1", "y1": "0", "x2": "0", "y2": "1"},
        {"x1": "1", "y1": "0", "x2": "0"},  # missing
        {"x1": "1", "y1": "0", "x2": "0", "y2": "1", "q": "0"},  # extra
        {"x1": 1, "y1": "0", "x2": "0", "y2": "1"},  # numeric
    ],
)
def test_set_config_direction_invalid(direction):
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "set_config", SERIAL, line=_VALID_LINE, direction=direction)
        _assert_failed(recorder, "passenger_flow.set_config", mode)


def test_set_config_direction_not_line_precision_accepted():
    # Direction is a signed decimal vector, not a [0,1] line coordinate: values
    # above 1 and more than 7 fractional digits must pass validation and be
    # serialized verbatim — the line regex must never be applied to direction.
    line = dict(_VALID_LINE)
    direction = {"x1": "5.12345678", "y1": "-2", "x2": "+0.999999999", "y2": "1"}
    snap_line, snap_dir = dict(line), dict(direction)
    reply = {"code": "200"}
    for form, response, mode in _run_ok(
        "set_config", (SERIAL,), {"line": line, "direction": direction}, reply
    ):
        assert json.loads(form["line"]) == snap_line, mode
        assert json.loads(form["direction"]) == snap_dir, mode
        assert response.data is None, mode
    # The caller-owned mappings are handed over read-only; nothing is mutated.
    assert line == snap_line
    assert direction == snap_dir


# --- caller mappings must not be mutated --------------------------------------


def test_set_config_does_not_mutate_mappings():
    line = dict(_VALID_LINE)
    direction = dict(_VALID_DIR)
    snapshot_line, snapshot_dir = dict(line), dict(direction)
    # Pass a direction with invalid value so validation fails; mappings must be intact.
    bad_dir = dict(direction)
    bad_dir["x1"] = "nan"
    for client, recorder, mode in _both(RegionConfig.china()):
        with pytest.raises(ValueError):
            _call(client, "set_config", SERIAL, line=line, direction=bad_dir)
    assert line == snapshot_line
    assert direction == snapshot_dir
    assert bad_dir["x1"] == "nan"
