"""Offline tests: examples.device_add_links.main via real clients + MockTransport."""

import json
from types import SimpleNamespace

import httpx
import pytest

import examples.device_add_links as mod
from ezviz_unified import AsyncClient, Client, RegionConfig

USAGE = "usage: python -m examples.device_add_links --mode sync|async --query records|links\n"
ERROR = "error: device add lookup failed\n"
PATHS = {
    "records": "/api/service/device/add/tokenNote",
    "links": "/api/service/device/add/tokenUrls",
}
ROWS = {
    "records": [{"id": 7, "bindTime": 1700000000000}],
    "links": [{"id": "7", "expired": False, "url": "https://example.invalid/x"}],
}


def _config(region="domestic", region_config=None):
    return SimpleNamespace(
        app_key="SYNTH_APP",
        app_secret="SYNTH_SECRET",
        region=region,
        region_config=region_config or RegionConfig.china(),
    )


def _handler(query, calls, override=None):
    def handle(request):
        calls.append(request)
        if request.url.path == "/api/lapp/token/get":
            assert request.method == "POST" and request.url.host == "open.ys7.com"
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "data": {
                        "accessToken": "TOK1",
                        "expireTime": 9999999999999,
                        "areaDomain": "https://open.ys7.com",
                    },
                },
            )
        assert request.method == "GET"
        assert (request.url.scheme, request.url.host, request.url.path) == (
            "https",
            "open.ys7.com",
            PATHS[query],
        )
        assert request.headers.get("accessToken") == "TOK1"
        assert dict(request.url.params) == {"pageSize": "1"}
        assert request.content == b"" and "content-type" not in request.headers
        return override or httpx.Response(
            200, json={"meta": {"code": 200, "message": "ok"}, "data": ROWS[query]}
        )

    return handle


def _factory(cls, handler):
    def make(*args, **kwargs):
        assert args[:2] == ("SYNTH_APP", "SYNTH_SECRET")
        assert isinstance(kwargs.get("region"), RegionConfig)
        kwargs["auth_transport"] = httpx.MockTransport(handler)
        kwargs["transport_factory"] = lambda *a, **k: httpx.MockTransport(handler)
        return cls(*args, **kwargs)

    return make


def _never(*a, **k):
    raise AssertionError("must not run")


def _wire(monkeypatch, handler):
    monkeypatch.setattr(mod, "load_config", lambda: _config())
    monkeypatch.setattr(mod, "Client", _factory(Client, handler))
    monkeypatch.setattr(mod, "AsyncClient", _factory(AsyncClient, handler))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("query", ["records", "links"])
def test_ok(monkeypatch, capsys, mode, query):
    calls = []
    _wire(monkeypatch, _handler(query, calls))
    assert mod.main(["--mode", mode, "--query", query]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {
        "region": "domestic",
        "mode": mode,
        "query": query,
        "status": "ok",
        "returned_items": 1,
    }
    assert out.err == ""
    assert [r.url.path for r in calls] == ["/api/lapp/token/get", PATHS[query]]
    for leak in ("note", "serial", "url", "cursor", "TOK1", "example.invalid"):
        assert leak not in out.out + out.err


def test_empty_rows(monkeypatch, capsys):
    calls = []
    empty = httpx.Response(200, json={"meta": {"code": 200, "message": "ok"}, "data": []})
    _wire(monkeypatch, _handler("links", calls, empty))
    assert mod.main(["--mode", "sync", "--query", "links"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["returned_items"] == 0 and out.err == ""


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_help(monkeypatch, capsys, argv):
    monkeypatch.setattr(mod, "load_config", _never)
    assert mod.main(argv) == 0
    assert capsys.readouterr() == (USAGE, "")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["-x"],
        ["--mode", "bad", "--query", "records"],
        ["--mode", "sync", "--query", "bad"],
        ["--query", "links", "--mode", "sync"],
        ["--mode", "sync", "--query", "links", "z"],
    ],
)
def test_bad_args(monkeypatch, capsys, argv):
    monkeypatch.setattr(mod, "load_config", _never)
    assert mod.main(argv) == 1
    assert capsys.readouterr() == ("", USAGE)


def test_overseas(monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_config", lambda: _config("overseas", RegionConfig.overseas()))
    monkeypatch.setattr(mod, "Client", _never)
    monkeypatch.setattr(mod, "AsyncClient", _never)
    assert mod.main(["--mode", "sync", "--query", "records"]) == 1
    assert capsys.readouterr() == ("", ERROR)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, 403),
        (200, 10031),
    ],
    ids=["http403", "biz10031"],
)
def test_denied(monkeypatch, capsys, mode, status, code):
    calls = []
    override = httpx.Response(status, json={"meta": {"code": code, "message": "x"}})
    _wire(monkeypatch, _handler("records", calls, override))
    assert mod.main(["--mode", mode, "--query", "records"]) == 1
    assert capsys.readouterr() == ("", ERROR)
    assert len(calls) == 2


def test_interrupt(monkeypatch, capsys):
    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "load_config", lambda: _config())
    monkeypatch.setattr(mod, "Client", boom)
    assert mod.main(["--mode", "sync", "--query", "links"]) == 130
    assert capsys.readouterr() == ("", ERROR)
