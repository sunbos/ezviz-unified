"""Regression tests for examples.webhook_receiver HTTP boundary handling.

Covers the observed defects: ``port`` must be validated before any socket
is allocated; ``BoundedWebhookReceiver.run`` must not swallow an unknown
exception (``ValueError``/``OSError``/``http.client.HTTPException``/
``TimeoutError`` — stdlib ``handle_one_request`` internally catches
``TimeoutError``/socket timeouts, so a stored processor error must still
be re-raised rather than mistaken for a peer disconnect) from
``ReceiverProcessor.process`` into ``request_errors`` or a normal return;
an absurdly long ``Content-Length`` digit string must get a fixed ``413``
instead of crashing ``int()`` parsing; and a peer that connects then
closes without any HTTP bytes must count one ``request_errors`` rather
than a clean zero-error connection.
"""

import hashlib
import hmac
import http.client
import logging
import socket
import sys
import threading
import time

import pytest

from examples._webhook_receiver import ReceiverConfig, ReceiverProcessor
from examples.webhook_receiver import BoundedWebhookReceiver

SECRET = "synthetic-webhook-secret"
PAYLOAD = b'{"header":{"messageId":"synthetic","type":"synthetic"},"body":{}}'
_ORIGINAL_CONNECT = socket.socket.connect  # captured before any patching


def _processor(config):
    return ReceiverProcessor(
        config,
        webhook_secret=SECRET,
        monotonic=time.monotonic,
        wall_time_ms=lambda: int(time.time() * 1000),
    )


def _guard_connect(monkeypatch, allowed):
    """Permit socket connects only to the receiver's own 127.0.0.1:port."""

    def _connect(self, address):
        if tuple(address[:2]) != allowed:
            raise AssertionError(f"unexpected connect target {address!r}")
        return _ORIGINAL_CONNECT(self, address)

    monkeypatch.setattr(socket.socket, "connect", _connect)


def _spawn_run(receiver):
    outcome = {}

    def _target():
        try:
            outcome["result"] = receiver.run()
        except BaseException as exc:  # relayed to the main thread below
            outcome["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread, outcome


def _request(payload=PAYLOAD, *, declared_length=None, fresh=True):
    timestamp = str(int(time.time() * 1000)) if fresh else "1700000000000"
    signature = (
        hmac.new(
            SECRET.encode("utf-8"), payload + timestamp.encode("utf-8"), hashlib.sha1
        ).hexdigest()
        if fresh
        else "0" * 40
    )
    length = len(payload) if declared_length is None else declared_length
    head = (
        "POST /ezviz/messages HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"type: synthetic\r\nt: {timestamp}\r\nsignature: {signature}\r\n"
        f"Content-Length: {length}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + payload


def _read_until_close(client, limit=65536):
    received = b""
    try:
        while len(received) < limit:
            chunk = client.recv(4096)
            if not chunk:
                return received, True
            received += chunk
    except socket.timeout:
        pass
    except OSError:
        return received, True
    return received, False


@pytest.mark.parametrize("bad_port", [True, False, "0", -1, 65536])
def test_invalid_port_rejected_before_socket_allocation(monkeypatch, bad_port):
    def _forbidden_socket(*args, **kwargs):
        pytest.fail(f"socket allocated before port validation: port={bad_port!r}")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)
    with pytest.raises((ValueError, TypeError)):
        BoundedWebhookReceiver(_processor(ReceiverConfig()), port=bad_port)


@pytest.mark.parametrize(
    "exc_type",
    [ValueError, OSError, http.client.HTTPException, TimeoutError],
    ids=["ValueError", "OSError", "HTTPException", "TimeoutError"],
)
def test_processor_unknown_error_propagates_out_of_run(monkeypatch, capsys, caplog, exc_type):
    caplog.set_level(logging.DEBUG)
    processor = _processor(ReceiverConfig(max_connections=1, max_run_seconds=2.0))
    sentinel = exc_type("synthetic-error-sentinel")

    def _raising_process(*args, **kwargs):  # propagation only, no compat shim
        raise sentinel

    monkeypatch.setattr(processor, "process", _raising_process)
    receiver = BoundedWebhookReceiver(processor, port=0)
    allowed = ("127.0.0.1", receiver.port)
    _guard_connect(monkeypatch, allowed)
    thread, outcome = _spawn_run(receiver)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(1.5)
            client.connect(allowed)
            client.sendall(_request())
            _read_until_close(client)
        thread.join(3.0)
    finally:
        receiver.close()
        thread.join(2.0)
    assert not thread.is_alive(), "run() did not terminate within its bounds"
    assert outcome.get("error") is sentinel, outcome
    assert "result" not in outcome, "run() returned normally; error was swallowed"
    captured = capsys.readouterr()
    assert "synthetic-error-sentinel" not in captured.out
    assert "synthetic-error-sentinel" not in captured.err
    assert "synthetic-error-sentinel" not in caplog.text


def test_extreme_content_length_digits_get_fixed_413(monkeypatch):
    restore_digits = None
    if hasattr(sys, "set_int_max_str_digits"):
        restore_digits = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
    try:
        processor = _processor(ReceiverConfig(max_connections=1, max_run_seconds=2.0))
        process_calls = []
        monkeypatch.setattr(processor, "process", lambda *a, **k: process_calls.append(1))
        receiver = BoundedWebhookReceiver(processor, port=0)
        allowed = ("127.0.0.1", receiver.port)
        _guard_connect(monkeypatch, allowed)
        thread, outcome = _spawn_run(receiver)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(allowed)
                client.sendall(_request(b"", declared_length="9" * 5000, fresh=False))
                response, closed = _read_until_close(client)
            thread.join(3.0)
        finally:
            receiver.close()
            thread.join(2.0)
        assert not thread.is_alive(), "run() did not terminate within its bounds"
        status_line = response.split(b"\r\n", 1)[0]
        assert response.startswith(b"HTTP/") and b" 413" in status_line, response[:80]
        assert closed, "receiver must close the rejected connection"
        assert process_calls == [], "Content-Length must be rejected before auth"
        assert "error" not in outcome
        assert isinstance(outcome.get("result"), dict)
    finally:
        if restore_digits is not None:
            sys.set_int_max_str_digits(restore_digits)


def test_eof_without_http_bytes_counts_request_error(monkeypatch):
    processor = _processor(ReceiverConfig(max_connections=1, max_run_seconds=2.0))
    process_calls = []
    monkeypatch.setattr(processor, "process", lambda *a, **k: process_calls.append(1))
    receiver = BoundedWebhookReceiver(processor, port=0)
    allowed = ("127.0.0.1", receiver.port)
    _guard_connect(monkeypatch, allowed)
    thread, outcome = _spawn_run(receiver)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(1.5)
            client.connect(allowed)
            # peer closes immediately without sending a single HTTP byte
        thread.join(3.0)
    finally:
        receiver.close()
        thread.join(2.0)
    assert not thread.is_alive(), "run() did not terminate within its bounds"
    assert "error" not in outcome, outcome
    result = outcome.get("result")
    assert isinstance(result, dict), outcome
    assert result["connections"] == 1, result
    assert result["request_errors"] == 1, result
    assert result["accepted"] == 0, result
    assert process_calls == [], "missing HTTP bytes must not reach the processor"
