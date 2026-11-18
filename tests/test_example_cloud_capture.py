"""Offline MockTransport tests for ``examples/cloud_capture.py``.

Every test drives real ``Client``/``AsyncClient`` instances; only the
HTTP transport layer is mocked -- SDK business methods are never
patched. All values are synthetic and nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import urllib.parse
from datetime import datetime

import httpx
import pytest

import examples.cloud_capture as ec
from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    Client,
    RegionConfig,
    StorageFileQuery,
    TransportError,
)

APP_KEY = "APP_KEY_TEST"
APP_SECRET = "APP_SECRET_TEST"
TOKEN = "TEST_TOKEN"
TASK_ID = "TASK_ABC_123"
NODE_A = "NODE_SYNTH_A"
NODE_OTHER = "NODE_SYNTH_OTHER"

INTERVAL_PATH = "/api/v3/open/cloud/video/frame/interval/start"
TIMING_PATH = "/api/v3/open/cloud/video/frame/timing/start"
TASK_PATH = "/api/v3/open/cloud/task/"
SEARCH_PATH = "/api/service/open/storage/engine/file/listById"
DOWNLOAD_PATH = "/api/service/open/storage/engine/file/downloadurls"

AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}
CREATE_DATA = {"taskId": TASK_ID, "extraCreateField": "kept"}
TASK_DATA = {"taskId": TASK_ID, "taskStatus": 0, "extraTaskField": "kept"}
# A non-target node is listed before the target on purpose: tests must pick
# the row matching NODE_A, never the first row.
SEARCH_DATA = {
    "hasNext": False,
    "fileList": [{"fileNodeId": NODE_OTHER}, {"fileNodeId": NODE_A}],
}
DOWNLOAD_DATA = [{"fileNodeId": NODE_A, "url": "https://leak.invalid/SECRET_URL"}]


def _meta(data, *, code=200):
    return {"meta": {"code": code, "message": "ok"}, "data": data}


class _Harness:
    """Auth + business transports recording requests; no stubbed SDK methods."""

    def __init__(self, *, business=None, auth=None):
        self.auth_calls: list[httpx.Request] = []
        self.business_calls: list[httpx.Request] = []
        self.factory_calls: list[str] = []
        self._business = business if business is not None else self._business_default
        self._auth = auth if auth is not None else self._auth_default

    def _auth_default(self, request):
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _business_default(self, request):
        path = request.url.path
        if path in (INTERVAL_PATH, TIMING_PATH):
            return httpx.Response(200, json=_meta(dict(CREATE_DATA)))
        if path == TASK_PATH + TASK_ID:
            return httpx.Response(200, json=_meta(dict(TASK_DATA)))
        if path == SEARCH_PATH:
            return httpx.Response(200, json=_meta(dict(SEARCH_DATA)))
        if path == DOWNLOAD_PATH:
            return httpx.Response(200, json=_meta(list(DOWNLOAD_DATA)))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def _handle_auth(self, request):
        self.auth_calls.append(request)
        return self._auth(request)

    def _handle_business(self, request):
        self.business_calls.append(request)
        return self._business(request)

    def _factory(self, origin: str) -> httpx.MockTransport:
        self.factory_calls.append(origin)
        return httpx.MockTransport(self._handle_business)

    def kwargs(self):
        return {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "region": RegionConfig.china(),
            "clock": lambda: 1000.0,
            "auth_transport": httpx.MockTransport(self._handle_auth),
            "transport_factory": self._factory,
        }

    def sync_client(self) -> Client:
        return Client(**self.kwargs())

    def async_client(self) -> AsyncClient:
        return AsyncClient(**self.kwargs())


def _form(request) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _query(request) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(request.url.query.decode("utf-8"), keep_blank_values=True)


def _check_create(request, path, fields):
    assert request.method == "POST"
    assert request.url.path == path
    assert not request.url.query
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert "accessToken" not in request.headers
    form = _form(request)
    wanted = {key: [value] for key, value in fields.items()}
    wanted["accessToken"] = [TOKEN]
    assert form == wanted


def _check_task(request, task_id=TASK_ID):
    assert request.method == "GET"
    assert request.url.path == TASK_PATH + task_id
    assert request.content == b""
    assert "content-type" not in request.headers
    assert "accessToken" not in request.headers
    assert _query(request) == {"accessToken": [TOKEN]}


def _check_search(request, fields):
    assert request.method == "POST"
    assert request.url.path == SEARCH_PATH
    assert not request.url.query
    assert request.headers["accessToken"] == TOKEN
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert _form(request) == {key: [value] for key, value in fields.items()}


def _check_download(request, params):
    assert request.method == "GET"
    assert request.url.path == DOWNLOAD_PATH
    assert request.content == b""
    assert "accessToken" not in request.headers
    wanted = {key: [value] for key, value in params.items()}
    wanted["accessToken"] = [TOKEN]
    assert _query(request) == wanted


def _set_env(monkeypatch, **overrides):
    values = {
        "EZVIZ_APP_KEY": APP_KEY,
        "EZVIZ_APP_SECRET": APP_SECRET,
        "EZVIZ_REGION": "domestic",
        "EZVIZ_CLOUD_TASK_ID": TASK_ID,
    }
    values.update(overrides)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _install(monkeypatch, harness, mode):
    """Replace example client constructors with real MockTransport clients."""
    cls = Client if mode == "sync" else AsyncClient
    client = harness.sync_client() if mode == "sync" else harness.async_client()
    monkeypatch.setattr(ec, cls.__name__, lambda *a, **kw: client)
    return client


def _run_sync(harness, fn):
    """Create, use and close one sync client around ``fn(client)``."""
    client = harness.sync_client()
    with client:
        return fn(client)


def _run_async(harness, coro_fn):
    """Create, use and aclose one AsyncClient inside a single event loop."""

    async def drive():
        client = harness.async_client()
        async with client:
            return await coro_fn(client)

    return asyncio.run(drive())


def _run(mode, harness, sync_fn, async_fn):
    """Dispatch one parametrized scenario to the matching client kind."""
    if mode == "sync":
        return _run_sync(harness, sync_fn)
    return _run_async(harness, async_fn)


_INTERVAL_KW = dict(
    channel=1,
    rec_type="cloud",
    start_time=datetime(2026, 1, 5, 8, 0, 0),
    end_time=datetime(2026, 1, 5, 9, 0, 0),
    frame_interval=60,
    result_space_id=900001,
)
_INTERVAL_FORM = {
    "deviceSerial": "C00000000",
    "channelNo": "1",
    "recType": "cloud",
    "startTime": "20260105080000",
    "endTime": "20260105090000",
    "frameInterval": "60",
    "resultSpaceId": "900001",
}
_TIMING_KW = dict(
    channel=1,
    rec_type="local",
    timing_points=[datetime(2026, 1, 5, 8, 30, 0)],
    project_id="SYNTHETIC_PROJECT",
)
_TIMING_FORM = {
    "deviceSerial": "C00000000",
    "channelNo": "1",
    "recType": "local",
    "timingPoints": "20260105083000",
    "projectId": "SYNTHETIC_PROJECT",
}
_SEARCH_QUERY = StorageFileQuery(tag_id=TASK_ID, page_size=1, last_id="")
_SEARCH_FORM = {"tagId": TASK_ID, "pageSize": "1", "lastId": ""}
_DOWNLOAD_PARAMS = {"fileNodeIds": NODE_A}


def _pick_target_nodes(page):
    """Take ``fileNodeId`` only from the row matching the known target.

    The page must hold a non-target row before NODE_A: selecting by match
    proves the caller inspected fileList instead of taking the first row
    or deriving the node id from the task id.
    """
    file_list = page.data["fileList"]
    assert isinstance(file_list, list)
    assert file_list[0]["fileNodeId"] == NODE_OTHER
    return [row["fileNodeId"] for row in file_list if row["fileNodeId"] == NODE_A]


def _chain_sync(client, kind):
    created = (
        client.cloud_capture.create_interval_task("C00000000", **_INTERVAL_KW)
        if kind == "interval"
        else client.cloud_capture.create_timing_task("C00000000", **_TIMING_KW)
    )
    task_id = created.data["taskId"]
    assert isinstance(task_id, str)
    task = ec.query_task_sync(client, task_id)
    query = StorageFileQuery(tag_id=task_id, page_size=1, last_id="")
    page = ec.search_files_sync(client, query)
    urls = ec.download_urls_sync(client, _pick_target_nodes(page))
    return created, task, page, urls


async def _chain_async(client, kind):
    created = await (
        client.cloud_capture.create_interval_task("C00000000", **_INTERVAL_KW)
        if kind == "interval"
        else client.cloud_capture.create_timing_task("C00000000", **_TIMING_KW)
    )
    task_id = created.data["taskId"]
    assert isinstance(task_id, str)
    task = await ec.query_task_async(client, task_id)
    query = StorageFileQuery(tag_id=task_id, page_size=1, last_id="")
    page = await ec.search_files_async(client, query)
    urls = await ec.download_urls_async(client, _pick_target_nodes(page))
    return created, task, page, urls


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("kind", ["interval", "timing"])
def test_full_explicit_flow(mode, kind):
    harness = _Harness()
    assert harness.auth_calls == [] and harness.business_calls == []
    created, task, page, urls = _run(
        mode,
        harness,
        lambda client: _chain_sync(client, kind),
        lambda client: _chain_async(client, kind),
    )
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 4
    create_request, task_request, search_request, download_request = harness.business_calls
    if kind == "interval":
        _check_create(create_request, INTERVAL_PATH, _INTERVAL_FORM)
    else:
        _check_create(create_request, TIMING_PATH, _TIMING_FORM)
    _check_task(task_request)
    _check_search(search_request, _SEARCH_FORM)
    _check_download(download_request, _DOWNLOAD_PARAMS)
    assert harness.factory_calls == ["https://open.ys7.com"]
    for response in (created, task, page, urls):
        assert isinstance(response, ApiResponse)
        assert response.code == 200
        assert isinstance(response.raw, dict)
    assert created.data["taskId"] == TASK_ID
    assert created.data["extraCreateField"] == "kept"
    assert task.data["extraTaskField"] == "kept"
    assert page.data["hasNext"] is False
    assert urls.data[0]["url"] == "https://leak.invalid/SECRET_URL"


def _minimal_sync(client):
    task = ec.query_task_sync(client, TASK_ID)
    page = ec.search_files_sync(client, _SEARCH_QUERY)
    urls = ec.download_urls_sync(client, [NODE_A])
    return task, page, urls


async def _minimal_async(client):
    task = await ec.query_task_async(client, TASK_ID)
    page = await ec.search_files_async(client, _SEARCH_QUERY)
    urls = await ec.download_urls_async(client, [NODE_A])
    return task, page, urls


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_existing_task_chain_skips_create(mode):
    """Pre-existing task id: auth + 3 business calls, nothing else."""
    harness = _Harness()
    _run(mode, harness, _minimal_sync, _minimal_async)
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 3
    _check_task(harness.business_calls[0])
    _check_search(harness.business_calls[1], _SEARCH_FORM)
    _check_download(harness.business_calls[2], _DOWNLOAD_PARAMS)


def test_helper_never_closes_borrowed_client():
    """Helpers reuse the caller's client; no close, one cached auth."""
    harness = _Harness()
    client = harness.sync_client()
    _minimal_sync(client)
    _minimal_sync(client)  # second pass still works: client stays open
    assert len(harness.business_calls) == 6
    assert len(harness.auth_calls) == 1
    client.close()


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_has_next_and_url_flow_stay_manual(mode):
    """hasNext=True triggers no follow-up page; download URLs are never fetched."""
    seen = []

    def business(request):
        seen.append(request.url.path)
        if request.url.path == SEARCH_PATH:
            data = dict(SEARCH_DATA)
            data["hasNext"] = True
            return httpx.Response(200, json=_meta(data))
        return httpx.Response(200, json=_meta(list(DOWNLOAD_DATA)))

    harness = _Harness(business=business)

    def sync(client):
        page = ec.search_files_sync(client, _SEARCH_QUERY)
        urls = ec.download_urls_sync(client, [NODE_A], expire_seconds=3600)
        return page, urls

    async def async_(client):
        page = await ec.search_files_async(client, _SEARCH_QUERY)
        urls = await ec.download_urls_async(client, [NODE_A], expire_seconds=3600)
        return page, urls

    page, urls = _run(mode, harness, sync, async_)
    assert page.data["hasNext"] is True
    assert urls.data[0]["fileNodeId"] == NODE_A
    assert len(harness.auth_calls) == 1
    # Exactly two explicit calls: no auto second page, no media fetch.
    assert seen == [SEARCH_PATH, DOWNLOAD_PATH]
    _check_search(harness.business_calls[0], _SEARCH_FORM)
    _check_download(
        harness.business_calls[1],
        {"fileNodeIds": NODE_A, "expireSeconds": "3600"},
    )


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_search_selectors_and_cursor_sent_verbatim(mode):
    """space_ids + tag_id are both sent (no AND claim); last_id passes through."""
    captured = {}

    def business(request):
        captured["form"] = _form(request)
        return httpx.Response(200, json=_meta(dict(SEARCH_DATA)))

    query = StorageFileQuery(
        tag_id=TASK_ID,
        space_ids=[900001, 900002],
        page_size=5,
        last_id="CURSOR_X9",
    )
    harness = _Harness(business=business)
    _run(
        mode,
        harness,
        lambda client: ec.search_files_sync(client, query),
        lambda client: ec.search_files_async(client, query),
    )
    assert len(harness.business_calls) == 1
    form = captured["form"]
    assert form["tagId"] == [TASK_ID]
    assert form["spaceIds"] == ["900001,900002"]
    assert form["pageSize"] == ["5"]
    assert form["lastId"] == ["CURSOR_X9"]
    assert "pageStart" not in form and "page_start" not in form


