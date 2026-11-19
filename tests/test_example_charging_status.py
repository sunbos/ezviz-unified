"""Contract tests for ``examples.charging_status.main``.

The real :class:`ezviz_unified.Client` and :class:`ezviz_unified.AsyncClient`
are exercised end to end, but every HTTP exchange is pinned to in-memory
``httpx.MockTransport`` handlers so no network access, real credentials, or
real user configuration is ever touched.  ``examples._common.load_config``
is replaced with a synthetic config and ``EZVIZ_DEVICE_SERIAL`` is always
set through ``monkeypatch``.  All tokens, serials, hosts and payloads below
are synthetic.
"""

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from examples import charging_status
from ezviz_unified import AsyncClient, Client, RegionConfig

_USAGE = "usage: python -m examples.charging_status --mode sync|async"
_ERROR = "error: charging status query failed"
_APP_KEY = "SYNTH_APP"
_APP_SECRET = "SYNTH_SECRET"
_SERIAL = "DevSN0099xA"
_AREA_DOMAIN = "https://open.ys7.com"
_HOST = "open.ys7.com"
_TOKEN_PATH = "/api/lapp/token/get"
_BUSINESS_PATH = "/api/v3/device/power/status/get"
_MODES = ("sync", "async")


def _business_success(power_status) -> dict:
    return {
        "code": "200",
        "data": {
            "valueInfo": {"powerStatus": power_status},
            "undocumentedField": "preserved",
        },
        "msg": "ok",
        "futureTopLevelField": True,
    }


def _expected_stdout(mode: str, status) -> str:
    payload = {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "charging_status": status,
    }
    return json.dumps(payload, separators=(",", ":")) + "\n"


class _Gateway:
    """In-memory stand-in for the auth host and the business API host.

    Any request that was not queued raises ``AssertionError`` instead of a
    transport-level error, so stray calls fail the test loudly rather than
    being masked as a generic SDK failure.
    """

    def __init__(self) -> None:
        self._payloads: list[dict] = []
        self.auth_requests: list[httpx.Request] = []
        self.business_requests: list[httpx.Request] = []
        self.issued_tokens: list[str] = []
        self.serial: str | None = None
        self.transport_error = False

    def expect_serial(self, serial: str) -> None:
        self.serial = serial

    def queue(self, *payloads: dict) -> None:
        self._payloads = list(payloads)

    def auth(self, request: httpx.Request) -> httpx.Response:
        self.auth_requests.append(request)
        assert request.method == "POST"
        assert request.url.scheme == "https"
        assert request.url.host == _HOST
        assert request.url.path == _TOKEN_PATH
        token = "TOKEN_%d" % len(self.issued_tokens)
        self.issued_tokens.append(token)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": token,
                    "expireTime": 9_999_999_999_999,
                    "areaDomain": _AREA_DOMAIN,
                },
            },
        )

    def business(self, request: httpx.Request) -> httpx.Response:
        self.business_requests.append(request)
        if self.transport_error:
            raise httpx.ConnectError("synthetic transport failure", request=request)
        # Frozen request shape: a single GET to the domestic power-status
        # route on open.ys7.com over HTTPS, exactly one accessToken header
        # carrying the last issued token and exactly one deviceSerial
        # header carrying the configured serial.  No Content-Type, no
        # query, no body.  Any other route or header shape fails loudly.
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == _HOST
        assert request.url.path == _BUSINESS_PATH
        assert request.url.query == b""
        assert "content-type" not in request.headers
        assert request.read() == b""
        assert self.serial is not None
        assert self.issued_tokens
        assert request.headers.get_list("accessToken") == [self.issued_tokens[-1]]
        assert request.headers.get_list("deviceSerial") == [self.serial]
        if not self._payloads:
            raise AssertionError("unexpected extra business request")
        return httpx.Response(200, json=self._payloads.pop(0))


