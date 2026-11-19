"""Offline coverage for a synthetic overseas device ONLINE webhook."""

import hashlib
import hmac
import json

from test_webhook_receiver_http import (
    _body_of,
    _connector,
    _exchange,
    _request_bytes,
    _running,
    _status,
)

from examples._webhook_receiver import ReceiverConfig, ReceiverProcessor
from examples.overseas_device_online import is_device_online, main
from examples.webhook_receiver import BoundedWebhookReceiver
from ezviz_unified.webhooks import WebhookMessage

SECRET = "synthetic-per-test-secret"
T_OPAQUE = "opaque-overseas"  # overseas timestamps stay opaque/nonnumeric

HEADER = {
    "messageId": "synthetic-msg-9f41",
    "type": "ys.onoffline",
    "channelNo": 1,
    "deviceId": "synthetic-device-01",
    "messageTime": 7,
}
BODY = {
    "msgType": "ONLINE",
    "devType": "synthetic-camera",
    "regTime": "2023-11-14 22:13:20",
    "natIp": "192.0.2.1",
    "subSerial": "synthetic-sub-01",
    "occurTime": "2023-11-14 22:13:24",
    "deviceName": "合成设备",
    "dasId": "synthetic-das-01",
    "futureField": {"flag": True, "items": [1, 2]},
}


def _signature(raw: bytes, t: str) -> str:
    return hmac.new(SECRET.encode("utf-8"), raw + t.encode("utf-8"), hashlib.sha1).hexdigest()


def _signed_request(raw: bytes, *, signature=None, t: str = T_OPAQUE) -> bytes:
    sig = _signature(raw, t) if signature is None else signature
    return _request_bytes(
        body=raw,
        headers=[
            (b"t", t.encode("utf-8")),
            (b"Signature", sig.encode("ascii")),
            (b"Content-Type", b"application/json"),
            (b"Content-Length", str(len(raw)).encode("ascii")),
        ],
    )


def _message(message_type, body):
    """Build a WebhookMessage without the processor or any network I/O."""
    raw = {"header": {"messageId": "m-syn", "type": message_type}, "body": body}
    raw_body = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    return WebhookMessage(
        message_id=raw["header"]["messageId"],
        message_type=message_type,
        body=body,
        raw=raw,
        raw_body=raw_body,
    )