def _observe_request(client, captured):
    """Wrap the real ``client.request`` to record each returned ApiResponse.

    The SDK business facades are never touched: the observer sits on the
    client instance only, calls the original request (awaited when async),
    stores its real return value, and passes it through unchanged.
    """
    original = client.request
    if isinstance(client, AsyncClient):

        async def observe_async(operation, **kwargs):
            result = await original(operation, **kwargs)
            captured.append(result)
            return result

        client.request = observe_async
    else:

        def observe_sync(operation, **kwargs):
            result = original(operation, **kwargs)
            captured.append(result)
            return result

        client.request = observe_sync
    return original


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_helper_returns_unmodified_api_response(mode):
    """The helper returns the exact ApiResponse object the real request made."""
    sentinel_data = {"taskId": TASK_ID, "taskStatus": 3, "weird": {"x": [1]}}
    envelope = {"meta": {"code": 200, "message": "ok"}, "data": sentinel_data, "top": 1}
    harness = _Harness(business=lambda request: httpx.Response(200, json=envelope))
    captured: list[ApiResponse] = []

    def sync(client):
        original = _observe_request(client, captured)
        try:
            return ec.query_task_sync(client, TASK_ID)
        finally:
            client.request = original

    async def async_(client):
        original = _observe_request(client, captured)
        try:
            return await ec.query_task_async(client, TASK_ID)
        finally:
            client.request = original

    response = _run(mode, harness, sync, async_)
    # Exactly one real authenticated business request produced one ApiResponse.
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _check_task(harness.business_calls[0])
    assert len(captured) == 1
    # The helper hands back the very object the SDK request returned.
    assert response is captured[0]
    # The envelope survived the wire round-trip by value (unknown and nested
    # fields included); the fixture dict itself was serialized, not shared.
    assert isinstance(response.raw, dict)
    assert response.raw == envelope
    assert response.data == sentinel_data
    # decode_response aliases data to the same mapping object inside raw.
    assert response.raw["data"] is response.data
    assert response.http_status == 200
    assert response.code == 200


