import asyncio
import json
import os

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.cloud_task_files as cli

USAGE = "usage: python -m examples.cloud_task_files --mode sync|async\n"
ERRMSG = "error: cloud task files example failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_TASK_ID": " TASK_PRIVATE+/%&= 项 ",
    "EZVIZ_APP_KEY": "SynApp",
    "EZVIZ_APP_SECRET": "SynSecret",
}


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


@pytest.fixture()
def rig(monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    state = _Rig()
    monkeypatch.setattr(cli, "Client", state.wrap(cli.Client, "sync"))
    monkeypatch.setattr(cli, "AsyncClient", state.wrap(cli.AsyncClient, "async"))
    return state


def _dead(*args, **kwargs):
    raise AssertionError("load_config must not run")


@pytest.mark.parametrize(
    "argv,code,so,se",
    [
        (["-h"], 0, USAGE, ""),
        (["--help"], 0, USAGE, ""),
        ([], 1, "", USAGE),
        (["--mode", "sync", "--mode", "async"], 1, "", USAGE),
        (["--mode", "sync", "--task-id", "TASK_PRIVATE"], 1, "", USAGE),
        (["--mode"], 1, "", USAGE),
        (["--mode", "bogus"], 1, "", USAGE),
        (["--write"], 1, "", USAGE),
        (["--mode", "sync", "--write"], 1, "", USAGE),
    ],
)
def test_usage(rig, capsys, monkeypatch, argv, code, so, se):
    monkeypatch.setattr(cli, "load_config", _dead)
    assert cli.main(argv) == code
    out = capsys.readouterr()
    assert (out.out, out.err) == (so, se)
    assert rig.calls == rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize(
    "over,drop,task",
    [
        ({"EZVIZ_REGION": "moon"}, None, None),
        ({}, "EZVIZ_REGION", None),
        ({}, "EZVIZ_APP_KEY", None),
        ({"EZVIZ_REGION": "overseas"}, None, None),
        ({}, "EZVIZ_APP_SECRET", None),
        ({}, None, ""),
        ({}, None, "   "),
        ({}, "EZVIZ_TASK_ID", None),
        ({}, None, "p\ud800q"),
    ],
)
def test_bad_env(rig, capsys, monkeypatch, over, drop, task):
    for k, v in over.items():
        monkeypatch.setenv(k, v)
    if drop:
        monkeypatch.delenv(drop, raising=False)
    if task is not None:
        monkeypatch.setenv("EZVIZ_TASK_ID", task)
    assert cli.main(["--mode", "sync"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"meta": {"code": 10031, "message": "UPSTREAM_PRIVATE"}, "data": []},
        {"meta": {"code": 200, "message": "UPSTREAM_PRIVATE"}, "data": {}},
        {
            "meta": {"code": 200, "message": "UPSTREAM_PRIVATE"},
            "data": [{"fileId": "FILE_PRIVATE", "fileSize": True}],
        },
    ],
    ids=["denied", "bad-shape", "invalid-file-size"],
)
def test_query_fail(rig, capsys, mode, body):
    rig.biz_hook = lambda request: httpx.Response(200, json=body)
    assert cli.main(["--mode", mode]) == 1
    out = capsys.readouterr()
    assert (
        (out.out, out.err) == ("", ERRMSG)
        and len(rig.auth_requests) == 1
        and len(rig.biz_requests) == 1
    )


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt()), ("async", asyncio.CancelledError())]
)
def test_interrupt(rig, capsys, mode, exc):
    def cancel(request):
        raise exc

    rig.biz_hook = cancel
    assert cli.main(["--mode", mode]) == 130
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", ERRMSG)
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


def test_unexpected_error_propagates(rig, capsys):
    def explode(request):
        raise RuntimeError("private bug")

    rig.biz_hook = explode
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
                "fileId": "FILE_PRIVATE",
                "expireTime": None,
                "fileType": 73,
                "downloadUrls": ["https://media.invalid/PRIVATE_PATH", {"future": "EXT_PRIVATE"}],
                "resultSpaceId": None,
            }
        ],
        [{"fileId": "FIRST_PRIVATE"}, {"fileId": "SECOND_PRIVATE"}],
    ],
    ids=["empty", "one-row", "multiple-returned"],
)
def test_ok(rig, capsys, mode, rows):
    rig.biz_hook = lambda request: httpx.Response(
        200,
        json={"meta": {"code": 200, "message": "UPSTREAM_PRIVATE", "moreInfo": None}, "data": rows},
    )
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    assert out.err == "" and out.out.endswith("\n") and out.out.count("\n") == 1
    payload = json.loads(out.out)
    assert payload == {"items": len(rows)} and type(payload["items"]) is int
    [(kind, key, secret, region)] = rig.calls
    assert (kind, key, secret) == (mode, "SynApp", "SynSecret") and region.scope == "domestic"
    [auth] = rig.auth_requests
    [biz] = rig.biz_requests
    assert auth.method == "POST" and auth.url.path == "/api/lapp/token/get"
    check_request(biz, ENV["EZVIZ_TASK_ID"])


def check_request(request, task_id):
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "open.ys7.com"
    assert request.url.path == "/api/v3/open/cloud/task/files"
    assert sorted(request.url.params.multi_items()) == sorted(
        [
            ("accessToken", "TOK1"),
            ("taskId", task_id),
            ("pageNumber", "0"),
            ("pageSize", "1"),
            ("hasUrl", "false"),
        ]
    )
    assert not request.content
    assert "content-type" not in request.headers
    assert "accesstoken" not in request.headers
