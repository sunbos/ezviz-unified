"""Loopback-only EZVIZ webhook receiver (HTTP boundary + CLI).

This module owns the network-facing HTTP layer and the CLI entry point.
Message authentication, deduplication and inbox storage live in
``examples._webhook_receiver``; the deadline/counting socket adapter
lives in ``examples._webhook_http_io``.

Guarantees kept here:

* Bind only IPv4 ``127.0.0.1`` on an explicit port (``0`` allowed so
  tests can pick a free port). No DNS lookups, no tunnelling, no
  background listeners or import-time side effects.
* One active connection, ``Connection: close`` always, exactly one
  request handled per connection; pipelined bytes are dropped.
* Absolute monotonic deadlines bound the whole run and each request;
  socket timeouts are re-applied by the I/O adapter on every recv/send.
* Fixed, non-reflecting error responses; no default ``send_error``
  echo, no access log, no traceback.
"""

from __future__ import annotations

import argparse
import http.client
import json
import select
import socket
import socketserver
import sys
import time
from getpass import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer

from examples._webhook_http_io import (
    DeadlineSocketIO,
    HeaderLimitExceeded,
    RequestDeadlineExceeded,
)
from examples._webhook_receiver import ReceiverConfig, ReceiverProcessor

__all__ = ["BoundedWebhookReceiver", "main"]

_ENDPOINT = "/ezviz/messages"
_POLL_SECONDS = 0.05

_ERROR_BODIES = {
    400: b'{"error":"bad_request"}',
    401: b'{"error":"unauthorized"}',
    404: b'{"error":"not_found"}',
    405: b'{"error":"method_not_allowed"}',
    413: b'{"error":"body_too_large"}',
    414: b'{"error":"bad_request"}',
    415: b'{"error":"unsupported_media_type"}',
    431: b'{"error":"header_limit_exceeded"}',
}
_DEFAULT_ERROR_BODY = b'{"error":"rejected"}'


class _WebhookHandler(BaseHTTPRequestHandler):
    """Single-request handler wired to the deadline socket adapter."""

    protocol_version = "HTTP/1.1"
    server_version = "EzvizWebhook/1.0"
    sys_version = ""

    def setup(self):
        self.connection = self.request
        config = self.server.processor.config
        io = DeadlineSocketIO(
            self.request,
            deadline=self.server.conn_deadline,
            max_header_bytes=config.max_header_bytes,
        )
        self.rfile = io
        self.wfile = io
        # Pessimistic default: failed until a final HTTP 200 response is
        # fully written inside the deadline budget. Empty request lines,
        # precheck rejections, deadlines and send failures all stay True.
        self.request_failed = True

    def handle(self):
        # Exactly one request per connection; anything pipelined after
        # it is discarded when the socket is closed by the receiver.
        self.close_connection = True
        try:
            self.handle_one_request()
        except Exception as exc:
            # Dispatch by origin first: the exact object stashed by
            # do_POST is a processor bug and must propagate untouched to
            # run(), never enter the wire/peer branches below.
            if exc is getattr(self.server, "processor_error", None):
                raise
            if isinstance(exc, HeaderLimitExceeded):
                self.send_error(431)
            elif isinstance(exc, (http.client.HTTPException, UnicodeDecodeError)):
                self.send_error(400)
            elif isinstance(exc, RequestDeadlineExceeded):
                # Deadline already exhausted: close without spending
                # budget on an extra response.
                self.request_failed = True
            elif isinstance(exc, (ConnectionError, OSError)):
                # Peer vanished or the receiver was closed underneath us.
                self.request_failed = True
            else:
                raise

    # No access log, no stderr tracebacks, no reflected reasons.
    def log_message(self, format, *args):
        pass

    log_request = log_message
    log_error = log_message

    def send_error(self, code, message=None, explain=None):
        del message, explain  # fixed bodies only; never echo input
        status = int(code)
        if getattr(self, "command", "") == "HEAD":
            body = b""
        else:
            body = _ERROR_BODIES.get(status, _DEFAULT_ERROR_BODY)
        self._send(status, body)

    def _send(self, status, body):
        reason = http.client.responses.get(status, "")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        try:
            self.wfile.write(head + body)
            self.wfile.flush()
        except (RequestDeadlineExceeded, OSError):
            # ACK for an already-accepted message may fail on the wire;
            # platform retry + dedup handles redelivery, so only count.
            self.request_failed = True
        else:
            # Final state: only a fully written/flushed HTTP 200 inside
            # the deadline budget clears the pessimistic setup default.
            # Any rejection — and any interim status such as 100 — stays
            # counted as a failed request.
            self.request_failed = status != 200

    def __getattr__(self, name):
        # Any verb without an explicit handler is rejected with 405
        # instead of the stdlib's 501 (HEAD also lands here, no body).
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def _method_not_allowed(self):
        self.send_error(405)

    def do_POST(self):
        config = self.server.processor.config
        if self.path != _ENDPOINT:
            return self.send_error(404)
        headers = self.headers
        if headers.get("Transfer-Encoding") is not None:
            return self.send_error(400)
        lengths = headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            return self.send_error(400)
        raw_length = lengths[0].strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            return self.send_error(400)
        # Compare digit strings, not ints: arbitrarily long literals must
        # never reach int() (capped by sys.set_int_max_str_digits).
        digits = raw_length.lstrip("0") or "0"
        limit = str(config.max_body_bytes)
        if len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit):
            return self.send_error(413)
        length = int(digits)
        ctype = headers.get("Content-Type")
        if ctype is None:
            return self.send_error(415)
        maintype = ctype.split(";", 1)[0].strip().lower()
        if maintype not in ("application/json", "text/plain"):
            return self.send_error(415)
        t_values = headers.get_all("t") or []
        sig_values = headers.get_all("Signature") or []
        if len(t_values) > 1 or len(sig_values) > 1:
            return self.send_error(400)
        if not t_values or not sig_values:
            return self.send_error(401)
        timestamp = t_values[0]
        signature = sig_values[0]
        if not 1 <= len(timestamp) <= 128:
            return self.send_error(400)
        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            return self.send_error(400)
        if time.monotonic() >= self.server.conn_deadline:
            # No budget left even to send an error; close silently.
            self.request_failed = True
            return
        try:
            response = self.server.processor.process(
                body,
                timestamp=timestamp,
                signature=signature,
                deadline=self.server.conn_deadline,
            )
        except Exception as exc:
            # Stash the original object; run() re-raises it untouched
            # after cleanup so a processor bug is never miscounted as
            # a peer disconnect.
            self.server.processor_error = exc
            raise
        # ACK body is written verbatim; never rebuilt here.
        self._send(int(response.status), bytes(response.body))


