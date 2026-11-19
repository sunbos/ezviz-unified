"""Offline pytest suite for ``examples.a1_detectors``.

All HTTP is mocked: the real ``Client``/``AsyncClient`` are constructed
with an ``httpx.MockTransport`` covering both the auth POST and the
single business call, while ``load_config`` is replaced by a synthetic
overseas configuration.  No real credentials, files or sockets.
"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl

import httpx
import pytest

import examples.a1_detectors as example
from ezviz_unified import AsyncClient, Client, EzvizError, RegionConfig

AUTH_ORIGIN = "https://synthetic-auth.ezvizlife.com"
API_ORIGIN = "https://synthetic-api.ezvizlife.com"
AUTH_PATH = "/api/lapp/token/get"
APP_KEY = "SYNTH"
APP_SECRET = "SYNTHSECRET"
TOKEN = "TOK1"
A1_SERIAL = "SYNTHETIC-A1-HOST"
DETECTOR_SERIAL = "SYNTHETIC-DETECTOR"
MARKER = "UNEMITTED-MARKER-VALUE"
ERROR_LINE = "error: A1 detector query failed\n"
USAGE_LINE = (
    "usage: python -m examples.a1_detectors --mode sync|async --query detectors|bindable|linked\n"
)

QUERY_PATHS = {
    "detectors": "/api/lapp/detector/list",
    "bindable": "/api/lapp/detector/ipc/list/bindable",
    "linked": "/api/lapp/detector/ipc/list/bind",
}


def _config(region="overseas"):
    return SimpleNamespace(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        region=region,
        region_config=RegionConfig.overseas(auth_base_url=AUTH_ORIGIN),
    )


def _business_body(path, items):
    envelope = {"code": "200", "msg": "ok", "data": items}
    return {"result": envelope} if path == QUERY_PATHS["detectors"] else envelope


def _transport(requests, items, state):
    def handler(request: httpx.Request) -> httpx.Response:
        pairs = parse_qsl(request.content.decode(), keep_blank_values=True)
        requests.append((request, pairs))
        if request.url.path == AUTH_PATH:
            return httpx.Response(
                200,
                json={
                    "code": state["auth_code"],
                    "msg": "ok",
                    "data": {
                        "accessToken": TOKEN,
                        "areaDomain": API_ORIGIN,
                        "expireTime": 9999999999999,
                    },
                },
            )
        return httpx.Response(200, json=_business_body(request.url.path, items))

    return httpx.MockTransport(handler)


def _block_clients(monkeypatch):
    monkeypatch.setattr(example, "Client", lambda *a, **k: pytest.fail("Client"))
    monkeypatch.setattr(example, "AsyncClient", lambda *a, **k: pytest.fail("AsyncClient"))


@pytest.fixture
def wired(monkeypatch):
    requests = []
    items = [
        {
            "detectorSerial": "DET-INNER-1",
            "ipcSerial": "IPC-INNER-1",
            "future": {"nested": [MARKER, {"deep": [1, 2, 3]}]},
        },
        {"detectorSerial": "DET-INNER-2", "channelNo": 7},
        {"detectorSerial": "DET-INNER-3", "linked": {"a": "b"}},
    ]
    state = {"auth_code": "200"}
    transport = _transport(requests, items, state)

    def make_client(app_key, app_secret, *, region):
        assert (app_key, app_secret) == (APP_KEY, APP_SECRET)
        return Client(
            app_key,
            app_secret,
            region=region,
            auth_transport=transport,
            transport_factory=lambda *args, **kwargs: transport,
        )

    def make_async_client(app_key, app_secret, *, region):
        assert (app_key, app_secret) == (APP_KEY, APP_SECRET)
        return AsyncClient(
            app_key,
            app_secret,
            region=region,
            auth_transport=transport,
            transport_factory=lambda *args, **kwargs: transport,
        )

    monkeypatch.setattr(example, "Client", make_client)
    monkeypatch.setattr(example, "AsyncClient", make_async_client)
    monkeypatch.setattr(example, "load_config", lambda: _config())
    monkeypatch.setenv("EZVIZ_DEVICE_SERIAL", A1_SERIAL)
    monkeypatch.delenv("EZVIZ_DETECTOR_SERIAL", raising=False)
    return SimpleNamespace(requests=requests, items=items, state=state)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("query", ["detectors", "bindable", "linked"])
def test_successful_query(wired, monkeypatch, capsys, mode, query):
    # Env detector serial is present for every query; only ``linked``
    # is allowed to send it as a parameter.
    monkeypatch.setenv("EZVIZ_DETECTOR_SERIAL", DETECTOR_SERIAL)

    assert example.main(["--mode", mode, "--query", query]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert MARKER not in captured.out
    assert "INNER" not in captured.out
    assert json.loads(captured.out) == {
        "region": "overseas",
        "mode": mode,
        "query": query,
        "status": "ok",
        "returned_items": len(wired.items),
    }

    assert len(wired.requests) == 2
    (auth_request, _), (business_request, business_pairs) = wired.requests

    assert auth_request.method == "POST"
    assert auth_request.url.path == AUTH_PATH
    assert f"{auth_request.url.scheme}://{auth_request.url.host}" == AUTH_ORIGIN

    assert business_request.method == "POST"
    assert business_request.url.path == QUERY_PATHS[query]
    origin = f"{business_request.url.scheme}://{business_request.url.host}"
    assert origin == API_ORIGIN
    assert not business_request.url.query

    expected = [("accessToken", TOKEN), ("deviceSerial", A1_SERIAL)]
    if query == "linked":
        expected.append(("detectorSerial", DETECTOR_SERIAL))
    assert sorted(business_pairs) == sorted(expected)


def test_empty_result_counts_zero(wired, capsys):
    # ``bindable`` run without EZVIZ_DETECTOR_SERIAL: not required.
    wired.items.clear()
    assert example.main(["--mode", "async", "--query", "bindable"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "overseas",
        "mode": "async",
        "query": "bindable",
        "status": "ok",
        "returned_items": 0,
    }


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_help_stops_before_config(argv, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(example, "load_config", lambda *a, **k: calls.append(1))
    _block_clients(monkeypatch)
    assert example.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == USAGE_LINE
    assert captured.err == ""
    assert calls == []


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "sync"],
        ["--mode", "sync", "--query", "detectors", "--extra"],
        ["--mode", "bogus", "--query", "detectors"],
        ["--mode", "sync", "--query", "bogus"],
        ["--query", "detectors", "--mode", "sync"],
    ],
)
def test_bad_flags_stop_before_config(argv, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(example, "load_config", lambda *a, **k: calls.append(1))
    _block_clients(monkeypatch)
    assert example.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == USAGE_LINE
    assert calls == []


def test_domestic_region_fails_before_http(wired, monkeypatch, capsys):
    monkeypatch.setattr(example, "load_config", lambda: _config(region="domestic"))
    assert example.main(["--mode", "sync", "--query", "detectors"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR_LINE
    assert wired.requests == []


def test_missing_device_serial_fails_before_http(wired, monkeypatch, capsys):
    monkeypatch.delenv("EZVIZ_DEVICE_SERIAL", raising=False)
    assert example.main(["--mode", "sync", "--query", "detectors"]) == 1
    assert capsys.readouterr().err == ERROR_LINE
    assert wired.requests == []


def test_missing_detector_serial_fails_before_http(wired, capsys):
    # EZVIZ_DETECTOR_SERIAL deleted by the fixture; required for linked.
    assert example.main(["--mode", "async", "--query", "linked"]) == 1
    assert capsys.readouterr().err == ERROR_LINE
    assert wired.requests == []


@pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
def test_interrupted_returns_130(exc, monkeypatch, capsys):
    def boom():
        raise exc()

    monkeypatch.setattr(example, "load_config", boom)
    _block_clients(monkeypatch)
    assert example.main(["--mode", "sync", "--query", "detectors"]) == 130
    assert capsys.readouterr().err == ERROR_LINE


@pytest.mark.parametrize("exc", [EzvizError, ValueError, OSError])
def test_expected_exceptions_return_1(exc, monkeypatch, capsys):
    def boom():
        raise exc("synthetic failure")

    monkeypatch.setattr(example, "load_config", boom)
    _block_clients(monkeypatch)
    assert example.main(["--mode", "sync", "--query", "detectors"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR_LINE


def test_failed_auth_returns_1(wired, capsys):
    wired.state["auth_code"] = "500"
    assert example.main(["--mode", "sync", "--query", "detectors"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR_LINE
    assert len(wired.requests) == 1
    assert wired.requests[0][0].url.path == AUTH_PATH
