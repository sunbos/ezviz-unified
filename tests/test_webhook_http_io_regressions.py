"""Regression: DeadlineSocketIO.read must sample the clock once per budget check.

Observed race: _remaining() called monotonic() twice — once for the >0 guard,
again for the return value — so a tick between calls produced a negative
remaining and settimeout() raised ValueError instead of proceeding to the
post-recv deadline check that raises RequestDeadlineExceeded.
"""

import pytest

from examples._webhook_http_io import DeadlineSocketIO, RequestDeadlineExceeded


class _FakeSocket:
    """Records timeouts; rejects negatives like a real socket; no I/O."""

    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        if value < 0:
            raise ValueError("negative timeout")
        self.timeouts.append(value)

    def recv(self, n):
        return b"x"[:n]


def _clock():
    ticks = iter([0.9, 1.1])
    return lambda: next(ticks, 1.1)


def test_read_computes_remaining_from_single_clock_sample():
    sock = _FakeSocket()
    io = DeadlineSocketIO(
        sock,
        deadline=1.0,
        max_header_bytes=512,
        monotonic=_clock(),
    )

    with pytest.raises(RequestDeadlineExceeded):
        io.read(1)

    assert sock.timeouts, "settimeout should have been called once"
    assert all(timeout > 0 for timeout in sock.timeouts)