def _fail_business_50001(request):
    return httpx.Response(200, json=_meta({}, code=50001))


def _fail_connect(request):
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    "business,error",
    [
        (_fail_business_50001, ApiError),
        (_fail_connect, TransportError),
    ],
    ids=["business-50001", "transport"],
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_helper_error_propagates_with_no_followup(mode, business, error):
    """A failing step raises and issues exactly one business request."""
    harness = _Harness(business=business)

    def sync(client):
        with pytest.raises(error):
            ec.query_task_sync(client, TASK_ID)

    async def async_(client):
        with pytest.raises(error):
            await ec.query_task_async(client, TASK_ID)

    _run(mode, harness, sync, async_)
    # The helper raised without issuing any follow-up request itself.
    assert [r.url.path for r in harness.business_calls] == [TASK_PATH + TASK_ID]


# ---------------------------------------------------------------------------
# CLI tests: real clients behind monkeypatched constructor entry points.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,status,expected",
    [
        ("sync", 0, "completed"),
        ("async", 0, "completed"),
        ("sync", 3, "ended"),
        ("async", 3, "ended"),
    ],
    ids=["sync-completed", "async-completed", "sync-ended", "async-ended"],
)
def test_cli_success_task_states(monkeypatch, capsys, mode, status, expected):
    """Exactly auth + one task query; 0 and 3 stay distinct fixed enums."""
    _set_env(monkeypatch)
    body = _meta({"taskId": TASK_ID, "taskStatus": status})
    harness = _Harness(business=lambda request: httpx.Response(200, json=body))
    _install(monkeypatch, harness, mode)
    assert ec.main(["--mode", mode]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out) == {
        "status": "ok",
        "mode": mode,
        "logical_calls": 1,
        "task_state": expected,
    }
    for leaked in (TASK_ID, TOKEN, APP_KEY, APP_SECRET):
        assert leaked not in out
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1  # no create/search/download on CLI
    _check_task(harness.business_calls[0])


