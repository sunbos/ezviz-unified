"""Tests for ``examples.traffic_usage``.

The real ``ezviz_unified`` clients are exercised end to end; only the HTTP
layer is synthetic. A recording ``httpx.MockTransport`` is injected through
the ``auth_transport`` / ``transport_factory`` hooks of the real client
classes via subclasses, so no credentials or traffic ever leave the
process. All monkeypatches are test-scoped and restored on teardown.
"""

from __future__ import annotations

import json
import runpy
import sys

import httpx
import pytest

import examples.traffic_usage as traffic_usage
import ezviz_unified
from examples._common import load_config
from ezviz_unified import AsyncClient, Client, EzvizError

USAGE = "usage: python -m examples.traffic_usage --mode sync|async"
ERROR_LINE = "error: traffic.account_summary failed"
SUCCESS_LINE = json.dumps(
    {"region": "domestic", "operation": "traffic.account_summary", "status": "ok"},
    separators=(",", ":"),
)
TRAFFIC_PATH = "/api/lapp/traffic/user/total"
ENV_VARS = ("EZVIZ_APP_KEY", "EZVIZ_APP_SECRET", "EZVIZ_REGION")
# Raw usage digits, credentials and tokens must never reach output.
FORBIDDEN_TOKENS = ("PRIVATEKEY", "PRIVATESECRET", "987654321", "TEST")

AUTH_PAYLOAD = {
    "code": "200",
    "data": {
        "accessToken": "TEST",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
TRAFFIC_PAYLOAD = {"code": "200", "data": {"usedFlow": 987654321}}


class _Recorder:
    """MockTransport that logs requests and serves canned responses."""

    def __init__(self, *, traffic_payload=None, traffic_exc=None):
        self.requests: list[httpx.Request] = []
        self._traffic_payload = TRAFFIC_PAYLOAD if traffic_payload is None else traffic_payload
        self._traffic_exc = traffic_exc
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if "token" in request.url.path:
            return httpx.Response(200, json=AUTH_PAYLOAD)
        if request.url.path == TRAFFIC_PATH:
            if self._traffic_exc is not None:
                raise self._traffic_exc
            return httpx.Response(200, json=self._traffic_payload)
        return httpx.Response(404, json={"code": "404", "msg": "unexpected"})

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def _patched(base, recorder: _Recorder):
    """Return a subclass of ``base`` injecting the synthetic transports."""

    class Patched(base):
        def __init__(self, *args, **kwargs):
            kwargs["auth_transport"] = recorder.transport
            kwargs["transport_factory"] = lambda *a, **k: recorder.transport
            super().__init__(*args, **kwargs)

    return Patched


def _install_clients(monkeypatch, module, recorder: _Recorder) -> None:
    monkeypatch.setattr(module, "Client", _patched(Client, recorder))
    monkeypatch.setattr(module, "AsyncClient", _patched(AsyncClient, recorder))


@pytest.fixture(autouse=True)
def _fresh_load_config():
    """Drop any cached config so env changes apply per test."""
    clear = getattr(load_config, "cache_clear", None)
    if callable(clear):
        clear()
    yield
    if callable(clear):
        clear()


@pytest.fixture
def domestic_env(monkeypatch):
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATEKEY")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATESECRET")
    monkeypatch.setenv("EZVIZ_REGION", "domestic")


@pytest.fixture
def missing_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _assert_clean(captured) -> None:
    blob = captured.out + captured.err
    for token in FORBIDDEN_TOKENS:
        assert token not in blob, f"leaked {token!r}"


def _assert_auth_then_traffic(recorder: _Recorder) -> None:
    # Exactly one auth call followed by the traffic summary call.
    assert len(recorder.paths) == 2
    assert "token" in recorder.paths[0]
    assert recorder.paths[1] == TRAFFIC_PATH


def _run_as_main(monkeypatch, argv) -> int:
    monkeypatch.setattr(sys, "argv", list(argv))
    # Re-execute the module under __main__ without stale sys.modules state
    # so its `from ezviz_unified import ...` binds the patched exports.
    monkeypatch.delitem(sys.modules, "examples.traffic_usage", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("examples.traffic_usage", run_name="__main__")
    return excinfo.value.code


class TestArgumentHandling:
    @pytest.mark.parametrize("argv", [["--help"], ["-h"]])
    def test_help(self, argv, missing_env, monkeypatch, capsys):
        monkeypatch.setattr(
            traffic_usage,
            "load_config",
            lambda: pytest.fail("--help must not read configuration"),
        )
        assert traffic_usage.main(list(argv)) == 0
        captured = capsys.readouterr()
        assert captured.out == USAGE + "\n"
        assert captured.err == ""

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["--mode"],
            ["--mode", "bogus"],
            ["--mode", "SYNC"],
            ["sync"],
            ["--mode", "sync", "extra"],
            ["--help", "extra"],
        ],
    )
    def test_bad_argv(self, argv, missing_env, monkeypatch, capsys):
        monkeypatch.setattr(
            traffic_usage,
            "load_config",
            lambda: pytest.fail("invalid args must not read configuration"),
        )
        assert traffic_usage.main(list(argv)) == 1
        captured = capsys.readouterr()
        # Bounded, fixed output that does not echo the arguments.
        assert captured.out == ""
        assert captured.err == USAGE + "\n"
        _assert_clean(captured)


