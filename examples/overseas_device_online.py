"""Offline simulation of an overseas EZVIZ device-online webhook event."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.client import HTTPConnection, HTTPException
from typing import Any

from examples._webhook_receiver import ReceiverConfig, ReceiverProcessor
from examples.webhook_receiver import BoundedWebhookReceiver
from ezviz_unified.webhooks import WebhookMessage

USAGE = "usage: python -m examples.overseas_device_online"
_FAILURE = "error: overseas online event simulation failed"


class SimulationFailure(Exception):
    """Raised when the offline simulation observes an unexpected result."""


def is_device_online(message: WebhookMessage) -> bool:
    """Classify a verified ``ys.onoffline`` ONLINE event.

    Performs no authentication; use only after verified intake.
    """
    return (
        message.message_type == "ys.onoffline"
        and isinstance(message.body, dict)
        and message.body.get("msgType") == "ONLINE"
    )


def _expect(condition: bool, detail: str) -> None:
    if not condition:
        raise SimulationFailure(detail)


def _synthetic_event() -> tuple[str, bytes]:
    message_id = "sim-overseas-online-00000001"
    event: dict[str, Any] = {
        "header": {
            "messageId": message_id,
            "type": "ys.onoffline",
            "channelNo": 1,
            "deviceId": "SIM00000000000001",
            "messageTime": 7,
        },
        "body": {
            "msgType": "ONLINE",
            "devType": "SIM-CAM",
            "regTime": "2024-01-01 00:00:00",
            "natIp": "203.0.113.10",
            "subSerial": "SIMSUB0000001",
            "occurTime": "2024-01-01 00:00:01",
            "deviceName": "合成设备",
            "dasId": "sim-das-01",
            "simUnknownMarker": "sim-unknown-field",
        },
    }
    return message_id, json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8")


def _signed_post(secret: str, raw: bytes, port: int, message_id: str) -> None:
    timestamp = "opaque-local-t"
    signature = hmac.new(
        secret.encode("utf-8"), raw + timestamp.encode("utf-8"), hashlib.sha1
    ).hexdigest()
    connection = HTTPConnection("127.0.0.1", port, timeout=2.0)
    try:
        connection.request(
            "POST",
            "/ezviz/messages",
            body=raw,
            headers={"Content-Type": "application/json", "t": timestamp, "Signature": signature},
        )
        response = connection.getresponse()
        status, payload = response.status, response.read(1025)
        _expect(len(payload) <= 1024, "oversized acknowledgement")
    finally:
        connection.close()
    _expect(status == 200, "unexpected HTTP status")
    _expect(json.loads(payload) == {"messageId": message_id}, "bad ACK body")


def _run_simulation() -> dict[str, Any]:
    secret = secrets.token_hex(32)
    message_id, raw = _synthetic_event()
    processor = ReceiverProcessor(
        ReceiverConfig(
            profile="overseas",
            timestamp_tolerance_ms=None,
            max_connections=2,
            max_run_seconds=5.0,
            max_request_seconds=2.0,
            max_body_bytes=4096,
            max_header_bytes=2048,
            inbox_capacity=1,
            dedup_capacity=2,
            dedup_ttl_seconds=30.0,
        ),
        webhook_secret=secret,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            receiver = BoundedWebhookReceiver(processor, port=0)
            try:
                worker = executor.submit(receiver.run)
                _signed_post(secret, raw, receiver.port, message_id)
                _signed_post(secret, raw, receiver.port, message_id)
                stats = worker.result(timeout=10.0)
            finally:
                receiver.close()
        _expect(stats.get("accepted") == 1, "accepted mismatch")
        _expect(stats.get("duplicate") == 1, "duplicate mismatch")
        _expect(stats.get("connections") == 2, "connections mismatch")
        _expect(stats.get("stop_reason") == "max_connections", "stop mismatch")
        message = processor.take_message()
        if message is None:
            raise SimulationFailure("no verified message")
        _expect(is_device_online(message), "not classified online")
        _expect(message.raw_body == raw, "raw body mismatch")
        _expect(
            message.body.get("simUnknownMarker") == "sim-unknown-field", "unknown marker dropped"
        )
        _expect(processor.take_message() is None, "inbox not drained")
        return {
            "profile": "overseas",
            "simulation": True,
            "accepted": stats["accepted"],
            "duplicate": stats["duplicate"],
            "online_events": 1,
        }
    finally:
        processor.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args:
        if args in (["-h"], ["--help"]):
            print(USAGE)
            return 0
        print(USAGE, file=sys.stderr)
        return 1
    try:
        summary = _run_simulation()
    except (
        OSError,
        ValueError,
        HTTPException,
        TimeoutError,
        FutureTimeoutError,
        SimulationFailure,
    ):
        print(_FAILURE, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(_FAILURE, file=sys.stderr)
        return 130
    print(json.dumps(summary, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