_MISSING = object()


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, "queued"),
        (2, "running"),
        (4, "abnormal_end"),
        (5, "paused"),
        (6, "cancelled"),
        (7, "not_started"),
        (True, "unknown"),
        (1.0, "unknown"),
        ("0", "unknown"),
        (9, "unknown"),
        (-1, "unknown"),
        (None, "unknown"),
        ({"s": 0}, "unknown"),
        (_MISSING, "unknown"),
    ],
)
def test_task_state_whitelist_mapping(value, expected):
    data = {} if value is _MISSING else {"taskStatus": value}
    response = ApiResponse(http_status=200, code=200, data=data)
    assert ec._task_state(response) == expected


def test_cli_unknown_task_state_never_echoes_raw(monkeypatch, capsys):
    _set_env(monkeypatch)
    body = _meta({"taskId": TASK_ID, "taskStatus": "SENTINEL_STATE"})
    harness = _Harness(business=lambda request: httpx.Response(200, json=body))
    _install(monkeypatch, harness, "sync")
    assert ec.main(["--mode", "sync"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["task_state"] == "unknown"
    assert "SENTINEL_STATE" not in out


@pytest.mark.parametrize(
    "data", [[], "text", None, 5, True], ids=["list", "str", "null", "int", "bool"]
)
def test_cli_non_dict_data_is_unexpected_response(monkeypatch, capsys, data):
    _set_env(monkeypatch)
    harness = _Harness(business=lambda request: httpx.Response(200, json=_meta(data)))
    _install(monkeypatch, harness, "sync")
    assert ec.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "unexpected_response"}
    assert len(harness.business_calls) == 1


@pytest.mark.parametrize("missing", ["EZVIZ_APP_KEY", "EZVIZ_APP_SECRET", "EZVIZ_CLOUD_TASK_ID"])
def test_cli_missing_env_is_configuration_error(monkeypatch, capsys, missing):
    _set_env(monkeypatch, **{missing: None})
    calls = []
    monkeypatch.setattr(ec, "Client", lambda *a, **kw: calls.append(kw))
    assert ec.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "configuration_error"}
    assert calls == []  # client is never constructed


