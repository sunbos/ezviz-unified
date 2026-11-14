"""Independent acceptance tests for the S021 webhook helpers.

Contract under test (docs/plans/2026-11-15-webhooks-and-candidate-inputs.md):
verify_webhook_signature / parse_webhook / parse_unverified_webhook and the
WebhookMessage DTO. Primary positive vectors are fixed, independently
produced values (RFC 2202 HMAC-SHA1 case 2 and a UTF-8 vector cross-checked
with OpenSSL by the plan owner); stdlib hmac is only used to derive inputs
for negative/structural cases, never to prove the primary vectors. Tests
perform no network access and read no real configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys

import pytest

from ezviz_unified import webhooks
from ezviz_unified.errors import AuthenticationError, ProtocolError

RFC2202_BODY = b"what do ya want for "
RFC2202_TIMESTAMP = "nothing?"
RFC2202_SECRET = "Jefe"
RFC2202_SIGNATURE = "effcdf6ae5eb2fa2d27416d5f184df9c259a7c79"

UTF8_BODY = (
    '{"header":{"type":"ys.test.msg","messageId":"EVAL_MESSAGE"},"body":"合成数据"}'
).encode("utf-8")
UTF8_TIMESTAMP = "1700000000000"
UTF8_SECRET = "EVAL_WEBHOOK_SECRET"
UTF8_SIGNATURE = "58a06fb31cd6aa6ca8a1eb98fb579551fc27d1a9"

LEAK_MARKER = "PRIVATEMARKER-9f27c1"
_ABSENT = object()


def _digest(raw_body: bytes, timestamp: str, secret: str) -> str:
    """stdlib HMAC-SHA1 hex; derives secondary/negative inputs only."""
    return hmac.new(
        secret.encode("utf-8"),
        raw_body + timestamp.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()


def _parse(raw_body, timestamp=UTF8_TIMESTAMP, secret=UTF8_SECRET, **kw):
    """parse_webhook with a correctly formed signature for *raw_body*."""
    return webhooks.parse_webhook(
        raw_body,
        timestamp=timestamp,
        signature=_digest(raw_body, timestamp, secret),
        webhook_secret=secret,
        **kw,
    )


def _doc(*, header=_ABSENT, body=_ABSENT, message_id="m-1", message_type="ys.test", **extra):
    doc = dict(extra)
    doc["header"] = {"type": message_type, "messageId": message_id} if header is _ABSENT else header
    if body is not _ABSENT:
        doc["body"] = body
    return doc


def _raw(doc=None, **kw) -> bytes:
    if doc is None:
        doc = _doc(**kw)
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _iter_chain(exc):
    seen, stack = set(), [exc]
    while stack:
        err = stack.pop()
        if not isinstance(err, BaseException) or id(err) in seen:
            continue
        seen.add(id(err))
        yield err
        stack.append(err.__cause__)
        stack.append(err.__context__)


def _assert_no_secret_leak(exc, *needles):
    for err in _iter_chain(exc):
        rendered = str(err) + repr(err) + repr(getattr(err, "__dict__", {}))
        for needle in needles:
            assert needle not in rendered


# --- signature verification ---------------------------------------------


def test_verify_rfc2202_case2_fixed_vector():
    assert (
        webhooks.verify_webhook_signature(
            RFC2202_BODY,
            timestamp=RFC2202_TIMESTAMP,
            signature=RFC2202_SIGNATURE,
            webhook_secret=RFC2202_SECRET,
        )
        is True
    )


def test_verify_utf8_fixed_vector():
    assert (
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
        )
        is True
    )


def test_verify_uppercase_hex_is_equivalent():
    for body, ts, sig, secret in (
        (RFC2202_BODY, RFC2202_TIMESTAMP, RFC2202_SIGNATURE, RFC2202_SECRET),
        (UTF8_BODY, UTF8_TIMESTAMP, UTF8_SIGNATURE, UTF8_SECRET),
    ):
        assert (
            webhooks.verify_webhook_signature(
                body,
                timestamp=ts,
                signature=sig.upper(),
                webhook_secret=secret,
            )
            is True
        )


def test_verify_accepts_non_json_utf8_body():
    body = "明文负载，不是JSON".encode("utf-8")
    signature = _digest(body, UTF8_TIMESTAMP, UTF8_SECRET)
    assert (
        webhooks.verify_webhook_signature(
            body,
            timestamp=UTF8_TIMESTAMP,
            signature=signature,
            webhook_secret=UTF8_SECRET,
        )
        is True
    )


def test_verify_accepts_empty_body_with_valid_signature():
    signature = _digest(b"", UTF8_TIMESTAMP, UTF8_SECRET)
    assert (
        webhooks.verify_webhook_signature(
            b"",
            timestamp=UTF8_TIMESTAMP,
            signature=signature,
            webhook_secret=UTF8_SECRET,
        )
        is True
    )


@pytest.mark.parametrize(
    ("raw_body", "timestamp", "signature", "secret"),
    [
        (b"what do ya want for?", RFC2202_TIMESTAMP, RFC2202_SIGNATURE, RFC2202_SECRET),
        (RFC2202_BODY + b" ", RFC2202_TIMESTAMP, RFC2202_SIGNATURE, RFC2202_SECRET),
        (RFC2202_BODY, RFC2202_TIMESTAMP + " ", RFC2202_SIGNATURE, RFC2202_SECRET),
        (RFC2202_BODY, RFC2202_TIMESTAMP, RFC2202_SIGNATURE, "jefe"),
        (RFC2202_BODY, RFC2202_TIMESTAMP, RFC2202_SIGNATURE, "Jefe "),
        (UTF8_BODY + b"\n", UTF8_TIMESTAMP, UTF8_SIGNATURE, UTF8_SECRET),
        (UTF8_BODY, UTF8_TIMESTAMP + "0", UTF8_SIGNATURE, UTF8_SECRET),
        (UTF8_BODY, UTF8_TIMESTAMP, UTF8_SIGNATURE, UTF8_SECRET + "x"),
    ],
    ids=[
        "body-changed",
        "body-trailing-space",
        "ts-trailing-space",
        "secret-changed",
        "secret-trailing-space",
        "utf8-body-newline",
        "ts-extra-digit",
        "secret-extra",
    ],
)
def test_verify_rejects_mismatched_material(raw_body, timestamp, signature, secret):
    assert (
        webhooks.verify_webhook_signature(
            raw_body,
            timestamp=timestamp,
            signature=signature,
            webhook_secret=secret,
        )
        is False
    )


def test_verify_does_not_reserialize_json():
    spaced = UTF8_BODY.replace(b'"body":"', b'"body": "')
    assert spaced != UTF8_BODY
    assert (
        webhooks.verify_webhook_signature(
            spaced,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
        )
        is False
    )
    canonical = json.dumps(
        json.loads(UTF8_BODY),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert canonical != UTF8_BODY
    assert (
        webhooks.verify_webhook_signature(
            canonical,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
        )
        is False
    )


@pytest.mark.parametrize(
    "signature",
    [
        "",
        RFC2202_SIGNATURE[:-1],
        RFC2202_SIGNATURE + "0",
        "0" * 39,
        "0" * 41,
        "g" * 40,
        "-" * 40,
        RFC2202_SIGNATURE[:20] + " " + RFC2202_SIGNATURE[21:],
        " " + RFC2202_SIGNATURE[1:],
        RFC2202_SIGNATURE[:-1] + " ",
        "é" * 40,
        "４" * 40,
    ],
    ids=[
        "empty",
        "short-39",
        "long-41",
        "zeros-39",
        "zeros-41",
        "non-hex",
        "dashes",
        "embedded-space",
        "leading-space",
        "trailing-space",
        "non-ascii",
        "fullwidth",
    ],
)
def test_verify_rejects_malformed_signature(signature):
    assert (
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp=UTF8_TIMESTAMP,
            signature=signature,
            webhook_secret=UTF8_SECRET,
        )
        is False
    )


@pytest.mark.parametrize("signature", [b"0" * 40, None])
def test_verify_rejects_non_string_signature(signature):
    assert (
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp=UTF8_TIMESTAMP,
            signature=signature,
            webhook_secret=UTF8_SECRET,
        )
        is False
    )


def test_verify_rejects_empty_timestamp():
    assert (
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp="",
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
        )
        is False
    )


def test_verify_uses_timestamp_verbatim():
    timestamp = " " + UTF8_TIMESTAMP + " "
    signature = _digest(UTF8_BODY, timestamp, UTF8_SECRET)
    assert (
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp=timestamp,
            signature=signature,
            webhook_secret=UTF8_SECRET,
        )
        is True
    )


def test_verify_uses_secret_verbatim():
    secret = " " + RFC2202_SECRET + " "
    signature = _digest(RFC2202_BODY, RFC2202_TIMESTAMP, secret)
    assert (
        webhooks.verify_webhook_signature(
            RFC2202_BODY,
            timestamp=RFC2202_TIMESTAMP,
            signature=signature,
            webhook_secret=secret,
        )
        is True
    )
    assert (
        webhooks.verify_webhook_signature(
            RFC2202_BODY,
            timestamp=RFC2202_TIMESTAMP,
            signature=signature,
            webhook_secret=RFC2202_SECRET,
        )
        is False
    )


@pytest.mark.parametrize("secret", ["", "   ", "\t\n", None, b"x", 42])
def test_verify_rejects_invalid_secret(secret):
    with pytest.raises(ValueError):
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=secret,
        )


@pytest.mark.parametrize("raw", ["not-bytes", None, 5, ["x"]])
def test_raw_body_requires_bytes(raw):
    with pytest.raises(ValueError):
        webhooks.verify_webhook_signature(
            raw,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
        )
    with pytest.raises(ValueError):
        webhooks.parse_webhook(
            raw,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
        )
    with pytest.raises(ValueError):
        webhooks.parse_unverified_webhook(raw)


def test_verify_max_bytes_boundary():
    limit = len(RFC2202_BODY) + 8
    body = RFC2202_BODY + b" " * (limit - len(RFC2202_BODY))
    assert len(body) == limit
    signature = _digest(body, RFC2202_TIMESTAMP, RFC2202_SECRET)
    assert (
        webhooks.verify_webhook_signature(
            body,
            timestamp=RFC2202_TIMESTAMP,
            signature=signature,
            webhook_secret=RFC2202_SECRET,
            max_bytes=limit,
        )
        is True
    )
    over = body + b"x"
    over_sig = _digest(over, RFC2202_TIMESTAMP, RFC2202_SECRET)
    with pytest.raises(ValueError):
        webhooks.verify_webhook_signature(
            over,
            timestamp=RFC2202_TIMESTAMP,
            signature=over_sig,
            webhook_secret=RFC2202_SECRET,
            max_bytes=limit,
        )


@pytest.mark.parametrize("bad", [True, False, 0, -1, 2.5, "1024", None])
def test_max_bytes_must_be_strict_positive_int(bad):
    with pytest.raises(ValueError):
        webhooks.verify_webhook_signature(
            UTF8_BODY,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
            max_bytes=bad,
        )
    with pytest.raises(ValueError):
        webhooks.parse_unverified_webhook(UTF8_BODY, max_bytes=bad)
    with pytest.raises(ValueError):
        webhooks.parse_webhook(
            UTF8_BODY,
            timestamp=UTF8_TIMESTAMP,
            signature=UTF8_SIGNATURE,
            webhook_secret=UTF8_SECRET,
            max_bytes=bad,
        )


# --- parse ordering / error types ----------------------------------------


def test_parse_verifies_signature_before_json():
    with pytest.raises(AuthenticationError):
        webhooks.parse_webhook(
            b"{definitely not json",
            timestamp=UTF8_TIMESTAMP,
            signature="0" * 40,
            webhook_secret=UTF8_SECRET,
        )


def test_parse_raises_authentication_error_on_bad_signature():
    for timestamp, signature, secret in (
        (UTF8_TIMESTAMP, "0" * 40, UTF8_SECRET),
        (UTF8_TIMESTAMP + "1", UTF8_SIGNATURE, UTF8_SECRET),
        (UTF8_TIMESTAMP, UTF8_SIGNATURE, "WRONG_SECRET"),
        ("", UTF8_SIGNATURE, UTF8_SECRET),
    ):
        with pytest.raises(AuthenticationError):
            webhooks.parse_webhook(
                UTF8_BODY,
                timestamp=timestamp,
                signature=signature,
                webhook_secret=secret,
            )


def test_parse_valid_signature_invalid_json_is_protocol_error():
    with pytest.raises(ProtocolError):
        webhooks.parse_webhook(
            RFC2202_BODY,
            timestamp=RFC2202_TIMESTAMP,
            signature=RFC2202_SIGNATURE,
            webhook_secret=RFC2202_SECRET,
        )


# --- parse: document shape ------------------------------------------------


def test_parse_utf8_fixed_vector():
    msg = webhooks.parse_webhook(
        UTF8_BODY,
        timestamp=UTF8_TIMESTAMP,
        signature=UTF8_SIGNATURE,
        webhook_secret=UTF8_SECRET,
    )
    assert msg.message_id == "EVAL_MESSAGE"
    assert msg.message_type == "ys.test.msg"
    assert msg.body == "合成数据"
    assert msg.raw_body == UTF8_BODY
    assert msg.raw == json.loads(UTF8_BODY)


@pytest.mark.parametrize(
    "body",
    [
        "text",
        "",
        0,
        -1.25,
        True,
        None,
        [],
        [1, "two", {"three": None}],
        {},
        {"nested": {"a": [1, 2, {"b": False}]}},
    ],
)
def test_parse_body_accepts_any_json_value(body):
    msg = _parse(_raw(body=body))
    assert msg.body == body
    assert msg.message_id == "m-1"
    assert msg.message_type == "ys.test"


def test_parse_requires_body_key():
    with pytest.raises(ProtocolError):
        _parse(_raw({"header": {"type": "ys.test", "messageId": "m"}}))


@pytest.mark.parametrize(
    "raw",
    [b"[]", b"[1, 2]", b'"text"', b"123", b"null", b"true", b"1.5", b"{}", b""],
)
def test_parse_requires_top_level_object(raw):
    with pytest.raises(ProtocolError):
        _parse(raw)


def test_parse_requires_header_key():
    with pytest.raises(ProtocolError):
        _parse(_raw({"body": 1}))


@pytest.mark.parametrize(
    "header",
    [
        "not-an-object",
        7,
        True,
        None,
        [],
        {"messageId": "m-1"},
        {"type": "ys.test"},
        {"type": "", "messageId": "m-1"},
        {"type": "   ", "messageId": "m-1"},
        {"type": 12, "messageId": "m-1"},
        {"type": None, "messageId": "m-1"},
        {"type": ["ys.test"], "messageId": "m-1"},
        {"type": "ys.test", "messageId": ""},
        {"type": "ys.test", "messageId": "   "},
        {"type": "ys.test", "messageId": 0},
        {"type": "ys.test", "messageId": None},
        {"type": "ys.test", "messageId": ["m-1"]},
    ],
)
def test_parse_header_shape_and_required_fields(header):
    with pytest.raises(ProtocolError):
        _parse(_raw(header=header))


def test_parse_preserves_unknown_fields_and_types():
    doc = _doc(
        message_id="id-9",
        message_type="vendor.custom.v9",
        body={"keep": {"deep": [1, "x"]}},
        extra_top=[3, 4],
    )
    doc["header"]["trace"] = "abc"
    raw = _raw(doc)
    msg = _parse(raw)
    assert msg.message_id == "id-9"
    assert msg.message_type == "vendor.custom.v9"
    assert msg.raw == doc
    assert msg.raw["header"]["trace"] == "abc"
    assert msg.raw["extra_top"] == [3, 4]
    assert msg.raw_body == raw


def test_parse_does_not_reparse_payload_string():
    payload = '{"inner": [1, 2]}'
    msg = _parse(_raw(body={"payload": payload}))
    assert isinstance(msg.body["payload"], str)
    assert msg.body["payload"] == payload


def test_parse_rejects_duplicate_keys():
    for raw in (
        b'{"header":{"type":"t","messageId":"m"},"body":1,"body":2}',
        b'{"header":{"type":"t","messageId":"m","messageId":"m2"},"body":1}',
        b'{"header":{"type":"t","messageId":"m"},"body":{"a":1,"a":2}}',
    ):
        with pytest.raises(ProtocolError):
            _parse(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"header":{"type":"t","messageId":"m"},"body":NaN}',
        b'{"header":{"type":"t","messageId":"m"},"body":Infinity}',
        b'{"header":{"type":"t","messageId":"m"},"body":-Infinity}',
        b'{"header":{"type":"t","messageId":"m"},"body":{"x":NaN}}',
        b'{"header":{"type":"t","messageId":"m"},"body":[Infinity]}',
    ],
)
def test_parse_rejects_non_finite_numbers(raw):
    with pytest.raises(ProtocolError):
        _parse(raw)


def test_parse_rejects_invalid_utf8():
    raw = b'{"header":{"type":"t","messageId":"m"},"body":"\xff\xfe"}'
    with pytest.raises(ProtocolError):
        _parse(raw)
    with pytest.raises(ProtocolError):
        webhooks.parse_unverified_webhook(raw)


def test_parse_rejects_deep_nesting():
    depth = 2048
    inner = b"[" * depth + b"0" + b"]" * depth
    raw = b'{"header":{"type":"t","messageId":"m"},"body":' + inner + b"}"
    with pytest.raises(ProtocolError):
        _parse(raw)
    with pytest.raises(ProtocolError):
        webhooks.parse_unverified_webhook(raw)


def test_parse_max_bytes_boundary():
    prefix = b'{"header":{"type":"t","messageId":"m"},"body":"'
    suffix = b'"}'
    limit = 256
    pad = limit - len(prefix) - len(suffix)
    raw = prefix + b"x" * pad + suffix
    assert len(raw) == limit
    msg = _parse(raw, max_bytes=limit)
    assert msg.body == "x" * pad
    with pytest.raises(ValueError):
        _parse(raw, max_bytes=limit - 1)
    msg2 = webhooks.parse_unverified_webhook(raw, max_bytes=limit)
    assert msg2.message_id == "m"
    with pytest.raises(ValueError):
        webhooks.parse_unverified_webhook(raw, max_bytes=limit - 1)


# --- error hygiene --------------------------------------------------------


def test_authentication_error_does_not_leak_materials():
    body = b'{"header":{"type":"t","messageId":"' + LEAK_MARKER.encode("utf-8") + b'"},"body":1}'
    with pytest.raises(AuthenticationError) as excinfo:
        webhooks.parse_webhook(
            body,
            timestamp=UTF8_TIMESTAMP,
            signature="a" * 40,
            webhook_secret=UTF8_SECRET,
        )
    _assert_no_secret_leak(excinfo.value, LEAK_MARKER, UTF8_SECRET, "a" * 40)


def test_protocol_error_does_not_leak_materials():
    raw = b"not-json " + LEAK_MARKER.encode("utf-8")
    with pytest.raises(ProtocolError) as excinfo:
        _parse(raw)
    _assert_no_secret_leak(excinfo.value, LEAK_MARKER, UTF8_SECRET)


# --- WebhookMessage DTO ---------------------------------------------------


def test_message_repr_excludes_payload_fields():
    msg = webhooks.parse_webhook(
        UTF8_BODY,
        timestamp=UTF8_TIMESTAMP,
        signature=UTF8_SIGNATURE,
        webhook_secret=UTF8_SECRET,
    )
    rendered = repr(msg) + " " + str(msg)
    for needle in (
        "EVAL_MESSAGE",
        "ys.test.msg",
        "合成数据",
        UTF8_BODY.decode("utf-8"),
        UTF8_SECRET,
        UTF8_TIMESTAMP,
        UTF8_SIGNATURE,
    ):
        assert needle not in rendered


def test_ack_returns_fresh_minimal_dict():
    msg = webhooks.parse_webhook(
        UTF8_BODY,
        timestamp=UTF8_TIMESTAMP,
        signature=UTF8_SIGNATURE,
        webhook_secret=UTF8_SECRET,
    )
    first = msg.ack()
    assert first == {"messageId": "EVAL_MESSAGE"}
    first["messageId"] = "tampered"
    first["extra"] = True
    second = msg.ack()
    assert second == {"messageId": "EVAL_MESSAGE"}
    assert second is not first
    assert list(second) == ["messageId"]


# --- explicit unverified entry point --------------------------------------


def test_parse_unverified_parses_without_signature():
    msg = webhooks.parse_unverified_webhook(UTF8_BODY)
    assert msg.message_id == "EVAL_MESSAGE"
    assert msg.message_type == "ys.test.msg"
    assert msg.body == "合成数据"
    assert msg.raw_body == UTF8_BODY


def test_parse_unverified_still_enforces_schema():
    with pytest.raises(ProtocolError):
        webhooks.parse_unverified_webhook(b"not json")
    with pytest.raises(ProtocolError):
        webhooks.parse_unverified_webhook(_raw({"body": 1}))
    with pytest.raises(ProtocolError):
        webhooks.parse_unverified_webhook(b'{"header":{"type":"t","messageId":"m"},"body":NaN}')


# --- scalar fidelity / interpreter-limit regressions -----------------------


def test_parse_body_preserves_scalar_types():
    for value, expected_type in (
        (True, bool),
        (False, bool),
        (None, type(None)),
        (0, int),
        (1, int),
        (-1, int),
        (-1.25, float),
        (0.5, float),
        ("text", str),
        ("", str),
    ):
        msg = _parse(_raw(body=value))
        assert type(msg.body) is expected_type
        assert msg.body == value
    assert _parse(_raw(body=True)).body is True
    assert _parse(_raw(body=False)).body is False
    assert _parse(_raw(body=None)).body is None


def test_parse_rejects_integer_beyond_stdlib_digit_limit():
    if not hasattr(sys, "set_int_max_str_digits"):
        pytest.skip("sys.set_int_max_str_digits is unavailable")
    digits = "9" * 4400
    raw = (
        b'{"header":{"type":"t","messageId":"'
        + LEAK_MARKER.encode("utf-8")
        + b'"},"body":'
        + digits.encode("ascii")
        + b"}"
    )
    original = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        # The lowered interpreter limit makes the stdlib JSON parser raise
        # ValueError internally; both public helpers must still surface a
        # fixed ProtocolError and must not leak raw material through the
        # exception chain.
        with pytest.raises(ProtocolError) as excinfo_unverified:
            webhooks.parse_unverified_webhook(raw)
        _assert_no_secret_leak(excinfo_unverified.value, LEAK_MARKER, digits)
        with pytest.raises(ProtocolError) as excinfo_verified:
            _parse(raw)
        _assert_no_secret_leak(excinfo_verified.value, LEAK_MARKER, digits)
    finally:
        sys.set_int_max_str_digits(original)
