"""Synthetic tests for ``examples.third_party_project``.

Each run drives the real ``ezviz_unified`` clients while all HTTP traffic
is intercepted by ``httpx.MockTransport`` handlers, so no real network I/O
or persistence occurs.  Importing the example module performs no
configuration or network access.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

import examples.third_party_project as example
from ezviz_unified import AsyncClient, Client, PermissionDeniedError, RegionConfig

_USAGE = "usage: python -m examples.third_party_project --mode sync|async --protocol dhreg|tiandy|huawei|jt808"
_ERROR = "error: third-party project query failed"
_PROJECT_PATH = "/api/service/thirdproto/tiandy/project"
_TOKEN = "TOK1"

_AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": _TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}
_BUSINESS_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {
        "productKey": "PTEST",
        "createTime": 7,
        "dasDomain": "sample.invalid",
        "dasPort": 8000,
    },
}


def _config(region="domestic"):
    return SimpleNamespace(
        app_key="APP_KEY",
        app_secret="APP_SECRET",
        region=region,
        region_config=RegionConfig.china() if region == "domestic" else RegionConfig.overseas(),
    )


def _auth(seen):
    def handler(request):
        request.read()
        seen.append(request)
        return httpx.Response(200, json=_AUTH_BODY)

    return handler


def _business(seen, body=None, error=None):
    def handler(request):
        request.read()
        seen.append(request)
        if error is not None:
            raise error
        return httpx.Response(200, json=body if body is not None else _BUSINESS_BODY)

    return handler


def _install(monkeypatch, config, auth_handler, business_handler):
    """Patch ``example.load_config`` and the SDK client factories."""
    created = []
    monkeypatch.setattr(example, "load_config", lambda: config)

    def client_factory(app_key, app_secret, *, region):
        created.append((app_key, app_secret, region))
        return Client(
            app_key,
            app_secret,
            region=region,
            auth_transport=httpx.MockTransport(auth_handler),
            transport_factory=lambda origin: httpx.MockTransport(business_handler),
        )

    def async_client_factory(app_key, app_secret, *, region):
        created.append((app_key, app_secret, region))
        return AsyncClient(
            app_key,
            app_secret,
            region=region,
            auth_transport=httpx.MockTransport(auth_handler),
            transport_factory=lambda origin: httpx.MockTransport(business_handler),
        )

    monkeypatch.setattr(example, "Client", client_factory)
    monkeypatch.setattr(example, "AsyncClient", async_client_factory)
    return created


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_project_query_success(monkeypatch, capsys, mode):
    config = _config()
    auth_seen, business_seen = [], []
    created = _install(monkeypatch, config, _auth(auth_seen), _business(business_seen))

    rc = example.main(["--mode", mode, "--protocol", "tiandy"])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "protocol": "tiandy",
    }
    for private in ("APP_KEY", "APP_SECRET", _TOKEN, "PTEST", "sample.invalid"):
        assert private not in captured.out

    assert created == [("APP_KEY", "APP_SECRET", config.region_config)]
    assert len(auth_seen) == 1
    assert len(business_seen) == 1
    request = business_seen[0]
    assert request.method == "GET"
    assert request.url.path == _PROJECT_PATH
    assert request.url.query in (b"", "")
    assert request.content == b""
    assert "content-type" not in request.headers
    assert request.headers.get_list("accessToken") == [_TOKEN]


@pytest.mark.parametrize("argv", (["-h"], ["--help"]))
def test_help_prints_usage_without_loading(monkeypatch, capsys, argv):
    calls = []
    monkeypatch.setattr(example, "load_config", lambda: calls.append("loaded"))

    assert example.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == _USAGE + "\n"
    assert captured.err == ""
    assert calls == []


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["--mode"],
        ["--mode", "sync"],
        ["--mode", "sync", "--protocol"],
        ["--mode", "invalid", "--protocol", "tiandy"],
        ["--mode", "sync", "--protocol", "invalid"],
        ["--protocol", "tiandy", "--mode", "sync"],
        ["--mode", "sync", "--protocol", "tiandy", "extra"],
        ["-h", "extra"],
        ["--help", "extra"],
    ),
)
def test_invalid_arguments_print_usage(monkeypatch, capsys, argv):
    calls = []
    monkeypatch.setattr(example, "load_config", lambda: calls.append("loaded"))

    assert example.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _USAGE + "\n"
    assert calls == []


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_overseas_region_rejected_before_network(monkeypatch, capsys, mode):
    auth_seen, business_seen = [], []
    created = _install(
        monkeypatch,
        _config(region="overseas"),
        _auth(auth_seen),
        _business(business_seen),
    )

    assert example.main(["--mode", mode, "--protocol", "tiandy"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
    assert created == []
    assert auth_seen == []
    assert business_seen == []


@pytest.mark.parametrize("mode", ("sync", "async"))
def test_permission_failure_reports_fixed_error(monkeypatch, capsys, mode):
    auth_seen, business_seen = [], []
    _install(
        monkeypatch,
        _config(),
        _auth(auth_seen),
        _business(business_seen, error=PermissionDeniedError("synthetic denial", code=10031)),
    )

    assert example.main(["--mode", mode, "--protocol", "tiandy"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"


@pytest.mark.parametrize("exc", (KeyboardInterrupt, asyncio.CancelledError))
@pytest.mark.parametrize("mode", ("sync", "async"))
def test_cancellation_reports_fixed_error(monkeypatch, capsys, mode, exc):
    auth_seen, business_seen = [], []
    _install(
        monkeypatch,
        _config(),
        _auth(auth_seen),
        _business(business_seen, error=exc()),
    )

    assert example.main(["--mode", mode, "--protocol", "tiandy"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _ERROR + "\n"
