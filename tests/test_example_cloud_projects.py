"""Offline contract tests for ``examples.cloud_projects.main`` (synthetic env only).

The default ``--mode sync|async`` path is fully simulated: the real config
loader is forbidden, every ``EZVIZ_*`` environment read is denied and the only
responses come from the ``MockTransport`` handlers shipped with the example.
``_Sim``, ``_install_spy`` and ``_strings`` are shared with the sibling
``test_example_cloud_*`` modules.
"""

import asyncio
import json
import os

import httpx
import pytest
from test_example_vod_task_files import (
    _dead,
    _Env,
    _offline_guard,  # noqa: F401  # autouse fixture
)

import examples._common as common
import examples.cloud_projects as cli

USAGE = "usage: python -m examples.cloud_projects --mode sync|async\n"
ERRMSG = "error: cloud projects example failed\n"


class _Sim:
    """Requests observed at ``MockTransport`` plus a GET-only business hook."""

    def __init__(self):
        self.requests = []
        self.responses = []
        self.biz_hook = None


def _install_spy(monkeypatch):
    """Record every in-process ``MockTransport`` request/response.

    With ``sim.biz_hook`` set, GET requests are answered by the hook while
    the canned auth POST keeps flowing through the shipped handler. With the
    hook unset both requests exercise the shipped handlers.
    """
    sim = _Sim()

    def record(self, request):
        sim.requests.append(request)
        if request.method == "GET" and sim.biz_hook is not None:
            response = sim.biz_hook(request)
        else:
            response = original_sync(self, request)
        sim.responses.append(response)
        return response

    async def record_async(self, request):
        sim.requests.append(request)
        if request.method == "GET" and sim.biz_hook is not None:
            response = sim.biz_hook(request)
        else:
            response = await original_async(self, request)
        sim.responses.append(response)
        return response

    original_sync = httpx.MockTransport.handle_request
    original_async = httpx.MockTransport.handle_async_request
    monkeypatch.setattr(httpx.MockTransport, "handle_request", record)
    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", record_async)
    return sim


def _strings(value):
    """Yield every string leaf inside a decoded JSON document."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.fixture()
def sim(monkeypatch):
    """Forbidden config/env plus a recorder on the in-process MockTransports."""
    monkeypatch.setattr(common, "load_config", _dead)
    monkeypatch.setattr(cli, "load_config", _dead, raising=False)
    monkeypatch.setattr(os, "environ", _Env())
    return _install_spy(monkeypatch)


@pytest.mark.parametrize(
    "argv,code,so,se",
    [
        (["-h"], 0, USAGE, ""),
        (["--help"], 0, USAGE, ""),
        ([], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--mode"], 1, "", USAGE),
        (["--mode", "bogus"], 1, "", USAGE),
        (["--write"], 1, "", USAGE),
        (["--mode", "sync", "--write"], 1, "", USAGE),
    ],
)
def test_usage(sim, capsys, argv, code, so, se):
    assert cli.main(argv) == code
    out = capsys.readouterr()
    assert (out.out, out.err) == (so, se)
    assert sim.requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "over,drop",
    [
        ({"EZVIZ_REGION": "moon"}, ()),
        ({}, ("EZVIZ_REGION",)),
        ({}, ("EZVIZ_APP_KEY",)),
        ({"EZVIZ_REGION": "overseas"}, ()),
        ({}, ("EZVIZ_APP_SECRET",)),
        ({"EZVIZ_FUTURE_FLAG": "yes"}, ()),
    ],
)
def test_env_ignored(sim, capsys, mode, over, drop):
    """Hostile, missing or irrelevant operator env must never be read."""
    for key, value in over.items():
        os.environ[key] = value
    for key in drop:
        os.environ.pop(key, None)
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    assert out.err == "" and json.loads(out.out) == {"items": 1}
    assert len(sim.requests) == 2


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_default_simulation(sim, capsys, mode):
    """Default invocation succeeds fully offline via the shipped handlers.

    The true ``Client``/``AsyncClient`` must issue exactly one canned auth
    POST followed by one business GET, and the GET must carry the token the
    shipped auth handler issued — everything observed only by wrapping
    ``httpx.MockTransport``. ``sim.biz_hook`` stays unset so the shipped
    business handler answers the page.
    """
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    assert out.err == "" and out.out.endswith("\n") and out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert payload == {"items": 1} and type(payload["items"]) is int
    [auth, biz] = sim.requests
    assert auth.method == "POST" and auth.url.path == "/api/lapp/token/get"
    check_request(biz)
    token = dict(biz.url.params.multi_items())["accessToken"]
    assert token in set(_strings(json.loads(sim.responses[0].content)))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"meta": {"code": 10031, "message": "UPSTREAM_PRIVATE"}, "data": []},
        {"meta": {"code": 200, "message": "UPSTREAM_PRIVATE"}, "data": {}},
        {
            "meta": {"code": 200, "message": "UPSTREAM_PRIVATE"},
            "data": [{"projectId": "PROJECT_PRIVATE", "expireDays": True}],
        },
    ],
    ids=["denied", "bad-shape", "invalid-expire-days"],
)
def test_query_fail(sim, capsys, mode, body):
    sim.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode]) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG) and "PRIVATE" not in out.err
    [auth, biz] = sim.requests
    assert auth.method == "POST" and biz.method == "GET"
    check_request(biz)


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())]
)
def test_interrupt(sim, capsys, mode, exc):
    def cancel(request):
        raise exc

    sim.biz_hook = cancel
    assert cli.main(["--mode", mode]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    [auth, biz] = sim.requests
    assert auth.method == "POST" and biz.method == "GET"


def test_unexpected_error_propagates(sim, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    sim.biz_hook = explode
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            {
                "projectId": "PROJECT_PRIVATE",
                "projectName": "NAME_PRIVATE",
                "expireDays": 30,
                "ext": {"future": "EXT_PRIVATE"},
            }
        ],
        [{"projectId": "FIRST_PRIVATE"}, {"projectId": "SECOND_PRIVATE"}],
    ],
    ids=["empty", "one-row", "multiple-returned"],
)
def test_ok(sim, capsys, mode, rows):
    sim.biz_hook = lambda request: httpx.Response(
        200,
        json={"meta": {"code": 200, "message": "UPSTREAM_PRIVATE", "moreInfo": None}, "data": rows},
    )
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    assert out.err == "" and out.out.endswith("\n") and out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert payload == {"items": len(rows)} and type(payload["items"]) is int
    [auth, biz] = sim.requests
    assert auth.method == "POST" and auth.url.path == "/api/lapp/token/get"
    check_request(biz)


def check_request(request):
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == "/api/open/cloud/v1/projects"
    items = request.url.params.multi_items()
    assert sorted(key for key, _ in items) == ["accessToken", "pageNumber", "pageSize"]
    params = dict(items)
    assert params["pageNumber"] == "0" and params["pageSize"] == "1"
    assert isinstance(params["accessToken"], str) and params["accessToken"]
    assert not request.content
    assert "content-type" not in request.headers
    assert "accesstoken" not in request.headers