class _Harness:
    """Synthetic config plus counters for client construction."""

    def __init__(self) -> None:
        self.gateway = _Gateway()
        self.constructed: list[str] = []
        self.credentials: list[tuple[str, str]] = []
        self.config_calls = 0
        self.config_error: BaseException | None = None
        self.config = SimpleNamespace(
            app_key=_APP_KEY,
            app_secret=_APP_SECRET,
            region="domestic",
            region_config=RegionConfig.china(),
        )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    h = _Harness()
    gateway = h.gateway
    gateway.expect_serial(_SERIAL)

    class _SyncClient(Client):
        def __init__(self, app_key, app_secret, **kwargs):
            h.constructed.append("sync")
            h.credentials.append((app_key, app_secret))
            super().__init__(
                app_key,
                app_secret,
                auth_transport=httpx.MockTransport(gateway.auth),
                transport_factory=lambda origin: httpx.MockTransport(gateway.business),
                **kwargs,
            )

    class _AsyncClient(AsyncClient):
        def __init__(self, app_key, app_secret, **kwargs):
            h.constructed.append("async")
            h.credentials.append((app_key, app_secret))
            super().__init__(
                app_key,
                app_secret,
                auth_transport=httpx.MockTransport(gateway.auth),
                transport_factory=lambda origin: httpx.MockTransport(gateway.business),
                **kwargs,
            )

    def _load_config():
        h.config_calls += 1
        if h.config_error is not None:
            raise h.config_error
        return h.config

    monkeypatch.setattr(charging_status, "Client", _SyncClient)
    monkeypatch.setattr(charging_status, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(charging_status, "load_config", _load_config)
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", _SERIAL)
    return h


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("power_status", ["0", "1", "2", "3", "4"])
def test_known_power_status_exact_output(harness, capsys, mode, power_status):
    harness.gateway.queue(_business_success(power_status))
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == _expected_stdout(mode, power_status)
    assert captured.err == ""
    assert harness.config_calls == 1
    assert harness.constructed == [mode]
    assert harness.credentials == [(_APP_KEY, _APP_SECRET)]
    assert len(harness.gateway.auth_requests) == 1
    # One logical read per run: exactly one business request and no
    # devices.status or any other device call.
    assert len(harness.gateway.business_requests) == 1


@pytest.mark.parametrize("mode", _MODES)
def test_unknown_power_status_string_reports_null_without_leak(harness, capsys, mode):
    unknown = "https://synthetic.invalid/future-power-status"
    harness.gateway.queue(_business_success(unknown))
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == _expected_stdout(mode, None)
    assert captured.err == ""
    assert unknown not in captured.out
    assert unknown not in captured.err
    assert "synthetic.invalid" not in captured.out
    assert "synthetic.invalid" not in captured.err
    assert len(harness.gateway.business_requests) == 1


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "payload",
    [
        {"code": "200", "data": {"valueInfo": {"powerStatus": 1}}},
        {"code": "200", "data": {"valueInfo": {"powerStatus": None}}},
        {"code": "200", "data": {"valueInfo": {"powerStatus": ["0"]}}},
        {"code": "200", "data": {"valueInfo": {}}},
        {"code": "200", "data": {}},
        {"code": "200"},
    ],
)
def test_malformed_business_data_fails_rc1(harness, capsys, mode, payload):
    harness.gateway.queue(payload)
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    # The physical request succeeded; only the logical call failed.
    assert len(harness.gateway.business_requests) == 1


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "payload",
    [
        {"code": "10031", "msg": "permission denied"},
        {"code": "10999", "msg": "generic business failure"},
    ],
)
def test_business_error_fails_rc1(harness, capsys, mode, payload):
    harness.gateway.queue(payload)
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert len(harness.gateway.business_requests) == 1


@pytest.mark.parametrize("mode", _MODES)
def test_transport_error_fails_rc1(harness, capsys, mode):
    harness.gateway.queue(_business_success("0"))
    harness.gateway.transport_error = True
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert len(harness.gateway.business_requests) >= 1


