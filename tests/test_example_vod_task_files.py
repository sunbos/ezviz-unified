"""Offline contract tests for ``examples.vod_task_files.main`` (synthetic env only).

The example's default ``--mode sync|async`` path is fully simulated: it must
succeed with the real config loader forbidden, no environment access and no
network — the canned ``MockTransport`` page is the only response.
"""

import asyncio
import json
import os
from urllib.parse import quote

import httpx
import pytest

import examples._common as common
import examples.vod_task_files as cli
from ezviz_unified.vod import AsyncVod, Vod

USAGE = "usage: python -m examples.vod_task_files --mode sync|async\n"
ERRMSG = "error: vod task files example failed\n"
PATH_PREFIX = "/api/service/open/cloud/vod/task/files/"


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    def spy(*args, **kwargs):
        pytest.fail("forbidden external call")

    for target in (
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
        "socket.create_connection",
        "webbrowser.open",
        "webbrowser.open_new",
        "webbrowser.open_new_tab",
    ):
        monkeypatch.setattr(target, spy)


def _dead(*args, **kwargs):
    raise AssertionError("load_config must not run")


class _Env(dict):
    """os.environ replacement that traps EZVIZ_ reads and enumeration."""

    def _trap(self, key):
        assert not str(key).startswith("EZVIZ_")

    def get(self, key, default=None):
        self._trap(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._trap(key)
        return super().__getitem__(key)

    def _bulk(self, *args, **kwargs):
        raise AssertionError("unexpected environment enumeration")

    __iter__ = keys = items = values = copy = _bulk

    def __contains__(self, key):
        self._trap(key)
        return super().__contains__(key)


@pytest.fixture()
def sim(monkeypatch):
    """Forbidden config/env plus a recorder on the in-process MockTransports."""
    # The real loader is killed at the source so no import path can reach it;
    # the module attribute is patched too in case a future edit re-imports it.
    monkeypatch.setattr(common, "load_config", _dead)
    monkeypatch.setattr(cli, "load_config", _dead, raising=False)
    # ``from os import environ`` aliases keep the real object, so delegate
    # its keyed and bulk access to the sentinel swapped into os.environ.
    sentinel = _Env()
    environ_cls = os.environ.__class__

    def _sentinel_getitem(self, key):
        return sentinel[key]

    def _sentinel_iter(self):
        return iter(sentinel)

    monkeypatch.setattr(environ_cls, "__getitem__", _sentinel_getitem)
    monkeypatch.setattr(environ_cls, "__iter__", _sentinel_iter)
    monkeypatch.setattr(os, "environ", sentinel)
    requests = []

    def record(self, request):
        requests.append(request)
        return original_sync(self, request)

    async def record_async(self, request):
        requests.append(request)
        return await original_async(self, request)

    original_sync = httpx.MockTransport.handle_request
    original_async = httpx.MockTransport.handle_async_request
    monkeypatch.setattr(httpx.MockTransport, "handle_request", record)
    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", record_async)
    return requests


@pytest.mark.parametrize(
    "argv,code,so,se",
    [
        (["-h"], 0, USAGE, ""),
        (["--help"], 0, USAGE, ""),
        ([], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--mode", "sync", "--task-id", "T"], 1, "", USAGE),
        (["--mode"], 1, "", USAGE),
        (["--mode", "bogus"], 1, "", USAGE),
        (["--write"], 1, "", USAGE),
    ],
)
def test_usage(sim, capsys, argv, code, so, se):
    assert cli.main(argv) == code
    out = capsys.readouterr()
    assert (out.out, out.err) == (so, se)
    assert sim == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_demo_ok(sim, capsys, monkeypatch, mode):
    """Default path succeeds offline with the loader/env forbidden."""
    calls = []
    cls = Vod if mode == "sync" else AsyncVod
    original = cls.task_files

    def spy(self, *args, **kw):
        calls.append((self._client.region.scope, args, kw))
        return original(self, *args, **kw)

    async def spy_async(self, *args, **kw):
        calls.append((self._client.region.scope, args, kw))
        return await original(self, *args, **kw)

    monkeypatch.setattr(cls, "task_files", spy if mode == "sync" else spy_async)
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    assert out.err == "" and out.out.endswith("\n") and out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert payload == {"items": len(cli._ROWS)} and type(payload["items"]) is int
    [(scope, args, kw)] = calls
    assert scope == "domestic" and args == (cli._TASK_ID,) and kw == {}
    auth = [r for r in sim if r.url.path == "/api/lapp/token/get"]
    biz = [r for r in sim if r.url.path != "/api/lapp/token/get"]
    assert len(auth) == 1 and len(biz) == 1
    request = biz[0]
    assert request.method == "GET"
    assert request.url.scheme == "https" and request.url.host == "open.ys7.com"
    # taskId is one encoded path segment; the token is in the header.
    assert request.url.raw_path.decode("ascii") == PATH_PREFIX + quote(cli._TASK_ID, safe="")
    assert request.headers["accessToken"] == cli._ACCESS_TOKEN
    assert len(request.url.params.multi_items()) == 0
    assert "accessToken" not in request.url.params
    assert "taskId" not in request.url.params
    assert not request.content
    assert "content-type" not in request.headers


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"meta": {"code": 10031, "message": "UPSTREAM_PRIVATE"}, "data": []},
        {"meta": {"code": 200, "message": "UPSTREAM_PRIVATE"}, "data": {}},
        {"meta": {"code": 200, "message": "UPSTREAM_PRIVATE"}, "data": ["not-a-row"]},
    ],
    ids=["denied", "bad-shape-dict", "bad-shape-row"],
)
def test_demo_fail(sim, capsys, monkeypatch, mode, body):
    def bad_business(request):
        return httpx.Response(200, json=body)

    monkeypatch.setattr(cli, "_business_handler", bad_business)
    assert cli.main(["--mode", mode]) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG) and "PRIVATE" not in out.err
    biz = [r for r in sim if r.url.path != "/api/lapp/token/get"]
    assert len(biz) == 1


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())]
)
def test_demo_interrupt(sim, capsys, monkeypatch, mode, exc):
    def cancel(request):
        raise exc

    monkeypatch.setattr(cli, "_business_handler", cancel)
    assert cli.main(["--mode", mode]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    biz = [r for r in sim if r.url.path != "/api/lapp/token/get"]
    assert len(biz) == 1


def test_demo_unexpected_error_propagates(sim, capsys, monkeypatch):
    def explode(request):
        raise RuntimeError("private bug")

    monkeypatch.setattr(cli, "_business_handler", explode)
    with pytest.raises(RuntimeError, match="private bug"):
        cli.main(["--mode", "sync"])
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", "")
