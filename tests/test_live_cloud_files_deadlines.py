"""Deadline, raw-parser and CLI tests for the live cloud-files probe.

The clock and socket fakes keep every fake-clock case fully synthetic: no
real port is bound, no thread or ``sleep`` is used, and no credential or
network material exists there. The single loopback acceptance test binds
a real ``127.0.0.1:0`` server, dials only that registered port with the
pre-deny ``connect``, and replaces ``probe.verify`` with a synthetic
allowlisted-report callback — the real cloud and real credentials are
never reached.
"""

import contextlib
import html
import io
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from tools.live import verify_cloud_files as probe

_SENTINEL = b"ZXSENTINEL9Q"
_TIMEOUT_REPORT_BODY = b"probe-report-sink"

# Captured before conftest's deny-network fixture replaces
# ``socket.socket.connect``: the loopback helper may only dial the one
# port registered by ``_serving``; every other destination keeps raising.
_ORIGINAL_CONNECT = socket.socket.connect
_ALLOWED_PORTS: set[int] = set()

# One fully-specified whitelisted callback report: counts, fixed enums and
# type buckets only — no entity values, identifiers, URLs, or secrets.
_CALLBACK_REPORT = {
    "schema_version": 1,
    "scope": "bounded_domestic_vod_resource_discovery",
    "region": "domestic",
    "recorded_at_utc": "2026-11-18T00:00:00+00:00",
    "window_start": "2026-10-19 08:00:00",
    "window_end": "2026-11-18 08:00:00",
    "window_basis": "createTime",
    "window_timezone_assumption": "Asia/Shanghai_wall_time",
    "max_http_requests": 7,
    "http_requests": 3,
    "logical_calls": 2,
    "route_counts": {"authentication": 1, "spaces": 1, "vod": 1},
    "stage": "complete",
    "outcome": "success",
    "reason": "ok",
    "space_page_count": 1,
    "space_has_next": False,
    "ordinary_vod_candidate_found": True,
    "vod_data_type": "array",
    "vod_item_count": 1,
    "error_family": "none",
    "cloud_recording_v1_discovery": "not_available_in_current_sdk",
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSocket:
    """recv/settimeout stand-in; each recv may advance the fake clock.

    ``stages`` optionally splits the payload into a fast prefix that costs
    no clock time and a drip remainder fed ``drip_chunk`` bytes per recv at
    ``step`` each; without ``stages`` the whole payload is that drip
    remainder (``step`` may be 0 for an instant feed). The optional
    ``after_deadline`` bytes sit behind a clock jump to ``jump_at`` so
    anything read only after the cutoff is provably never consumed.
    """

    def __init__(
        self,
        payload: bytes,
        clock: FakeClock,
        step: float,
        chunk: int,
        *,
        stages: tuple[int, int] | None = None,
        drip_chunk: int | None = None,
        after_deadline: bytes = b"",
        jump_at: float | None = None,
    ) -> None:
        if stages is None:
            # Unstaged: every recv drips ``chunk`` payload bytes at ``step``
            # each (step=0 stays instant); nothing is clock-free.
            self._fast = bytearray()
            self._drip = bytearray(payload)
            self.drip_chunk = chunk
        else:
            cut, _second = stages
            self._fast = bytearray(payload[:cut])
            self._drip = bytearray(payload[cut:])
            self.drip_chunk = drip_chunk or chunk
        self._after = bytearray(after_deadline)
        self._jump_at = jump_at
        self._jumped = False
        self.clock = clock
        self.step = step
        self.chunk = chunk
        self.timeouts: list[float] = []

    @property
    def payload(self) -> bytearray:
        return self._fast + self._drip + self._after

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def recv(self, _size: int) -> bytes:
        if self._fast:
            out = bytes(self._fast[: self.chunk])
            del self._fast[: self.chunk]
            return out
        if self._drip:
            self.clock.advance(self.step)
            out = bytes(self._drip[: self.drip_chunk])
            del self._drip[: self.drip_chunk]
            return out
        if self._after:
            if self._jump_at is not None and not self._jumped:
                self._jumped = True
                self.clock.now = self._jump_at
            out = bytes(self._after[: self.chunk])
            del self._after[: self.chunk]
            return out
        return b""


class FakeHTTPServer:
    """Never binds; monkeypatched in for the probe's ``HTTPServer`` name."""

    clock = None
    tick = 0.0
    instances: list = []

    def __init__(self, _address, handler_class) -> None:
        self.handler_class = handler_class
        self.server_port = 8765
        self.timeout = None
        self.closed = False
        type(self).instances.append(self)

    def handle_request(self) -> None:
        if type(self).clock is not None:
            type(self).clock.advance(type(self).tick)

    def server_close(self) -> None:
        self.closed = True


@pytest.fixture()
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(time, "monotonic", fake.monotonic)
    return fake


@pytest.fixture()
def make_server(monkeypatch):
    """``_make_server`` with a never-binding ``HTTPServer`` for fake-clock cases."""
    FakeHTTPServer.instances = []
    FakeHTTPServer.clock = None
    FakeHTTPServer.tick = 0.0
    monkeypatch.setattr(probe, "HTTPServer", FakeHTTPServer)

    def _factory(output: Path):
        return probe._make_server(output)  # server, route, origin, state

    yield _factory
    FakeHTTPServer.clock = None
    FakeHTTPServer.tick = 0.0
    FakeHTTPServer.instances = []


@pytest.fixture()
def real_server(tmp_path):
    """Real ``_make_server`` loopback instance plus its output path."""
    output = tmp_path / "r.json"
    server, route, origin, state = probe._make_server(output)
    return server, route, origin, state, output


@pytest.fixture()
def verify_calls(monkeypatch):
    """Fail-fast ``probe.verify`` spy for the slow-intake cases.

    Slow header/body requests must die inside the read deadline long before
    verification could run — any recorded call fails loudly rather than
    letting a rejection on other grounds masquerade as a deadline death.
    """
    calls: list = []

    def _spy(config):
        calls.append(config)
        pytest.fail("probe.verify ran although intake died at the read deadline")

    monkeypatch.setattr(probe, "verify", _spy)
    return calls


def _dial(port: int) -> socket.socket:
    """Connect only to a ``_serving``-registered loopback port."""
    if port not in _ALLOWED_PORTS:
        raise RuntimeError("offline tests may only dial the test-bound loopback port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        _ORIGINAL_CONNECT(sock, ("127.0.0.1", port))
    except Exception:
        sock.close()
        raise
    return sock


@contextlib.contextmanager
def _serving(server):
    """Drive ``handle_request`` on a daemon thread; always close and join.

    The thread serves only this test's own short-lived loopback server.
    ``server.timeout`` keeps ``handle_request`` returning promptly, the
    per-connection intake deadline bounds each accepted socket, and the
    listener is closed and the thread joined in ``finally`` so a failing
    case cannot leak either.
    """
    server.timeout = 0.05

    def run() -> None:
        while not getattr(server, "_stop_serving", False):
            try:
                server.handle_request()
            except OSError:
                continue

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    _ALLOWED_PORTS.add(server.server_port)
    try:
        yield thread
    finally:
        server._stop_serving = True
        try:
            server.server_close()
        finally:
            thread.join(timeout=3.0)
            _ALLOWED_PORTS.discard(server.server_port)
        assert not thread.is_alive(), "serving thread did not finish in bound"


def _exchange(port: int, payload: bytes) -> bytes:
    """Send one request on the registered loopback port; read until close."""
    sock = _dial(port)
    try:
        try:
            sock.sendall(payload)
        except OSError:
            pass  # early peer close; the recv loop reports the outcome
        data = b""
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                pytest.fail("timed out waiting for the loopback response")
            except OSError:
                break  # connection reset/EOF ends the response
            if not chunk:
                break
            data += chunk
        return data
    finally:
        sock.close()


def _run(
    server,
    raw: bytes,
    clock: FakeClock,
    *,
    step: float = 0.0,
    chunk: int = 65536,
    stages: tuple[int, int] | None = None,
    drip_chunk: int | None = None,
    after_deadline: bytes = b"",
    jump_at: float | None = None,
):
    """Drive one raw request through the real one-shot handler."""
    sock = FakeSocket(
        raw,
        clock,
        step,
        chunk,
        stages=stages,
        drip_chunk=drip_chunk,
        after_deadline=after_deadline,
        jump_at=jump_at,
    )
    handler = object.__new__(server.handler_class)
    handler.connection = sock
    handler.server = server
    handler.client_address = ("127.0.0.1", 0)
    handler.wfile = io.BytesIO()
    try:
        handler.handle()
    except Exception:
        # Mirror socketserver: handler faults reach the quiet handle_error,
        # then still propagate so a broken fixture fails the test instead of
        # being swallowed as a quiet handler error.
        server.handle_error(sock, handler.client_address)
        raise
    return handler, sock


def _assert_deadline_timeouts(
    timeouts: list[float], *, limit: float, step: float, fast_recvs: int
) -> None:
    """Assert every armed recv timeout is the deadline's exact remainder.

    ``fast_recvs`` leading recvs cost no clock, so their arming timeouts all
    equal ``limit``; each drip recv afterwards must sit exactly ``step``
    below its predecessor — a deadline reset or extra slack fails rather
    than being tolerated.
    """
    assert timeouts, "handler never armed a socket timeout"
    for index, timeout in enumerate(timeouts):
        expected = limit - step * max(0, index - fast_recvs)
        assert timeout == pytest.approx(expected), (
            f"recv timeout #{index} is {timeout}, expected {expected}"
        )


def test_slow_drip_never_resets_absolute_deadline(clock, make_server, tmp_path, verify_calls):
    server, route, _origin, _state = make_server(tmp_path / "r.json")
    assert probe._CONNECTION_DEADLINE_SECONDS == 10
    request_line = f"GET {route} HTTP/1.1\r\n".encode()
    headers = f"Host: 127.0.0.1:{server.server_port}\r\nX-Fill: ".encode() + b"a" * 512
    raw = request_line + headers
    limit = probe._CONNECTION_DEADLINE_SECONDS
    step = 0.6
    start = clock.now
    handler, sock = _run(
        server,
        raw,
        clock,
        step=step,
        chunk=65536,
        stages=(len(request_line), len(raw)),
        drip_chunk=1,
        after_deadline=b"\r\n\r\n",
        jump_at=start + 600.0,
    )
    # A complete valid request line arrives instantly, then headers drip one
    # byte per recv. The instant prefix costs no clock, so the first drip
    # recv is still armed with the whole limit; every later arming sits
    # exactly ``step`` lower. Intake dies at one absolute 10 s point — past
    # it, the head terminator the drip never delivered stays un-served.
    assert sock.timeouts[0] == pytest.approx(limit)
    assert sock.timeouts[1] == pytest.approx(limit)
    _assert_deadline_timeouts(sock.timeouts, limit=limit, step=step, fast_recvs=1)
    assert limit <= clock.now - start <= limit + step
    assert len(sock.payload) > 0
    assert verify_calls == []
    assert handler.wfile.getvalue() == b""
    assert handler.close_connection is True


def test_staged_post_body_drip_dies_at_absolute_deadline(
    clock, make_server, tmp_path, verify_calls
):
    server, route, origin, _state = make_server(tmp_path / "r.json")
    limit = probe._CONNECTION_DEADLINE_SECONDS
    body = b"app_key=deadline-k&app_secret=deadline-s&fill=" + b"x" * 256
    request_line = f"POST {route} HTTP/1.1\r\n".encode()
    headers = (
        f"Host: 127.0.0.1:{server.server_port}\r\n"
        f"Origin: {origin}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode()
    fast_head = request_line + headers
    step = 0.6
    start = clock.now
    handler, sock = _run(
        server,
        fast_head + body,
        clock,
        step=step,
        chunk=65536,
        stages=(len(fast_head), len(fast_head) + len(body)),
        drip_chunk=1,
        after_deadline=_TIMEOUT_REPORT_BODY,
        jump_at=start + 600.0,
    )
    # A complete POST head — correct Host, Origin, Content-Type and a
    # Content-Length spanning the whole legal form body — is served
    # instantly, then the body drips one byte per recv. Intake still dies at
    # the absolute 10 s cutoff: the queued tail is never consumed, verify
    # never runs, and the connection closes.
    assert sock.timeouts[0] == pytest.approx(limit)
    assert sock.timeouts[1] == pytest.approx(limit)
    _assert_deadline_timeouts(sock.timeouts, limit=limit, step=step, fast_recvs=1)
    assert limit <= clock.now - start <= limit + step
    assert len(sock.payload) > 0
    assert verify_calls == []
    assert handler.wfile.getvalue() == b""
    assert handler.close_connection is True


def test_server_intake_deadline_stops_intake_earlier(clock, make_server, tmp_path, verify_calls):
    server, _route, _origin, _state = make_server(tmp_path / "r.json")
    server._intake_deadline = clock.now + 2.0
    raw = b"GET /" + b"a" * 64 + b" HTTP/1.1\r\n\r\n"
    step = 0.6
    start = clock.now
    handler, sock = _run(server, raw, clock, step=step, chunk=1)
    assert sock.timeouts[0] == pytest.approx(2.0)
    _assert_deadline_timeouts(sock.timeouts, limit=2.0, step=step, fast_recvs=0)
    assert 2.0 <= clock.now - start <= 2.0 + step
    assert verify_calls == []
    assert handler.wfile.getvalue() == b""
    assert handler.close_connection is True


def test_server_intake_deadline_caps_staged_header_drip(clock, make_server, tmp_path, verify_calls):
    server, route, _origin, _state = make_server(tmp_path / "r.json")
    server._intake_deadline = clock.now + 2.0
    request_line = f"GET {route} HTTP/1.1\r\n".encode()
    headers = f"Host: 127.0.0.1:{server.server_port}\r\nX-Fill: ".encode() + b"a" * 512
    raw = request_line + headers
    step = 0.6
    start = clock.now
    handler, sock = _run(
        server,
        raw,
        clock,
        step=step,
        chunk=65536,
        stages=(len(request_line), len(raw)),
        drip_chunk=1,
        after_deadline=b"\r\n\r\n",
        jump_at=start + 600.0,
    )
    # Same staged GET as the absolute-deadline case, but the server's own
    # intake deadline lands first: the fast request line is already in and
    # the drip dies at 2.0 s, not 10 s.
    assert sock.timeouts[0] == pytest.approx(2.0)
    assert sock.timeouts[1] == pytest.approx(2.0)
    _assert_deadline_timeouts(sock.timeouts, limit=2.0, step=step, fast_recvs=1)
    assert 2.0 <= clock.now - start <= 2.0 + step
    assert len(sock.payload) > 0
    assert verify_calls == []
    assert handler.wfile.getvalue() == b""
    assert handler.close_connection is True


def test_server_intake_deadline_caps_staged_post_body_drip(
    clock, make_server, tmp_path, verify_calls
):
    server, route, origin, _state = make_server(tmp_path / "r.json")
    server._intake_deadline = clock.now + 2.0
    body = b"app_key=deadline-k&app_secret=deadline-s&fill=" + b"x" * 256
    request_line = f"POST {route} HTTP/1.1\r\n".encode()
    headers = (
        f"Host: 127.0.0.1:{server.server_port}\r\n"
        f"Origin: {origin}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode()
    fast_head = request_line + headers
    step = 0.6
    start = clock.now
    handler, sock = _run(
        server,
        fast_head + body,
        clock,
        step=step,
        chunk=65536,
        stages=(len(fast_head), len(fast_head) + len(body)),
        drip_chunk=1,
        after_deadline=_TIMEOUT_REPORT_BODY,
        jump_at=start + 600.0,
    )
    # The complete POST head — correct Host, Origin, Content-Type and full
    # Content-Length — is already in when the server's 2 s intake deadline
    # caps the body drip; the queued tail is never read and verify never
    # runs.
    assert sock.timeouts[0] == pytest.approx(2.0)
    assert sock.timeouts[1] == pytest.approx(2.0)
    _assert_deadline_timeouts(sock.timeouts, limit=2.0, step=step, fast_recvs=1)
    assert 2.0 <= clock.now - start <= 2.0 + step
    assert len(sock.payload) > 0
    assert verify_calls == []
    assert handler.wfile.getvalue() == b""
    assert handler.close_connection is True


def test_raw_get_on_route_returns_form(clock, make_server, tmp_path):
    server, route, _origin, _state = make_server(tmp_path / "r.json")
    raw = (f"GET {route} HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n\r\n").encode()
    handler, _sock = _run(server, raw, clock)
    body = handler.wfile.getvalue()
    assert body.startswith(b"HTTP/1.1 200")
    assert b"Content-Security-Policy" in body
    assert handler.close_connection is True


def test_unknown_method_gets_fixed_405(clock, make_server, tmp_path):
    server, route, _origin, _state = make_server(tmp_path / "r.json")
    raw = (f"TEAPOT {route} HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n\r\n").encode()
    handler, _sock = _run(server, raw, clock)
    body = handler.wfile.getvalue()
    assert body.startswith(b"HTTP/1.1 405")
    assert body.endswith(b"Method not allowed")
    assert handler.close_connection is True


def test_oversized_request_line_rejected_without_echo(clock, make_server, tmp_path):
    server, _route, _origin, _state = make_server(tmp_path / "r.json")
    raw = b"GET /" + _SENTINEL * 9000 + b" HTTP/1.1\r\n\r\n"
    # ``_run`` re-raises any handler fault after the quiet handle_error, so
    # the former AttributeError inside send_error(414) cannot slip through.
    handler, _sock = _run(server, raw, clock)
    body = handler.wfile.getvalue()
    assert body.startswith(b"HTTP/1.1 414")
    assert body.endswith(b"Request line too long")
    assert _SENTINEL not in body
    assert handler.close_connection is True


def test_oversized_header_rejected_without_echo(clock, make_server, tmp_path):
    server, route, _origin, _state = make_server(tmp_path / "r.json")
    raw = f"GET {route} HTTP/1.1\r\nX-Fill: ".encode() + _SENTINEL * 9000 + b"\r\n\r\n"
    handler, _sock = _run(server, raw, clock)
    body = handler.wfile.getvalue()
    assert body.startswith(b"HTTP/1.1 431")
    assert body.endswith(b"Headers too large")
    assert _SENTINEL not in body
    assert handler.close_connection is True


def test_quiet_handle_error_never_prints(clock, make_server, tmp_path, capsys):
    server, _route, _origin, _state = make_server(tmp_path / "r.json")
    server.handle_error(None, None)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_serve_times_out_closes_and_reports_fixed_status(
    clock, make_server, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(probe, "_SERVE_TIMEOUT_SECONDS", 0.25)
    FakeHTTPServer.clock = clock
    FakeHTTPServer.tick = 0.5
    output = tmp_path / "r.json"
    before = clock.now
    probe.serve(output)
    server = FakeHTTPServer.instances[-1]
    assert server._intake_deadline == pytest.approx(before + 0.25)
    assert server.timeout == 0.5
    assert server.closed is True
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("http://127.0.0.1:8765/")
    assert lines[1:] == ["verification timed out"]
    assert not output.exists()


def test_main_arg_errors_exit_2_and_never_serve(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(probe, "serve", lambda output: calls.append(output))
    for argv in ([], ["--serve"], ["--output", str(tmp_path / "r.json")]):
        with pytest.raises(SystemExit) as exc:
            probe.main(list(argv))
        assert exc.value.code == 2
    existing = tmp_path / "exists.json"
    existing.write_text("x")
    with pytest.raises(SystemExit) as exc:
        probe.main(["--serve", "--output", str(existing)])
    assert exc.value.code == 2
    assert calls == []


def test_main_help_exits_0_without_serving(monkeypatch):
    calls: list = []
    monkeypatch.setattr(probe, "serve", lambda output: calls.append(output))
    with pytest.raises(SystemExit) as exc:
        probe.main(["--help"])
    assert exc.value.code == 0
    assert calls == []


def test_loopback_acceptance_get_and_post(real_server, monkeypatch):
    server, route, origin, state, output = real_server
    monkeypatch.setattr(probe, "_CONNECTION_DEADLINE_SECONDS", 4.0)
    server._intake_deadline = time.monotonic() + 10.0
    seen: dict[str, object] = {}
    calls: list[dict] = []

    def stub_verify(config: dict) -> dict:
        calls.append(config)
        seen["snapshot"] = dict(config)
        seen["config"] = config
        return dict(_CALLBACK_REPORT)

    monkeypatch.setattr(probe, "verify", stub_verify)

    with _serving(server):
        port = server.server_port
        host = f"127.0.0.1:{port}"

        raw = _exchange(port, f"GET {route} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        head, _, form_body = raw.partition(b"\r\n\r\n")
        assert raw.startswith(b"HTTP/1.1 200")
        assert b"Cache-Control: no-store" in head
        assert b"Content-Security-Policy:" in head
        assert b"Connection: close" in head
        assert b"type=password" in form_body
        assert b"name=app_key" in form_body and b"name=app_secret" in form_body

        body = b"app_key=k-live&app_secret=s-live"
        post = (
            f"POST {route} HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        raw = _exchange(port, post)
        head, _, summary = raw.partition(b"\r\n\r\n")
        assert raw.startswith(b"HTTP/1.1 200")
        assert b"Cache-Control: no-store" in head
        for forbidden in (b"k-live", b"s-live", b"app_key", b"app_secret"):
            assert forbidden not in raw
        # The summary is HTML-escaped inside the page's <pre>; unescape the
        # decoded body before lifting the JSON object out of it.
        summary_text = html.unescape(summary.decode("utf-8"))
        summary_json = summary_text[summary_text.find("{") : summary_text.rfind("}") + 1]
        parsed = json.loads(summary_json)
        assert set(parsed) == {"outcome", "stage", "reason"}
        assert parsed == {"outcome": "success", "stage": "complete", "reason": "ok"}

    assert state == {"consumed": True, "done": True}
    assert output.exists()
    assert json.loads(output.read_text()) == _CALLBACK_REPORT
    # The snapshot copy records what verify actually received; the original
    # dict the handler passed in was cleared right after the call returned.
    assert len(calls) == 1
    assert calls[0] is seen["config"]
    assert seen["snapshot"] == {"app_key": "k-live", "app_secret": "s-live"}
    assert seen["config"] == {}, "handler must clear the config dict it passed to verify"