class _LoopbackHTTPServer(HTTPServer):
    """HTTPServer without the getfqdn() DNS lookup in server_bind."""

    address_family = socket.AF_INET
    request_queue_size = 4
    allow_reuse_address = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]

    def handle_error(self, request, client_address):
        # Called inside serve_forever's except block: re-raise the
        # original exception object instead of logging or swallowing.
        raise


class BoundedWebhookReceiver:
    """Foreground, single-connection webhook receiver on 127.0.0.1."""

    def __init__(self, processor: ReceiverProcessor, *, port: int = 8765):
        # Validate before any socket is allocated; bools are not ints
        # here and no coercion (int("0"), int(-1)) is applied.
        if isinstance(port, bool) or not isinstance(port, int):
            raise TypeError("port must be an int in range 0-65535")
        if not 0 <= port <= 65535:
            raise ValueError("port must be an int in range 0-65535")
        self._processor = processor
        self._closed = False
        self._conn = None
        server = _LoopbackHTTPServer(("127.0.0.1", port), _WebhookHandler)
        server.processor = processor
        server.conn_deadline = 0.0
        self._server = server

    @property
    def port(self) -> int:
        return self._server.server_port

    def close(self) -> None:
        """Release the listener and the active connection, if any.

        Idempotent and safe to call from another thread while ``run``
        is blocked. Never touches the processor or its inbox.
        """
        self._closed = True
        self._close_conn()
        try:
            self._server.server_close()
        except OSError:
            pass

    def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def run(self) -> dict[str, int | str]:
        config = self._processor.config
        run_deadline = time.monotonic() + float(config.max_run_seconds)
        connections = 0
        request_errors = 0
        stop_reason = "closed"
        pending_processor_error = None
        try:
            while True:
                # Connections bound wins over a trailing idempotent close
                # so a saturated run reports "max_connections".
                if connections >= config.max_connections:
                    stop_reason = "max_connections"
                    break
                if self._closed:
                    stop_reason = "closed"
                    break
                remaining = run_deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason = "deadline"
                    break
                try:
                    ready, _, _ = select.select(
                        [self._server.socket],
                        [],
                        [],
                        min(_POLL_SECONDS, remaining),
                    )
                    if not ready:
                        continue
                    conn, address = self._server.socket.accept()
                except (OSError, ValueError):
                    # Listener/selector I/O only: errors raised while
                    # handling a request live in the inner try below and
                    # are never mistaken for a dropped listener here.
                    if self._closed:
                        stop_reason = "closed"
                        break
                    raise
                connections += 1
                self._conn = conn
                try:
                    self._server.conn_deadline = min(
                        run_deadline,
                        time.monotonic() + float(config.max_request_seconds),
                    )
                    self._server.processor_error = None
                    handler = _WebhookHandler(conn, address, self._server)
                    # Postcondition: a stored processor_error is the
                    # authoritative signal of a processor failure even
                    # when handle_one_request returned normally (stdlib
                    # swallows socket timeouts, including TimeoutError,
                    # internally), so it is checked on both exit paths.
                    if self._server.processor_error is not None:
                        raise self._server.processor_error
                    if handler.request_failed:
                        request_errors += 1
                except Exception as exc:
                    if exc is getattr(self._server, "processor_error", None):
                        # Identity first: a processor exception escapes
                        # untouched after cleanup even when its type also
                        # matches an I/O branch below (OSError, HTTPException).
                        pending_processor_error = exc
                    elif isinstance(exc, (RequestDeadlineExceeded, HeaderLimitExceeded)):
                        request_errors += 1
                    elif isinstance(exc, OSError):
                        # I/O-originated failures only. A close under an
                        # in-flight handler ends the run cleanly; anything
                        # else is a bounded peer/request failure.
                        if self._closed:
                            self._close_conn()
                            stop_reason = "closed"
                            break
                        request_errors += 1
                    else:
                        # No proven peer origin (e.g. a stray ValueError):
                        # count nothing and let it propagate.
                        raise
                self._close_conn()
                if pending_processor_error is not None:
                    break
        except KeyboardInterrupt:
            stop_reason = "interrupted"
        finally:
            self._close_conn()
            try:
                self._server.server_close()
            except OSError:
                pass
        if pending_processor_error is not None:
            raise pending_processor_error
        # An earlier natural stop reason wins over a trailing idempotent
        # close; only fall back to "closed" when nothing else stopped us.
        return self._receipt(connections, request_errors, stop_reason)

    def _receipt(self, connections, request_errors, stop_reason):
        receipt = dict(self._processor.public_summary())
        receipt.setdefault("schema_version", 1)
        receipt["connections"] = connections
        receipt["request_errors"] = request_errors
        receipt["stop_reason"] = stop_reason
        return receipt