def test_cli_overseas_region_rejected_before_client(monkeypatch, capsys):
    _set_env(monkeypatch, EZVIZ_REGION="overseas")
    calls = []
    monkeypatch.setattr(ec, "Client", lambda *a, **kw: calls.append(kw))
    assert ec.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "configuration_error"}
    assert calls == []


def test_cli_whitespace_task_id_is_input_error_zero_http(monkeypatch, capsys):
    """Surrounding spaces are not stripped; SDK validation rejects offline."""
    _set_env(monkeypatch, EZVIZ_CLOUD_TASK_ID="  TASK_ABC_123  ")
    harness = _Harness()
    _install(monkeypatch, harness, "sync")
    assert ec.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "input_error"}
    assert harness.auth_calls == []
    assert harness.business_calls == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "SENTINEL_MODE"],
        ["--mode", "sync", "--SENTINEL_FLAG"],
        ["--SENTINEL_ONLY"],
        [],
    ],
    ids=["bad-choice", "extra-flag", "unknown-only", "missing-mode"],
)
def test_cli_invalid_argv_fixed_input_error(capsys, argv):
    # No env is set: argv failures are reported before any env/client work.
    assert ec.main(argv) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "input_error"}
    assert "SENTINEL" not in err


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_cli_business_code_error_is_sdk_error(monkeypatch, capsys, mode):
    _set_env(monkeypatch)
    body = {"meta": {"code": 50001, "message": "LEAK SENTINEL MSG"}, "data": {}}
    harness = _Harness(business=lambda request: httpx.Response(200, json=body))
    _install(monkeypatch, harness, mode)
    assert ec.main(["--mode", mode]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "sdk_error"}
    for leaked in ("LEAK", "SENTINEL", "50001", TASK_ID, TOKEN, APP_SECRET):
        assert leaked not in err
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1  # error adds no extra query
    _check_task(harness.business_calls[0])


