"""HTTP-layer tests for the bounded webhook receiver example.

Only the frozen public surface is exercised: ``ReceiverConfig`` /
``ReceiverProcessor`` / ``BoundedWebhookReceiver``. Requests travel over a
real 127.0.0.1 TCP connection; signatures are derived independently with
stdlib hmac so nothing here shares code with the receiver under test.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import socket
import threading
import time

import pytest

from examples._webhook_receiver import ReceiverConfig, ReceiverProcessor
from examples.webhook_receiver import BoundedWebhookReceiver

SECRET = "test-secret-not-real"

# Captured at import time, before conftest's deny-network fixture replaces
# socket.socket.connect. Helpers below may only dial a port that _running()
# has registered for a live receiver on 127.0.0.1; every other destination
# keeps raising.
_ORIGINAL_CONNECT = socket.socket.connect
_ALLOWED_PORTS = set()


def _dial(host: str, port: int) -> socket.socket:
    if host != "127.0.0.1" or port not in _ALLOWED_PORTS:
        raise RuntimeError("offline tests may only dial registered receiver ports")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    try:
        _ORIGINAL_CONNECT(sock, (host, port))
    except Exception:
        sock.close()
        raise
    return sock


def _connector(port: int):
    """Dial only the given receiver-bound loopback port, retrying briefly
    while the accept loop starts."""

    def connect() -> socket.socket:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                return _dial("127.0.0.1", port)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)

    return connect


@contextlib.contextmanager
def _running(receiver: BoundedWebhookReceiver):
    """Run the receiver on an explicit thread; always close, join with a
    bound, and re-raise any unknown server exception in this thread."""
    results, errors = [], []

    def target() -> None:
        try:
            results.append(receiver.run())
        except Exception as exc:  # surfaced to the test thread below
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    _ALLOWED_PORTS.add(receiver.port)
    try:
        yield results
    finally:
        receiver.close()
        thread.join(timeout=4.0)
        _ALLOWED_PORTS.discard(receiver.port)
        assert not thread.is_alive(), "run() did not return after close()"
        if errors:
            raise errors[0]


def _config(**overrides) -> ReceiverConfig:
    base = dict(
        max_connections=8,
        max_run_seconds=5,
        max_request_seconds=1.0,
        max_body_bytes=128,
        max_header_bytes=512,
    )
    base.update(overrides)
    return ReceiverConfig(**base)


def _msg(message_id: str, msg_type: str = "device_alarm", body=None) -> bytes:
    return json.dumps(
        {
            "header": {"messageId": message_id, "type": msg_type},
            "body": {} if body is None else body,
        },
        separators=(",", ":"),
    ).encode()


def _sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha1).hexdigest()


def _signed_headers(body: bytes, ctype: str = "application/json", secret: str = SECRET):
    timestamp = str(int(time.time() * 1000))
    return [
        (b"t", timestamp.encode()),
        (b"Signature", _sign(body, timestamp, secret).encode()),
        (b"Content-Type", ctype.encode()),
        (b"Content-Length", str(len(body)).encode()),
    ]


def _request_bytes(path=b"/ezviz/messages", *, method=b"POST", body=b"", headers=()):
    lines = [method + b" " + path + b" HTTP/1.1", b"Host: 127.0.0.1"]
    lines += [name + b": " + value for name, value in headers]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


def _exchange(connect, payload: bytes, *, half_close: bool = False) -> bytes:
    sock = connect()
    try:
        try:
            sock.sendall(payload)
        except socket.timeout:
            pytest.fail("timed out sending request payload")
        except OSError:
            pass  # early peer reset/close; the recv loop reports the outcome
        if half_close:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                pytest.fail(
                    "timed out waiting for the server to finish the "
                    "response and close the connection"
                )
            except OSError:
                break  # known connection resets/EOF end the response
            if not chunk:
                break
            data += chunk
        return data
    finally:
        sock.close()


def _parse(raw: bytes, *, head: bool = False):
    """Validate one complete HTTP response; return (status, headers, body).

    Every response must carry ``Connection: close``. A non-HEAD response
    must also carry a Content-Length matching the exact body byte count; a
    HEAD response must carry no body bytes at all (a Content-Length stating
    the hypothetical GET body length is allowed).
    """
    assert raw.startswith(b"HTTP/"), f"expected HTTP response, got {raw[:48]!r}"
    head_block, sep, rest = raw.partition(b"\r\n\r\n")
    assert sep, f"unterminated response head: {raw[:48]!r}"
    lines = head_block.split(b"\r\n")
    status = int(lines[0].split(b" ", 2)[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers[name.strip().lower()] = value.strip()
    assert headers.get(b"connection", b"").lower() == b"close", (
        f"missing Connection: close in {head_block!r}"
    )
    if head:
        assert rest == b"", "HEAD response must not carry body bytes"
        return status, headers, b""
    declared = headers.get(b"content-length")
    assert declared is not None, f"missing Content-Length in {head_block!r}"
    assert len(rest) == int(declared), f"body byte count {len(rest)} != Content-Length {declared!r}"
    return status, headers, rest


def _status(raw: bytes) -> int:
    return _parse(raw)[0]


def _body_of(raw: bytes) -> bytes:
    return _parse(raw)[2]


def _post(connect, body: bytes, *, ctype: str = "application/json", secret: str = SECRET):
    raw = _exchange(
        connect, _request_bytes(body=body, headers=_signed_headers(body, ctype, secret))
    )
    return _status(raw), _body_of(raw)


def test_loopback_guard_and_conftest_deny_still_apply():
    with pytest.raises(RuntimeError):
        _dial("192.0.2.1", 8080)
    sock = socket.socket()
    try:
        with pytest.raises(RuntimeError):
            sock.connect(("192.0.2.1", 8080))
    finally:
        sock.close()
    # Unregistered loopback ports are refused by _dial without touching
    # the network; registered ports are covered by the receiver tests.
    with pytest.raises(RuntimeError):
        _dial("127.0.0.1", 9)


def test_tcp_chain_end_to_end():
    proc = ReceiverProcessor(_config(max_connections=6), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        connect = _connector(receiver.port)

        body = _msg("m-accept-1")
        status, ack = _post(connect, body)
        assert status == 200 and json.loads(ack) == {"messageId": "m-accept-1"}

        # Same id + same raw bytes: acknowledged again, not re-enqueued.
        status, ack = _post(connect, body)
        assert status == 200 and json.loads(ack) == {"messageId": "m-accept-1"}

        # Same message_id + semantically equal body, different raw bytes:
        # id conflict. Reserializing with whitespace stays <= max_body 128.
        conflict = json.dumps(json.loads(body), indent=1).encode()
        assert conflict != body and len(conflict) <= 128
        status, _ = _post(connect, conflict)
        assert status == 409

        # Bad signature is rejected before parsing.
        bad_body = _msg("m-badsig")
        t = str(int(time.time() * 1000)).encode()
        raw = _exchange(
            connect,
            _request_bytes(
                body=bad_body,
                headers=[
                    (b"t", t),
                    (b"Signature", b"0" * 40),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(bad_body)).encode()),
                ],
            ),
        )
        assert _status(raw) == 401

        status, _ = _post(connect, b"{not json")
        assert status == 400

        # Unknown message type is still accepted once verified.
        status, ack = _post(connect, _msg("m-unknown", "totally_unknown_type"))
        assert status == 200 and json.loads(ack) == {"messageId": "m-unknown"}

    assert results
    receipt = results[0]
    assert set(receipt) == {
        "schema_version",
        "connections",
        "request_errors",
        "stop_reason",
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
    assert receipt["schema_version"] == 1
    assert receipt["connections"] == 6
    assert receipt["stop_reason"] == "max_connections"
    assert receipt["accepted"] == 2
    assert receipt["duplicate"] == 1
    assert receipt["id_conflict"] == 1
    assert receipt["authentication_failed"] == 1
    assert receipt["invalid_message"] == 1
    # The 409 + 401 + 400 responses are exactly three request errors; the
    # re-ACKed duplicate is not an error and must not be counted as one.
    assert receipt["request_errors"] == 3

    # Only genuinely accepted items may sit in the inbox, and each parsed
    # message must keep the envelope's messageId, type, and raw body JSON.
    seen = []
    while (msg := proc.take_message()) is not None:
        seen.append(msg)
    assert [msg.message_id for msg in seen] == ["m-accept-1", "m-unknown"]
    assert seen[0].message_type == "device_alarm"
    assert seen[0].body == {}
    assert seen[1].message_type == "totally_unknown_type"
    assert seen[1].body == {}


def test_http_request_rejections():
    proc = ReceiverProcessor(_config(max_connections=8), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        connect = _connector(receiver.port)

        raw = _exchange(
            connect, _request_bytes(method=b"HEAD", headers=[(b"Content-Length", b"0")])
        )
        status, _, head_body = _parse(raw, head=True)
        assert status == 405
        assert head_body == b""  # error body must not be sent to HEAD

        raw = _exchange(
            connect, _request_bytes(path=b"/elsewhere", headers=[(b"Content-Length", b"0")])
        )
        assert _status(raw) == 404
        assert b"/elsewhere" not in raw

        status, _ = _post(connect, _msg("m-ct"), ctype="application/xml")
        assert status == 415

        raw = _exchange(
            connect,
            _request_bytes(
                body=b"x", headers=[(b"Content-Length", b"1"), (b"Transfer-Encoding", b"chunked")]
            ),
        )
        assert _status(raw) == 400

        raw = _exchange(
            connect, _request_bytes(headers=[(b"Content-Length", b"0"), (b"Content-Length", b"0")])
        )
        assert _status(raw) == 400

        body = _msg("m-not")
        t = str(int(time.time() * 1000))
        raw = _exchange(
            connect,
            _request_bytes(
                body=body,
                headers=[
                    (b"Signature", _sign(body, t).encode()),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(body)).encode()),
                ],
            ),
        )
        assert _status(raw) == 401

        raw = _exchange(
            connect,
            _request_bytes(
                body=body,
                headers=[
                    (b"t", t.encode()),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(body)).encode()),
                ],
            ),
        )
        assert _status(raw) == 401

        big = b"x" * 200
        raw = _exchange(
            connect,
            _request_bytes(
                body=big,
                headers=[
                    (b"t", t.encode()),
                    (b"Signature", _sign(big, t).encode()),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(big)).encode()),
                ],
            ),
        )
        assert _status(raw) == 413

    assert results
    assert results[0]["connections"] == 8
    assert results[0]["stop_reason"] == "max_connections"


def test_http_duplicate_signature_header_rejected():
    # Kept in its own run so test_http_request_rejections stays at the
    # frozen <= 8 connections per run.
    proc = ReceiverProcessor(_config(max_connections=1), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        connect = _connector(receiver.port)
        body = _msg("m-dupsig")
        t = str(int(time.time() * 1000))
        sig = _sign(body, t).encode()
        raw = _exchange(
            connect,
            _request_bytes(
                body=body,
                headers=[
                    (b"t", t.encode()),
                    (b"Signature", sig),
                    (b"Signature", sig),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(body)).encode()),
                ],
            ),
        )
        assert _status(raw) == 400

    assert results
    assert results[0]["connections"] == 1
    assert results[0]["stop_reason"] == "max_connections"


def test_body_header_length_and_pipelining_rules():
    proc = ReceiverProcessor(_config(max_connections=8), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        connect = _connector(receiver.port)

        # Truncated body: fewer bytes than Content-Length, then EOF -> 400.
        body = _msg("m-trunc")
        t = str(int(time.time() * 1000))
        payload = (
            _request_bytes(
                headers=[
                    (b"t", t.encode()),
                    (b"Signature", _sign(body, t).encode()),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(body)).encode()),
                ]
            )
            + body[:10]
        )
        assert _status(_exchange(connect, payload, half_close=True)) == 400

        # Header block exceeding max_header_bytes -> 431.
        raw = _exchange(
            connect,
            b"POST /ezviz/messages HTTP/1.1\r\nHost: x\r\n" + b"X-Pad: " + b"A" * 700 + b"\r\n\r\n",
            half_close=True,
        )
        assert _status(raw) == 431

        for bad_cl in (b"abc", b"-5"):
            raw = _exchange(connect, _request_bytes(headers=[(b"Content-Length", bad_cl)]))
            assert _status(raw) == 400

        # Duplicate protocol headers are rejected.
        raw = _exchange(
            connect,
            _request_bytes(
                body=body,
                headers=[
                    (b"t", t.encode()),
                    (b"t", t.encode()),
                    (b"Signature", _sign(body, t).encode()),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(body)).encode()),
                ],
            ),
        )
        assert _status(raw) == 400

        # Both allowed content types, including charset parameters.
        status, ack = _post(connect, _msg("m-txt"), ctype="text/plain; charset=utf-8")
        assert status == 200 and json.loads(ack) == {"messageId": "m-txt"}
        status, ack = _post(connect, _msg("m-jsn"), ctype="application/json; charset=utf-8")
        assert status == 200 and json.loads(ack) == {"messageId": "m-jsn"}

        # A pipelined second request on one connection is never processed.
        first = _request_bytes(body=_msg("m-pipe"), headers=_signed_headers(_msg("m-pipe")))
        second = _request_bytes(body=_msg("m-pipe2"), headers=_signed_headers(_msg("m-pipe2")))
        raw = _exchange(connect, first + second)
        assert raw.count(b"HTTP/") == 1
        assert _status(raw) == 200

    assert results
    assert results[0]["connections"] == 8
    seen = []
    while (msg := proc.take_message()) is not None:
        seen.append(msg)
    assert len(seen) == 3


def test_unparseable_content_type_rejected_415():
    # A well-formed, correctly signed envelope whose Content-Type is the
    # bare token ``garbage`` carries no maintype/subtype at all. A parser
    # that defaults malformed values to text/plain (the behaviour of
    # email.Message.get_content_type) would wrongly admit it under the
    # frozen {application/json, text/plain} whitelist; the receiver must
    # instead answer 415 and enqueue nothing.
    proc = ReceiverProcessor(_config(max_connections=1), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        status, _ = _post(_connector(receiver.port), _msg("m-ctype-garbage"), ctype="garbage")
        assert status == 415

    assert results
    receipt = results[0]
    assert receipt["connections"] == 1
    assert receipt["stop_reason"] == "max_connections"
    assert receipt["accepted"] == 0
    assert receipt["request_errors"] == 1
    assert proc.take_message() is None


def test_preread_body_does_not_count_against_header_cap():
    # An envelope of ~93 bytes plus pad key yields a legal body > 512 (the
    # header cap) yet < 1024 (this config's body cap). It must be accepted,
    # proving bytes read ahead of the header terminator are not charged to
    # the header budget.
    body = _msg("m-preread", body={"pad": "B" * 600})
    assert 512 < len(body) < 1024
    proc = ReceiverProcessor(_config(max_connections=1, max_body_bytes=1024), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        status, ack = _post(_connector(receiver.port), body)
        assert status == 200
        assert json.loads(ack) == {"messageId": "m-preread"}

    assert results
    assert results[0]["accepted"] == 1
    assert results[0]["request_errors"] == 0
    assert results[0]["connections"] == 1
    msg = proc.take_message()
    assert msg is not None
    assert msg.message_id == "m-preread"
    assert msg.body == {"pad": "B" * 600}
    assert proc.take_message() is None


def test_slow_headers_bounded_by_absolute_deadline():
    proc = ReceiverProcessor(
        _config(max_connections=1, max_request_seconds=0.4), webhook_secret=SECRET
    )
    receiver = BoundedWebhookReceiver(proc, port=0)
    with _running(receiver) as results:
        sock = _connector(receiver.port)()
        # Drip thread: one header byte every 0.05 s for at most 2.0 s. Every
        # gap stays well under the 0.4 s per-request budget, so only an
        # implementation enforcing an absolute deadline closes the socket.
        start = time.monotonic()
        state = {"closed_at": None}
        stop = threading.Event()

        def drip() -> None:
            try:
                sock.sendall(b"POST /ezviz/messages HTTP/1.1\r\n")
                while not stop.is_set() and time.monotonic() - start < 2.0:
                    sock.sendall(b"A")
                    time.sleep(0.05)
            except OSError:
                # Server closed the connection mid-drip (deadline hit).
                state["closed_at"] = time.monotonic() - start

        thread = threading.Thread(target=drip, daemon=True)
        thread.start()
        try:
            # Main thread: poll recv in short intervals, only observing; a
            # per-read-timeout implementation would starve the drip and be
            # caught by the deadline assertions below.
            while thread.is_alive():
                try:
                    sock.settimeout(0.05)
                    data = sock.recv(512)
                except (socket.timeout, BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    state["closed_at"] = time.monotonic() - start
                    break
                if data == b"":
                    state["closed_at"] = time.monotonic() - start
                    break
        finally:
            stop.set()
            thread.join(timeout=3.0)
            sock.close()
        assert not thread.is_alive(), "drip thread did not finish in bound"
        closed_at = state["closed_at"]
        assert closed_at is not None, "connection outlived its absolute deadline"
        assert 0.2 <= closed_at <= 1.0

    assert results
    assert results[0]["connections"] == 1
    assert results[0]["stop_reason"] == "max_connections"


def test_close_stops_run_releases_port_and_keeps_inbox():
    proc = ReceiverProcessor(_config(), webhook_secret=SECRET)
    receiver = BoundedWebhookReceiver(proc, port=0)
    port = receiver.port
    assert port > 0
    with _running(receiver) as results:
        status, ack = _post(_connector(port), _msg("m-close"))
        assert status == 200 and json.loads(ack) == {"messageId": "m-close"}
        receiver.close()

    assert results
    assert results[0]["stop_reason"] == "closed"
    # The port is provably released: rebinding it succeeds (SO_REUSEADDR
    # copes with TIME_WAIT) without needing a whitelisted connect.
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
    finally:
        probe.close()

    # Network shutdown must not discard already-accepted messages.
    assert proc.take_message() is not None
    assert proc.take_message() is None


def test_public_output_carries_no_sensitive_data(capsys, caplog):
    secret = "shh-SECRET-SENTINEL"
    # The sentinel body stays within the explicit body cap; the header cap
    # remains at the default 512.
    proc = ReceiverProcessor(_config(max_connections=3, max_body_bytes=512), webhook_secret=secret)
    receiver = BoundedWebhookReceiver(proc, port=0)
    with caplog.at_level(logging.DEBUG):
        with _running(receiver) as results:
            connect = _connector(receiver.port)
            # Exact messageId on the wire is a required protocol ACK,
            # asserted below; the signature covers the raw body containing
            # the body sentinel, derived with this test's own secret.
            body = _msg("MSG-SENTINEL-01", body={"pad": "SENTINEL-BODY"})
            assert len(body) <= 512
            status, ack = _post(connect, body, secret=secret)
            assert status == 200
            assert json.loads(ack) == {"messageId": "MSG-SENTINEL-01"}

            raw = _exchange(
                connect,
                _request_bytes(
                    path=b"/SENTINEL-PATH",
                    headers=[(b"X-Marker", b"SENTINEL-HEADER"), (b"Content-Length", b"0")],
                ),
            )
            assert _status(raw) == 404
            assert b"SENTINEL-PATH" not in raw
            assert b"SENTINEL-HEADER" not in raw

            raw = _exchange(
                connect,
                _request_bytes(
                    body=body,
                    headers=[
                        (b"t", str(int(time.time() * 1000)).encode()),
                        (b"Signature", b"0" * 40),
                        (b"Content-Type", b"application/json"),
                        (b"Content-Length", str(len(body)).encode()),
                    ],
                ),
            )
            assert _status(raw) == 401
            assert b"SENTINEL-BODY" not in raw
            assert b"MSG-SENTINEL-01" not in raw

    assert results
    public = repr(results[0]) + repr(proc.public_summary()) + repr(proc) + repr(receiver)
    out, err = capsys.readouterr()
    logs = "\n".join(record.getMessage() for record in caplog.records)
    body_digest = hashlib.sha256(body).hexdigest()
    for token in (
        "SENTINEL-PATH",
        "SENTINEL-HEADER",
        "SENTINEL-BODY",
        "MSG-SENTINEL-01",
        secret,
        body_digest,
    ):
        assert token not in public
        assert token not in out
        assert token not in err
        assert token not in logs
