"""Synthetic tests for ``examples.passenger_flow``.

Nothing here touches the network: the real ``Client`` / ``AsyncClient``
constructors are wrapped so every HTTP request is served by one
``httpx.MockTransport`` handler that records the URL and decoded form.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

import examples.passenger_flow as pf
from ezviz_unified import AsyncClient, Client

HOST = "https://open.ys7.com"
TOKEN_URL = f"{HOST}/api/lapp/token/get"
REPORT_URL = {
    "daily": f"{HOST}/api/lapp/passengerflow/daily",
    "hourly": f"{HOST}/api/lapp/passengerflow/hourly",
}
_ERROR = "error: passenger flow query failed"

AUTH = {
    "code": "200",
    "data": {
        "accessToken": "SYNTH_TOKEN",
        "expireTime": 9999999999999,
        "areaDomain": HOST,
    },
}
REPORTS = {
    "daily": {"code": "200", "data": {"date": "2019-06-21", "inFlow": 5, "outFlow": 3}},
    "hourly": {"code": "200", "data": [{"hourIndex": 8, "inFlow": 1, "outFlow": 0}]},
}
_APP_KEY = "synthetic" + "-key"
_APP_SECRET = "synthetic" + "-secret"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_APP_KEY": _APP_KEY,
    "EZVIZ_APP_SECRET": _APP_SECRET,
    "EZVIZ_DEVICE_SERIAL": "SYNTHFLOW",
    "EZVIZ_CHANNEL": "2",
}
ARGV = {"sync": ["--mode", "sync"], "async": ["--mode", "async"]}


class State:
    """One handler for every HTTP request; serves canned JSON payloads."""

    def __init__(self, payloads=(), report_error=None):
        self.payloads = list(payloads)
        self.report_error = report_error
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            (
                str(request.url),
                {k: v[0] for k, v in parse_qs(request.content.decode()).items()},
            )
        )
        if self.report_error is not None and request.url.path != "/api/lapp/token/get":
            raise self.report_error
        return httpx.Response(200, json=self.payloads.pop(0))


def _ctor(cls, state):
    return lambda *args, **kwargs: cls(
        *args,
        **kwargs,
        auth_transport=httpx.MockTransport(state.handler),
        transport_factory=lambda origin: httpx.MockTransport(state.handler),
    )


@pytest.fixture()
def transport(monkeypatch):
    state = State()
    monkeypatch.setattr(pf, "Client", _ctor(Client, state))
    monkeypatch.setattr(pf, "AsyncClient", _ctor(AsyncClient, state))
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("EZVIZ_FLOW_DAY", "EZVIZ_FLOW_DATE_MS"):
        monkeypatch.delenv(key, raising=False)
    return state


def _argv(mode, report):
    return [*ARGV[mode], "--report", report]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("report", ["daily", "hourly"])
def test_main_ok(transport, capsys, mode, report):
    transport.payloads = [AUTH, REPORTS[report]]
    assert pf.main(_argv(mode, report)) == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {
        "region": "domestic",
        "report": report,
        "mode": mode,
        "status": "ok",
    }
    for leaked in ("SYNTHFLOW", "SYNTH_TOKEN", "synthetic-key", "synthetic"):
        assert leaked not in out
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == TOKEN_URL
    assert transport.calls[1][0] == REPORT_URL[report]
    url, form = transport.calls[1]
    assert url.startswith(HOST)
    assert str(form.get("accessToken")) == "SYNTH_TOKEN"
    assert str(form.get("deviceSerial")) == "SYNTHFLOW"
    assert str(form.get("channelNo")) == "2"
    assert "day" not in form and "date" not in form


def test_daily_explicit_day(transport, monkeypatch, capsys):
    monkeypatch.setenv("EZVIZ_FLOW_DAY", "2019-06-21T00:00:00+08:00")
    transport.payloads = [AUTH, REPORTS["daily"]]
    assert pf.main(_argv("sync", "daily")) == 0
    capsys.readouterr()
    assert str(transport.calls[1][1].get("date")) == "1561046400000"


def test_hourly_explicit_date_ms(transport, monkeypatch, capsys):
    monkeypatch.setenv("EZVIZ_FLOW_DATE_MS", "146744312353")
    transport.payloads = [AUTH, REPORTS["hourly"]]
    assert pf.main(_argv("sync", "hourly")) == 0
    capsys.readouterr()
    assert str(transport.calls[1][1].get("date")) == "146744312353"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "sync"],
        ["--report", "daily"],
        ["--mode", "bogus", "--report", "daily"],
        ["--mode", "sync", "--report", "bogus"],
    ],
)
def test_bad_argv_usage(transport, capsys, argv):
    assert pf.main(argv) == 1
    captured = capsys.readouterr()
    assert "usage" in (captured.out + captured.err).lower()
    assert transport.calls == []


def test_missing_channel_fails(transport, monkeypatch, capsys):
    monkeypatch.delenv("EZVIZ_CHANNEL")
    transport.payloads = [AUTH, REPORTS["daily"]]
    assert pf.main(_argv("sync", "daily")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err != ""
    assert transport.calls == []


def test_overseas_region_fails(transport, monkeypatch, capsys):
    monkeypatch.setenv("EZVIZ_REGION", "overseas")
    transport.payloads = [AUTH, REPORTS["daily"]]
    assert pf.main(_argv("sync", "daily")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err != ""
    assert transport.calls == []


def test_invalid_day_fails(transport, monkeypatch, capsys):
    monkeypatch.setenv("EZVIZ_FLOW_DAY", "not-a-date")
    transport.payloads = [AUTH, REPORTS["daily"]]
    assert pf.main(_argv("sync", "daily")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err != ""
    assert transport.calls == []


def test_business_error_fails(transport, capsys):
    secret_msg = "private vendor diagnostic"
    transport.payloads = [AUTH, {"code": "60020", "msg": secret_msg}]
    assert pf.main(_argv("sync", "daily")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == _ERROR + "\n"
    assert secret_msg not in captured.err
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == TOKEN_URL
    assert transport.calls[1][0] == REPORT_URL["daily"]


def test_runtime_error_fails(transport, capsys):
    transport.payloads = [AUTH]
    transport.report_error = RuntimeError("synthetic boom")
    assert pf.main(_argv("sync", "daily")) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == _ERROR + "\n"
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == TOKEN_URL
    assert transport.calls[1][0] == REPORT_URL["daily"]


def test_keyboard_interrupt_returns_130(transport, capsys):
    transport.payloads = [AUTH]
    transport.report_error = KeyboardInterrupt()
    assert pf.main(_argv("sync", "daily")) == 130
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == _ERROR + "\n"
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == TOKEN_URL
    assert transport.calls[1][0] == REPORT_URL["daily"]
