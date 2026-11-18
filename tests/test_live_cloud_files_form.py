"""Bounded form-handling tests for the live probe: synthetic data, no sockets."""

import copy
import email.message
import io
import json
from types import SimpleNamespace

import pytest

from tools.live import verify_cloud_files as probe

HOST = "127.0.0.1:8765"
CTYPE = "application/x-www-form-urlencoded"
APP_KEY = "ak" + "K" * 40
APP_SECRET = "sk" + "S" * 40

REPORT = {
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
    "vod_item_count": 1,
    "ordinary_vod_candidate_found": True,
    "vod_data_type": "array",
    "error_family": "none",
    "cloud_recording_v1_discovery": "not_available_in_current_sdk",
}


class _FakeServer:
    """probe.HTTPServer stand-in; the tool's quiet server subclasses it."""

    def __init__(self, address, handler_class):
        self.handler_class = handler_class
        self.server_port = 8765


def _env(monkeypatch, tmp_path, verify=None, output=None):
    """Fresh fake server + counting verify stub; each call owns state/output."""
    monkeypatch.setattr(probe, "HTTPServer", _FakeServer)
    calls, box = [], {}

    def _default(config):
        box["ref"] = config
        calls.append(dict(config))
        return copy.deepcopy(REPORT)

    monkeypatch.setattr(probe, "verify", verify or _default)
    out = output or tmp_path / "report.json"
    server, route, origin, state = probe._make_server(out)
    return SimpleNamespace(
        server=server, route=route, origin=origin, state=state, output=out, calls=calls, box=box
    )


def _handler(env, path=None, body=b"", headers=()):
    """Bare handler via object.__new__; response API captured, _reader faked."""
    h = object.__new__(env.server.handler_class)
    h.path = env.route if path is None else path
    h.headers = email.message.Message()
    for name, value in headers:
        h.headers[name] = value  # __setitem__ appends; duplicates preserved
    h._reader, h.wfile = io.BytesIO(body), io.BytesIO()
    h.close_connection = False
    cap = {"status": None, "headers": []}
    h.send_response = lambda code: cap.__setitem__("status", code)
    h.send_header = lambda k, v: cap["headers"].append((k.lower(), v))
    h.end_headers = lambda: None
    h._cap = cap
    return h


def _post_headers(env, n):
    return [
        ("Host", HOST),
        ("Origin", env.origin),
        ("Content-Type", CTYPE),
        ("Content-Length", str(n)),
    ]


def _body():
    return f"app_key={APP_KEY}&app_secret={APP_SECRET}".encode()


def _post(env, body):
    h = _handler(env, body=body, headers=_post_headers(env, len(body)))
    h.do_POST()
    return h


def test_get_form_then_404s(monkeypatch, tmp_path):
    env = _env(monkeypatch, tmp_path)
    h = _handler(env, headers=[("Host", HOST)])
    h.do_GET()
    assert h._cap["status"] == 200
    assert ("cache-control", "no-store") in h._cap["headers"]
    assert any(k == "content-security-policy" and "form-action" in v for k, v in h._cap["headers"])
    page = h.wfile.getvalue().decode()
    assert "password" in page and APP_KEY not in page and APP_SECRET not in page
    for path, host in [(env.route + "x", HOST), (env.route, "evil.example")]:
        bad = _handler(env, path=path, headers=[("Host", host)])
        bad.do_GET()
        assert bad._cap["status"] == 404


def test_post_success_once_then_409(monkeypatch, tmp_path):
    env = _env(monkeypatch, tmp_path)
    h = _post(env, _body())
    assert h._cap["status"] == 200
    assert env.calls == [{"app_key": APP_KEY, "app_secret": APP_SECRET}]
    assert env.box["ref"] == {}
    assert env.state["consumed"] is True and env.state["done"] is True
    page = h.wfile.getvalue().decode()
    assert APP_KEY not in page and APP_SECRET not in page
    raw = env.output.read_text()
    assert APP_KEY not in raw and APP_SECRET not in raw
    assert json.loads(raw)["outcome"] == "success"
    replay = _post(env, _body())
    assert replay._cap["status"] == 409 and len(env.calls) == 1
    assert env.output.read_text() == raw