@pytest.mark.parametrize("mode", _MODES)
def test_expired_token_replays_single_read_at_most_once(harness, capsys, mode):
    harness.gateway.queue(
        {"code": "10002", "msg": "access token expired"},
        _business_success("3"),
    )
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == _expected_stdout(mode, "3")
    assert captured.err == ""
    # One logical read, replayed at most once after a token refresh.
    assert len(harness.gateway.business_requests) == 2
    assert len(harness.gateway.auth_requests) == 2
    second = harness.gateway.business_requests[1]
    assert second.headers.get_list("accessToken") == ["TOKEN_1"]


@pytest.mark.parametrize("mode", _MODES)
def test_overseas_region_rejected_before_client(harness, capsys, mode):
    harness.config.region = "overseas"
    harness.config.region_config = RegionConfig.overseas()
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert harness.constructed == []
    assert harness.gateway.auth_requests == []
    assert harness.gateway.business_requests == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "serial",
    [
        "",
        "   ",
        " DEVSN",
        "DEVSN ",
        "DEV\nSN",
        "DEV\tSN",
        "DEV\rSN",
        "DEV\x01SN",
        "DÉVSN",
        "设备序列号",
    ],
)
def test_invalid_serial_rejected_before_client(harness, capsys, monkeypatch, mode, serial):
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", serial)
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert harness.constructed == []
    assert harness.gateway.auth_requests == []
    assert harness.gateway.business_requests == []


@pytest.mark.parametrize("mode", _MODES)
def test_missing_serial_rejected_before_client(harness, capsys, monkeypatch, mode):
    monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert harness.constructed == []
    assert harness.gateway.auth_requests == []
    assert harness.gateway.business_requests == []


@pytest.mark.parametrize("mode", _MODES)
def test_config_error_fails_rc1(harness, capsys, mode):
    harness.config_error = ValueError("EZVIZ_APP_KEY is required")
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert harness.config_calls == 1
    assert harness.constructed == []
    assert harness.gateway.business_requests == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "exc",
    [KeyboardInterrupt(), asyncio.CancelledError()],
)
def test_interruption_returns_130(harness, capsys, mode, exc):
    harness.config_error = exc
    rc = charging_status.main(["--mode", mode])
    captured = capsys.readouterr()
    assert rc == 130
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode"],
        ["--mode", "sync", "extra"],
        ["--mode", "SYNC"],
        ["--mode", "bogus"],
        ["--mode", ""],
        ["sync"],
        ["async"],
        ["--mode=sync"],
        ["-m", "sync"],
        ["--help", "--mode", "sync"],
        ["--mode", "sync", "--help"],
    ],
)
def test_invalid_arguments_print_usage_and_stop(harness, capsys, argv):
    harness.config_error = AssertionError("load_config must not run")
    rc = charging_status.main(argv)
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == _USAGE + "\n"
    assert harness.config_calls == 0
    assert harness.constructed == []
    assert harness.gateway.auth_requests == []
    assert harness.gateway.business_requests == []


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_help_never_touches_config_or_clients(capsys, monkeypatch, argv):
    def _forbidden(*args, **kwargs):
        raise AssertionError("must not be invoked")

    monkeypatch.setattr(charging_status, "load_config", _forbidden)
    monkeypatch.setattr(charging_status, "Client", _forbidden)
    monkeypatch.setattr(charging_status, "AsyncClient", _forbidden)
    rc = charging_status.main(argv)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == _USAGE + "\n"
    assert captured.err == ""


def test_main_defaults_to_sys_argv(harness, capsys, monkeypatch):
    harness.gateway.queue(_business_success("2"))
    monkeypatch.setattr(sys, "argv", ["charging_status", "--mode", "sync"])
    rc = charging_status.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == _expected_stdout("sync", "2")
    assert captured.err == ""
    assert harness.constructed == ["sync"]
    assert len(harness.gateway.business_requests) == 1
