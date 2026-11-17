"""Sequential webhook receiver core for the unified SDK (SP098-A).

Pure, injectable processing pipeline:

    size gate -> parse_webhook (config/UTF-8 -> HMAC -> JSON envelope)
    -> domestic timestamp freshness -> messageId byte-length cap
    -> dedup expiry sweep -> conflict / deadline / duplicate / capacity
    -> non-blocking bounded enqueue -> dedup record -> ACK

No sockets, no logging, no environment or file access; both clocks are
injected callables. The processor is designed for sequential use; do not
call ``process``/``take_message``/``close`` concurrently.

All signature/HMAC verification, JSON envelope parsing (``header`` +
``body``), and the SDK error taxonomy live inside
``ezviz_unified.webhooks.parse_webhook`` / ``WebhookMessage.ack`` and are
deliberately not re-implemented here. Those helpers are not raw-data
redactors: the parsed message retains the full payload, and only
``repr``/dataclass field hiding plus the fixed-field count projection in
this example keep bodies, ids, and secrets out of public output.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

from ezviz_unified.errors import AuthenticationError, ProtocolError
from ezviz_unified.webhooks import parse_webhook

if TYPE_CHECKING:
    from ezviz_unified.webhooks import WebhookMessage

__all__ = ["ReceiverConfig", "ReceiverProcessor", "ReceiverResponse"]

_SCHEMA_VERSION = 1
_MAX_MESSAGE_ID_BYTES = 256
_MAX_TIMESTAMP_CHARS = 16
_MAX_TIMESTAMP_TOLERANCE_MS = 3_600_000

_INT_FIELD_BOUNDS = {
    "max_body_bytes": (1, 1_048_576),
    "max_header_bytes": (512, 65_536),
    "max_connections": (1, 10_000),
    "inbox_capacity": (1, 1_024),
    "dedup_capacity": (1, 4_096),
}
_SECONDS_FIELD_BOUNDS = {
    "max_request_seconds": 60.0,
    "max_run_seconds": 3_600.0,
    "dedup_ttl_seconds": 3_600.0,
}

_OUTCOMES = (
    "accepted",
    "duplicate",
    "authentication_failed",
    "invalid_message",
    "body_too_large",
    "id_conflict",
    "capacity_rejected",
    "deadline_exceeded",
    "closed",
)
_OUTCOME_SET = frozenset(_OUTCOMES)


def _check_int(name: str, value: object, lo: int, hi: int) -> None:
    # ``type(value) is int`` rejects bool and non-int numerics in one step.
    if type(value) is not int or value < lo or value > hi:
        raise ValueError(f"{name} must be an int in [{lo}, {hi}]")


def _check_seconds(name: str, value: object, hi: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number of seconds")
    if not math.isfinite(value) or value <= 0 or value > hi:
        raise ValueError(f"{name} must be finite and in (0, {hi}]")


@dataclass(frozen=True, kw_only=True)
class ReceiverConfig:
    """Bounded, validated receiver settings. Never carries secrets."""

    profile: Literal["domestic", "overseas"] = "domestic"
    max_body_bytes: int = 65536
    max_header_bytes: int = 16384
    max_request_seconds: float = 1.5
    max_run_seconds: float = 60.0
    max_connections: int = 100
    inbox_capacity: int = 32
    dedup_capacity: int = 128
    dedup_ttl_seconds: float = 300.0
    timestamp_tolerance_ms: int | None = 300000

    def __post_init__(self) -> None:
        if self.profile not in ("domestic", "overseas"):
            raise ValueError("profile must be 'domestic' or 'overseas'")
        for name, (lo, hi) in _INT_FIELD_BOUNDS.items():
            _check_int(name, getattr(self, name), lo, hi)
        for name, hi in _SECONDS_FIELD_BOUNDS.items():
            _check_seconds(name, getattr(self, name), hi)
        tolerance = self.timestamp_tolerance_ms
        if self.profile == "overseas":
            if tolerance is not None:
                raise ValueError("overseas profile requires timestamp_tolerance_ms=None")
        else:
            _check_int(
                "timestamp_tolerance_ms",
                tolerance,
                0,
                _MAX_TIMESTAMP_TOLERANCE_MS,
            )


@dataclass(frozen=True)
class ReceiverResponse:
    """Frozen outcome envelope. ``body`` is repr-hidden: it may carry the
    protocol ACK's messageId. ``outcome`` is restricted to a fixed enum."""

    status: int
    body: bytes = field(repr=False)
    outcome: str = field()

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOME_SET:
            raise ValueError("outcome must be one of the fixed receiver outcomes")


def _default_wall_time_ms() -> int:
    return int(time.time() * 1000)