_REJECT_CASES = [
    # (raw body or None for valid, header name to drop, extra headers, status)
    (None, "", [("Host", HOST)], 403),  # duplicate Host
    (None, "Host", [("Host", "evil.example")], 403),
    (None, "", [("Origin", "http://evil.example")], 403),  # duplicate Origin
    (None, "Origin", [("Origin", "http://evil.example")], 403),
    (None, "", [("Content-Type", CTYPE)], 400),  # duplicate
    (None, "Content-Type", [("Content-Type", "text/plain")], 400),
    (None, "", [("Content-Length", "5")], 400),  # duplicate
    (None, "", [("Transfer-Encoding", "chunked")], 400),
    (None, "", [("Transfer-Encoding", "")], 400),
    (None, "Content-Length", [("Content-Length", "-1")], 400),
    (None, "Content-Length", [("Content-Length", "４２")], 400),  # unicode digits
    (None, "Content-Length", [("Content-Length", "8193")], 400),  # over cap
    (None, "Content-Length", [("Content-Length", "999")], 400),  # > actual
    (b"app_key=a&app_key=b&app_secret=c", "", [], 400),  # dup key
    (b"app_key=a&app_secret=c&extra=d", "", [], 400),  # unknown key
    (b"app_key=&app_secret=c", "", [], 400),  # blank value
    (b"app_key=a&app_secret=%FF", "", [], 400),  # undecodable
    (b"app_key=a&app_secret=abc%", "", [], 400),  # trailing %
    (b"app_key=a&app_secret=%2", "", [], 400),  # short escape
    (b"app_key=a&app_secret=\xff\xfe", "", [], 400),  # raw bad utf-8
]


@pytest.mark.parametrize("raw,drop,extra,status", _REJECT_CASES)
def test_post_rejected(monkeypatch, tmp_path, raw, drop, extra, status):
    env = _env(monkeypatch, tmp_path)
    body = _body() if raw is None else raw
    headers = [p for p in _post_headers(env, len(body)) if p[0] != drop] + extra
    h = _handler(env, body=body, headers=headers)
    h.do_POST()
    assert h._cap["status"] == status
    assert env.calls == [] and not env.output.exists()
    assert env.state["consumed"] is False


def test_post_failure_paths_latch_done(monkeypatch, tmp_path):
    body, seen = _body(), []

    def raising(exc):
        def _v(config):
            seen.append(config)
            raise exc

        return _v

    class _BoomWfile:
        def write(self, data):
            raise BrokenPipeError("gone")

    env = _env(monkeypatch, tmp_path, verify=raising(RuntimeError("nope")))
    assert _post(env, body)._cap["status"] == 500 and env.state["done"] is True
    assert seen[0] == {} and not env.output.exists()

    def leaky(config):
        return dict(REPORT, app_secret=APP_SECRET)

    env = _env(monkeypatch, tmp_path, verify=leaky, output=tmp_path / "b.json")
    assert _post(env, body)._cap["status"] == 500 and env.state["done"] is True
    assert not env.output.exists()

    out = tmp_path / "c.json"
    out.write_bytes(b"ORIGINAL")
    env = _env(monkeypatch, tmp_path, output=out)
    assert _post(env, body)._cap["status"] == 500 and env.box["ref"] == {}
    assert out.read_bytes() == b"ORIGINAL" and env.state["done"] is True

    env = _env(monkeypatch, tmp_path, output=tmp_path / "no" / "d.json")
    assert _post(env, body)._cap["status"] == 500 and env.box["ref"] == {}
    assert env.state["done"] is True

    env = _env(monkeypatch, tmp_path, output=tmp_path / "e.json")
    h = _handler(env, body=body, headers=_post_headers(env, len(body)))
    h.wfile = _BoomWfile()
    h.do_POST()
    assert h._cap["status"] == 200 and env.state["done"] is True
    assert env.box["ref"] == {} and APP_SECRET not in env.output.read_text()

    env = _env(
        monkeypatch, tmp_path, verify=raising(KeyboardInterrupt()), output=tmp_path / "f.json"
    )
    with pytest.raises(KeyboardInterrupt):
        _post(env, body)
    assert env.state["done"] is True and seen[1] == {}


