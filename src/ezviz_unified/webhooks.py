"""Pure-function helpers for inbound EZVIZ webhook push messages.

Verification implements the archived formula ``hmac_sha1(UTF8(secret),
UTF8(body + t))`` from the official message-push document (help/566)
using only the standard library. The archived material reviewed for
this module provides no test vectors or timestamp units: a matching
signature proves only that the given body and timestamp match the MAC
under the configured secret — replayed bytes pass too. It does not
prove the current sender's identity, freshness, or replay safety;
callers must deduplicate by messageId and apply their own timestamp
policy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, field
from typing import Any

from .errors import AuthenticationError, ProtocolError

__all__ = [
    "WebhookMessage",
    "parse_unverified_webhook",
    "parse_webhook",
    "verify_webhook_signature",
]

_MAX_JSON_DEPTH = 64
_HEX_DIGITS = "0123456789abcdefABCDEF"
_VERIFY_OPERATION = "webhooks.verify"
_PARSE_OPERATION = "webhooks.parse"


class _DuplicateKeyError(Exception):
    """Sentinel raised inside the JSON pairs hook; carries no payload."""


@dataclass(frozen=True)
class WebhookMessage:
    """Parsed webhook envelope; every field is excluded from repr.

    Instances carry no authentication state: objects built by
    ``parse_unverified_webhook`` have the same type, and a ``verified``
    key inside ``raw`` is caller data rather than a trust signal.
    """

    message_id: str = field(repr=False)
    message_type: str = field(repr=False)
    body: Any = field(repr=False)
    raw: dict[str, Any] = field(repr=False)
    raw_body: bytes = field(repr=False)

    def ack(self) -> dict[str, str]:
        """Return a fresh ``{"messageId": ...}`` dict for each call.

        Emitting HTTP 200 with this body is the caller framework's job.
        """
        return {"messageId": self.message_id}


def _require_max_bytes(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive int")


def _require_raw_body(raw_body: bytes, max_bytes: int) -> None:
    if not isinstance(raw_body, bytes):
        raise ValueError("raw_body must be bytes")
    if len(raw_body) > max_bytes:
        raise ValueError("raw_body exceeds max_bytes")


def _require_secret(webhook_secret: str) -> None:
    ok = isinstance(webhook_secret, str) and bool(webhook_secret.strip())
    if ok:
        encodable = True
        try:
            webhook_secret.encode("utf-8")
        except UnicodeEncodeError:
            encodable = False
        ok = encodable
    if not ok:
        raise ValueError("webhook_secret must be a non-empty UTF-8 str")


def _decode_utf8(raw_body: bytes, operation: str) -> str:
    text: str | None = None
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise ProtocolError("webhook body is not valid UTF-8", operation=operation)
    return text


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError()
        result[key] = value
    return result


def _loads_envelope(text: str) -> Any:
    parsed: Any = None
    error: ProtocolError | None = None
    try:
        parsed = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except _DuplicateKeyError:
        error = ProtocolError(
            "webhook JSON contains a duplicate object key", operation=_PARSE_OPERATION
        )
    except json.JSONDecodeError:
        error = ProtocolError("webhook body is not valid JSON", operation=_PARSE_OPERATION)
    except RecursionError:
        error = ProtocolError(
            "webhook JSON nesting exceeds the SDK limit", operation=_PARSE_OPERATION
        )
    except ValueError:
        error = ProtocolError(
            "webhook JSON value exceeds parser limits", operation=_PARSE_OPERATION
        )
    if error is not None:
        raise error
    return parsed


def _check_json_shape(node: Any) -> None:
    stack: list[tuple[Any, int]] = [(node, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_JSON_DEPTH:
            raise ProtocolError(
                "webhook JSON nesting exceeds the SDK limit", operation=_PARSE_OPERATION
            )
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ProtocolError(
                "webhook JSON contains a non-finite number", operation=_PARSE_OPERATION
            )


def verify_webhook_signature(
    raw_body: bytes,
    *,
    timestamp: str,
    signature: str,
    webhook_secret: str,
    max_bytes: int = 1048576,
) -> bool:
    """Return True only when ``signature`` matches ``raw_body`` + ``timestamp``.

    Header values are untrusted: non-str, empty, or non-40-hex inputs
    yield False. Caller configuration problems (body type or size, blank
    secret, invalid max_bytes) raise ValueError, and a non-UTF-8 body
    raises ProtocolError. The raw body and timestamp are concatenated
    as-is: no re-serialization or stripping. The body is never parsed
    as JSON here, so arbitrary UTF-8 payloads can be checked for
    algorithm vectors.
    """
    _require_max_bytes(max_bytes)
    _require_raw_body(raw_body, max_bytes)
    _require_secret(webhook_secret)
    _decode_utf8(raw_body, _VERIFY_OPERATION)
    if not isinstance(timestamp, str) or not timestamp:
        return False
    if (
        not isinstance(signature, str)
        or len(signature) != 40
        or any(char not in _HEX_DIGITS for char in signature)
    ):
        return False
    timestamp_bytes: bytes | None = None
    try:
        timestamp_bytes = timestamp.encode("utf-8")
    except UnicodeEncodeError:
        timestamp_bytes = None
    if timestamp_bytes is None:
        return False
    digest = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body + timestamp_bytes,
        hashlib.sha1,
    ).digest()
    return hmac.compare_digest(digest, bytes.fromhex(signature))


def parse_webhook(
    raw_body: bytes,
    *,
    timestamp: str,
    signature: str,
    webhook_secret: str,
    max_bytes: int = 1048576,
) -> WebhookMessage:
    """Verify the signature first, then parse the envelope.

    A failed signature raises AuthenticationError(code=None) before any
    JSON parsing, so a bad signature never leaks parse-level details.
    """
    if not verify_webhook_signature(
        raw_body,
        timestamp=timestamp,
        signature=signature,
        webhook_secret=webhook_secret,
        max_bytes=max_bytes,
    ):
        raise AuthenticationError(
            "webhook signature verification failed",
            code=None,
            operation=_VERIFY_OPERATION,
        )
    return parse_unverified_webhook(raw_body, max_bytes=max_bytes)


def parse_unverified_webhook(
    raw_body: bytes,
    *,
    max_bytes: int = 1048576,
) -> WebhookMessage:
    """Parse the envelope without authentication; explicitly unauthenticated.

    The result carries no trust. This entry point exists for diagnostics
    and interop checks where the caller deliberately accepts unsigned
    bytes.
    """
    _require_max_bytes(max_bytes)
    _require_raw_body(raw_body, max_bytes)
    parsed = _loads_envelope(_decode_utf8(raw_body, _PARSE_OPERATION))
    _check_json_shape(parsed)
    if not isinstance(parsed, dict):
        raise ProtocolError("webhook envelope must be a JSON object", operation=_PARSE_OPERATION)
    header = parsed.get("header")
    if not isinstance(header, dict):
        raise ProtocolError("webhook header must be a JSON object", operation=_PARSE_OPERATION)
    if "body" not in parsed:
        raise ProtocolError(
            "webhook envelope is missing the body field", operation=_PARSE_OPERATION
        )
    message_id = header.get("messageId")
    if not isinstance(message_id, str) or not message_id.strip():
        raise ProtocolError(
            "webhook header messageId must be a non-blank string",
            operation=_PARSE_OPERATION,
        )
    message_type = header.get("type")
    if not isinstance(message_type, str) or not message_type.strip():
        raise ProtocolError(
            "webhook header type must be a non-blank string",
            operation=_PARSE_OPERATION,
        )
    return WebhookMessage(
        message_id=message_id,
        message_type=message_type,
        body=parsed["body"],
        raw=parsed,
        raw_body=raw_body,
    )
