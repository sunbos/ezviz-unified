"""Targeted regressions for observed examples._webhook_receiver defects.

Synthetic data only: no network, filesystem, or environment access.
"""

import hashlib
import hmac
import json

import pytest

import examples._webhook_receiver as wr

SECRET = "synthetic-secret"
TIMESTAMP = "1700000000000"
WALL_MS = 1_700_000_000_000
RAW = b'{"header":{"messageId":"synthetic-id","type":"synthetic"},"body":{}}'
SENTINEL = "SENTINEL-9f3-marker"


class _Clock:
    def __init__(self):
        self.t = 1000.0


def _signature(raw=RAW, timestamp=TIMESTAMP):
    return hmac.new(SECRET.encode(), raw + timestamp.encode(), hashlib.sha1).hexdigest()


def _config(**overrides):
    return wr.ReceiverConfig(**overrides)


def _processor(clock):
    return wr.ReceiverProcessor(
        _config(),
        webhook_secret=SECRET,
        monotonic=lambda: clock.t,
        wall_time_ms=lambda: WALL_MS,
    )


def _status(result):
    return result.status


def _count(proc, name):
    return proc.public_summary()[name]


@pytest.mark.parametrize("secret", ["", "   ", "\t\n "])
def test_blank_webhook_secret_rejected(secret):
    clock = _Clock()
    with pytest.raises((ValueError, TypeError)):
        wr.ReceiverProcessor(
            _config(),
            webhook_secret=secret,
            monotonic=lambda: clock.t,
            wall_time_ms=lambda: WALL_MS,
        )


def test_illegal_unicode_secret_error_omits_secret():
    clock = _Clock()
    secret = SENTINEL + chr(0xD800)
    with pytest.raises(ValueError) as excinfo:
        wr.ReceiverProcessor(
            _config(),
            webhook_secret=secret,
            monotonic=lambda: clock.t,
            wall_time_ms=lambda: WALL_MS,
        )
    rendered = str(excinfo.value) + repr(excinfo.value)
    assert SENTINEL not in rendered
    assert chr(0xD800) not in rendered


def test_str_raw_body_over_max_bytes_is_caller_error():
    clock = _Clock()
    config, limit = wr.ReceiverConfig(max_body_bytes=64), 64
    proc = wr.ReceiverProcessor(
        config,
        webhook_secret=SECRET,
        monotonic=lambda: clock.t,
        wall_time_ms=lambda: WALL_MS,
    )
    raw = "x" * (limit + 1)
    with pytest.raises((ValueError, TypeError)):
        proc.process(
            raw,
            timestamp=TIMESTAMP,
            signature=_signature(raw.encode()),
            deadline=None,
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, False])
def test_non_finite_or_bool_deadline_is_caller_error(bad):
    proc = _processor(_Clock())
    with pytest.raises((ValueError, TypeError)):
        proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=bad)


def _patch_slow_sha256(monkeypatch, clock):
    original = wr.hashlib.sha256

    def slow_sha256(*args, **kwargs):
        digest = original(*args, **kwargs)
        clock.t += 1.0
        return digest

    monkeypatch.setattr(wr.hashlib, "sha256", slow_sha256)


def test_deadline_crossed_during_sha256_returns_408(monkeypatch):
    clock = _Clock()
    proc = _processor(clock)
    _patch_slow_sha256(monkeypatch, clock)
    clock.t = 1000.0
    result = proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=1000.5)
    assert _status(result) == 408
    assert proc.take_message() is None
    assert _count(proc, "accepted") == 0


def test_duplicate_after_deadline_cross_returns_408(monkeypatch):
    clock = _Clock()
    proc = _processor(clock)
    clock.t = 1000.0
    first = proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=1000.5)
    assert _status(first) == 200
    _patch_slow_sha256(monkeypatch, clock)
    clock.t = 1000.0
    second = proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=1000.5)
    assert _status(second) == 408
    assert _count(proc, "duplicate") == 0


def test_deadline_none_ttl_starts_at_true_acceptance_moment(monkeypatch):
    clock = _Clock()
    proc = wr.ReceiverProcessor(
        _config(dedup_ttl_seconds=0.5),
        webhook_secret=SECRET,
        monotonic=lambda: clock.t,
        wall_time_ms=lambda: WALL_MS,
    )
    # Advance the clock only during the first call's digest work so the
    # recorded expiry must be based on the post-hash acceptance moment
    # (1001.0), not the pre-hash read (1000.0). monkeypatch.context keeps
    # the network/other guards untouched outside the block.
    with monkeypatch.context() as mp:
        _patch_slow_sha256(mp, clock)
        clock.t = 1000.0
        first = proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=None)
    assert _status(first) == 200
    # At t=1001.0 a TTL anchored at 1000.0 has expired; anchored at the
    # true acceptance moment 1001.0 it is still live -> duplicate.
    second = proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=None)
    assert second.outcome == "duplicate"
    assert _count(proc, "duplicate") == 1
    assert _count(proc, "accepted") == 1


def test_parse_result_attribute_error_propagates(monkeypatch):
    monkeypatch.setattr(wr, "parse_webhook", lambda *a, **k: object())
    proc = _processor(_Clock())
    with pytest.raises(AttributeError):
        proc.process(RAW, timestamp=TIMESTAMP, signature=_signature(), deadline=None)


def test_bad_id_unicode_encode_error_still_400():
    raw = json.dumps(
        {"header": {"messageId": chr(0xD800), "type": "synthetic"}, "body": {}}
    ).encode("utf-8")
    proc = _processor(_Clock())
    result = proc.process(raw, timestamp=TIMESTAMP, signature=_signature(raw), deadline=None)
    assert result.status == 400