def test_handle_error_stays_quiet(capsys, monkeypatch, tmp_path):
    env = _env(monkeypatch, tmp_path)
    try:
        raise RuntimeError(APP_SECRET)
    except RuntimeError:
        env.server.handle_error(None, ("127.0.0.1", 1))
    out = capsys.readouterr()
    assert "Traceback" not in out.err and APP_SECRET not in out.out + out.err


_TIME_SENTINEL = (
    "SYNTHETIC_TIME_FIELD_SENTINEL_" + "0123456789ABCDEF" * 8 + "__never_a_real_timestamp__"
)


@pytest.mark.parametrize("field", ["recorded_at_utc", "window_start", "window_end"])
def test_time_field_sentinel_cannot_leak(capsys, monkeypatch, tmp_path, field):
    # A whitelisted time field carrying a long synthetic sentinel fails the
    # fixed-format report check: real do_POST must 500, write nothing, and
    # never echo the sentinel into the response page or stdout/stderr.
    def leaky(config):
        return dict(REPORT, **{field: _TIME_SENTINEL})

    env = _env(monkeypatch, tmp_path, verify=leaky)
    h = _post(env, _body())
    assert h._cap["status"] == 500
    assert env.state["consumed"] is True and env.state["done"] is True
    assert not env.output.exists()
    assert _TIME_SENTINEL not in h.wfile.getvalue().decode()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert _TIME_SENTINEL not in captured.out + captured.err


def test_verify_exception_sentinel_cannot_leak(capsys, monkeypatch, tmp_path):
    # A RuntimeError carrying a synthetic secret is swallowed to a fixed 500:
    # it must surface in no response bytes, no output file, no stdout/stderr.
    sentinel = "SYNTHETIC_SECRET_SENTINEL_" + "X" * 128

    def raising(config):
        raise RuntimeError(sentinel)

    env = _env(monkeypatch, tmp_path, verify=raising)
    h = _post(env, _body())
    assert h._cap["status"] == 500
    assert env.state["consumed"] is True and env.state["done"] is True
    assert not env.output.exists()
    assert sentinel not in h.wfile.getvalue().decode()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert sentinel not in captured.out + captured.err


def test_parsed_values_and_config_cleared(monkeypatch, tmp_path):
    # Wrap parse_qs to capture the actual values dict the handler clears, and
    # keep the fake's own reference to the real config dict. The dict objects
    # must be empty after each submission — object-level clearing only; this
    # asserts nothing about erasing the underlying memory.
    parsed = []
    real_parse_qs = probe.parse_qs

    def recording_parse_qs(*args, **kwargs):
        result = real_parse_qs(*args, **kwargs)
        parsed.append(result)
        return result

    monkeypatch.setattr(probe, "parse_qs", recording_parse_qs)
    env = _env(monkeypatch, tmp_path)
    # Invalid submissions first: after ``consumed`` latches, a replay is 409
    # and never reaches the parse step.
    for raw in (b"app_key=a&app_secret=c&unknownkey=d", b"app_key=&app_secret=c"):
        rejected = _post(env, raw)
        assert rejected._cap["status"] == 400
        assert parsed[-1] == {}
        assert env.state["consumed"] is False

    h = _post(env, _body())
    assert h._cap["status"] == 200
    assert env.state["consumed"] is True and env.state["done"] is True
    config_ref = env.box["ref"]
    assert config_ref == {}
    assert parsed[-1] == {}
    assert len(parsed) == 3 and len(env.calls) == 1