def test_cli_http_503_is_sdk_error(monkeypatch, capsys):
    _set_env(monkeypatch)
    harness = _Harness(business=lambda request: httpx.Response(503, text="SENTINEL upstream"))
    _install(monkeypatch, harness, "sync")
    assert ec.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err) == {"status": "failed", "category": "sdk_error"}
    assert "SENTINEL" not in err and "503" not in err
    assert len(harness.business_calls) == 1


def test_import_and_help_do_no_env_reads_or_network(monkeypatch, capsys):
    real_get = os.environ.get

    def guarded_get(key, default=None):
        if str(key).startswith("EZVIZ_"):
            raise AssertionError("environment read during import/help")
        return real_get(key, default)

    def no_socket(*args, **kwargs):
        raise AssertionError("network access during import/help")

    monkeypatch.setattr(os.environ, "get", guarded_get)
    monkeypatch.setattr(socket, "socket", no_socket)
    importlib.reload(ec)  # importing the module must perform no I/O
    with pytest.raises(SystemExit) as excinfo:
        ec.main(["--help"])
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert "python -m examples.cloud_capture" in out
    assert "EZVIZ_" not in out


# ---------------------------------------------------------------------------
# Docstring regression: the two creation blocks are extracted from the
# reviewed module docstring and executed once each against a synthetic
# recorder (explicitly not a wire client), then the captured call is bound
# through the real SDK to verify the frozen wire shape.
# ---------------------------------------------------------------------------


def _doc_snippet(marker: str) -> str:
    lines = ec.__doc__.splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    snippet = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("    "):
            break
        snippet.append(line[4:] if line.startswith("    ") else "")
    return "\n".join(snippet).strip()


class _DocResponse:
    def __init__(self):
        self.data = {"taskId": TASK_ID}


class _DocClient:
    """Synthetic recorder for doc-snippet exec only -- not a wire client."""

    def __init__(self):
        self.calls = []
        self.cloud_capture = self

    def create_interval_task(self, *args, **kwargs):
        self.calls.append(("interval", args, kwargs))
        return _DocResponse()

    def create_timing_task(self, *args, **kwargs):
        self.calls.append(("timing", args, kwargs))
        return _DocResponse()


def test_doc_interval_block_matches_frozen_signature_and_wire():
    stub = _DocClient()
    exec(
        compile(_doc_snippet("Option A"), "<doc-interval>", "exec"),
        {"client": stub, "datetime": datetime},
    )
    assert len(stub.calls) == 1
    name, args, kwargs = stub.calls[0]
    assert name == "interval"
    assert args == ("C00000000",)
    assert kwargs == {
        "channel": 1,
        "rec_type": "cloud",
        "start_time": datetime(2026, 1, 5, 8, 0, 0),
        "end_time": datetime(2026, 1, 5, 9, 0, 0),
        "frame_interval": 60,
        "result_space_id": 900001,
    }
    # Bind the documented call through the real SDK: frozen wire shape.
    harness = _Harness()
    client = harness.sync_client()
    with client:
        client.cloud_capture.create_interval_task(*args, **kwargs)
    assert len(harness.business_calls) == 1
    _check_create(harness.business_calls[0], INTERVAL_PATH, _INTERVAL_FORM)


def test_doc_timing_block_matches_frozen_signature_and_wire():
    stub = _DocClient()
    exec(
        compile(_doc_snippet("Option B"), "<doc-timing>", "exec"),
        {"client": stub, "datetime": datetime},
    )
    assert len(stub.calls) == 1
    name, args, kwargs = stub.calls[0]
    assert name == "timing"
    assert args == ("C00000000",)
    assert kwargs == {
        "channel": 1,
        "rec_type": "local",
        "timing_points": [datetime(2026, 1, 5, 8, 30, 0)],
        "project_id": "SYNTHETIC_PROJECT",
    }
    harness = _Harness()
    client = harness.sync_client()
    with client:
        client.cloud_capture.create_timing_task(*args, **kwargs)
    assert len(harness.business_calls) == 1
    _check_create(harness.business_calls[0], TIMING_PATH, _TIMING_FORM)
