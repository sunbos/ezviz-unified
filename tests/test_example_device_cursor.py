import asyncio
import json
import os

import httpx
import pytest
from test_example_night_vision import _Rig

import examples.device_cursor as cli

USAGE = "usage: python -m examples.device_cursor --mode sync|async\n"
ERRMSG = "error: device cursor query failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
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
    monkeypatch.delenv("EZVIZ_DEVICE_CURSOR", raising=False)
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
        (["--mode", "sync", "--cursor", "PRIVATE_CURSOR"], 1, "", USAGE),
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
    "over,drop,cursor",
    [
        ({"EZVIZ_REGION": "moon"}, None, None),
        ({}, "EZVIZ_REGION", None),
        ({}, "EZVIZ_APP_KEY", None),
        ({"EZVIZ_REGION": "overseas"}, None, None),
        ({}, "EZVIZ_APP_SECRET", None),
        ({}, None, ""),
        ({}, None, "   "),
        ({}, None, "a\rB"),
    ],
)
def test_bad_env(rig, capsys, monkeypatch, over, drop, cursor):
    for k, v in over.items():
        monkeypatch.setenv(k, v)
    if drop:
        monkeypatch.delenv(drop, raising=False)
    if cursor is not None:
        monkeypatch.setenv("EZVIZ_DEVICE_CURSOR", cursor)
    assert cli.main(["--mode", "sync"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERRMSG
    assert rig.auth_requests == rig.biz_requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "body",
    [
        {"code": "10031", "msg": "PRIVATE_UPSTREAM"},
        {"code": "200", "data": [], "page": {}},
        {
            "code": "200",
            "data": [{"id": "ROW_PRIVATE_A"}, {"id": "ROW_PRIVATE_B"}],
            "page": {"nextId": "NEXT_PRIVATE"},
        },
    ],
    ids=["denied", "missing-next-id", "too-many-rows"],
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
@pytest.mark.parametrize("cursor", [None, " REQUEST_PRIVATE+/%&= 私 "])
@pytest.mark.parametrize(
    "rows,next_id",
    [
        ([], "NEXT_PRIVATE_A"),
        (
            [
                {
                    "id": "ROW_PRIVATE_Z",
                    "serial": "SER_PRIVATE",
                    "name": "NM_PRIVATE",
                    "netAddress": "NET_PRIVATE",
                }
            ],
            "",
        ),
        (
            [
                {
                    "id": "ROW_PRIVATE_Z",
                    "serial": "SER_PRIVATE",
                    "name": "NM_PRIVATE",
                    "netAddress": "NET_PRIVATE",
                }
            ],
            None,
        ),
        (
            [
                {
                    "id": "ROW_PRIVATE_Z",
                    "serial": "SER_PRIVATE",
                    "name": "NM_PRIVATE",
                    "netAddress": "NET_PRIVATE",
                }
            ],
            "NEXT_PRIVATE_B",
        ),
    ],
    ids=["empty-rows", "next-empty", "next-null", "next-set"],
)
def test_query_ok(rig, capsys, monkeypatch, mode, cursor, rows, next_id):
    if cursor is not None:
        monkeypatch.setenv("EZVIZ_DEVICE_CURSOR", cursor)
    rig.biz_hook = lambda request: httpx.Response(
        200,
        json={
            "code": "200",
            "msg": "PRIVATE_MSG",
            "data": rows,
            "page": {"total": 416, "size": 50, "nextId": next_id},
        },
    )
    assert cli.main(["--mode", mode]) == 0
    out = capsys.readouterr()
    (line,) = out.out.splitlines()
    assert out.err == "" and out.out == line + "\n"
    payload = json.loads(out.out)
    assert payload == {"returned_items": len(rows), "has_next": next_id not in ("", None)}
    assert type(payload["returned_items"]) is int and type(payload["has_next"]) is bool
    leaked = (
        "PRIVATE_MSG",
        "NEXT_PRIVATE",
        "SER_PRIVATE",
        "NM_PRIVATE",
        "NET_PRIVATE",
        "ROW_PRIVATE_Z",
        "TOK1",
        "REQUEST_PRIVATE",
    )
    assert not any(raw in out.out for raw in leaked)
    [(kind, key, secret, region)] = rig.calls
    assert (kind, key, secret, region.scope) == (mode, "SynApp", "SynSecret", "domestic")
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1
    req = rig.biz_requests[0]
    assert (req.method, req.url.scheme, req.url.host, req.url.path) == (
        "POST",
        "https",
        "open.ys7.com",
        "/api/lapp/device/list",
    )
    params = [("accessToken", "TOK1"), ("pageSize", "1")]
    if cursor is not None:
        params.append(("id", cursor))
    assert sorted(req.url.params.multi_items()) == sorted(params)
    assert req.read() == b"" and "content-type" not in req.headers
    assert "accessToken" not in req.headers and "TOK1" not in req.headers.values()