def test_overseas_online_signed_loopback(capsys):
    config = ReceiverConfig(
        profile="overseas",
        timestamp_tolerance_ms=None,
        max_connections=4,
        max_run_seconds=5.0,
        max_request_seconds=2.0,
        max_body_bytes=4096,
        max_header_bytes=2048,
        inbox_capacity=1,
        dedup_capacity=2,
        dedup_ttl_seconds=30.0,
    )
    processor = ReceiverProcessor(config, webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(processor, port=0)
    envelope = {"header": dict(HEADER), "body": dict(BODY)}
    original = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    reserialized = json.dumps(
        envelope, ensure_ascii=True, sort_keys=True, separators=(", ", ": ")
    ).encode("utf-8")
    assert reserialized != original  # same semantics, different bytes
    other = {"header": dict(HEADER, messageId="synthetic-msg-9f42"), "body": dict(BODY)}
    other_raw = json.dumps(other, ensure_ascii=False).encode("utf-8")
    ack = {"messageId": HEADER["messageId"]}
    try:
        with _running(receiver) as results:
            connect = _connector(receiver.port)
            response = _exchange(connect, _signed_request(original))
            assert _status(response) == 200
            assert json.loads(_body_of(response)) == ack
            response = _exchange(connect, _signed_request(original))  # replay
            assert _status(response) == 200
            assert json.loads(_body_of(response)) == ack
            response = _exchange(connect, _signed_request(reserialized))
            assert _status(response) == 409
            assert json.loads(_body_of(response)) == {"error": "id_conflict"}
            bad = _signed_request(other_raw, signature="0" * 40)
            response = _exchange(connect, bad)
            assert _status(response) == 401
            assert json.loads(_body_of(response)) == {"error": "authentication_failed"}
        receipt = results[0]
        assert receipt["connections"] == 4
        assert receipt["accepted"] == 1
        assert receipt["duplicate"] == 1
        assert receipt["id_conflict"] == 1
        assert receipt["authentication_failed"] == 1
        assert receipt["stop_reason"] == "max_connections"
        serialized = json.dumps(receipt)
        captured = capsys.readouterr()
        for canary in (
            SECRET,
            T_OPAQUE,
            HEADER["messageId"],
            BODY["subSerial"],
            BODY["natIp"],
            BODY["deviceName"],
            _signature(original, T_OPAQUE),
        ):
            assert canary not in serialized
            assert canary not in captured.out
            assert canary not in captured.err
        assert captured.err == ""
        assert all(not key.startswith("_") for key in receipt)
        assert "errors" not in receipt and "stderr" not in receipt
        # Drain only after the receiver loop has stopped, before close().
        message = processor.take_message()
        assert isinstance(message, WebhookMessage)
        assert message.raw_body == original
        assert message.message_type == "ys.onoffline"
        assert is_device_online(message) is True
        assert message.raw["header"] == HEADER
        assert message.body == BODY
        assert message.body["futureField"] == BODY["futureField"]
        assert processor.take_message() is None
    finally:
        processor.close()
        receiver.close()


def test_is_device_online_strict_and_signature_blind():
    # Hand-built messages carry no signature or freshness proof; the helper
    # judges only the message type and direction.
    online = _message("ys.onoffline", {"msgType": "ONLINE"})
    assert is_device_online(online) is True
    assert is_device_online(_message("ys.onoffline", {"msgType": "OFFLINE"})) is False
    assert is_device_online(_message("ys.keepalive", {"msgType": "ONLINE"})) is False
    assert is_device_online(_message("ys.onoffline", {"msgType": "online"})) is False
    assert is_device_online(_message("ys.onoffline", {})) is False
    assert is_device_online(_message("ys.onoffline", None)) is False
    assert is_device_online(_message("ys.onoffline", "ONLINE")) is False


def test_cli_help_and_invalid_args(capsys):
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "usage: python -m examples.overseas_device_online\n"
    assert captured.err == ""
    assert main(["--no-such-argument"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "usage: python -m examples.overseas_device_online\n"
    # main([]) runs the real demo loop; a dedicated real subprocess proof
    # covers it, and the nested conftest socket policy is not bypassed here.


def test_example_overseas_device_online(monkeypatch, capsys):
    import json

    from test_webhook_receiver_http import _ALLOWED_PORTS, _dial

    from examples import overseas_device_online as demo

    real_receiver_cls = demo.BoundedWebhookReceiver
    real_connection_cls = demo.HTTPConnection
    receivers, registered, dialed = [], [], []

    def receiver_factory(processor, port):
        assert port == 0
        receiver = real_receiver_cls(processor, port=port)
        receivers.append(receiver)
        registered.append(receiver.port)
        _ALLOWED_PORTS.add(receiver.port)
        return receiver

    class LoopbackHTTPConnection(real_connection_cls):
        def connect(self):
            assert self.host == "127.0.0.1"
            assert self.port == receivers[0].port
            dialed.append(self.port)
            self.sock = _dial(self.host, self.port)

    monkeypatch.setattr(demo, "BoundedWebhookReceiver", receiver_factory)
    monkeypatch.setattr(demo, "HTTPConnection", LoopbackHTTPConnection)
    try:
        assert demo.main([]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {
            "profile": "overseas",
            "simulation": True,
            "accepted": 1,
            "duplicate": 1,
            "online_events": 1,
        }
        assert captured.err == ""
        assert len(receivers) == 1
        assert dialed == [receivers[0].port, receivers[0].port]
    finally:
        for receiver in receivers:
            receiver.close()
        for port in registered:
            _ALLOWED_PORTS.discard(port)
