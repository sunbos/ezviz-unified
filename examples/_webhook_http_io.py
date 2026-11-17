"""Internal deadline-aware socket I/O adapter for the webhook receiver.

This module is the thin bridging layer between a connected socket and the
file-like objects (``rfile`` / ``wfile``) expected by
:class:`http.server.BaseHTTPRequestHandler`.  It performs no buffering,
no HTTP parsing and no pre-reading: every ``recv``/``send`` moves exactly
the bytes the caller asked for.  The owning receiver keeps responsibility
for the socket's lifecycle; :meth:`DeadlineSocketIO.close` only detaches
this adapter.
"""

from __future__ import annotations

import math
import socket
import time
from typing import Callable

__all__ = [
    "DeadlineSocketIO",
    "HeaderLimitExceeded",
    "RequestDeadlineExceeded",
]

_MAX_HEADER_BYTES = 65536


class RequestDeadlineExceeded(Exception):
    """The absolute request deadline was reached during socket I/O.

    Deliberately does not inherit from ``TimeoutError`` so the stdlib
    HTTP handler does not silently swallow it as a socket timeout.
    """


class HeaderLimitExceeded(Exception):
    """Cumulative header bytes read via ``readline`` exceeded the limit."""


class DeadlineSocketIO:
    """File-like adapter over ``sock`` enforcing an absolute deadline.

    ``readline`` reads one byte at a time so that no bytes belonging to a
    following request are consumed, and accumulates the header byte
    budget across calls.  ``read`` fetches exactly ``size`` bytes (fewer
    on EOF, leaving the status decision to the HTTP layer) and is not
    counted against the header budget.  ``write`` loops over partial
    sends and returns the total number of bytes sent.  The socket
    timeout is re-armed with the remaining budget before every
    ``recv``/``send`` rather than being reset to the full budget.
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        deadline: float,
        max_header_bytes: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise ValueError("deadline must be a finite number")
        if (
            isinstance(max_header_bytes, bool)
            or not isinstance(max_header_bytes, int)
            or not 0 < max_header_bytes <= _MAX_HEADER_BYTES
        ):
            raise ValueError("max_header_bytes must be a positive integer <= 65536")
        if not callable(monotonic):
            raise ValueError("monotonic must be callable")
        self._sock = sock
        self._deadline = float(deadline)
        self._max_header_bytes = max_header_bytes
        self._monotonic = monotonic
        self._header_bytes = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this adapter has been closed."""
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file")

    def _remaining(self) -> float:
        """Seconds left until the absolute deadline.

        Raises:
            RequestDeadlineExceeded: if the deadline has been reached.
        """
        remaining = self._deadline - self._monotonic()
        if remaining <= 0.0:
            raise RequestDeadlineExceeded("request deadline exceeded")
        return remaining

    def _recv(self, size: int) -> bytes:
        """Receive up to ``size`` bytes within the remaining budget."""
        self._sock.settimeout(self._remaining())
        try:
            chunk = self._sock.recv(size)
        except socket.timeout as exc:
            raise RequestDeadlineExceeded("request deadline exceeded") from exc
        self._remaining()
        return chunk

    def readline(self, size: int = -1) -> bytes:
        """Read one header line byte-by-byte.

        Every byte consumed is counted against the cumulative header
        budget.  ``size`` caps the returned line length; the deadline and
        header limit still apply.
        """
        self._ensure_open()
        if size == 0:
            return b""
        cap = size if size and size > 0 else None
        line = bytearray()
        while cap is None or len(line) < cap:
            chunk = self._recv(1)
            if not chunk:
                break
            line += chunk
            self._header_bytes += 1
            if self._header_bytes > self._max_header_bytes:
                raise HeaderLimitExceeded("header limit exceeded")
            if chunk == b"\n":
                break
        return bytes(line)

    def read(self, size: int) -> bytes:
        """Read exactly ``size`` body bytes, or fewer at EOF.

        Returns a short ``bytes`` object on EOF so the HTTP layer can
        decide the appropriate error; negative sizes are rejected so no
        unbounded read-all can happen.
        """
        self._ensure_open()
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("read size must be a non-negative integer")
        if size == 0:
            return b""
        parts = bytearray()
        remaining = size
        while remaining > 0:
            chunk = self._recv(remaining)
            if not chunk:
                break
            parts += chunk
            remaining -= len(chunk)
        return bytes(parts)

    def write(self, data: bytes) -> int:
        """Send all of ``data``, looping over partial sends.

        Returns the total number of bytes sent.  A send that returns 0
        or a broken connection is an error; unknown exceptions propagate.
        """
        self._ensure_open()
        view = memoryview(data)
        total = 0
        while total < len(view):
            self._sock.settimeout(self._remaining())
            try:
                sent = self._sock.send(view[total:])
            except socket.timeout as exc:
                raise RequestDeadlineExceeded("request deadline exceeded") from exc
            self._remaining()
            if sent <= 0:
                raise ConnectionError("socket connection broken")
            total += sent
        return total

    def flush(self) -> None:
        """No-op: this adapter performs no buffering."""

    def close(self) -> None:
        """Detach this adapter only; the socket belongs to the receiver."""
        self._closed = True