class ReceiverProcessor:
    """Sequential webhook processor with bounded inbox + dedup cache.

    ``webhook_secret`` is an explicit argument: it is never stored in the
    config, a repr, a response body, or exception text. It keeps the
    helpers' semantics: a non-empty ``str`` that must encode as UTF-8;
    no platform-specific length/charset limits are imposed here.
    """

    def __init__(
        self,
        config: ReceiverConfig,
        *,
        webhook_secret: str,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(config, ReceiverConfig):
            raise TypeError("config must be a ReceiverConfig")
        if not isinstance(webhook_secret, str) or not webhook_secret.strip():
            raise ValueError("webhook_secret must be a non-empty str")
        try:
            webhook_secret.encode("utf-8")
        except UnicodeError:  # surrogates: fixed ValueError, no secret echo
            raise ValueError("webhook_secret must encode as UTF-8") from None
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")
        if wall_time_ms is None:
            wall_time_ms = _default_wall_time_ms
        if not callable(wall_time_ms):
            raise TypeError("wall_time_ms must be callable")
        self._config = config
        self._webhook_secret = webhook_secret
        self._monotonic = monotonic
        self._wall_time_ms = wall_time_ms
        # message_id -> (sha256(raw_body).digest(), monotonic expiry)
        self._dedup: dict[str, tuple[bytes, float]] = {}
        self._inbox: deque[WebhookMessage] = deque()
        self._counts: dict[str, int] = {name: 0 for name in _OUTCOMES}
        self._closed = False

    @property
    def config(self) -> ReceiverConfig:
        return self._config

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(profile={self._config.profile!r}, "
            f"pending={len(self._inbox)}, closed={self._closed})"
        )

    # -- helpers -----------------------------------------------------------

    def _domestic_timestamp_fresh(self, timestamp: str) -> bool:
        """Domestic-only: non-negative ASCII-decimal millisecond value,
        at most 16 chars, within the configured wall-clock tolerance."""
        if (
            not isinstance(timestamp, str)
            or not timestamp
            or len(timestamp) > _MAX_TIMESTAMP_CHARS
            or not timestamp.isascii()
            or not timestamp.isdigit()
        ):
            return False
        delta = self._wall_time_ms() - int(timestamp)
        return abs(delta) <= self._config.timestamp_tolerance_ms

    def _expire_dedup(self, now: float) -> None:
        expired = [mid for mid, (_, exp) in self._dedup.items() if exp <= now]
        for mid in expired:
            del self._dedup[mid]

    def _error(self, status: int, outcome: str) -> ReceiverResponse:
        self._counts[outcome] += 1
        body = json.dumps({"error": outcome}).encode("utf-8")
        return ReceiverResponse(status=status, body=body, outcome=outcome)

    def _ack(self, outcome: str, message: WebhookMessage) -> ReceiverResponse:
        self._counts[outcome] += 1
        body = json.dumps(message.ack()).encode("utf-8")
        return ReceiverResponse(status=200, body=body, outcome=outcome)

    # -- main entry --------------------------------------------------------

    def process(
        self,
        raw_body: bytes,
        *,
        timestamp: str,
        signature: str,
        deadline: float | None = None,
    ) -> ReceiverResponse:
        if not isinstance(raw_body, bytes):
            raise TypeError("raw_body must be bytes")
        if deadline is not None:
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
                raise TypeError("deadline must be a real number or None")
            if not math.isfinite(deadline):
                raise ValueError("deadline must be finite")
        if self._closed:
            return self._error(503, "closed")
        if len(raw_body) > self._config.max_body_bytes:
            return self._error(413, "body_too_large")

        # Signature/authenticity and envelope parsing belong to the SDK:
        # authentication failures -> 401, protocol/envelope errors -> 400.
        # Anything else (config/programmer errors) propagates unchanged.
        try:
            message = parse_webhook(
                raw_body,
                timestamp=timestamp,
                signature=signature,
                webhook_secret=self._webhook_secret,
                max_bytes=self._config.max_body_bytes,
            )
        except AuthenticationError:
            return self._error(401, "authentication_failed")
        except ProtocolError:
            return self._error(400, "invalid_message")

        # Domestic freshness is an authentication property: the timestamp
        # was an authenticated signature input, so failures map to 401.
        # Overseas treats ``timestamp`` as an opaque signature input only.
        if self._config.profile == "domestic" and not self._domestic_timestamp_fresh(timestamp):
            return self._error(401, "authentication_failed")

        try:
            msg_id_len = len(message.message_id.encode("utf-8"))
        except UnicodeEncodeError:
            return self._error(400, "invalid_message")
        if msg_id_len > _MAX_MESSAGE_ID_BYTES:
            return self._error(400, "invalid_message")

        now = self._monotonic()
        self._expire_dedup(now)

        # In-memory equality check only; the digest never leaves this object.
        digest = hashlib.sha256(raw_body).digest()
        entry = self._dedup.get(message.message_id)

        # Same id, different raw bytes -> permanent conflict (never a win).
        if entry is not None and entry[0] != digest:
            return self._error(409, "id_conflict")

        # The acceptance moment is the real post-digest clock reading,
        # regardless of whether a deadline was supplied. An expired
        # deadline rejects everything after this point, including
        # previously accepted duplicates; hashing/cache work above may
        # have crossed it.
        now = self._monotonic()
        if deadline is not None and now >= deadline:
            return self._error(408, "deadline_exceeded")

        # Already-accepted duplicate: keep ACKing without re-enqueueing and
        # without consulting the new-message capacity gates. The stored
        # expiry is never renewed here.
        if entry is not None:
            return self._ack("duplicate", message)

        # Capacity failures are retryable: nothing is enqueued or recorded.
        if len(self._inbox) >= self._config.inbox_capacity:
            return self._error(503, "capacity_rejected")
        if len(self._dedup) >= self._config.dedup_capacity:
            return self._error(503, "capacity_rejected")

        self._inbox.append(message)
        self._dedup[message.message_id] = (
            digest,
            now + self._config.dedup_ttl_seconds,
        )
        return self._ack("accepted", message)

    def take_message(self) -> WebhookMessage | None:
        """Non-blocking drain of the bounded inbox."""
        if self._closed or not self._inbox:
            return None
        return self._inbox.popleft()

    def public_summary(self) -> dict[str, int]:
        """Fixed-shape counters only: no ids, types, bodies, digests,
        secrets, addresses, or exception text."""
        summary = {"schema_version": _SCHEMA_VERSION}
        summary.update(self._counts)
        return summary

    def close(self) -> None:
        """Drop inbox/dedup references and reject further ``process`` calls.

        This releases references held by the processor; it is not a
        guarantee of secure memory erasure, and messages already handed
        out via ``take_message`` are the caller's responsibility.
        """
        self._closed = True
        self._inbox.clear()
        self._dedup.clear()