@pytest.mark.parametrize("mode", ["sync", "async"])
class TestModes:
    def test_success(self, mode, domestic_env, monkeypatch, capsys):
        recorder = _Recorder()
        _install_clients(monkeypatch, traffic_usage, recorder)
        assert traffic_usage.main(["--mode", mode]) == 0
        captured = capsys.readouterr()
        assert captured.out == SUCCESS_LINE + "\n"
        assert captured.err == ""
        _assert_clean(captured)
        _assert_auth_then_traffic(recorder)

    def test_business_error(self, mode, domestic_env, monkeypatch, capsys):
        recorder = _Recorder(traffic_payload={"code": "10017", "msg": "synthetic-denied"})
        _install_clients(monkeypatch, traffic_usage, recorder)
        assert traffic_usage.main(["--mode", mode]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ERROR_LINE + "\n"
        assert "10017" not in captured.err
        assert "synthetic-denied" not in captured.err
        _assert_clean(captured)
        _assert_auth_then_traffic(recorder)

    def test_sdk_error_is_sanitized(self, mode, domestic_env, monkeypatch, capsys):
        recorder = _Recorder(traffic_exc=EzvizError("raw-internal-PRIVATESECRET"))
        _install_clients(monkeypatch, traffic_usage, recorder)
        assert traffic_usage.main(["--mode", mode]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ERROR_LINE + "\n"
        assert "raw-internal" not in captured.err
        _assert_clean(captured)
        _assert_auth_then_traffic(recorder)

    def test_keyboard_interrupt(self, mode, domestic_env, monkeypatch, capsys):
        recorder = _Recorder(traffic_exc=KeyboardInterrupt())
        _install_clients(monkeypatch, traffic_usage, recorder)
        assert traffic_usage.main(["--mode", mode]) == 130
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ERROR_LINE + "\n"
        _assert_clean(captured)
        _assert_auth_then_traffic(recorder)


class TestConfigFailures:
    def test_missing_env(self, missing_env, monkeypatch, capsys):
        recorder = _Recorder()
        _install_clients(monkeypatch, traffic_usage, recorder)
        assert traffic_usage.main(["--mode", "sync"]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ERROR_LINE + "\n"
        _assert_clean(captured)
        assert recorder.requests == []

    @pytest.mark.parametrize("mode", ["sync", "async"])
    def test_overseas_region(self, mode, monkeypatch, capsys):
        monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATEKEY")
        monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATESECRET")
        monkeypatch.setenv("EZVIZ_REGION", "overseas")
        recorder = _Recorder()
        _install_clients(monkeypatch, traffic_usage, recorder)
        assert traffic_usage.main(["--mode", mode]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ERROR_LINE + "\n"
        _assert_clean(captured)
        # Region is rejected before any HTTP request is made.
        assert recorder.requests == []


class TestMainGuard:
    def test_help(self, missing_env, monkeypatch, capsys):
        code = _run_as_main(monkeypatch, ["examples.traffic_usage", "--help"])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.out == USAGE + "\n"
        assert captured.err == ""

    def test_missing_env(self, missing_env, monkeypatch, capsys):
        code = _run_as_main(monkeypatch, ["examples.traffic_usage", "--mode", "sync"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ERROR_LINE + "\n"
        _assert_clean(captured)

    @pytest.mark.parametrize("mode", ["sync", "async"])
    def test_success(self, mode, domestic_env, monkeypatch, capsys):
        recorder = _Recorder()
        # The module binds its imports at exec time under runpy, so patch
        # the package exports to inject the instrumented subclasses.
        _install_clients(monkeypatch, ezviz_unified, recorder)
        code = _run_as_main(monkeypatch, ["examples.traffic_usage", "--mode", mode])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.out == SUCCESS_LINE + "\n"
        assert captured.err == ""
        _assert_clean(captured)
        _assert_auth_then_traffic(recorder)