# --------------------------------------------------------------------------
# CLI


def _int_option(text):
    try:
        return int(text, 10)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be an integer")


def _positive_int(text):
    value = _int_option(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _port_number(text):
    value = _int_option(text)
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("must be in range 0-65535")
    return value


def _positive_float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a number")
    if not value > 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # ``message`` may embed user-supplied values; never echo it.
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: invalid arguments\n")


def _build_parser() -> argparse.ArgumentParser:
    # CLI defaults mirror ReceiverConfig() so they cannot drift apart.
    defaults = ReceiverConfig()
    parser = _SafeArgumentParser(
        prog="ezviz-webhook-receiver",
        description="Bounded loopback EZVIZ webhook receiver.",
    )
    parser.add_argument(
        "--profile",
        choices=("domestic", "overseas"),
        default=defaults.profile,
        help="receiver profile",
    )
    parser.add_argument(
        "--port",
        type=_port_number,
        default=8765,
        metavar="N",
        help="loopback port to bind (0 picks a free port)",
    )
    parser.add_argument(
        "--max-seconds",
        type=_positive_float,
        default=defaults.max_run_seconds,
        metavar="SEC",
        help="overall run deadline in seconds",
    )
    parser.add_argument(
        "--max-connections",
        type=_positive_int,
        default=defaults.max_connections,
        metavar="N",
        help="stop after N accepted connections",
    )
    parser.add_argument(
        "--max-body-bytes",
        type=_positive_int,
        default=defaults.max_body_bytes,
        metavar="N",
        help="maximum accepted body size",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    # Argument and config validation finish before any secret prompt;
    # an unknown profile or bad value must never reach getpass.
    config_kwargs = {
        "profile": args.profile,
        "max_body_bytes": args.max_body_bytes,
        "max_run_seconds": args.max_seconds,
        "max_connections": args.max_connections,
    }
    if args.profile == "overseas":
        config_kwargs["timestamp_tolerance_ms"] = None
    try:
        config = ReceiverConfig(**config_kwargs)
    except Exception:
        sys.stderr.write("error: invalid receiver configuration\n")
        return 1

    if not sys.stdin.isatty():
        sys.stderr.write("error: secret input requires an interactive terminal\n")
        return 1
    try:
        prompt = getpass if callable(getpass) else getpass.getpass
        secret = prompt("Webhook secret: ")
    except (KeyboardInterrupt, EOFError):
        return 130

    processor = None
    receiver = None
    receipt = None
    exit_code = None
    cleanup_failed = False
    try:
        processor = ReceiverProcessor(config, webhook_secret=secret)
        receiver = BoundedWebhookReceiver(processor, port=args.port)
        receipt = receiver.run()
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        sys.stderr.write("error: receiver failed\n")
        exit_code = 1
    finally:
        # Cleanup is always attempted for both objects; a cleanup
        # failure turns a would-be success into a fixed error result.
        if receiver is not None:
            try:
                receiver.close()
            except Exception:
                cleanup_failed = True
        if processor is not None:
            try:
                processor.close()
            except Exception:
                cleanup_failed = True

    if cleanup_failed:
        sys.stderr.write("error: receiver cleanup failed\n")
        if exit_code is None:
            return 1
    if exit_code is not None:
        return exit_code

    print(json.dumps(receipt, sort_keys=True))
    if receipt.get("stop_reason") == "interrupted":
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
