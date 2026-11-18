"""Core contract tests for examples/_webhook_receiver.py (SP098-A3).

All data is fully synthetic: no network, env, or file inputs. Signatures are
always produced locally via hmac+sha1 over ``raw + timestamp.encode()`` —
never via the implementation under test. Both clocks are injected callables.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import examples._webhook_receiver as wr

SECRET = "synthetic-secret"
WALL_MS = 1_700_000_000_000
TS = str(WALL_MS)  # domestic ASCII-decimal millisecond timestamp


class Clock:
    """Controllable monotonic clock."""

    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def sign(raw: bytes, timestamp: str) -> str:
    return hmac.new(SECRET.encode(), raw + timestamp.encode(), hashlib.sha1).hexdigest()


def envelope(msg_id: str, mtype: str = "synthetic.type", body=None) -> bytes:
    return json.dumps(
        {
            "header": {"messageId": msg_id, "type": mtype},
            "body": {} if body is None else body,
        }
    ).encode()


def make_proc(clk: Clock, **cfg_kwargs) -> "wr.ReceiverProcessor":
    config = wr.ReceiverConfig(**cfg_kwargs)
    return wr.ReceiverProcessor(
        config,
        webhook_secret=SECRET,
        monotonic=clk,
        wall_time_ms=lambda: WALL_MS,
    )


def run(proc, raw: bytes, timestamp: str = TS, deadline: float | None = None):
    """Process with a freshly computed, valid signature."""
    return proc.process(raw, timestamp=timestamp, signature=sign(raw, timestamp), deadline=deadline)


# --- happy path -----------------------------------------------------------


def test_accept_unknown_type_and_ack_dict():
    proc = make_proc(Clock())
    resp = run(proc, envelope("id-1", mtype="totally.unknown.type"))
    assert resp.status == 200 and resp.outcome == "accepted"
    assert json.loads(resp.body) == {"messageId": "id-1"}
    proc.close()


# --- signature vs JSON ordering -------------------------------------------


def test_tampered_raw_with_old_signature_is_401():
    proc = make_proc(Clock())
    raw = envelope("id-2")
    old_sig = sign(raw, TS)
    resp = proc.process(raw + b"   ", timestamp=TS, signature=old_sig)
    assert resp.status == 401 and resp.outcome == "authentication_failed"
    err = json.loads(resp.body)
    assert set(err) == {"error"} and isinstance(err["error"], str)


def test_bad_json_bad_signature_is_401():
    # signature is verified before JSON parsing (valid UTF-8, invalid JSON)
    proc = make_proc(Clock())
    raw = b"{ not json"
    resp = proc.process(raw, timestamp=TS, signature="0" * 40)
    assert resp.status == 401


def test_valid_signature_bad_json_is_400():
    proc = make_proc(Clock())
    raw = b"{ not json"
    resp = proc.process(raw, timestamp=TS, signature=sign(raw, TS))
    assert resp.status == 400 and resp.outcome == "invalid_message"


# --- timestamp handling ----------------------------------------------------


def test_domestic_stale_timestamp_rejected():
    proc = make_proc(Clock(), timestamp_tolerance_ms=1_000)
    stale = str(WALL_MS - 60_000)
    raw = envelope("id-3")
    resp = proc.process(raw, timestamp=stale, signature=sign(raw, stale))
    assert resp.status == 401


def test_overseas_opaque_timestamp_accepted():
    proc = make_proc(Clock(), profile="overseas", timestamp_tolerance_ms=None)
    opaque = "2026-11-18T00:00:00Z"  # not a number; only a signature input
    raw = envelope("id-4")
    resp = proc.process(raw, timestamp=opaque, signature=sign(raw, opaque))
    assert resp.status == 200


@pytest.mark.parametrize(
    "offset_ms,expected",
    [
        (-1_000, 200),  # at lower bound
        (-1, 200),
        (0, 200),
        (999, 200),
        (1_000, 200),  # at upper bound
        (-1_001, 401),  # beyond lower bound
        (1_001, 401),  # beyond upper bound
    ],
)
def test_domestic_timestamp_tolerance_boundary(offset_ms, expected):
    proc = make_proc(Clock(), timestamp_tolerance_ms=1_000)
    ts = str(WALL_MS + offset_ms)
    raw = envelope(f"ts-{offset_ms}")
    resp = proc.process(raw, timestamp=ts, signature=sign(raw, ts))
    assert resp.status == expected


@pytest.mark.parametrize(
    "timestamp",
    [
        "-1",  # negative
        "١٢٣",  # non-ASCII decimal digits
        "9" * 17,  # more than 16 digits
        "not-a-number",  # non-numeric
    ],
)
def test_domestic_malformed_timestamp_is_401(timestamp):
    proc = make_proc(Clock())
    raw = envelope("ts-malformed")
    resp = proc.process(raw, timestamp=timestamp, signature=sign(raw, timestamp))
    assert resp.status == 401 and resp.outcome == "authentication_failed"


# --- envelope limits ---------------------------------------------------------


def test_message_id_byte_boundary():
    proc = make_proc(Clock())
    assert run(proc, envelope("x" * 256)).status == 200
    resp = run(proc, envelope("x" * 257))
    assert resp.status == 400 and resp.outcome == "invalid_message"


def test_message_id_byte_boundary_multibyte():
    # 128 x "é" = 256 UTF-8 bytes: byte length, not char count, is the limit
    proc = make_proc(Clock())
    assert run(proc, envelope("é" * 128)).status == 200
    resp = run(proc, envelope("é" * 129))
    assert resp.status == 400 and resp.outcome == "invalid_message"


def test_body_too_large_is_413():
    proc = make_proc(Clock(), max_body_bytes=64)
    raw = b"x" * 100
    resp = proc.process(raw, timestamp=TS, signature=sign(raw, TS))
    assert resp.status == 413 and resp.outcome == "body_too_large"


# --- dedup / conflict / capacity ---------------------------------------------


def test_duplicate_acks_and_conflict_is_409():
    proc = make_proc(Clock())
    raw = envelope("dup-1", body={"v": 1})
    assert run(proc, raw).status == 200
    dup = run(proc, raw)
    assert dup.status == 200 and dup.outcome == "duplicate"
    assert json.loads(dup.body) == {"messageId": "dup-1"}
    conflict = run(proc, envelope("dup-1", body={"v": 2}))
    assert conflict.status == 409 and conflict.outcome == "id_conflict"


def test_inbox_full_503_but_accepted_duplicate_still_200():
    proc = make_proc(Clock(), inbox_capacity=1)
    raw_a = envelope("cap-a")
    assert run(proc, raw_a).status == 200
    raw_b = envelope("cap-b")
    full = run(proc, raw_b)
    assert full.status == 503 and full.outcome == "capacity_rejected"
    assert "messageId" not in json.loads(full.body)  # no ACK on failure
    # duplicate check precedes capacity: still ACKed even with a full inbox
    assert run(proc, raw_a).outcome == "duplicate"
    # rejected delivery is not recorded; retry succeeds after draining
    assert proc.take_message() is not None
    assert run(proc, raw_b).status == 200


def test_dedup_full_503_does_not_evict_live_entry():
    proc = make_proc(Clock(), dedup_capacity=1)
    raw_a = envelope("dedup-a")
    assert run(proc, raw_a).status == 200
    assert run(proc, envelope("dedup-b")).status == 503
    assert run(proc, raw_a).outcome == "duplicate"  # live entry kept


def test_ttl_expiry_allows_new_acceptance():
    clk = Clock()
    proc = make_proc(clk, dedup_ttl_seconds=10)
    raw = envelope("ttl-1", body={"v": 1})
    assert run(proc, raw).status == 200
    proc.take_message()
    clk.t += 11.0
    resp = run(proc, envelope("ttl-1", body={"v": 2}))
    assert resp.status == 200 and resp.outcome == "accepted"
    assert proc.take_message() is not None


# --- deadline / close ----------------------------------------------------------


def test_deadline_rejects_new_and_duplicate():
    clk = Clock()
    proc = make_proc(clk)
    raw = envelope("dl-1")
    assert run(proc, raw).status == 200
    past = clk.t - 0.001
    resp = run(proc, envelope("dl-2"), deadline=past)
    assert resp.status == 408 and resp.outcome == "deadline_exceeded"
    assert run(proc, raw, deadline=past).status == 408


def test_take_and_close():
    proc = make_proc(Clock())
    run(proc, envelope("c-1"))
    assert proc.take_message() is not None
    assert proc.take_message() is None
    proc.close()
    assert proc.take_message() is None
    resp = run(proc, envelope("c-2"))
    assert resp.status == 503 and resp.outcome == "closed"


def test_close_discards_pending_inbox():
    # close() clears messages that were never taken
    proc = make_proc(Clock())
    run(proc, envelope("c-pending"))
    proc.close()
    assert proc.take_message() is None


# --- repr / summary whitelist ------------------------------------------------


def test_repr_and_summary_leak_nothing():
    proc = make_proc(Clock())
    marker = "sentinel-9f8d-marker"
    raw = envelope(marker, body={"payload": marker})
    resp = run(proc, raw)
    digest = hashlib.sha256(raw).hexdigest()
    for text in (repr(resp), repr(proc)):
        assert SECRET not in text
        assert marker not in text
        assert digest not in text
    summary = proc.public_summary()
    allowed = {
        "schema_version",
        "accepted",
        "duplicate",
        "authentication_failed",
        "invalid_message",
        "body_too_large",
        "id_conflict",
        "capacity_rejected",
        "deadline_exceeded",
        "closed",
    }
    assert set(summary) == allowed
    assert type(summary["schema_version"]) is int
    assert summary["schema_version"] == 1
    for key, value in summary.items():
        if key != "schema_version":
            assert type(value) is int and value >= 0


def test_summary_counts_outcomes():
    proc = make_proc(Clock())
    raw = envelope("s-1")
    run(proc, raw)  # accepted
    run(proc, raw)  # duplicate
    run(proc, envelope("s-1", body={"v": 2}))  # id_conflict
    summary = proc.public_summary()
    assert summary["accepted"] == 1
    assert summary["duplicate"] == 1
    assert summary["id_conflict"] == 1


# --- config bounds --------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_body_bytes": True},
        {"max_body_bytes": 0},
        {"max_body_bytes": 1_048_577},
        {"max_header_bytes": 511},
        {"max_header_bytes": 65_537},
        {"max_connections": 0},
        {"max_connections": 10_001},
        {"inbox_capacity": True},
        {"inbox_capacity": 0},
        {"inbox_capacity": 1_025},
        {"dedup_capacity": 0},
        {"dedup_capacity": 4_097},
        {"max_request_seconds": 0},
        {"max_request_seconds": 60.1},
        {"max_request_seconds": float("inf")},
        {"max_run_seconds": float("nan")},
        {"max_run_seconds": 0},
        {"max_run_seconds": 3_601},
        {"dedup_ttl_seconds": True},
        {"dedup_ttl_seconds": -1.0},
        {"dedup_ttl_seconds": 3_600.5},
        {"timestamp_tolerance_ms": True},
        {"timestamp_tolerance_ms": -1},
        {"timestamp_tolerance_ms": 3_600_001},
        {"timestamp_tolerance_ms": 1.5},
        {"profile": "overseas"},  # tolerance must be None overseas
        {"profile": "overseas", "timestamp_tolerance_ms": 100},
        {"webhook_secret": "x"},  # secret is a processor arg, not config
    ],
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises((TypeError, ValueError)):
        wr.ReceiverConfig(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"max_body_bytes": 1},
        {"max_body_bytes": 1_048_576},
        {"max_header_bytes": 512},
        {"max_header_bytes": 65_536},
        {"max_connections": 1},
        {"max_connections": 10_000},
        {"inbox_capacity": 1},
        {"inbox_capacity": 1_024},
        {"dedup_capacity": 1},
        {"dedup_capacity": 4_096},
        {"max_request_seconds": 0.001},
        {"max_request_seconds": 60},
        {"max_run_seconds": 3_600},
        {"dedup_ttl_seconds": 0.5},
        {"dedup_ttl_seconds": 3_600},
        {"timestamp_tolerance_ms": 0},
        {"timestamp_tolerance_ms": 3_600_000},
        {"profile": "overseas", "timestamp_tolerance_ms": None},
    ],
)
def test_valid_config_accepted(kwargs):
    assert wr.ReceiverConfig(**kwargs) == wr.ReceiverConfig(**kwargs)
